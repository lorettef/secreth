"""Validate and persist one completed game atomically."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditLog, Game, GameEvent, GamePlayer, GameSession, Player, SessionParticipant
from app.domain.enums import GameRole, GameStatus, SessionStatus, Team, VictoryType
from app.domain.permissions import ActorContext, SessionContext, can_create_game, can_edit_game, can_manage_club, can_view_club
from app.domain.revisions import revision
from app.domain.roles import RoleValidationError, assign_liberals, validate_game_roles
from app.domain.victories import VictoryValidationError, validate_victory
from app.services.access import resolve_actor

logger = logging.getLogger(__name__)


class GameErrorCode(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    NO_ACCESS = "NO_ACCESS"
    SESSION_FINISHED = "SESSION_FINISHED"
    NOT_STATISTICIAN = "NOT_STATISTICIAN"
    TOO_FEW_PRESENT = "TOO_FEW_PRESENT"
    INVALID_PARTICIPANTS = "INVALID_PARTICIPANTS"
    INVALID_ROLES = "INVALID_ROLES"
    INVALID_VICTORY = "INVALID_VICTORY"
    DRAFT_CONFLICT = "DRAFT_CONFLICT"
    STALE = "STALE"
    EVENT_CONFLICT = "EVENT_CONFLICT"
    INVALID_NAME = "INVALID_NAME"


class GameError(ValueError):
    def __init__(self, code: GameErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class GamePerson:
    id: uuid.UUID
    name: str


@dataclass(frozen=True)
class GameSetup:
    session_id: uuid.UUID
    sequence_number: int
    roster: tuple[GamePerson, ...]
    candidates: tuple[GamePerson, ...]


@dataclass(frozen=True)
class GameResult:
    id: uuid.UUID
    session_id: uuid.UUID
    sequence_number: int
    participants: tuple[GamePerson, ...]
    hitler: GamePerson
    fascists: tuple[GamePerson, ...]
    liberals: tuple[GamePerson, ...]
    winner: Team
    victory_type: VictoryType


class GameService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def setup(self, telegram_id: int, session_id: uuid.UUID) -> GameSetup:
        async with self._session_factory() as db:
            evening, _ = await self._editor(db, telegram_id, session_id)
            roster = await self._roster(db, session_id)
            if len(roster) < 5:
                raise GameError(GameErrorCode.TOO_FEW_PRESENT)
            if len(roster) > 10:
                candidates = roster
            else:
                active = tuple(GamePerson(*row) for row in (await db.execute(
                    select(Player.id, Player.display_name).where(
                        Player.club_id == evening.club_id, Player.is_active)
                    .order_by(Player.display_name, Player.id)
                )).all())
                candidates = tuple(sorted({p.id: p for p in (*active, *roster)}.values(),
                                          key=lambda p: (p.name, p.id)))
            number = (await db.scalar(select(func.max(Game.sequence_number)).where(
                Game.session_id == session_id)) or 0) + 1
            return GameSetup(session_id, number, roster, candidates)

    async def create_game(
        self, telegram_id: int, session_id: uuid.UUID, draft_id: uuid.UUID,
        participants: tuple[uuid.UUID, ...], hitler: uuid.UUID,
        fascists: tuple[uuid.UUID, ...], winner: Team, victory_type: VictoryType,
    ) -> tuple[GameResult, bool]:
        if not isinstance(draft_id, uuid.UUID):
            raise GameError(GameErrorCode.DRAFT_CONFLICT)
        async with self._session_factory() as db, db.begin():
            evening = await db.scalar(select(GameSession).where(
                GameSession.id == session_id).with_for_update())
            if evening is None:
                raise GameError(GameErrorCode.NOT_FOUND)
            actor = await self._actor(db, telegram_id, evening.club_id)
            if actor.player_id is None or not actor.player_is_active:
                raise GameError(GameErrorCode.NO_ACCESS)
            existing = await db.scalar(select(Game).where(Game.draft_id == draft_id))
            if existing is not None:
                if existing.session_id != session_id or existing.created_by_player_id != actor.player_id:
                    raise GameError(GameErrorCode.DRAFT_CONFLICT)
                return await self._result(db, existing), False
            if evening.status is not SessionStatus.ACTIVE:
                raise GameError(GameErrorCode.SESSION_FINISHED)
            if not can_create_game(actor, SessionContext(evening.club_id, evening.status,
                                                         evening.statistician_player_id)):
                raise GameError(GameErrorCode.NOT_STATISTICIAN)
            present_count = await db.scalar(select(func.count(SessionParticipant.id)).where(
                SessionParticipant.session_id == session_id, SessionParticipant.is_present)
                .join(Player, Player.id == SessionParticipant.player_id).where(Player.is_active))
            if present_count < 5:
                raise GameError(GameErrorCode.TOO_FEW_PRESENT)
            club_ids = set((await db.scalars(select(Player.id).where(
                Player.club_id == evening.club_id, Player.is_active))).all())
            try:
                liberals = assign_liberals(participants, hitler, fascists)
                assignments = [(pid, GameRole.HITLER) for pid in (hitler,)]
                assignments += [(pid, GameRole.FASCIST) for pid in fascists]
                assignments += [(pid, GameRole.LIBERAL) for pid in liberals]
                validate_game_roles(participants, assignments, club_player_ids=club_ids)
            except RoleValidationError as error:
                code = (GameErrorCode.INVALID_PARTICIPANTS if "participant" in str(error).lower()
                        or "5–10" in str(error) else GameErrorCode.INVALID_ROLES)
                raise GameError(code) from error
            try:
                validate_victory(winner, victory_type)
            except (VictoryValidationError, KeyError) as error:
                raise GameError(GameErrorCode.INVALID_VICTORY) from error
            number = (await db.scalar(select(func.max(Game.sequence_number)).where(
                Game.session_id == session_id)) or 0) + 1
            game = Game(session_id=session_id, draft_id=draft_id, sequence_number=number,
                        winner_team=winner, victory_type=victory_type,
                        created_by_player_id=actor.player_id)
            db.add(game)
            await db.flush()
            db.add_all(GamePlayer(game_id=game.id, player_id=pid, role=role)
                       for pid, role in assignments)
            await db.flush()
            logger.info("Game created: session=%s game=%s sequence=%s", session_id, game.id, number)
            return await self._result(db, game), True

    async def saved_draft(self, telegram_id: int, draft_id: uuid.UUID) -> GameResult | None:
        async with self._session_factory() as db:
            game = await db.scalar(select(Game).where(Game.draft_id == draft_id))
            if game is None:
                return None
            evening = await db.get(GameSession, game.session_id)
            actor = await self._actor(db, telegram_id, evening.club_id)
            if actor.player_id != game.created_by_player_id:
                raise GameError(GameErrorCode.DRAFT_CONFLICT)
            return await self._result(db, game)

    async def game(self, telegram_id: int, game_id: uuid.UUID) -> GameResult:
        async with self._session_factory() as db:
            game = await db.get(Game, game_id)
            if game is None:
                raise GameError(GameErrorCode.NOT_FOUND)
            evening = await db.get(GameSession, game.session_id)
            actor = await self._actor(db, telegram_id, evening.club_id)
            if (game.status is GameStatus.VOID or evening.status is SessionStatus.VOID) and not can_manage_club(actor, evening.club_id):
                raise GameError(GameErrorCode.NO_ACCESS)
            return await self._result(db, game)

    async def _editor(self, db: AsyncSession, telegram_id: int,
                      session_id: uuid.UUID) -> tuple[GameSession, ActorContext]:
        evening = await db.get(GameSession, session_id)
        if evening is None:
            raise GameError(GameErrorCode.NOT_FOUND)
        actor = await self._actor(db, telegram_id, evening.club_id)
        if actor.player_id is None or not actor.player_is_active:
            raise GameError(GameErrorCode.NO_ACCESS)
        if evening.status is not SessionStatus.ACTIVE:
            raise GameError(GameErrorCode.SESSION_FINISHED)
        if not can_create_game(actor, SessionContext(evening.club_id, evening.status,
                                                     evening.statistician_player_id)):
            raise GameError(GameErrorCode.NOT_STATISTICIAN)
        return evening, actor

    async def _actor(self, db: AsyncSession, telegram_id: int,
                     club_id: uuid.UUID) -> ActorContext:
        actor = await resolve_actor(db, telegram_id, club_id)
        if not can_view_club(actor, club_id):
            raise GameError(GameErrorCode.NO_ACCESS)
        return actor

    async def _roster(self, db: AsyncSession, session_id: uuid.UUID) -> tuple[GamePerson, ...]:
        rows = await db.execute(select(Player.id, Player.display_name)
            .join(SessionParticipant, SessionParticipant.player_id == Player.id)
            .where(SessionParticipant.session_id == session_id, SessionParticipant.is_present, Player.is_active)
            .order_by(Player.display_name, Player.id))
        return tuple(GamePerson(*row) for row in rows)

    async def _result(self, db: AsyncSession, game: Game) -> GameResult:
        rows = (await db.execute(select(GamePlayer.player_id, Player.display_name, GamePlayer.role)
            .join(Player, Player.id == GamePlayer.player_id)
            .where(GamePlayer.game_id == game.id)
            .order_by(Player.display_name, Player.id))).all()
        participants = tuple(GamePerson(pid, name) for pid, name, _ in rows)
        by_role = {role: tuple(GamePerson(pid, name) for pid, name, assigned in rows if assigned is role)
                   for role in GameRole}
        return GameResult(game.id, game.session_id, game.sequence_number, participants,
                          by_role[GameRole.HITLER][0], by_role[GameRole.FASCIST],
                          by_role[GameRole.LIBERAL], game.winner_team, game.victory_type)

    @staticmethod
    def snapshot(game: Game, assignments: list[tuple[uuid.UUID, GameRole]]) -> dict:
        return {"session_id": str(game.session_id), "sequence_number": game.sequence_number,
                "status": game.status.value, "winner": game.winner_team.value,
                "victory_type": game.victory_type.value if game.victory_type else None,
                "participants": [{"player_id": str(pid), "role": role.value}
                                 for pid, role in sorted(assignments, key=lambda item: item[0])],
                "updated_at": game.updated_at.isoformat()}

    async def _change_context(self, db: AsyncSession, telegram_id: int, game_id: uuid.UUID) -> tuple[Game, GameSession, ActorContext, list[GamePlayer]]:
        game = await db.get(Game, game_id)
        if game is None:
            raise GameError(GameErrorCode.NOT_FOUND)
        # Same lock order as event writes and session lifecycle operations.
        evening = await db.scalar(select(GameSession).where(GameSession.id == game.session_id).with_for_update())
        game = await db.scalar(select(Game).where(Game.id == game_id).with_for_update()
                               .execution_options(populate_existing=True))
        actor = await self._actor(db, telegram_id, evening.club_id)
        rows = (await db.scalars(select(GamePlayer).where(GamePlayer.game_id == game_id))).all()
        return game, evening, actor, rows

    @staticmethod
    def _require_revision(game: Game, rows: list[GamePlayer], expected_revision: str) -> dict:
        before = GameService.snapshot(game, [(p.player_id, p.role) for p in rows])
        if revision(before) != expected_revision:
            raise GameError(GameErrorCode.STALE)
        return before

    async def _validate_snapshot(self, db: AsyncSession, evening: GameSession,
                                 participants: tuple[uuid.UUID, ...], hitler: uuid.UUID,
                                 fascists: tuple[uuid.UUID, ...], winner: Team,
                                 victory_type: VictoryType, *, allow_unknown: bool = False) -> list[tuple[uuid.UUID, GameRole]]:
        club_ids = (await db.scalars(select(Player.id).where(Player.club_id == evening.club_id))).all()
        try:
            liberals = assign_liberals(participants, hitler, fascists)
            assignments = [(hitler, GameRole.HITLER), *[(pid, GameRole.FASCIST) for pid in fascists],
                           *[(pid, GameRole.LIBERAL) for pid in liberals]]
            validate_game_roles(participants, assignments, club_player_ids=club_ids)
        except RoleValidationError as exc:
            raise GameError(GameErrorCode.INVALID_ROLES) from exc
        try:
            validate_victory(winner, victory_type, allow_unknown=allow_unknown)
        except VictoryValidationError as exc:
            raise GameError(GameErrorCode.INVALID_VICTORY) from exc
        return assignments

    async def edit_game(self, telegram_id: int, game_id: uuid.UUID, *, expected_revision: str,
                        participants: tuple[uuid.UUID, ...], hitler: uuid.UUID, fascists: tuple[uuid.UUID, ...],
                        winner: Team, victory_type: VictoryType) -> GameResult:
        async with self._session_factory() as db, db.begin():
            game, evening, actor, rows = await self._change_context(db, telegram_id, game_id)
            if not can_edit_game(actor, SessionContext(evening.club_id, evening.status, evening.statistician_player_id)):
                raise GameError(GameErrorCode.NO_ACCESS)
            if game.status is not GameStatus.ACTIVE:
                raise GameError(GameErrorCode.STALE)
            before = self._require_revision(game, rows, expected_revision)
            assignments = await self._validate_snapshot(db, evening, participants, hitler, fascists, winner, victory_type,
                                                        allow_unknown=game.victory_type in (None, VictoryType.UNKNOWN) and victory_type is VictoryType.UNKNOWN)
            event_players = set((await db.scalars(select(GameEvent.player_id).where(GameEvent.game_id == game_id))).all())
            if not event_players <= set(participants):
                raise GameError(GameErrorCode.EVENT_CONFLICT)
            targets = dict(assignments)
            existing = {p.player_id: p for p in rows}
            # Keep referenced GamePlayer rows. Delete only removed, event-free participants.
            for pid, row in existing.items():
                if pid not in targets:
                    await db.delete(row)
                else:
                    row.role = targets[pid]
            db.add_all(GamePlayer(game_id=game_id, player_id=pid, role=role)
                       for pid, role in assignments if pid not in existing)
            game.winner_team, game.victory_type = winner, victory_type
            game.updated_at = datetime.now(timezone.utc)
            await db.flush()
            self._audit_change(db, evening, actor, telegram_id, game, "GAME_EDIT", before,
                               self.snapshot(game, assignments))
            logger.info("Game edited: game=%s", game_id)
            return await self._result(db, game)

    async def set_game_void(self, telegram_id: int, game_id: uuid.UUID, *, expected_revision: str,
                            restore: bool = False) -> None:
        async with self._session_factory() as db, db.begin():
            game, evening, actor, rows = await self._change_context(db, telegram_id, game_id)
            if not can_manage_club(actor, evening.club_id):
                raise GameError(GameErrorCode.NO_ACCESS)
            before = self._require_revision(game, rows, expected_revision)
            if game.status is not (GameStatus.VOID if restore else GameStatus.ACTIVE):
                raise GameError(GameErrorCode.STALE)
            if restore:
                assignments = [(p.player_id, p.role) for p in rows]
                club_ids = (await db.scalars(select(Player.id).where(Player.club_id == evening.club_id))).all()
                try:
                    validate_game_roles(tuple(p.player_id for p in rows), assignments, club_player_ids=club_ids)
                except RoleValidationError as exc:
                    raise GameError(GameErrorCode.INVALID_ROLES) from exc
                try:
                    validate_victory(game.winner_team, game.victory_type or VictoryType.UNKNOWN, allow_unknown=True)
                except VictoryValidationError as exc:
                    raise GameError(GameErrorCode.INVALID_VICTORY) from exc
            game.status = GameStatus.ACTIVE if restore else GameStatus.VOID
            game.updated_at = datetime.now(timezone.utc)
            await db.flush()
            self._audit_change(db, evening, actor, telegram_id, game, "GAME_RESTORE" if restore else "GAME_VOID",
                               before, self.snapshot(game, [(p.player_id, p.role) for p in rows]))
            logger.info("Game status changed: game=%s status=%s", game_id, game.status.value)

    @staticmethod
    def _audit_change(db: AsyncSession, evening: GameSession, actor: ActorContext, telegram_id: int,
                      game: Game, action: str, before: dict, after: dict) -> None:
        db.add(AuditLog(club_id=evening.club_id, actor_player_id=actor.player_id, entity_type="Game",
                        entity_id=game.id, action=action, before_data=before,
                        created_at=func.clock_timestamp(),
                        after_data={**after, "actor_telegram_id": telegram_id}))
