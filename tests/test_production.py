"""Production boundaries, lifecycle, CLI tools and real PostgreSQL recovery."""

import asyncio
import logging
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from aiogram.types import ErrorEvent, Update, Chat, Message
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.bot.callbacks import ExportClub
from app.bot.handlers.errors import unexpected_error
from app.bot.handlers.exports import export_club
from app.config import Settings
from app.db.models import Club, Game, GameEvent, Player
from app.domain.enums import EventType, RuinReason, Team, VictoryType
from app.operations import RedactingFormatter, check_database
from app.services.events import EventService
from app.services.games import GameService
from app.services.history import HistoryService
from app.services.sessions import SessionService
from app.services.stats import StatsService
from app.services.spreadsheets import ExportService
from conftest import check_test_database
from scripts.db_tools import backup, restore, target
from test_games_integration import test_database_url  # noqa: F401
from test_spreadsheets import exchange, raw_tables  # noqa: F401


@pytest.mark.parametrize(
    "prod",
    [
        "postgresql+asyncpg://u:p@127.0.0.1:5432/main_test",
        "postgresql+asyncpg://other@localhost/main_test",
    ],
)
def test_database_guard_rejects_same_db_ignoring_user_password_host_alias(prod):
    with pytest.raises(ValueError):
        check_test_database("postgresql+asyncpg://test@localhost/main_test", prod)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://u@host/prod",
        "sqlite:///x_test",
        "postgresql+asyncpg://u@host",
    ],
)
def test_database_guard_rejects_unsafe_targets(url):
    with pytest.raises(ValueError):
        check_test_database(url)


def test_database_guard_accepts_dedicated_database():
    check_test_database(
        "postgresql+asyncpg://u@host/app_test", "postgresql+asyncpg://u@host/app"
    )


def test_redacted_exception_logs_preserve_context():
    token = "123456789:abcdefghijklmnopqrstuvwxyz123456789"
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        "test.py",
        1,
        f"failure {token} postgresql+asyncpg://user:password@host/db",
        (),
        None,
    )
    output = RedactingFormatter((token, "password")).format(record)
    assert token not in output and "password" not in output and "user" not in output
    assert "failure" in output


@pytest.mark.asyncio
async def test_global_error_response_is_generic_and_logged(caplog, monkeypatch):
    import app.bot.handlers.errors as errors

    monkeypatch.setattr(errors.logger, "disabled", False)
    message = Message(message_id=1, date=0, chat=Chat(id=1, type="private"), text="hi")
    with patch.object(Message, "answer", AsyncMock()) as answer:
        assert await unexpected_error(
            ErrorEvent(
                update=Update(update_id=1, message=message),
                exception=RuntimeError("unexpected bug"),
            )
        )
        assert "unexpected bug" not in answer.call_args.args[0]
        assert "unexpected bug" in caplog.text


@pytest.mark.asyncio
async def test_export_document_is_in_memory_and_no_tempfile(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    message = Message(message_id=1, date=0, chat=Chat(id=1, type="private"))
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=1), message=message, answer=AsyncMock()
    )
    service = SimpleNamespace(export=AsyncMock(return_value=("club.xlsx", b"workbook")))
    with patch.object(Message, "answer_document", AsyncMock()) as send:
        await export_club(callback, ExportClub(club_id=uuid4()), service)
        assert send.call_args.args[0].filename == "club.xlsx"
        assert send.call_args.args[0].data == b"workbook"
    assert not list(tmp_path.iterdir())


def test_restore_requires_explicit_target_and_confirmation(tmp_path, monkeypatch):
    dump = tmp_path / "backup.dump"
    dump.write_bytes(b"anything")
    monkeypatch.setenv(
        "RESTORE_DATABASE_URL",
        "postgresql+asyncpg://user:secret@localhost/recovery_test",
    )
    monkeypatch.setattr("builtins.input", lambda _: "NO")
    with patch("scripts.db_tools.subprocess.run") as run:
        with pytest.raises(ValueError):
            restore(dump, "RESTORE_DATABASE_URL")
        run.assert_not_called()


def test_pg_credentials_not_passed_in_arguments(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "BACKUP_TEST_URL", "postgresql+asyncpg://user:secret@localhost/backup_test"
    )
    with patch("scripts.db_tools.subprocess.run") as run:
        filename = backup(tmp_path, "BACKUP_TEST_URL")
        assert filename.stat().st_mode & 0o777 == 0o600
        assert "secret" not in " ".join(run.call_args.args[0])
        assert run.call_args.kwargs["env"]["PGPASSWORD"] == "secret"


