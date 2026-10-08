"""Read-only aggregates against migrated PostgreSQL; no counters or history UI."""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import AuditLog, Club, ClubMembership, Game, GameEvent, GamePlayer, GameSession, Player, SessionParticipant
from app.domain.enums import EventType, GameRole, GameStatus, MembershipRole, MembershipStatus, RuinReason, SessionStatus, Team, VictoryType
from app.domain.roles import get_role_distribution
from app.domain.stats import LeaderboardKind, percentage
from app.services.stats import StatsError, StatsErrorCode, StatsService
from test_games_integration import seed, test_database_url


@dataclass
class Fixture:
    stats: StatsService
    factory: object
    engine: object
    club: UUID
    evening: UUID
    second: UUID
    void_session: UUID
    players: tuple[UUID, ...]
    games: tuple[UUID, ...]
    events: tuple[UUID, ...]
    other_club: UUID
    other_evening: UUID
    other_players: tuple[UUID, ...]


async def add_game(factory, session, players, sequence, target_role, winner, *, victory=None, status=GameStatus.ACTIVE):
    """Valid role snapshot: target is always players[0]."""
    distribution = get_role_distribution(len(players))
    hitler = players[0] if target_role is GameRole.HITLER else players[1]
    fascists = [pid for pid in players if pid != hitler and (pid != players[0] or target_role is GameRole.FASCIST)][:distribution.fascists]
    if target_role is GameRole.FASCIST and players[0] not in fascists:
        fascists[0] = players[0]
    async with factory() as db, db.begin():
        game = Game(session_id=session, sequence_number=sequence, winner_team=winner, status=status,
                    victory_type= victory or (VictoryType.LIBERAL_POLICIES if winner is Team.LIBERAL else VictoryType.FASCIST_POLICIES),
                    created_by_player_id=players[0])
        db.add(game)
        await db.flush()
        db.add_all(GamePlayer(game_id=game.id, player_id=pid,
                             role=GameRole.HITLER if pid == hitler else GameRole.FASCIST if pid in fascists else GameRole.LIBERAL)
                   for pid in players)
        await db.flush()
        return game.id


async def add_event(factory, game, player, kind, *, reason=None, admitted=False):
    async with factory() as db, db.begin():
        moment = GameEvent(game_id=game, player_id=player, event_type=kind, ruin_reason=reason,
                           self_admitted=admitted, created_by_player_id=player)
        db.add(moment)
        await db.flush()
        return moment.id


