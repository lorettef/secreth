"""Snapshot-based events, permissions, idempotent writes and administrative audit."""

import logging
from datetime import datetime, timezone
from dataclasses import asdict, dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditLog, Game, GameEvent, GamePlayer, GameSession, Player
from app.domain.enums import EventType, GameStatus, MembershipStatus, RuinReason, SessionStatus
from app.domain.revisions import revision
from app.domain.events import EventFields, EventValidationError, validate_event
from app.domain.permissions import ActorContext, SessionContext, can_edit_game, can_manage_club
from app.services.access import resolve_actor
from app.services.games import GamePerson

logger = logging.getLogger(__name__)


class EventErrorCode(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    NO_ACCESS = "NO_ACCESS"
    INVALID_EVENT = "INVALID_EVENT"
    DRAFT_CONFLICT = "DRAFT_CONFLICT"
    STALE = "STALE"


class EventError(ValueError):
    def __init__(self, code: EventErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class EventView:
    id: UUID
    game_id: UUID
    player_id: UUID
    player_name: str
    event_type: EventType
    ruin_reason: RuinReason | None
    self_admitted: bool
    comment: str | None


@dataclass(frozen=True)
class EventSetup:
    game_id: UUID
    session_id: UUID
    participants: tuple[GamePerson, ...]
    can_create: bool


class EventService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def setup(self, telegram_id: int, game_id: UUID) -> EventSetup:
        async with self._session_factory() as db:
            game, evening, actor = await self._context(db, telegram_id, game_id)
            rows = (await db.execute(select(Player.id, Player.display_name)
                .join(GamePlayer, GamePlayer.player_id == Player.id)
                .where(GamePlayer.game_id == game_id).order_by(Player.display_name, Player.id))).all()
            return EventSetup(game_id, evening.id, tuple(GamePerson(*row) for row in rows),
                              self._can_create(game, evening, actor))

    async def create_event(self, telegram_id: int, game_id: UUID, draft_id: UUID,
                           player_id: UUID, event_type: EventType,
                           ruin_reason: RuinReason | None = None, self_admitted: bool = False,
                           comment: str | None = None) -> tuple[EventView, bool]:
        if not isinstance(draft_id, UUID):
            raise EventError(EventErrorCode.DRAFT_CONFLICT)
        async with self._session_factory() as db, db.begin():
            game, evening, actor = await self._context(db, telegram_id, game_id, lock=True)
            if not self._can_create(game, evening, actor):
                raise EventError(EventErrorCode.NO_ACCESS)
            existing = await db.get(GameEvent, draft_id)
            if existing is not None:
                self._same_draft(existing, game_id, actor)
                return await self._view(db, existing), False
            # A replay must not resurrect an event that an ADMIN already deleted.
            deleted = await db.scalar(select(AuditLog.id).where(
                AuditLog.entity_type == "GameEvent", AuditLog.entity_id == draft_id,
                AuditLog.action == "EVENT_DELETE"))
            if deleted:
                raise EventError(EventErrorCode.DRAFT_CONFLICT)
            fields = await self._validate(db, game_id, player_id, event_type, ruin_reason,
                                          self_admitted, comment)
            # The existing event PK is the draft key; no new schema is needed.
            inserted = await db.scalar(insert(GameEvent).values(
                id=draft_id, game_id=game_id, player_id=player_id,
                created_by_player_id=actor.player_id, **asdict(fields)
            ).on_conflict_do_nothing(index_elements=["id"]).returning(GameEvent.id))
            event = await db.get(GameEvent, draft_id)
            self._same_draft(event, game_id, actor)
            if inserted:
                if can_manage_club(actor, evening.club_id):
                    self._audit(db, evening, actor, event, "EVENT_CREATE", None, self._snapshot(event))
                logger.info("Event created: game=%s event=%s", game_id, event.id)
            return await self._view(db, event), inserted is not None

    async def list_game_events(self, telegram_id: int, game_id: UUID) -> tuple[EventView, ...]:
        async with self._session_factory() as db:
            await self._context(db, telegram_id, game_id)
            events = (await db.scalars(select(GameEvent).where(GameEvent.game_id == game_id)
                                      .order_by(GameEvent.created_at, GameEvent.id))).all()
            return tuple([await self._view(db, event) for event in events])

    async def event(self, telegram_id: int, event_id: UUID) -> EventView:
        async with self._session_factory() as db:
            event = await db.get(GameEvent, event_id)
            if event is None:
                raise EventError(EventErrorCode.NOT_FOUND)
            await self._context(db, telegram_id, event.game_id)
            return await self._view(db, event)

    async def update_event(self, telegram_id: int, event_id: UUID, *, player_id: UUID,
                           event_type: EventType, ruin_reason: RuinReason | None = None,
                           self_admitted: bool = False, comment: str | None = None,
                           expected_revision: str | None = None) -> EventView:
        async with self._session_factory() as db, db.begin():
            event, evening, actor = await self._editable_event(db, telegram_id, event_id)
            if expected_revision is not None and expected_revision != self.revision(event):
                raise EventError(EventErrorCode.STALE)
            fields = await self._validate(db, event.game_id, player_id, event_type,
                                          ruin_reason, self_admitted, comment)
            before = self._snapshot(event)
            event.player_id = player_id
            for key, value in asdict(fields).items():
                setattr(event, key, value)
            event.updated_at = datetime.now(timezone.utc)
            await db.flush()
            self._audit(db, evening, actor, event, "EVENT_EDIT", before, self._snapshot(event))
            logger.info("Event edited: event=%s", event_id)
            return await self._view(db, event)

    async def delete_event(self, telegram_id: int, event_id: UUID, *, expected_revision: str | None = None) -> None:
        async with self._session_factory() as db, db.begin():
            event, evening, actor = await self._editable_event(db, telegram_id, event_id, admin_only=True)
            if expected_revision is not None and expected_revision != self.revision(event):
                raise EventError(EventErrorCode.STALE)
            self._audit(db, evening, actor, event, "EVENT_DELETE", self._snapshot(event), None)
            await db.delete(event)
            logger.info("Event deleted: event=%s", event_id)

    async def _context(self, db: AsyncSession, telegram_id: int, game_id: UUID,
                       *, lock: bool = False) -> tuple[Game, GameSession, ActorContext]:
        game = await db.get(Game, game_id)
        if game is None:
            raise EventError(EventErrorCode.NOT_FOUND)
        statement = select(GameSession).where(GameSession.id == game.session_id)
        if lock:
            statement = statement.with_for_update()
        evening = await db.scalar(statement)
        if lock:
            game = await db.scalar(select(Game).where(Game.id == game_id).with_for_update()
                                   .execution_options(populate_existing=True))
        actor = await resolve_actor(db, telegram_id, evening.club_id)
        if actor.membership_status is not MembershipStatus.ACTIVE:
            raise EventError(EventErrorCode.NO_ACCESS)
        if (game.status is GameStatus.VOID or evening.status is SessionStatus.VOID) and not can_manage_club(actor, evening.club_id):
            raise EventError(EventErrorCode.NO_ACCESS)
        return game, evening, actor

    @classmethod
    def revision(cls, event: GameEvent) -> str:
        return revision({**cls._snapshot(event), "updated_at": event.updated_at.isoformat()})

    async def edit_setup(self, telegram_id: int, event_id: UUID) -> tuple[EventView, EventSetup, str]:
        async with self._session_factory() as db, db.begin():
            event, evening, actor = await self._editable_event(db, telegram_id, event_id)
            rows = (await db.execute(select(Player.id, Player.display_name).join(GamePlayer)
                                    .where(GamePlayer.game_id == event.game_id).order_by(Player.display_name, Player.id))).all()
            return await self._view(db, event), EventSetup(event.game_id, evening.id,
                   tuple(GamePerson(*row) for row in rows), True), self.revision(event)

    @staticmethod
    def _can_create(game: Game, evening: GameSession, actor: ActorContext) -> bool:
        return (game.status is GameStatus.ACTIVE and actor.player_id is not None
                and can_edit_game(actor, SessionContext(
                    evening.club_id, evening.status, evening.statistician_player_id)))

    async def _editable_event(self, db: AsyncSession, telegram_id: int,
                              event_id: UUID, *, admin_only: bool = False) -> tuple[GameEvent, GameSession, ActorContext]:
        event = await db.get(GameEvent, event_id)
        if event is None:
            raise EventError(EventErrorCode.NOT_FOUND)
        _, evening, actor = await self._context(db, telegram_id, event.game_id, lock=True)
        allowed = (can_manage_club(actor, evening.club_id) if admin_only else
                   can_edit_game(actor, SessionContext(evening.club_id, evening.status, evening.statistician_player_id)))
        if not allowed:
            raise EventError(EventErrorCode.NO_ACCESS)
        # Refresh after session lock: another admin may have edited/deleted it while waiting.
        event = await db.scalar(select(GameEvent).where(GameEvent.id == event_id)
                               .execution_options(populate_existing=True))
        if event is None:
            raise EventError(EventErrorCode.NOT_FOUND)
        return event, evening, actor

    @staticmethod
    def _same_draft(event: GameEvent, game_id: UUID, actor: ActorContext) -> None:
        if event.game_id != game_id or event.created_by_player_id != actor.player_id:
            raise EventError(EventErrorCode.DRAFT_CONFLICT)

    async def _validate(self, db: AsyncSession, game_id: UUID, player_id: UUID,
                        event_type: EventType, ruin_reason: RuinReason | None,
                        self_admitted: bool, comment: str | None) -> EventFields:
        participants = (await db.scalars(select(GamePlayer.player_id).where(GamePlayer.game_id == game_id))).all()
        try:
            return validate_event(player_id, participants, event_type, ruin_reason, self_admitted, comment)
        except EventValidationError as error:
            raise EventError(EventErrorCode.INVALID_EVENT) from error

    async def _view(self, db: AsyncSession, event: GameEvent) -> EventView:
        name = await db.scalar(select(Player.display_name).where(Player.id == event.player_id))
        return EventView(event.id, event.game_id, event.player_id, name, event.event_type,
                         event.ruin_reason, event.self_admitted, event.comment)

    @staticmethod
    def _snapshot(event: GameEvent) -> dict:
        return {"game_id": str(event.game_id), "player_id": str(event.player_id),
                "event_type": event.event_type.value,
                "ruin_reason": event.ruin_reason.value if event.ruin_reason else None,
                "self_admitted": event.self_admitted, "comment": event.comment,
                "created_by_player_id": str(event.created_by_player_id)}

    @staticmethod
    def _audit(db: AsyncSession, evening: GameSession, actor: ActorContext, event: GameEvent,
               action: str, before: dict | None, after: dict | None) -> None:
        db.add(AuditLog(club_id=evening.club_id, actor_player_id=actor.player_id,
                        entity_type="GameEvent", entity_id=event.id, action=action,
                        created_at=func.clock_timestamp(),
                        before_data=before, after_data=after))