def test_backup_failure_removes_partial_file(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "BACKUP_TEST_URL", "postgresql+asyncpg://user@localhost/backup_test"
    )
    with patch("scripts.db_tools.subprocess.run", side_effect=RuntimeError("failure")):
        with pytest.raises(RuntimeError):
            backup(tmp_path, "BACKUP_TEST_URL")
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_schema_ready_and_outdated(exchange):
    engine = exchange[0]
    await check_database(engine)
    async with engine.begin() as db:
        await db.execute(text("UPDATE alembic_version SET version_num='old'"))
    try:
        with pytest.raises(RuntimeError, match="outdated"):
            await check_database(engine)
    finally:
        async with engine.begin() as db:
            await db.execute(
                text(
                    "UPDATE alembic_version SET version_num='0004_statistician_recovery'"
                )
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_application_disposes_resources_on_polling_finish_or_failure(
    monkeypatch, failure
):
    from app import main

    engine = SimpleNamespace(dispose=AsyncMock())
    bot = SimpleNamespace(__aenter__=AsyncMock(), __aexit__=AsyncMock())
    bot.__aenter__.return_value = bot
    # MagicMock supplies asynchronous context-manager methods to Bot factory.
    from unittest.mock import MagicMock

    context = MagicMock()
    context.__aenter__ = bot.__aenter__
    context.__aexit__ = bot.__aexit__
    context.__aexit__.return_value = None
    polling = AsyncMock(side_effect=RuntimeError("polling failed") if failure else None)
    monkeypatch.setattr(
        main,
        "Settings",
        lambda: Settings(
            _env_file=None,
            bot_token="123456:example",
            database_url="postgresql+asyncpg://u:p@host/db",
        ),
    )
    monkeypatch.setattr(main, "configure_logging", lambda _: None)
    monkeypatch.setattr(main, "create_engine", lambda _: engine)
    monkeypatch.setattr(main, "create_session_factory", lambda _: None)
    monkeypatch.setattr(main, "check_database", AsyncMock())
    monkeypatch.setattr(main, "Bot", lambda **_: context)
    monkeypatch.setattr(
        main, "create_dispatcher", lambda: SimpleNamespace(start_polling=polling)
    )
    if failure:
        with pytest.raises(RuntimeError):
            await main.main()
    else:
        await main.main()
    engine.dispose.assert_awaited_once()
    context.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_application_e2e_and_backup_restore_stats_history(
    exchange, tmp_path, monkeypatch
):
    import asyncpg
    from sqlalchemy.engine import make_url
    from app.services.membership import MembershipService, TelegramProfile
    from aiogram.fsm.storage.memory import MemoryStorage
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.context import FSMContext

    engine, factory, club, _, players, _ = exchange
    membership = MembershipService(factory)
    # Invite and explicit historical linking preserve Player identity.
    await membership.register_telegram_user(
        TelegramProfile(777888, username=None, last_name=None, first_name="Связанный")
    )
    invite = await membership.create_invite(8802, club, max_uses=1)
    preview = await membership.preview_invite(invite)
    await membership.join_invite(777888, preview.id)
    request = await membership.request_player_link(777888, players[6])
    await membership.review_player_link(8802, request, approve=True)
    sessions, games, events = (
        SessionService(factory),
        GameService(factory),
        EventService(factory),
    )
    evening, created = await sessions.start(
        777888, club, (players[0], players[1], players[2], players[3], players[6])
    )
    assert created
    people = (players[0], players[1], players[2], players[3], players[6])
    game, _ = await games.create_game(
        777888,
        evening.id,
        uuid4(),
        people,
        people[0],
        (people[1],),
        Team.LIBERAL,
        VictoryType.LIBERAL_POLICIES,
    )
    await events.create_event(
        777888,
        game.id,
        uuid4(),
        people[1],
        EventType.RUIN,
        RuinReason.OTHER,
        True,
        "Ошибся",
    )
    await events.create_event(777888, game.id, uuid4(), people[2], EventType.IQ_200)
    # Unfinished in-memory wizard is disposable and never becomes official data.
    store = MemoryStorage()
    state = FSMContext(store, StorageKey(bot_id=1, chat_id=777888, user_id=777888))
    await state.set_state("GameFlow:hitler")
    await state.set_data({"draft_id": str(uuid4())})
    await store.close()
    fresh = MemoryStorage()
    fresh_state = FSMContext(fresh, state.key)
    assert await fresh_state.get_state() is None and await fresh_state.get_data() == {}
    await fresh.close()
    await sessions.remove_player(777888, evening.id, people[3])
    await sessions.add_player(777888, evening.id, players[7])
    second_people = (*people[:3], people[4], players[7])
    await games.create_game(
        777888,
        evening.id,
        uuid4(),
        second_people,
        people[0],
        (people[1],),
        Team.FASCIST,
        VictoryType.FASCIST_POLICIES,
    )
    await sessions.finish(777888, evening.id)
    history = HistoryService(factory)
    stats = StatsService(factory)
    assert (await history.list_games(777888, club)).total == 3
    card = await history.card(8802, game.id)
    await games.edit_game(
        8802,
        game.id,
        expected_revision=card.revision,
        participants=people,
        hitler=people[0],
        fascists=(people[1],),
        winner=Team.FASCIST,
        victory_type=VictoryType.FASCIST_POLICIES,
    )
    card = await history.card(8802, game.id)
    await games.set_game_void(8802, game.id, expected_revision=card.revision)
    card = await history.card(8802, game.id)
    await games.set_game_void(
        8802, game.id, expected_revision=card.revision, restore=True
    )
    before = await stats.get_club_stats(8802, club)
    _, exported = await ExportService(engine).export(8802, club)
    assert len(raw_tables(exported)["Games"]) == 4
    source_url = os.environ["TEST_DATABASE_URL"]
    url = make_url(source_url)
    restore_name = "secreth_phase10_recovery_test"
    restore_url = url.set(database=restore_name).render_as_string(hide_password=False)
    check_test_database(restore_url)
    admin = await asyncpg.connect(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        database="postgres",
    )
    # Fixed disposable marker, no arbitrary SQL identifier from untrusted input.
    await admin.execute("CREATE DATABASE secreth_phase10_recovery_test")
    monkeypatch.setenv("BACKUP_TEST_URL", source_url)
    monkeypatch.setenv("RESTORE_TEST_URL", restore_url)
    restored_engine = create_async_engine(restore_url)
    try:
        dump = await asyncio.to_thread(backup, tmp_path, "BACKUP_TEST_URL")
        # Occupied target must fail atomically: no --clean / silent overwrite.
        with pytest.raises(subprocess.CalledProcessError):
            await asyncio.to_thread(restore, dump, "BACKUP_TEST_URL", yes=True)
        assert await stats.get_club_stats(8802, club) == before
        await asyncio.to_thread(restore, dump, "RESTORE_TEST_URL", yes=True)
        await check_database(restored_engine)
        restored_factory = async_sessionmaker(restored_engine, expire_on_commit=False)
        after = await StatsService(restored_factory).get_club_stats(8802, club)
        assert after == before
        assert (
            await HistoryService(restored_factory).list_games(777888, club)
        ).total == 3
        async with restored_factory() as db:
            assert await db.scalar(select(func.count(Game.id))) == 4
            assert await db.scalar(select(func.count(GameEvent.id))) == 4
            assert await db.scalar(select(func.count(Player.id))) == 20
        with patch.dict(os.environ, {"DATABASE_URL": restore_url}):
            await asyncio.to_thread(command.current, Config("alembic.ini"))
    finally:
        await restored_engine.dispose()
        await admin.execute("DROP DATABASE secreth_phase10_recovery_test")
        await admin.close()


@pytest.mark.asyncio
async def test_fresh_install_full_migration_cycle_bootstrap_and_minimal_workflow(
    test_database_url,
):
    import asyncpg
    from sqlalchemy.engine import make_url
    from app.bootstrap_admin import bootstrap_first_admin
    from app.db.models import ClubMembership
    from app.domain.enums import MembershipRole

    url = make_url(test_database_url)
    fresh_url = url.set(database="secreth_phase10_install_test").render_as_string(
        hide_password=False
    )
    check_test_database(fresh_url)
    admin = await asyncpg.connect(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        database="postgres",
    )
    await admin.execute("CREATE DATABASE secreth_phase10_install_test")
    engine = create_async_engine(fresh_url)
    try:
        with pytest.raises(RuntimeError, match="missing"):
            await check_database(engine)
        with patch.dict(os.environ, {"DATABASE_URL": fresh_url}):
            config = Config("alembic.ini")
            await asyncio.to_thread(command.upgrade, config, "head")
            await asyncio.to_thread(command.downgrade, config, "base")
            await asyncio.to_thread(command.upgrade, config, "head")
            await asyncio.to_thread(command.check, config)
        await check_database(engine)
        kwargs = dict(
            telegram_id=557799,
            club_name="Новый клуб",
            timezone_name="Asia/Yakutsk",
            display_name="Первый ADMIN",
        )
        await bootstrap_first_admin(fresh_url, **kwargs)
        with pytest.raises(ValueError, match="empty"):
            await bootstrap_first_admin(fresh_url, **kwargs)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db, db.begin():
            club = await db.scalar(select(Club.id))
            first = await db.scalar(select(Player.id))
            assert (
                await db.scalar(
                    select(func.count(ClubMembership.id)).where(
                        ClubMembership.role == MembershipRole.ADMIN
                    )
                )
                == 1
            )
            players = [first]
            for i in range(4):
                player = Player(club_id=club, display_name=f"Исторический {i}")
                db.add(player)
                await db.flush()
                players.append(player.id)
        evening, created = await SessionService(factory).start(
            557799, club, tuple(players)
        )
        assert created
        game, _ = await GameService(factory).create_game(
            557799,
            evening.id,
            uuid4(),
            tuple(players),
            first,
            (players[1],),
            Team.LIBERAL,
            VictoryType.LIBERAL_POLICIES,
        )
        assert (
            await StatsService(factory).get_club_stats(557799, club)
        ).score.games_count == 1
        assert (await HistoryService(factory).card(557799, game.id)).game.id == game.id
    finally:
        await engine.dispose()
        await admin.execute("DROP DATABASE secreth_phase10_install_test")
        await admin.close()


@pytest.mark.parametrize("value", ["Invalid/Timezone", ""])
def test_invalid_timezone_is_startup_configuration_error(value):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            bot_token="123456:example",
            database_url="postgresql+asyncpg://u@h/db",
            default_timezone=value,
        )