@pytest_asyncio.fixture
async def stats_data(test_database_url):
    engine = create_async_engine(test_database_url)
    async with engine.connect() as connection:
        tx = await connection.begin()
        factory = async_sessionmaker(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        club, evening, players, _ = await seed(factory)
        other, other_evening, others, _ = await seed(factory, telegram_ids=(8901, 8902))
        async with factory() as db, db.begin():
            db.add_all([
                GameSession(id=UUID(int=2001), club_id=club, status=SessionStatus.FINISHED,
                            created_by_player_id=players[0], statistician_player_id=players[0]),
                GameSession(id=UUID(int=2002), club_id=club, status=SessionStatus.VOID,
                            created_by_player_id=players[0], statistician_player_id=players[0]),
            ])
            (await db.get(Player, players[0])).display_name = "Максим"
        games = []
        # Exact manual sanity example: Liberal 4/3, Fascist 2/1, Hitler 2/1.
        outcomes = [(GameRole.LIBERAL, Team.LIBERAL), (GameRole.LIBERAL, Team.LIBERAL),
                    (GameRole.LIBERAL, Team.LIBERAL), (GameRole.LIBERAL, Team.FASCIST),
                    (GameRole.FASCIST, Team.FASCIST), (GameRole.FASCIST, Team.LIBERAL),
                    (GameRole.HITLER, Team.FASCIST), (GameRole.HITLER, Team.LIBERAL)]
        for index, (role, winner) in enumerate(outcomes):
            # Late joiner p5: three Games only; early leaver p4: first six.
            group = players[:5] if index < 3 else players[:6] if index < 6 else players[:4] + (players[5],)
            victory = [VictoryType.LIBERAL_POLICIES, VictoryType.HITLER_KILLED, VictoryType.UNKNOWN,
                       VictoryType.HITLER_CHANCELLOR, VictoryType.FASCIST_POLICIES, VictoryType.HITLER_KILLED,
                       VictoryType.FASCIST_POLICIES, VictoryType.LIBERAL_POLICIES][index]
            games.append(await add_game(factory, evening if index < 6 else UUID(int=2001), group,
                                        index + 1 if index < 6 else index - 5, role, winner, victory=victory))
        games.append(await add_game(factory, evening, players[:5], 7, GameRole.HITLER, Team.FASCIST, status=GameStatus.VOID))
        await add_game(factory, other_evening, others[:5], 1, GameRole.HITLER, Team.FASCIST)
        events = []
        for index, reason, admitted in [(0, RuinReason.WRONG_VOTE, True), (0, RuinReason.ROLE_REVEAL, False),
                                        (1, RuinReason.WRONG_VOTE, True)]:
            events.append(await add_event(factory, games[index], players[0], EventType.RUIN, reason=reason, admitted=admitted))
        for kind in (EventType.PALIVO, EventType.IQ_200, EventType.PROPHET):
            events.append(await add_event(factory, games[0], players[0], kind))
            await add_event(factory, games[-1], players[0], kind)
        await add_event(factory, games[-1], players[0], EventType.RUIN, reason=RuinReason.OTHER)
        await add_event(factory, games[0], players[2], EventType.IQ_200)
        # Same event type in a different club must not leak into club or player aggregates.
        async with factory() as db:
            other_game = await db.scalar(select(Game.id).where(Game.session_id == other_evening))
        await add_event(factory, other_game, others[0], EventType.RUIN, reason=RuinReason.OTHER)
        yield Fixture(StatsService(factory), factory, engine, club, evening, UUID(int=2001), UUID(int=2002),
                      players, tuple(games), tuple(events), other, other_evening, others)
        await tx.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_manual_player_sanity_and_independent_aggregates(stats_data):
    f = stats_data
    p = await f.stats.get_my_stats(8801, f.club)
    assert (p.total_games, p.total_wins, p.overall_winrate) == (8, 5, 62.5)
    assert [(s.games, s.wins, s.winrate) for s in p.roles.values()] == [(4, 3, 75), (2, 1, 50), (2, 1, 50)]
    assert (p.ruin_events, p.games_with_ruin, p.ruin_rate) == (3, 2, 25)
    assert p.self_admitted_ruins == 2
    assert (p.palivo_count, p.iq_200_count, p.prophet_count) == (1, 1, 1)
    assert p.ruin_reasons == {r: 2 if r is RuinReason.WRONG_VOTE else 1 if r is RuinReason.ROLE_REVEAL else 0 for r in RuinReason}
    assert not p.overall_eligibility.eligible and p.overall_eligibility.required_games == 10
    print(f"StatsService: {p.total_games} games, {p.total_wins} wins, {p.overall_winrate}%; "
          + "; ".join(f"{role.value}: {score.games} games/{score.wins} wins={score.winrate}%" for role, score in p.roles.items())
          + f"; RUIN: {p.ruin_events} events/{p.games_with_ruin} games={p.ruin_rate}%")


@pytest.mark.asyncio
async def test_historical_player_without_telegram_and_zero_data(stats_data):
    f = stats_data
    p = await f.stats.get_player_stats(8801, f.club, f.players[2])
    assert p.total_games == 8
    zero = await f.stats.get_player_stats(8801, f.club, f.players[-1])
    assert (zero.total_games, zero.total_wins, zero.overall_winrate, zero.ruin_rate) == (0, 0, 0, 0)
    assert all(s.games == s.wins == s.winrate == 0 for s in zero.roles.values())
    assert all(v == 0 for v in zero.ruin_reasons.values())
    assert zero.ruin_events == zero.palivo_count == zero.iq_200_count == zero.prophet_count == 0


@pytest.mark.asyncio
async def test_session_totals_and_snapshot_late_joiner_early_leaver(stats_data):
    f = stats_data
    s = await f.stats.get_session_stats(8801, f.club, f.evening)
    assert (s.score.games_count, s.score.liberal_wins, s.score.fascist_wins) == (6, 4, 2)
    assert s.score.liberal_winrate == pytest.approx(100 * 4 / 6)
    assert s.event_counts == {EventType.RUIN: 3, EventType.PALIVO: 1, EventType.IQ_200: 2, EventType.PROPHET: 1}
    participants = {p.player_id: p for p in s.players.items}
    assert participants[f.players[4]].total_games == 6
    assert participants[f.players[5]].total_games == 3
    assert f.players[6] not in participants  # Present all evening, but never played.
    finished = await f.stats.get_session_stats(8801, f.club, f.second)
    assert finished.score.games_count == 2
    assert f.players[4] not in {p.player_id for p in finished.players.items}
    # Roster changes have no effect on saved scores.
    async with f.factory() as db, db.begin():
        await db.execute(update(SessionParticipant).where(SessionParticipant.session_id == f.evening)
                         .values(is_present=False, left_at=datetime.now(timezone.utc)))
    assert await f.stats.get_session_stats(8801, f.club, f.evening) == s


@pytest.mark.asyncio
async def test_club_totals_victory_distribution_and_player_counts(stats_data):
    f = stats_data
    s = await f.stats.get_club_stats(8801, f.club)
    assert s.total_sessions == 2
    assert (s.score.games_count, s.score.liberal_wins, s.score.fascist_wins) == (8, 5, 3)
    assert (s.score.liberal_winrate, s.score.fascist_winrate) == (62.5, 37.5)
    assert s.victory_types == {VictoryType.LIBERAL_POLICIES: 2, VictoryType.HITLER_KILLED: 2,
                              VictoryType.FASCIST_POLICIES: 2, VictoryType.HITLER_CHANCELLOR: 1, VictoryType.UNKNOWN: 1}
    assert s.games_by_player_count[5].games_count == 5
    assert s.games_by_player_count[6].games_count == 3
    assert s.games_by_player_count[5].liberal_wins == 4
    assert s.games_by_player_count[6].fascist_wins == 2
    assert all(s.games_by_player_count[size].games_count == 0 for size in (7, 8, 9, 10))
    assert s.event_counts[EventType.RUIN] == 3
    assert s.event_counts[EventType.IQ_200] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(LeaderboardKind))
