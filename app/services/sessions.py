"""Transactional game evening and live presence operations."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditLog, Club, ClubMembership, Game, GameSession, Player, SessionParticipant, TelegramUser
from app.domain.enums import GameStatus, MembershipStatus, SessionStatus, Team
from app.domain.permissions import ActorContext, SessionContext, can_manage_current_session, can_start_session

logger = logging.getLogger(__name__)


class SessionErrorCode(StrEnum):
    NO_ACCESS = "NO_ACCESS"
    PLAYER_REQUIRED = "PLAYER_REQUIRED"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_FINISHED = "SESSION_FINISHED"
    NOT_STATISTICIAN = "NOT_STATISTICIAN"
    PLAYER_NOT_AVAILABLE = "PLAYER_NOT_AVAILABLE"
    ALREADY_PRESENT = "ALREADY_PRESENT"
    NOT_PRESENT = "NOT_PRESENT"
    INVALID_SELECTION = "INVALID_SELECTION"
    TRANSFER_NOT_AVAILABLE = "TRANSFER_NOT_AVAILABLE"


class SessionError(ValueError):
    def __init__(self, code: SessionErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class RosterPlayer:
    id: uuid.UUID
    name: str


@dataclass(frozen=True)
class SessionView:
    id: uuid.UUID
    club_id: uuid.UUID
    club_name: str
    status: SessionStatus
    statistician_name: str
    roster: tuple[RosterPlayer, ...]
    game_count: int
    liberal_wins: int
    fascist_wins: int
    can_manage: bool


@dataclass(frozen=True)
class SessionMenuState:
    active_session_id: uuid.UUID | None
    can_start: bool


class SessionService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def menu_state(self, telegram_id: int, club_id: uuid.UUID) -> SessionMenuState:
        async with self._session_factory() as db:
            actor = await self._actor(db, telegram_id, club_id)
            active = await self._active(db, club_id)
            return SessionMenuState(
                active.id if active else None,
                can_start_session(actor, club_id, has_active_session=active is not None),
            )

    async def active_players(self, telegram_id: int, club_id: uuid.UUID) -> tuple[RosterPlayer, ...]:
        async with self._session_factory() as db:
            actor = await self._actor(db, telegram_id, club_id)
            if not can_start_session(actor, club_id, has_active_session=await self._active(db, club_id) is not None):
                raise SessionError(SessionErrorCode.NO_ACCESS)
            return await self._players(db, club_id)

    async def start(
        self, telegram_id: int, club_id: uuid.UUID, present_player_ids: tuple[uuid.UUID, ...]
    ) -> tuple[SessionView, bool]:
        async with self._session_factory() as db, db.begin():
            # Serialize creators by club. The partial unique index is the final DB guard.
            club = await db.scalar(select(Club).where(Club.id == club_id).with_for_update())
            if club is None:
                raise SessionError(SessionErrorCode.NO_ACCESS)
            actor = await self._actor(db, telegram_id, club_id)
            if actor.player_id is None or not actor.player_is_active:
                raise SessionError(SessionErrorCode.PLAYER_REQUIRED)
            active = await self._active(db, club_id)
            if active is not None:
                return await self._view(db, active, actor), False
            if not can_start_session(actor, club_id, has_active_session=False):
                raise SessionError(SessionErrorCode.NO_ACCESS)
            if len(set(present_player_ids)) != len(present_player_ids):
                raise SessionError(SessionErrorCode.INVALID_SELECTION)
            valid = {p.id for p in await self._players(db, club_id)}
            if not set(present_player_ids) <= valid:
                raise SessionError(SessionErrorCode.INVALID_SELECTION)
            evening = GameSession(
                club_id=club_id, status=SessionStatus.ACTIVE,
                created_by_player_id=actor.player_id,
                statistician_player_id=actor.player_id,
            )
            db.add(evening)
            await db.flush()
            db.add_all(SessionParticipant(session_id=evening.id, player_id=pid) for pid in present_player_ids)
            await db.flush()
            view = await self._view(db, evening, actor)
            logger.info("Game session created: club=%s session=%s", club_id, evening.id)
            return view, True

    async def view(self, telegram_id: int, session_id: uuid.UUID) -> SessionView:
        async with self._session_factory() as db:
            evening = await db.get(GameSession, session_id)
            if evening is None:
                raise SessionError(SessionErrorCode.SESSION_NOT_FOUND)
            actor = await self._actor(db, telegram_id, evening.club_id)
            return await self._view(db, evening, actor)

    async def roster_choices(
        self, telegram_id: int, session_id: uuid.UUID, action: str
    ) -> tuple[RosterPlayer, ...]:
        async with self._session_factory() as db:
            evening, _ = await self._managed(db, telegram_id, session_id)
            present = {p.id for p in await self._roster(db, session_id)}
            if action == "remove":
                return await self._roster(db, session_id)
            if action == "add":
                return tuple(p for p in await self._players(db, evening.club_id) if p.id not in present)
            if action == "transfer":
                present = {p.id for p in await self._roster(db, session_id)}
                rows = await db.execute(
                    select(Player.id, Player.display_name)
                    .join(ClubMembership, (ClubMembership.club_id == Player.club_id) & (ClubMembership.telegram_user_id == Player.telegram_user_id))
                    .where(Player.club_id == evening.club_id, Player.is_active,
                           ClubMembership.status == MembershipStatus.ACTIVE,
                           Player.id != evening.statistician_player_id)
                    .order_by(Player.display_name, Player.id)
                )
                return tuple(sorted((RosterPlayer(*row) for row in rows),
                                    key=lambda p: (p.id not in present, p.name, p.id)))
            raise SessionError(SessionErrorCode.INVALID_SELECTION)

    async def add_player(self, telegram_id: int, session_id: uuid.UUID, player_id: uuid.UUID) -> SessionView:
        async with self._session_factory() as db, db.begin():
            evening, actor = await self._managed(db, telegram_id, session_id, lock=True)
            if player_id not in {p.id for p in await self._players(db, evening.club_id)}:
                raise SessionError(SessionErrorCode.PLAYER_NOT_AVAILABLE)
            if player_id in {p.id for p in await self._roster(db, session_id)}:
                raise SessionError(SessionErrorCode.ALREADY_PRESENT)
            db.add(SessionParticipant(session_id=session_id, player_id=player_id))
            await db.flush()
            return await self._view(db, evening, actor)

    async def remove_player(self, telegram_id: int, session_id: uuid.UUID, player_id: uuid.UUID) -> SessionView:
        async with self._session_factory() as db, db.begin():
            evening, actor = await self._managed(db, telegram_id, session_id, lock=True)
            interval = await db.scalar(select(SessionParticipant).where(
                SessionParticipant.session_id == session_id,
                SessionParticipant.player_id == player_id,
                SessionParticipant.is_present,
            ))
            if interval is None:
                raise SessionError(SessionErrorCode.NOT_PRESENT)
            interval.is_present = False
            interval.left_at = datetime.now(timezone.utc)
            await db.flush()
            return await self._view(db, evening, actor)

    async def transfer(self, telegram_id: int, session_id: uuid.UUID, player_id: uuid.UUID) -> SessionView:
        async with self._session_factory() as db, db.begin():
            evening, actor = await self._managed(db, telegram_id, session_id, lock=True)
            rows = await db.execute(
                select(Player.id).join(ClubMembership,
                    (ClubMembership.club_id == Player.club_id) & (ClubMembership.telegram_user_id == Player.telegram_user_id))
                .where(Player.id == player_id, Player.club_id == evening.club_id, Player.is_active,
                       ClubMembership.status == MembershipStatus.ACTIVE)
            )
            if player_id == evening.statistician_player_id or rows.first() is None:
                raise SessionError(SessionErrorCode.TRANSFER_NOT_AVAILABLE)
            previous = evening.statistician_player_id
            evening.statistician_player_id = player_id
            db.add(AuditLog(club_id=evening.club_id, actor_player_id=actor.player_id,
                            entity_type="GameSession", entity_id=evening.id,
                            action="STATISTICIAN_TRANSFER",
                            before_data={"statistician_player_id": str(previous)},
                            after_data={"statistician_player_id": str(player_id)}))
            await db.flush()
            logger.info("Statistician transferred: session=%s", session_id)
            return await self._view(db, evening, actor)

    async def finish(self, telegram_id: int, session_id: uuid.UUID) -> SessionView:
        async with self._session_factory() as db, db.begin():
            evening, actor = await self._managed(db, telegram_id, session_id, lock=True)
            evening.status = SessionStatus.FINISHED
            evening.finished_at = datetime.now(timezone.utc)
            await db.flush()
            logger.info("Game session finished: session=%s", session_id)
            return await self._view(db, evening, actor)

    async def _actor(self, db: AsyncSession, telegram_id: int, club_id: uuid.UUID) -> ActorContext:
        row = (await db.execute(
            select(ClubMembership, Player)
            .join(TelegramUser, TelegramUser.id == ClubMembership.telegram_user_id)
            .outerjoin(Player, (Player.club_id == ClubMembership.club_id) & (Player.telegram_user_id == TelegramUser.id))
            .where(TelegramUser.telegram_user_id == telegram_id, ClubMembership.club_id == club_id)
        )).first()
        if row is None or row.ClubMembership.status is not MembershipStatus.ACTIVE:
            raise SessionError(SessionErrorCode.NO_ACCESS)
        membership, player = row
        return ActorContext(club_id, membership.role, membership.status,
                            player.id if player else None, bool(player and player.is_active))

    async def _active(self, db: AsyncSession, club_id: uuid.UUID) -> GameSession | None:
        return await db.scalar(select(GameSession).where(GameSession.club_id == club_id,
                                                         GameSession.status == SessionStatus.ACTIVE))

    async def _managed(self, db: AsyncSession, telegram_id: int, session_id: uuid.UUID,
                       *, lock: bool = False) -> tuple[GameSession, ActorContext]:
        query = select(GameSession).where(GameSession.id == session_id)
        if lock:
            query = query.with_for_update()
        evening = await db.scalar(query)
        if evening is None:
            raise SessionError(SessionErrorCode.SESSION_NOT_FOUND)
        actor = await self._actor(db, telegram_id, evening.club_id)
        if evening.status is not SessionStatus.ACTIVE:
            raise SessionError(SessionErrorCode.SESSION_FINISHED)
        if not can_manage_current_session(actor, SessionContext(evening.club_id, evening.status,
                                                                 evening.statistician_player_id)):
            raise SessionError(SessionErrorCode.NOT_STATISTICIAN)
        return evening, actor

    async def _players(self, db: AsyncSession, club_id: uuid.UUID) -> tuple[RosterPlayer, ...]:
        rows = await db.execute(select(Player.id, Player.display_name).where(
            Player.club_id == club_id, Player.is_active).order_by(Player.display_name, Player.id))
        return tuple(RosterPlayer(*row) for row in rows)

    async def _roster(self, db: AsyncSession, session_id: uuid.UUID) -> tuple[RosterPlayer, ...]:
        rows = await db.execute(select(Player.id, Player.display_name)
            .join(SessionParticipant, SessionParticipant.player_id == Player.id)
            .where(SessionParticipant.session_id == session_id, SessionParticipant.is_present)
            .order_by(Player.display_name, Player.id))
        return tuple(RosterPlayer(*row) for row in rows)

    async def _view(self, db: AsyncSession, evening: GameSession, actor: ActorContext) -> SessionView:
        club_name = await db.scalar(select(Club.name).where(Club.id == evening.club_id))
        stat_name = await db.scalar(select(Player.display_name).where(Player.id == evening.statistician_player_id))
        scores = (await db.execute(select(Game.winner_team, func.count(Game.id)).where(
            Game.session_id == evening.id, Game.status == GameStatus.ACTIVE).group_by(Game.winner_team))).all()
        score = dict(scores)
        return SessionView(evening.id, evening.club_id, club_name, evening.status, stat_name,
                           await self._roster(db, evening.id), sum(score.values()),
                           score.get(Team.LIBERAL, 0), score.get(Team.FASCIST, 0),
                           can_manage_current_session(actor, SessionContext(evening.club_id,
                               evening.status, evening.statistician_player_id)))