@pytest.mark.parametrize(
    "dbname", ["other?password=x", "other user=evil", "other=connection"]
)
def test_pg_target_cannot_be_libpq_connection_string(dbname, monkeypatch):
    from sqlalchemy.engine import URL

    value = URL.create(
        "postgresql+asyncpg", username="u", host="localhost", database=dbname
    ).render_as_string(hide_password=False)
    monkeypatch.setenv("BAD_TARGET", value)
    with pytest.raises(ValueError):
        target("BAD_TARGET")


@pytest.mark.asyncio
async def test_event_and_names_are_plain_text_under_html_bot_defaults():
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.types import CallbackQuery, User
    from app.bot.handlers.common import reply

    text = "<tg-spoiler><b>Юникод</b>&_*["
    bot = Bot(token="123456:example", default=DefaultBotProperties(parse_mode="HTML"))
    msg = Message(message_id=1, date=0, chat=Chat(id=1, type="private"))
    cb = CallbackQuery(
        id="test",
        chat_instance="test",
        from_user=User(id=1, is_bot=False, first_name="test"),
        message=msg,
    )
    try:
        with (
            patch.object(Message, "answer", AsyncMock()) as answer,
            patch.object(CallbackQuery, "answer", AsyncMock()),
        ):
            await reply(cb.as_(bot), text)
            assert answer.call_args.args[0] == text
            assert answer.call_args.kwargs["parse_mode"] is None
    finally:
        await bot.session.close()


def test_backup_collision_never_removes_existing_user_dump(monkeypatch, tmp_path):
    from datetime import datetime, timezone

    monkeypatch.setenv(
        "BACKUP_TEST_URL", "postgresql+asyncpg://user@localhost/backup_test"
    )
    moment = datetime(2026, 10, 9, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "scripts.db_tools.datetime", SimpleNamespace(now=lambda _: moment)
    )

    def dump(*args, **kwargs):
        kwargs["stdout"].write(b"existing valid dump")

    with patch("scripts.db_tools.subprocess.run", side_effect=dump):
        filename = backup(tmp_path, "BACKUP_TEST_URL")
        with pytest.raises(FileExistsError):
            backup(tmp_path, "BACKUP_TEST_URL")
    assert filename.read_bytes() == b"existing valid dump"
