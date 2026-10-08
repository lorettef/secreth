"""Read-only, club-scoped statistics. Games/roles and events aggregate independently."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Generic, TypeVar
from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Club, ClubMembership, Game, GameEvent, GamePlayer, GameSession, Player, TelegramUser
from app.domain.enums import EventType, GameRole, GameStatus, MembershipRole, MembershipStatus, RuinReason, SessionStatus, Team, VictoryType
from app.domain.stats import LeaderboardKind, percentage

PAGE_SIZE = 10
T = TypeVar("T")


class StatsErrorCode(StrEnum):
    NO_ACCESS = "NO_ACCESS"
    NOT_FOUND = "NOT_FOUND"
    NO_PLAYER = "NO_PLAYER"
    INVALID_PAGE = "INVALID_PAGE"
    INVALID_BOARD = "INVALID_BOARD"


class StatsError(ValueError):
    def __init__(self, code: StatsErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    page: int
    total: int

    @property
    def pages(self) -> int:
        return max(1, (self.total + PAGE_SIZE - 1) // PAGE_SIZE)


@dataclass(frozen=True)
class Score:
    games: int
    wins: int

    @property
    def winrate(self) -> float:
        return percentage(self.wins, self.games)


@dataclass(frozen=True)
class Eligibility:
    games: int
    required_games: int

    @property
    def eligible(self) -> bool:
        return self.games >= self.required_games


@dataclass(frozen=True)
class PlayerStats:
    player_id: UUID
    display_name: str
    is_active: bool
    total_games: int
    total_wins: int
    roles: dict[GameRole, Score]
    ruin_events: int
    games_with_ruin: int
    ruin_reasons: dict[RuinReason, int]
    self_admitted_ruins: int
    palivo_count: int
    iq_200_count: int
    prophet_count: int
    overall_eligibility: Eligibility
    role_eligibility: dict[GameRole, Eligibility]

    @property
    def overall_winrate(self) -> float:
        return percentage(self.total_wins, self.total_games)

    @property
    def ruin_rate(self) -> float:
        return percentage(self.games_with_ruin, self.total_games)


@dataclass(frozen=True)
class TeamStats:
    games_count: int
    liberal_wins: int
    fascist_wins: int

    @property
    def liberal_winrate(self) -> float:
        return percentage(self.liberal_wins, self.games_count)

    @property
    def fascist_winrate(self) -> float:
        return percentage(self.fascist_wins, self.games_count)


@dataclass(frozen=True)
class SessionStats:
    session_id: UUID
    club_id: UUID
    started_at: datetime
    timezone: str
    score: TeamStats
    event_counts: dict[EventType, int]
    victory_types: dict[VictoryType, int]
    players: Page[PlayerStats]


@dataclass(frozen=True)
class ClubStats:
    club_id: UUID
    club_name: str
    total_sessions: int
    score: TeamStats
    event_counts: dict[EventType, int]
    victory_types: dict[VictoryType, int]
    games_by_player_count: dict[int, TeamStats]


@dataclass(frozen=True)
class SessionChoice:
    id: UUID
    started_at: datetime
    timezone: str


@dataclass(frozen=True)
class Leaderboard:
    kind: LeaderboardKind
    required_games: int
    entries: Page[PlayerStats]


class StatsService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def player_profile(self, telegram_id: int, player_id: UUID) -> tuple[UUID, PlayerStats]:
        """Resolve compact callback identity, then authorize against its actual club."""
        club_id = await self._object_club(Player, player_id)
        return club_id, await self.get_player_stats(telegram_id, club_id, player_id)

    async def session_profile(self, telegram_id: int, session_id: UUID, *, page: int = 0) -> SessionStats:
        club_id = await self._object_club(GameSession, session_id)
        return await self.get_session_stats(telegram_id, club_id, session_id, page=page)

    async def _object_club(self, model, object_id: UUID) -> UUID:
        async with self._session_factory() as db:
            club_id = await db.scalar(select(model.club_id).where(model.id == object_id))
            if club_id is None:
                raise StatsError(StatsErrorCode.NOT_FOUND)
            return club_id

    async def get_player_stats(self, telegram_id: int, club_id: UUID, player_id: UUID) -> PlayerStats:
        async with self._session_factory() as db:
            club, role = await self._access(db, telegram_id, club_id)
            query, _, _ = self._players_query(club_id)
            row = (await db.execute(query.where(Player.id == player_id))).mappings().first()
            if row is None:
                raise StatsError(StatsErrorCode.NOT_FOUND)
            if not row["is_active"] and role is not MembershipRole.ADMIN:
                raise StatsError(StatsErrorCode.NO_ACCESS)
            return self._player(row, club)

    async def get_my_stats(self, telegram_id: int, club_id: UUID) -> PlayerStats:
        async with self._session_factory() as db:
            club, _ = await self._access(db, telegram_id, club_id)
            query, _, _ = self._players_query(club_id)
            query = query.join(TelegramUser, TelegramUser.id == Player.telegram_user_id).where(
                TelegramUser.telegram_user_id == telegram_id)
            row = (await db.execute(query)).mappings().first()
            if row is None:
                raise StatsError(StatsErrorCode.NO_PLAYER)
            return self._player(row, club)

    async def list_player_stats(self, telegram_id: int, club_id: UUID, *, page: int = 0) -> Page[PlayerStats]:
        self._check_page(page)
        async with self._session_factory() as db:
            club, role = await self._access(db, telegram_id, club_id)
            query, _, _ = self._players_query(club_id)
            if role is not MembershipRole.ADMIN:
                query = query.where(Player.is_active)
            return await self._player_page(db, query.order_by(Player.display_name, Player.id), club, page)

    async def get_session_stats(self, telegram_id: int, club_id: UUID,
                                session_id: UUID, *, page: int = 0) -> SessionStats:
        self._check_page(page)
        async with self._session_factory() as db:
            club, _ = await self._access(db, telegram_id, club_id)
            evening = await db.scalar(select(GameSession).where(GameSession.id == session_id,
                                                                 GameSession.club_id == club_id))
            if evening is None:
                raise StatsError(StatsErrorCode.NOT_FOUND)
            games = self._games(club_id, session_id)
            score, victories, events = await self._totals(db, games)
            query, roles, _ = self._players_query(club_id, session_id)
            players = await self._player_page(db, query.where(roles.c.games > 0)
                                              .order_by(Player.display_name, Player.id), club, page)
            return SessionStats(session_id, club_id, evening.started_at, club.timezone,
                                score, events, victories, players)

    async def list_sessions(self, telegram_id: int, club_id: UUID, *, page: int = 0) -> Page[SessionChoice]:
        self._check_page(page)
        async with self._session_factory() as db:
            club, _ = await self._access(db, telegram_id, club_id)
            query = select(GameSession).where(GameSession.club_id == club_id, GameSession.status != SessionStatus.VOID)
            total = await db.scalar(select(func.count()).select_from(query.subquery()))
            current = self._clamp(page, total)
            rows = (await db.scalars(query.order_by(GameSession.started_at.desc(), GameSession.id)
                                    .offset(current * PAGE_SIZE).limit(PAGE_SIZE))).all()
            return Page(tuple(SessionChoice(row.id, row.started_at, club.timezone) for row in rows), current, total)

    async def get_club_stats(self, telegram_id: int, club_id: UUID) -> ClubStats:
        async with self._session_factory() as db:
            club, _ = await self._access(db, telegram_id, club_id)
            games = self._games(club_id)
            score, victories, events = await self._totals(db, games)
            sessions = await db.scalar(select(func.count(GameSession.id)).where(
                GameSession.club_id == club_id, GameSession.status != SessionStatus.VOID))
            sizes = select(GamePlayer.game_id, func.count(GamePlayer.id).label("size"))\
                .join(games, games.c.id == GamePlayer.game_id).group_by(GamePlayer.game_id).subquery()
            rows = (await db.execute(select(sizes.c.size, games.c.winner_team, func.count())
                                    .join_from(games, sizes, sizes.c.game_id == games.c.id)
                                    .group_by(sizes.c.size, games.c.winner_team))).all()
            counts = {size: {Team.LIBERAL: 0, Team.FASCIST: 0} for size in range(5, 11)}
            for size, winner, count in rows:
                if size in counts:
                    counts[size][winner] = count
            by_size = {size: TeamStats(sum(values.values()), values[Team.LIBERAL], values[Team.FASCIST])
                       for size, values in counts.items()}
            return ClubStats(club_id, club.name, sessions, score, events, victories, by_size)

    async def get_leaderboard(self, telegram_id: int, club_id: UUID,
                              kind: LeaderboardKind = LeaderboardKind.OVERALL, *, page: int = 0) -> Leaderboard:
        self._check_page(page)
        if not isinstance(kind, LeaderboardKind):
            raise StatsError(StatsErrorCode.INVALID_BOARD)
        async with self._session_factory() as db:
            club, _ = await self._access(db, telegram_id, club_id)
            query, roles, events = self._players_query(club_id)
            role_map = {LeaderboardKind.LIBERAL: GameRole.LIBERAL, LeaderboardKind.FASCIST: GameRole.FASCIST,
                        LeaderboardKind.HITLER: GameRole.HITLER}
            role = role_map.get(kind)
            games = roles.c[f"{role.value}_games"] if role else roles.c.games
            wins = roles.c[f"{role.value}_wins"] if role else roles.c.wins
            required = club.minimum_role_games_for_leaderboard if role else club.minimum_total_games_for_leaderboard
            query = query.where(Player.is_active, func.coalesce(games, 0) >= required)
            if kind is LeaderboardKind.RUIN:
                query = query.order_by(self._rate(events.c.games_with_ruin, roles.c.games).desc(),
                                       func.coalesce(events.c.games_with_ruin, 0).desc())
            elif kind is LeaderboardKind.IQ_200:
                query = query.order_by(func.coalesce(events.c.iq_200_count, 0).desc())
            else:
                query = query.order_by(self._rate(wins, games).desc(), func.coalesce(wins, 0).desc(),
                                       func.coalesce(games, 0).desc())
            query = query.order_by(Player.display_name, Player.id)
            return Leaderboard(kind, required, await self._player_page(db, query, club, page))

    async def get_role_leaderboard(self, telegram_id: int, club_id: UUID, role: GameRole,
                                   *, page: int = 0) -> Leaderboard:
        if not isinstance(role, GameRole):
            raise StatsError(StatsErrorCode.INVALID_BOARD)
        return await self.get_leaderboard(telegram_id, club_id, LeaderboardKind(role.value.lower()), page=page)

    async def get_event_leaderboard(self, telegram_id: int, club_id: UUID, event_type: EventType,
                                    *, page: int = 0) -> Leaderboard:
        if not isinstance(event_type, EventType) or event_type not in (EventType.RUIN, EventType.IQ_200):
            raise StatsError(StatsErrorCode.INVALID_BOARD)
        kind = LeaderboardKind.RUIN if event_type is EventType.RUIN else LeaderboardKind.IQ_200
        return await self.get_leaderboard(telegram_id, club_id, kind, page=page)

    async def _access(self, db: AsyncSession, telegram_id: int, club_id: UUID) -> tuple[Club, MembershipRole]:
        row = (await db.execute(select(Club, ClubMembership.role)
            .join(ClubMembership, ClubMembership.club_id == Club.id)
            .join(TelegramUser, TelegramUser.id == ClubMembership.telegram_user_id)
            .where(Club.id == club_id, TelegramUser.telegram_user_id == telegram_id,
                   ClubMembership.status == MembershipStatus.ACTIVE))).first()
        if row is None:
            raise StatsError(StatsErrorCode.NO_ACCESS)
        return row.Club, row.role

    @staticmethod
    def _games(club_id: UUID, session_id: UUID | None = None):
        query = select(Game.id, Game.winner_team, Game.victory_type).join(GameSession).where(
            GameSession.club_id == club_id, Game.status == GameStatus.ACTIVE)
        if session_id is not None:
            query = query.where(Game.session_id == session_id)
        return query.subquery()

    def _players_query(self, club_id: UUID, session_id: UUID | None = None):
        games = self._games(club_id, session_id)
        won = or_((GamePlayer.role == GameRole.LIBERAL) & (games.c.winner_team == Team.LIBERAL),
                  GamePlayer.role.in_((GameRole.FASCIST, GameRole.HITLER)) & (games.c.winner_team == Team.FASCIST))
        columns = [func.count().label("games"), func.sum(case((won, 1), else_=0)).label("wins")]
        for role in GameRole:
            columns += [func.count().filter(GamePlayer.role == role).label(f"{role.value}_games"),
                        func.sum(case((won, 1), else_=0)).filter(GamePlayer.role == role).label(f"{role.value}_wins")]
        roles = select(GamePlayer.player_id, *columns).join(games, games.c.id == GamePlayer.game_id)\
            .group_by(GamePlayer.player_id).subquery()
        ruin = GameEvent.event_type == EventType.RUIN
        event_columns = [func.count().filter(ruin).label("ruin_events"),
                         func.count(func.distinct(GameEvent.game_id)).filter(ruin).label("games_with_ruin"),
                         func.count().filter(ruin & GameEvent.self_admitted).label("self_admitted_ruins")]
        for event_type, label in [(EventType.PALIVO, "palivo_count"), (EventType.IQ_200, "iq_200_count"),
                                  (EventType.PROPHET, "prophet_count")]:
            event_columns.append(func.count().filter(GameEvent.event_type == event_type).label(label))
        for reason in RuinReason:
            event_columns.append(func.count().filter(ruin & (GameEvent.ruin_reason == reason)).label(reason.value))
        events = select(GameEvent.player_id, *event_columns).join(games, games.c.id == GameEvent.game_id)\
            .group_by(GameEvent.player_id).subquery()
        query = select(Player.id.label("player_id"), Player.display_name, Player.is_active,
                       *[func.coalesce(roles.c[c.name], 0).label(c.name) for c in columns],
                       *[func.coalesce(events.c[c.name], 0).label(c.name) for c in event_columns])\
            .outerjoin(roles, roles.c.player_id == Player.id).outerjoin(events, events.c.player_id == Player.id)\
            .where(Player.club_id == club_id)
        return query, roles, events

    @staticmethod
    def _player(row, club: Club) -> PlayerStats:
        roles = {role: Score(row[f"{role.value}_games"], row[f"{role.value}_wins"]) for role in GameRole}
        return PlayerStats(row["player_id"], row["display_name"], row["is_active"], row["games"], row["wins"], roles,
            row["ruin_events"], row["games_with_ruin"], {reason: row[reason.value] for reason in RuinReason},
            row["self_admitted_ruins"], row["palivo_count"], row["iq_200_count"], row["prophet_count"],
            Eligibility(row["games"], club.minimum_total_games_for_leaderboard),
            {role: Eligibility(score.games, club.minimum_role_games_for_leaderboard) for role, score in roles.items()})

    async def _player_page(self, db: AsyncSession, query, club: Club, page: int) -> Page[PlayerStats]:
        total = await db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
        current = self._clamp(page, total)
        rows = (await db.execute(query.offset(current * PAGE_SIZE).limit(PAGE_SIZE))).mappings().all()
        return Page(tuple(self._player(row, club) for row in rows), current, total)

    async def _totals(self, db: AsyncSession, games) -> tuple[TeamStats, dict[VictoryType, int], dict[EventType, int]]:
        rows = (await db.execute(select(games.c.winner_team, games.c.victory_type, func.count())
                                .group_by(games.c.winner_team, games.c.victory_type))).all()
        wins = {team: 0 for team in Team}
        victories = {victory: 0 for victory in VictoryType}
        for winner, victory, count in rows:
            wins[winner] += count
            victories[victory or VictoryType.UNKNOWN] += count
        event_rows = (await db.execute(select(GameEvent.event_type, func.count())
            .join(games, games.c.id == GameEvent.game_id).group_by(GameEvent.event_type))).all()
        events = {event_type: 0 for event_type in EventType}
        events.update(dict(event_rows))
        return TeamStats(sum(wins.values()), wins[Team.LIBERAL], wins[Team.FASCIST]), victories, events

    @staticmethod
    def _rate(numerator, denominator):
        return func.coalesce(numerator * 100.0 / func.nullif(denominator, 0), 0)

    @staticmethod
    def _check_page(page: int) -> None:
        if type(page) is not int or page < 0:
            raise StatsError(StatsErrorCode.INVALID_PAGE)

    @staticmethod
    def _clamp(page: int, total: int) -> int:
        return min(page, max(0, (total - 1) // PAGE_SIZE))
