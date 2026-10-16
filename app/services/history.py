"""Club-scoped history, administrative player changes and audit reads."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditLog, Club, Game, GameEvent, GamePlayer, GameSession, Player
from app.domain.enums import GameRole, GameStatus, MembershipStatus, SessionStatus, Team, VictoryType
from app.domain.permissions import ActorContext, SessionContext, can_edit_game, can_manage_club
from app.domain.revisions import revision
from app.services.access import resolve_actor
from app.services.events import EventView
from app.services.games import GameError, GameErrorCode, GamePerson, GameService
from app.services.stats import PAGE_SIZE, Page

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HistorySession:
    id: UUID
    started_at: datetime
    timezone: str
    status: SessionStatus
    games_count: int


@dataclass(frozen=True)
class HistoryGame:
    id: UUID
    session_id: UUID
    sequence_number: int
    played_at: datetime
    timezone: str
    winner: Team
    status: GameStatus
    player_count: int


@dataclass(frozen=True)
class HistoryPlayer:
    id: UUID
    name: str
    is_active: bool
    revision: str


@dataclass(frozen=True)
class HistoryCard:
    game: HistoryGame
    club_id: UUID
    victory_type: VictoryType
    participants: tuple[GamePerson, ...]
    roles: dict[UUID, GameRole]
    events: Page[EventView]
    revision: str
    can_edit: bool
    is_admin: bool
    can_add_event: bool


@dataclass(frozen=True)
class AuditEntry:
    id: UUID
    created_at: datetime
    timezone: str
    actor_name: str
    action: str
    entity_type: str
    entity_id: UUID
    before: dict | None
    after: dict | None


class HistoryService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def club_access(self, telegram_id: int, club_id: UUID, *, admin: bool = False) -> None:
        async with self._session_factory() as db:
            actor = await self._actor(db, telegram_id, club_id)
            if admin and not can_manage_club(actor, club_id):
                raise GameError(GameErrorCode.NO_ACCESS)

    async def list_sessions(self, telegram_id: int, club_id: UUID, *, page: int = 0) -> Page[HistorySession]:
        async with self._session_factory() as db:
            actor = await self._actor(db, telegram_id, club_id)
            counts = select(Game.session_id, func.count().label("games_count")).where(Game.status == GameStatus.ACTIVE)\
                .group_by(Game.session_id).subquery()
            query = select(GameSession, Club.timezone, func.coalesce(counts.c.games_count, 0)).join(Club)\
                .outerjoin(counts, counts.c.session_id == GameSession.id).where(GameSession.club_id == club_id)
            if not can_manage_club(actor, club_id):
                query = query.where(GameSession.status != SessionStatus.VOID)
            rows, current, total = await self._page(db, query.order_by(GameSession.started_at.desc(), GameSession.id), page)
            return Page(tuple(HistorySession(s.id, s.started_at, tz, s.status, count) for s, tz, count in rows), current, total)

    async def list_games(self, telegram_id: int, club_id: UUID, *, page: int = 0,
                         session_id: UUID | None = None, player_id: UUID | None = None) -> Page[HistoryGame]:
        async with self._session_factory() as db:
            actor = await self._actor(db, telegram_id, club_id)
            if session_id is not None:
                evening = await db.scalar(select(GameSession).where(GameSession.id == session_id, GameSession.club_id == club_id))
                if evening is None or (evening.status is SessionStatus.VOID and not can_manage_club(actor, club_id)):
                    raise GameError(GameErrorCode.NOT_FOUND)
            if player_id is not None and await db.scalar(select(Player.id).where(Player.id == player_id, Player.club_id == club_id)) is None:
                raise GameError(GameErrorCode.NOT_FOUND)
            counts = select(GamePlayer.game_id, func.count().label("player_count")).group_by(GamePlayer.game_id).subquery()
            query = select(Game, Club.timezone, func.coalesce(counts.c.player_count, 0)).select_from(Game)\
                .join(GameSession, GameSession.id == Game.session_id).join(Club, Club.id == GameSession.club_id)\
                .outerjoin(counts, counts.c.game_id == Game.id).where(GameSession.club_id == club_id)
            if not can_manage_club(actor, club_id):
                query = query.where(Game.status == GameStatus.ACTIVE, GameSession.status != SessionStatus.VOID)
            if session_id:
                query = query.where(Game.session_id == session_id)
            if player_id:
                query = query.where(select(GamePlayer.id).where(GamePlayer.game_id == Game.id, GamePlayer.player_id == player_id).exists())
            rows, current, total = await self._page(db, query.order_by(Game.played_at.desc(), Game.sequence_number.desc(), Game.id), page)
            return Page(tuple(self._game(g, tz, count) for g, tz, count in rows), current, total)

    async def object_club(self, telegram_id: int, entity: str, object_id: UUID, *, admin: bool = False) -> UUID:
        models = {"session": GameSession, "player": Player, "audit": AuditLog}
        async with self._session_factory() as db:
            if entity == "event":
                query = select(GameSession.club_id).join(Game).join(GameEvent).where(GameEvent.id == object_id)
            elif entity == "game":
                query = select(GameSession.club_id).join(Game).where(Game.id == object_id)
            elif entity in models:
                model = models[entity]
                query = select(model.club_id).where(model.id == object_id)
            else:
                raise GameError(GameErrorCode.NOT_FOUND)
            club_id = await db.scalar(query)
            if club_id is None:
                raise GameError(GameErrorCode.NOT_FOUND)
            actor = await self._actor(db, telegram_id, club_id)
            if admin and not can_manage_club(actor, club_id):
                raise GameError(GameErrorCode.NO_ACCESS)
            if entity == "session":
                status = await db.scalar(select(GameSession.status).where(GameSession.id == object_id))
                if status is SessionStatus.VOID and not can_manage_club(actor, club_id):
                    raise GameError(GameErrorCode.NO_ACCESS)
            return club_id

    async def card(self, telegram_id: int, game_id: UUID, *, event_page: int = 0) -> HistoryCard:
        async with self._session_factory() as db:
            row = (await db.execute(select(Game, GameSession, Club.timezone).select_from(Game)
                                    .join(GameSession, GameSession.id == Game.session_id).join(Club, Club.id == GameSession.club_id)
                                    .where(Game.id == game_id))).first()
            if row is None:
                raise GameError(GameErrorCode.NOT_FOUND)
            game, evening, tz = row
            actor = await self._actor(db, telegram_id, evening.club_id)
            admin = can_manage_club(actor, evening.club_id)
            if not admin and (game.status is GameStatus.VOID or evening.status is SessionStatus.VOID):
                raise GameError(GameErrorCode.NO_ACCESS)
            rows = (await db.execute(select(Player.id, Player.display_name, GamePlayer.role).join(GamePlayer)
                                    .where(GamePlayer.game_id == game_id).order_by(Player.display_name, Player.id))).all()
            events = select(GameEvent, Player.display_name).join(Player, Player.id == GameEvent.player_id)\
                .where(GameEvent.game_id == game_id).order_by(GameEvent.created_at, GameEvent.id)
            event_rows, current, total = await self._page(db, events, event_page)
            views = tuple(EventView(e.id, e.game_id, e.player_id, name, e.event_type, e.ruin_reason, e.self_admitted, e.comment)
                          for e, name in event_rows)
            editable = game.status is GameStatus.ACTIVE and can_edit_game(actor, SessionContext(
                evening.club_id, evening.status, evening.statistician_player_id))
            return HistoryCard(self._game(game, tz, len(rows)), evening.club_id, game.victory_type or VictoryType.UNKNOWN,
                tuple(GamePerson(pid, name) for pid, name, role in rows), {pid: role for pid, name, role in rows},
                Page(views, current, total), revision(GameService.snapshot(game, [(pid, role) for pid, name, role in rows])),
                editable, admin, editable and actor.player_id is not None)

    async def edit_candidates(self, telegram_id: int, game_id: UUID) -> tuple[GamePerson, ...]:
        card = await self.card(telegram_id, game_id)
        if not card.can_edit:
            raise GameError(GameErrorCode.NO_ACCESS)
        # Historical corrections can retain or select historical inactive Players.
        async with self._session_factory() as db:
            return tuple(GamePerson(*r) for r in (await db.execute(select(Player.id, Player.display_name)
                .where(Player.club_id == card.club_id).order_by(Player.display_name, Player.id))).all())

    async def list_players(self, telegram_id: int, club_id: UUID, *, page: int = 0,
                           admin: bool = False) -> Page[HistoryPlayer]:
        async with self._session_factory() as db:
            actor = await self._actor(db, telegram_id, club_id)
            if admin and not can_manage_club(actor, club_id):
                raise GameError(GameErrorCode.NO_ACCESS)
            rows, current, total = await self._page(db, select(Player).where(Player.club_id == club_id)
                                                    .order_by(Player.display_name, Player.id), page)
            return Page(tuple(self._player(p) for p, in rows), current, total)

    async def player(self, telegram_id: int, player_id: UUID) -> tuple[UUID, HistoryPlayer]:
        club_id = await self.object_club(telegram_id, "player", player_id, admin=True)
        async with self._session_factory() as db:
            return club_id, self._player(await db.get(Player, player_id))

    async def edit_player(self, telegram_id: int, player_id: UUID, *, expected_revision: str,
                          display_name: str | None = None, is_active: bool | None = None) -> HistoryPlayer:
        async with self._session_factory() as db, db.begin():
            p = await db.scalar(select(Player).where(Player.id == player_id).with_for_update())
            if p is None:
                raise GameError(GameErrorCode.NOT_FOUND)
            actor = await self._actor(db, telegram_id, p.club_id)
            if not can_manage_club(actor, p.club_id):
                raise GameError(GameErrorCode.NO_ACCESS)
            before = self._player_snapshot(p)
            if revision(before) != expected_revision:
                raise GameError(GameErrorCode.STALE)
            if display_name is not None:
                display_name = display_name.strip()
                if not display_name or len(display_name) > 255:
                    raise GameError(GameErrorCode.INVALID_NAME)
                p.display_name = display_name
            if is_active is not None:
                if type(is_active) is not bool:
                    raise GameError(GameErrorCode.INVALID_NAME)
                p.is_active = is_active
            p.updated_at = datetime.now(timezone.utc)
            await db.flush()
            db.add(AuditLog(club_id=p.club_id, actor_player_id=actor.player_id, entity_type="Player", entity_id=p.id,
                            action="PLAYER_EDIT", before_data=before,
                            created_at=func.clock_timestamp(),
                            after_data={**self._player_snapshot(p), "actor_telegram_id": telegram_id}))
            logger.info("Player edited: player=%s", p.id)
            return self._player(p)

    async def list_audit(self, telegram_id: int, club_id: UUID, *, page: int = 0) -> Page[AuditEntry]:
        async with self._session_factory() as db:
            actor = await self._actor(db, telegram_id, club_id)
            if not can_manage_club(actor, club_id):
                raise GameError(GameErrorCode.NO_ACCESS)
            query = select(AuditLog, Player.display_name, Club.timezone).select_from(AuditLog).join(Club, Club.id == AuditLog.club_id)\
                .outerjoin(Player, Player.id == AuditLog.actor_player_id).where(AuditLog.club_id == club_id)
            rows, current, total = await self._page(db, query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()), page)
            return Page(tuple(self._audit(log, name, tz) for log, name, tz in rows), current, total)

    async def audit(self, telegram_id: int, audit_id: UUID) -> tuple[UUID, AuditEntry]:
        club_id = await self.object_club(telegram_id, "audit", audit_id, admin=True)
        async with self._session_factory() as db:
            log, name, tz = (await db.execute(select(AuditLog, Player.display_name, Club.timezone).select_from(AuditLog)
                .join(Club, Club.id == AuditLog.club_id)
                .outerjoin(Player, Player.id == AuditLog.actor_player_id).where(AuditLog.id == audit_id, AuditLog.club_id == club_id))).one()
            return club_id, self._audit(log, name, tz)

    @staticmethod
    async def _actor(
        db: AsyncSession, telegram_id: int, club_id: UUID
    ) -> ActorContext:
        actor = await resolve_actor(db, telegram_id, club_id)
        if actor.membership_status is not MembershipStatus.ACTIVE:
            raise GameError(GameErrorCode.NO_ACCESS)
        return actor

    @staticmethod
    def _audit(log: AuditLog, name: str | None, tz: str) -> AuditEntry:
        actor = name or str((log.after_data or {}).get("actor_telegram_id", "—"))
        return AuditEntry(log.id, log.created_at, tz, actor, log.action, log.entity_type, log.entity_id, log.before_data, log.after_data)

    @staticmethod
    def _player_snapshot(p: Player) -> dict:
        return {"display_name": p.display_name, "is_active": p.is_active, "updated_at": p.updated_at.isoformat()}

    @classmethod
    def _player(cls, p: Player) -> HistoryPlayer:
        return HistoryPlayer(p.id, p.display_name, p.is_active, revision(cls._player_snapshot(p)))

    @staticmethod
    def _game(g: Game, tz: str, count: int) -> HistoryGame:
        return HistoryGame(g.id, g.session_id, g.sequence_number, g.played_at, tz, g.winner_team, g.status, count)

    @staticmethod
    async def _page(db: AsyncSession, query, page: int) -> tuple[list, int, int]:
        if type(page) is not int or page < 0:
            raise GameError(GameErrorCode.STALE)
        total = await db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
        current = min(page, max(0, (total - 1) // PAGE_SIZE))
        return (await db.execute(query.limit(PAGE_SIZE).offset(current * PAGE_SIZE))).all(), current, total