async def test_all_six_leaderboards_thresholds_and_historical_eligibility(stats_data, kind):
    f = stats_data
    default = await f.stats.get_leaderboard(8801, f.club, kind)
    assert f.players[0] not in {p.player_id for p in default.entries.items}
    assert default.required_games == (5 if kind in (LeaderboardKind.LIBERAL, LeaderboardKind.FASCIST, LeaderboardKind.HITLER) else 10)
    if kind not in (LeaderboardKind.LIBERAL, LeaderboardKind.FASCIST, LeaderboardKind.HITLER):
        assert not default.entries.items
    async with f.factory() as db, db.begin():
        club = await db.get(Club, f.club)
        club.minimum_total_games_for_leaderboard = 8
        club.minimum_role_games_for_leaderboard = 2
    board = await f.stats.get_leaderboard(8801, f.club, kind)
    assert board.required_games == (2 if kind in (LeaderboardKind.LIBERAL, LeaderboardKind.FASCIST, LeaderboardKind.HITLER) else 8)
    assert f.players[0] in {p.player_id for p in board.entries.items}
    if kind in (LeaderboardKind.OVERALL, LeaderboardKind.RUIN, LeaderboardKind.IQ_200):
        assert f.players[2] in {p.player_id for p in board.entries.items}  # No Telegram binding.
        assert f.players[5] not in {p.player_id for p in board.entries.items}  # Five games <8.
    for p in board.entries.items:
        info = p.role_eligibility[GameRole(kind.value.upper())] if kind in (LeaderboardKind.LIBERAL, LeaderboardKind.FASCIST, LeaderboardKind.HITLER) else p.overall_eligibility
        assert info.eligible


