"""Validate and persist one completed game atomically."""

import logging
import uuid
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ClubMembership, Game, GamePlayer, GameSession, Player, SessionParticipant, TelegramUser
from app.domain.enums import GameRole, MembershipStatus, SessionStatus, Team, VictoryType
from app.domain.permissions import ActorContext, SessionContext, can_create_game
from app.domain.roles import RoleValidationError, assign_liberals, validate_game_roles
from app.domain.victories import VictoryValidationError, validate_victory

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
                SessionParticipant.session_id == session_id, SessionParticipant.is_present))
            if present_count < 5:
                raise GameError(GameErrorCode.TOO_FEW_PRESENT)
            club_ids = set((await db.scalars(select(Player.id).where(
                Player.club_id == evening.club_id))).all())
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
            await self._actor(db, telegram_id, evening.club_id)
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
        row = (await db.execute(select(ClubMembership, Player)
            .join(TelegramUser, TelegramUser.id == ClubMembership.telegram_user_id)
            .outerjoin(Player, (Player.club_id == club_id) &
                       (Player.telegram_user_id == TelegramUser.id))
            .where(TelegramUser.telegram_user_id == telegram_id,
                   ClubMembership.club_id == club_id))).first()
        if row is None or row.ClubMembership.status is not MembershipStatus.ACTIVE:
            raise GameError(GameErrorCode.NO_ACCESS)
        membership, player = row
        return ActorContext(club_id, membership.role, membership.status,
                            player.id if player else None, bool(player and player.is_active))

    async def _roster(self, db: AsyncSession, session_id: uuid.UUID) -> tuple[GamePerson, ...]:
        rows = await db.execute(select(Player.id, Player.display_name)
            .join(SessionParticipant, SessionParticipant.player_id == Player.id)
            .where(SessionParticipant.session_id == session_id, SessionParticipant.is_present)
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