@pytest.mark.asyncio
async def test_winrate_tie_breaker_wins_games_name_id_and_event_ordering(stats_data):
    f = stats_data
    # At threshold zero, zero-game players exercise final name/id ties deterministically.
    async with f.factory() as db, db.begin():
        (await db.get(Club, f.club)).minimum_total_games_for_leaderboard = 0
        (await db.get(Club, f.club)).minimum_role_games_for_leaderboard = 0
        for pid in f.players[6:]:
            (await db.get(Player, pid)).display_name = "Equal"
    board = await f.stats.get_leaderboard(8801, f.club)
    expected = sorted(board.entries.items, key=lambda p: (-p.overall_winrate, -p.total_wins, -p.total_games, p.display_name, p.player_id))
    assert list(board.entries.items) == expected
    assert [p.player_id for p in board.entries.items if p.display_name == "Equal"] == sorted(f.players[6:])
    ruin = await f.stats.get_event_leaderboard(8801, f.club, EventType.RUIN)
    assert ruin.entries.items[0].player_id == f.players[0]
    iq = await f.stats.get_event_leaderboard(8801, f.club, EventType.IQ_200)
    assert [p.player_id for p in iq.entries.items[:2]] == [f.players[2], f.players[0]]  # Equal count, name ASC.
    for role in GameRole:
        board = await f.stats.get_role_leaderboard(8801, f.club, role)
        assert list(board.entries.items) == sorted(board.entries.items, key=lambda p: (-p.roles[role].winrate,
                -p.roles[role].wins, -p.roles[role].games, p.display_name, p.player_id))


@pytest.mark.asyncio
async def test_inactive_player_retains_stats_admin_profile_but_not_leaderboard(stats_data):
    f = stats_data
    async with f.factory() as db, db.begin():
        (await db.get(Player, f.players[2])).is_active = False
        (await db.get(Club, f.club)).minimum_total_games_for_leaderboard = 1
    assert (await f.stats.get_player_stats(8802, f.club, f.players[2])).total_games == 8
    with pytest.raises(StatsError, match="NO_ACCESS"):
        await f.stats.get_player_stats(8801, f.club, f.players[2])
    for kind in LeaderboardKind:
        assert f.players[2] not in {p.player_id for p in (await f.stats.get_leaderboard(8802, f.club, kind)).entries.items}
    assert f.players[2] not in {p.player_id for p in (await f.stats.list_player_stats(8801, f.club)).items}
    assert f.players[2] in {p.player_id for p in (await f.stats.list_player_stats(8802, f.club)).items}


@pytest.mark.asyncio
async def test_void_restore_affects_all_aggregates_and_leaderboards(stats_data):
    f = stats_data
    async with f.factory() as db, db.begin():
        (await db.get(Club, f.club)).minimum_total_games_for_leaderboard = 8
        (await db.get(Club, f.club)).minimum_role_games_for_leaderboard = 4
    before = await f.stats.get_player_stats(8801, f.club, f.players[0])
    async with f.factory() as db, db.begin():
        (await db.get(Game, f.games[0])).status = GameStatus.VOID
    p = await f.stats.get_player_stats(8801, f.club, f.players[0])
    assert (p.total_games, p.total_wins, p.ruin_events, p.games_with_ruin) == (7, 4, 1, 1)
    assert p.roles[GameRole.LIBERAL].games == 3
    assert (p.palivo_count, p.iq_200_count, p.prophet_count) == (0, 0, 0)
    assert (await f.stats.get_session_stats(8801, f.club, f.evening)).score.games_count == 5
    club = await f.stats.get_club_stats(8801, f.club)
    assert club.score.games_count == 7 and club.games_by_player_count[5].games_count == 4
    assert club.event_counts[EventType.RUIN] == 1 and club.event_counts[EventType.IQ_200] == 0
    for kind in LeaderboardKind:
        assert f.players[0] not in {p.player_id for p in (await f.stats.get_leaderboard(8801, f.club, kind)).entries.items}
    async with f.factory() as db, db.begin():
        (await db.get(Game, f.games[0])).status = GameStatus.ACTIVE
    assert await f.stats.get_player_stats(8801, f.club, f.players[0]) == before
    assert (await f.stats.get_club_stats(8801, f.club)).score.games_count == 8
    assert (await f.stats.get_leaderboard(8801, f.club)).entries.total > 0


@pytest.mark.asyncio
async def test_game_and_event_edits_are_immediately_reflected(stats_data):
    f = stats_data
    async with f.factory() as db, db.begin():
        game = await db.get(Game, f.games[0])
        game.winner_team, game.victory_type = Team.FASCIST, VictoryType.HITLER_CHANCELLOR
        # Swap target Liberal with Hitler to preserve the role distribution.
        snapshots = (await db.scalars(select(GamePlayer).where(GamePlayer.game_id == game.id))).all()
        target = next(p for p in snapshots if p.player_id == f.players[0])
        hitler = next(p for p in snapshots if p.role is GameRole.HITLER)
        target.role, hitler.role = GameRole.HITLER, GameRole.LIBERAL
        event_row = await db.get(GameEvent, f.events[0])
        event_row.event_type, event_row.ruin_reason, event_row.self_admitted = EventType.IQ_200, None, False
        await db.delete(await db.get(GameEvent, f.events[1]))
    p = await f.stats.get_player_stats(8801, f.club, f.players[0])
    assert p.total_wins == 5
    assert (p.roles[GameRole.LIBERAL].games, p.roles[GameRole.LIBERAL].wins) == (3, 2)
    assert (p.roles[GameRole.HITLER].games, p.roles[GameRole.HITLER].wins) == (3, 2)
    assert (p.ruin_events, p.games_with_ruin, p.iq_200_count) == (1, 1, 2)
    club = await f.stats.get_club_stats(8801, f.club)
    assert (club.score.liberal_wins, club.score.fascist_wins) == (4, 4)
    assert club.victory_types[VictoryType.HITLER_CHANCELLOR] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("api", ["player", "session", "club", "board", "players", "sessions", "player_callback", "session_callback"])
async def test_multi_club_access_is_denied(stats_data, api):
    f = stats_data
    calls = {
        "player": lambda: f.stats.get_player_stats(8801, f.other_club, f.other_players[0]),
        "session": lambda: f.stats.get_session_stats(8801, f.other_club, f.other_evening),
        "club": lambda: f.stats.get_club_stats(8801, f.other_club),
        "board": lambda: f.stats.get_leaderboard(8801, f.other_club),
        "players": lambda: f.stats.list_player_stats(8801, f.other_club),
        "sessions": lambda: f.stats.list_sessions(8801, f.other_club),
        "player_callback": lambda: f.stats.player_profile(8801, f.other_players[0]),
        "session_callback": lambda: f.stats.session_profile(8801, f.other_evening),
    }
    with pytest.raises(StatsError, match="NO_ACCESS"):
        await calls[api]()


@pytest.mark.asyncio
async def test_forged_object_id_and_blocked_membership(stats_data):
    f = stats_data
    with pytest.raises(StatsError, match="NOT_FOUND"):
        await f.stats.get_player_stats(8801, f.club, f.other_players[0])
    with pytest.raises(StatsError, match="NOT_FOUND"):
        await f.stats.get_session_stats(8801, f.club, f.other_evening)
    async with f.factory() as db, db.begin():
        await db.execute(update(ClubMembership).where(ClubMembership.club_id == f.club)
                         .values(status=MembershipStatus.BLOCKED))
    with pytest.raises(StatsError, match="NO_ACCESS"):
        await f.stats.get_my_stats(8801, f.club)


@pytest.mark.asyncio
async def test_membership_without_link_has_friendly_service_error(stats_data):
    f = stats_data
    async with f.factory() as db, db.begin():
        (await db.get(Player, f.players[0])).telegram_user_id = None
    with pytest.raises(StatsError, match="NO_PLAYER"):
        await f.stats.get_my_stats(8801, f.club)


@pytest.mark.asyncio
async def test_zero_sessions_games_events_and_void_session_semantics(stats_data):
    f = stats_data
    # A saved ACTIVE Game is counted even if its parent is VOID, per Game-status source of truth.
    async with f.factory() as db, db.begin():
        (await db.get(GameSession, f.second)).status = SessionStatus.VOID
    assert (await f.stats.get_club_stats(8801, f.club)).score.games_count == 8
    assert (await f.stats.get_club_stats(8801, f.club)).total_sessions == 1
    assert (await f.stats.list_sessions(8801, f.club)).total == 1
    empty = await f.stats.get_session_stats(8801, f.club, f.void_session)
    assert empty.score.games_count == 0 and empty.players.total == 0
    assert all(v == 0 for v in empty.event_counts.values())
    async with f.factory() as db, db.begin():
        blank = Club(name="Empty", timezone="UTC")
        db.add(blank)
        await db.flush()
        member = await db.scalar(select(ClubMembership).where(ClubMembership.club_id == f.club).limit(1))
        db.add(ClubMembership(club_id=blank.id, telegram_user_id=member.telegram_user_id,
                              role=MembershipRole.PLAYER, status=MembershipStatus.ACTIVE))
        blank_id = blank.id
        actor = 8801 if member.role.value == "PLAYER" else 8802
    result = await f.stats.get_club_stats(actor, blank_id)
    assert result.total_sessions == result.score.games_count == result.score.liberal_winrate == 0
    assert all(v == 0 for v in result.event_counts.values())
    assert not (await f.stats.list_sessions(actor, blank_id)).items
    assert not (await f.stats.list_player_stats(actor, blank_id)).items
    for kind in LeaderboardKind:
        assert not (await f.stats.get_leaderboard(actor, blank_id, kind)).entries.items


@pytest.mark.asyncio
async def test_pagination_is_bounded_and_query_count_does_not_grow(stats_data):
    f = stats_data
    async with f.factory() as db, db.begin():
        (await db.get(Club, f.club)).minimum_total_games_for_leaderboard = 0
        db.add_all(Player(club_id=f.club, display_name=f"Extra {i:02d}") for i in range(21))
        db.add_all(GameSession(club_id=f.club, status=SessionStatus.FINISHED,
                              created_by_player_id=f.players[0], statistician_player_id=f.players[0]) for _ in range(12))
    queries = []
    def record(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("SELECT"):
            queries.append(statement)
    event.listen(f.engine.sync_engine, "before_cursor_execute", record)
    try:
        board = await f.stats.get_leaderboard(8801, f.club, page=1)
        assert len(queries) == 3  # membership, count, aggregate page: independent of Player count.
    finally:
        event.remove(f.engine.sync_engine, "before_cursor_execute", record)
    assert board.entries.total == 31 and board.entries.pages == 4 and len(board.entries.items) == 10
    last = await f.stats.list_player_stats(8801, f.club, page=999)
    assert (last.page, len(last.items)) == (3, 1)
    repeated = await f.stats.list_player_stats(8801, f.club, page=999)
    assert repeated == last
    sessions = await f.stats.list_sessions(8801, f.club, page=999)
    assert (sessions.total, sessions.page, len(sessions.items)) == (14, 1, 4)


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [-1, True, 1.5])
async def test_invalid_page_rejected(stats_data, page):
    with pytest.raises(StatsError, match="INVALID_PAGE"):
        await stats_data.stats.get_leaderboard(8801, stats_data.club, page=page)


@pytest.mark.asyncio
async def test_reading_stats_does_not_create_audit_or_mutate_models(stats_data):
    f = stats_data
    async with f.factory() as db:
        before = await db.scalar(select(func.count(AuditLog.id)))
    for kind in LeaderboardKind:
        await f.stats.get_leaderboard(8801, f.club, kind)
    await f.stats.get_club_stats(8801, f.club)
    await f.stats.get_session_stats(8801, f.club, f.evening)
    async with f.factory() as db:
        assert await db.scalar(select(func.count(AuditLog.id))) == before
    prohibited = {"total_games", "total_wins", "liberal_wins", "hitler_wins", "ruin_count", "iq_count"}
    for model in (Player, Club, GameSession):
        assert not prohibited.intersection(model.__table__.columns.keys())


def test_percentages_zero_and_full_precision():
    assert percentage(0, 0) == 0
    assert percentage(1, 3) == pytest.approx(100 / 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("size", range(5, 11))
async def test_games_by_each_supported_player_count_use_snapshots(stats_data, size):
    f = stats_data
    before = await f.stats.get_club_stats(8801, f.club)
    await add_game(f.factory, f.second, f.players[:size], 3, GameRole.HITLER, Team.FASCIST)
    result = await f.stats.get_club_stats(8801, f.club)
    assert result.score.games_count == before.score.games_count + 1
    for bucket in range(5, 11):
        old, new = before.games_by_player_count[bucket], result.games_by_player_count[bucket]
        assert new.games_count == old.games_count + (bucket == size)
        assert new.fascist_wins == old.fascist_wins + (bucket == size)
        assert new.liberal_wins == old.liberal_wins


@pytest.mark.asyncio
async def test_session_players_paginate_and_same_data_for_admin_and_member(stats_data):
    f = stats_data
    async with f.factory() as db, db.begin():
        extras = [Player(club_id=f.club, display_name=f"Late {i:02d}") for i in range(6)]
        db.add_all(extras)
        await db.flush()
        ids = tuple(p.id for p in extras)
    await add_game(f.factory, f.evening, (f.players[0],) + ids, 8, GameRole.HITLER, Team.FASCIST)
    first = await f.stats.get_session_stats(8801, f.club, f.evening)
    last = await f.stats.get_session_stats(8802, f.club, f.evening, page=99)
    assert (first.players.total, len(first.players.items), first.players.pages) == (12, 10, 2)
    assert (last.players.page, len(last.players.items)) == (1, 2)
    assert (await f.stats.get_club_stats(8801, f.club)) == (await f.stats.get_club_stats(8802, f.club))
    assert (await f.stats.get_session_stats(8801, f.club, f.evening)) == (await f.stats.get_session_stats(8802, f.club, f.evening))


@pytest.mark.asyncio
async def test_two_clubs_aggregates_and_boards_never_mix(stats_data):
    f = stats_data
    async with f.factory() as db, db.begin():
        for cid in (f.club, f.other_club):
            c = await db.get(Club, cid)
            c.minimum_total_games_for_leaderboard = c.minimum_role_games_for_leaderboard = 0
    for actor, cid, ids, games, ruins in [(8801, f.club, set(f.players), 8, 3),
                                        (8901, f.other_club, set(f.other_players), 1, 1)]:
        club = await f.stats.get_club_stats(actor, cid)
        assert club.score.games_count == games and club.event_counts[EventType.RUIN] == ruins
        assert {p.player_id for p in (await f.stats.list_player_stats(actor, cid)).items} == ids
        for kind in LeaderboardKind:
            assert {p.player_id for p in (await f.stats.get_leaderboard(actor, cid, kind)).entries.items} == ids


@pytest.mark.asyncio
async def test_ruin_order_is_rate_then_distinct_games_not_event_count(stats_data):
    f = stats_data
    async with f.factory() as db, db.begin():
        (await db.get(Club, f.club)).minimum_total_games_for_leaderboard = 1
    # Late joiner has 2/5=40%, target 2/8=25%. Repeated rows are no new games.
    for gid in (f.games[3], f.games[4]):
        await add_event(f.factory, gid, f.players[5], EventType.RUIN, reason=RuinReason.OTHER)
    for _ in range(4):
        await add_event(f.factory, f.games[0], f.players[0], EventType.RUIN, reason=RuinReason.OTHER)
    board = await f.stats.get_event_leaderboard(8801, f.club, EventType.RUIN)
    assert [p.player_id for p in board.entries.items[:2]] == [f.players[5], f.players[0]]
    assert board.entries.items[0].ruin_events == 2 and board.entries.items[1].ruin_events == 7


@pytest.mark.asyncio
async def test_ruin_secondary_tie_breaker_and_iq_primary_count(stats_data):
    f = stats_data
    async with f.factory() as db, db.begin():
        (await db.get(Club, f.club)).minimum_total_games_for_leaderboard = 1
    for gid in (f.games[0], f.games[1], f.games[2]):
        await add_event(f.factory, gid, f.players[4], EventType.RUIN, reason=RuinReason.OTHER)
    # 3/6 = 4/8 =50%, more distinct games wins the tie despite later name.
    for gid in (f.games[2], f.games[3]):
        await add_event(f.factory, gid, f.players[0], EventType.RUIN, reason=RuinReason.OTHER)
    board = await f.stats.get_event_leaderboard(8801, f.club, EventType.RUIN)
    assert [p.player_id for p in board.entries.items[:2]] == [f.players[0], f.players[4]]
    assert board.entries.items[0].ruin_rate == board.entries.items[1].ruin_rate == 50
    await add_event(f.factory, f.games[1], f.players[0], EventType.IQ_200)
    assert (await f.stats.get_event_leaderboard(8801, f.club, EventType.IQ_200)).entries.items[0].player_id == f.players[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["kind", "role", "event", "string_event"])
async def test_unsupported_ratings_rejected(stats_data, operation):
    f = stats_data
    with pytest.raises(StatsError, match="INVALID_BOARD"):
        if operation == "kind":
            await f.stats.get_leaderboard(8801, f.club, "overall")
        elif operation == "role":
            await f.stats.get_role_leaderboard(8801, f.club, "LIBERAL")
        else:
            await f.stats.get_event_leaderboard(8801, f.club, EventType.PALIVO if operation == "event" else "RUIN")
