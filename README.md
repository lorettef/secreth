# Secret Hitler Stats

## Что это

Небольшой Telegram-бот для регулярных вечеров Secret Hitler: быстро записывает завершённые партии и вычисляет статистику по сохранённой истории. Продуктовые требования — [docs/SPEC.md](docs/SPEC.md), правила разработки — [AGENTS.md](AGENTS.md).

## Основные возможности

- Вступление по invite, historical Players без Telegram, linking с ADMIN approval.
- Игровые вечера, live roster с уходом/возвращением, statistician transfer и emergency recovery.
- Game wizard: участники → Hitler → Fascists → automatic Liberals → winner → victory → review → save. Snapshot состава независим от roster; повторный Save защищён `draft_id` в PostgreSQL.
- Ровно четыре Events: RUIN, PALIVO, IQ_200, PROPHET. RUIN имеет семь причин и self_admitted; comment optional, до 500 символов. Несколько одинаковых событий допустимы.
- Player/role/session/club statistics и шесть leaderboards: общий winrate, Liberal, Fascist, Hitler, Руины, 200 IQ. Общие/event thresholds — 10 игр, role threshold — 5; значения задаются в Club. **Ruin rate = games with at least one ruin / total games × 100%**, а не число событий / игр.
- History по вечерам, последним Games и Player; pagination 10/page, Game Cards с ролями и Events.
- ADMIN historical corrections, VOID/RESTORE, Player rename/deactivate/reactivate, AuditLog.
- ADMIN XLSX export, normalized legacy import CLI, PostgreSQL backup/restore tools.

PLAYER читает данные и может начать вечер. STATISTICIAN управляет только своим ACTIVE вечером; после завершения/передачи теряет edit rights. ADMIN исправляет историю своего клуба, удаляет Events, делает VOID/RESTORE и управляет Players. Backend повторно проверяет права/club scope.

Game edits работают как draft и сохраняются одной транзакцией. Участника с Events нельзя удалить из Game до переназначения/удаления этих Events. Game никогда не удаляется физически: VOID исключает её и события из stats, RESTORE возвращает. Номера Games не переиспользуются. Inactive Player сохраняет историю, но исключён из нового roster и leaderboards. Stats вычисляются SQL, без ручных counters/cache.

Mutation buttons связаны с entity revision и FSM screen token; старые Back/Cancel/Save/confirmation не изменяют новый flow. Audit actions: PLAYER_LINK, PLAYER_EDIT, STATISTICIAN_CHANGE, GAME_EDIT, GAME_VOID, GAME_RESTORE, EVENT_CREATE, EVENT_EDIT, EVENT_DELETE, IMPORT. Исторический STATISTICIAN_TRANSFER сохранён и читается как прежний код. Просмотр не создаёт AuditLog.

## Архитектура

Python 3.12+, aiogram 3, SQLAlchemy async/asyncpg, PostgreSQL 16, Alembic, pydantic-settings, openpyxl. Handlers разбирают Telegram input; domain содержит правила ролей/победы/событий/permissions; services координируют транзакции, queries и audit. GamePlayer — snapshot конкретной партии. Player и TelegramUser — разные сущности. FSM — MemoryStorage + SimpleEventIsolation.

## Requirements

- Python 3.12+ для local setup либо Docker Engine/Desktop с Compose plugin.
- PostgreSQL 16; client tools `pg_dump`/`pg_restore` версии 16 для backup/restore (в Docker image включены).
- Реальный BOT_TOKEN от BotFather для long polling; доступ к Telegram API.
- Для полного pytest: отдельная disposable PostgreSQL 16 database, test role с правом CREATE DATABASE, PostgreSQL client tools в PATH. Нельзя использовать dev/production БД.

## Environment

```bash
cp .env.example .env
```

Заполните BOT_TOKEN, DATABASE_URL, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD; замените `change-me`. DATABASE_URL имеет driver `postgresql+asyncpg` и тот же password, что PostgreSQL. В Compose hostname `db`, локально — адрес вашего сервера. Пароль со специальными символами нужно URL-encode. LOG_LEVEL default INFO. DEFAULT_TIMEZONE — валидный IANA timezone (default UTC), используется при bootstrap; каждый Club хранит свой timezone.

`.env` ignored Git/Docker. Не печатайте config или URLs с credentials. Production logs редактируют token, URL и DB password; SQLAlchemy не включает query parameters в errors. Alembic требует DATABASE_URL **в environment**; самостоятельно `.env` он не читает. Далее команды экспорта переменных исполняйте только с доверенным локальным `.env`.

## Local setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
.venv/bin/pip install -e '.[test]'
```

`requirements.lock` фиксирует production/transitive версии, использованные при проверке. Runtime dependencies не обновляются автоматически в Docker. При намеренном обновлении пересоберите lock и прогоните полный suite.

Settings/import/backup tools читают `.env`. Для Alembic можно загрузить DATABASE_URL через dotenv без вывода secrets:

```bash
.venv/bin/python -c 'from dotenv import load_dotenv; load_dotenv(); from alembic.config import Config; from alembic import command; command.upgrade(Config("alembic.ini"), "head")'
```

## Docker setup

Compose services называются **db** (PostgreSQL) и **app** (bot). Named volume `postgres_data` сохраняет данные; db healthcheck — pg_isready. Оба services имеют restart unless-stopped. Bot image работает от unprivileged user, без dev dependencies/.git/.env/backups. HTTP API запускается отдельным процессом и пока намеренно не добавлен в Compose.

```bash
docker compose up -d db
docker compose build app
docker compose run --rm app alembic upgrade head
# Затем создайте первого ADMIN по следующему разделу.
docker compose up -d app
docker compose logs -f app
```

Остановить без удаления данных: `docker compose down`. **`docker compose down -v` УДАЛЯЕТ PostgreSQL volume**: не используйте для обычной остановки или обновления.

Без настоящего token bot не запускает полноценный polling. Fake production режим отсутствует. Schema/config можно проверить через migration/bootstrap команды без обращения к Telegram; настоящий bot smoke выполняется человеком.

Отдельный production stack для «Министерства культуры» находится в
`compose.ministry.yml`: PostgreSQL 16, один long-polling bot, FastAPI, собранный
React frontend и Caddy с автоматическим HTTPS. Он использует отдельные volumes и
не заменяет старый `docker-compose.yml`. Полная пошаговая инструкция для Ubuntu
24.04, DNS, BotFather, обновления, отката и restore rehearsal находится в
[docs/MINISTRY_VPS.md](docs/MINISTRY_VPS.md). Пример окружения —
`.env.ministry.example`.

## Database migrations

- 0001_initial_models — основная schema/constraints.
- 0002_player_link_requests — ADMIN approval linking.
- 0003_game_draft_id — duplicate Save protection.
- 0004_statistician_recovery — persistent emergency requests/confirmations.
- 0005_owner_role — единственная роль OWNER внутри клуба.
- 0006_bootstrap_invites — одно системное bootstrap-приглашение на клуб.
- 0007_player_profiles — nullable профиль участника и уникальный без учёта регистра ник внутри клуба.
- 0008_member_statuses — канонические ACTIVE / INACTIVE / BLOCKED статусы membership; текущая единственная head.

```bash
docker compose run --rm app alembic current
docker compose run --rm app alembic heads
docker compose run --rm app alembic check
docker compose run --rm app alembic upgrade head
```

Startup сверяет Alembic revision с head и отказывается запускаться при missing/outdated schema. Migrations выполняет оператор: bot не делает auto migration. `downgrade base` удаляет tables, поэтому полный cycle проверяйте только на disposable test DB.

## Первоначальная настройка клуба Ministry v1.1

Сначала примените миграции к новой базе. Затем оператор выполняет три отдельные
команды. Учётные данные базы остаются в переменных окружения и не передаются в
аргументах команд.

```bash
# 1. Создать пустой клуб и сохранить выведенный UUID.
docker compose run --rm app python -m app.bootstrap_club create-club \
  --club "Наш клуб" --timezone "Asia/Yakutsk"

# 2. Создать единственное bootstrap-приглашение. Выведенный токен является секретом.
docker compose run --rm app python -m app.bootstrap_club create-invite \
  --club-id 00000000-0000-0000-0000-000000000000

# 3. После вступления пользователя назначить его membership владельцем.
docker compose run --rm app python -m app.bootstrap_club assign-owner \
  --club-id 00000000-0000-0000-0000-000000000000 \
  --telegram-id 123456789
```

Для локального запуска замените `docker compose run --rm app python` на
`.venv/bin/python`. Приглашение одноразовое и бессрочное. Второе
bootstrap-приглашение, назначение пользователя из другого клуба и назначение
второго OWNER отклоняются. Повторный `assign-owner` для того же membership
безопасен. Токен выводится только как явный результат CLI и не записывается в
логи приложения или данные аудита.

## Legacy first ADMIN

На мигрированной базе **без клубов**:

```bash
docker compose run --rm app python -m app.bootstrap_admin \
  --telegram-id 123456789 --club "Наш клуб" \
  --timezone "Asia/Yakutsk" --player "Имя администратора"
```

Для local setup: `.venv/bin/python -m app.bootstrap_admin` с теми же аргументами. Замените numeric Telegram ID на свой. Повторный bootstrap безопасно отклоняется, без duplicate membership. После создания клуба `/start` → «Управление клубом» → invite. Historical Player linking требует отдельного подтверждения ADMIN.

## Running the bot

```bash
.venv/bin/python -m app.main
# либо
docker compose up -d app
```

**Run exactly one bot application instance.** Long polling, без webhook. Startup: validate config → DB connection/schema check → polling. Shutdown закрывает bot HTTP session/FSM resources и SQLAlchemy engine.

## Running the HTTP API

FastAPI запускается независимо от Telegram polling, но использует те же `Settings`,
SQLAlchemy session factory и application services:

```bash
.venv/bin/uvicorn app.http:app --host 127.0.0.1 --port 8000
```

`GET /health` возвращает только `{"status":"ok"}`. `GET /api/v1/me` требует
`Authorization: tma <raw Telegram.WebApp.initData>` и обязательный `club_id` query
parameter; club scope повторно проверяется сервером. Default TTL initData — 3600
секунд, допустимый конфигурационный максимум — 86400 секунд. CORS по умолчанию
отключён; `MINI_APP_ALLOWED_ORIGINS` принимает JSON-массив точных HTTPS origins,
например `["https://mini.example.org"]`. Wildcard запрещён.

`POST /api/v1/onboarding/register` принимает одноразовый `invite_token`, имя,
фамилию и ник Школы 21. Telegram ID берётся только из проверенного `initData`;
membership, Player, профиль и погашение приглашения сохраняются одной транзакцией.
Для существующего активного membership в состоянии `PROFILE_REQUIRED` используется
`POST /api/v1/onboarding/profile` с `club_id` и теми же полями профиля; повторное
приглашение не требуется и завершённый профиль этим endpoint не редактируется.
`GET /api/v1/my-clubs` возвращает только собственные доступные ACTIVE/INACTIVE
memberships проверенного Telegram-пользователя; BLOCKED и чужие клубы не раскрывает.

## Telegram Mini App frontend

Frontend находится в `frontend/` и использует React, TypeScript и Vite. Для
локальной разработки Vite проксирует `/api` на FastAPI `127.0.0.1:8000`:

```bash
cd frontend
npm ci
npm run dev
# проверки и production assets
npm run typecheck
npm test
npm run build
```

Production build создаётся в `frontend/dist/`. На production рекомендуется один
HTTPS origin: reverse proxy раздаёт frontend и направляет `/api/*` и `/health` в
FastAPI. Bot token, DATABASE_URL и другие серверные переменные не передаются Vite
и не должны объявляться как `VITE_*`.

Для Telegram настройте Main Mini App вручную в BotFather и укажите публичный HTTPS
URL. Ссылки приглашений имеют вид
`https://t.me/<bot_username>?startapp=<invite_token>`. Затем задайте на сервере:

```dotenv
MINI_APP_URL=https://mini.example.org
# Нужен только при раздельных frontend/API origins; wildcard запрещён.
MINI_APP_ALLOWED_ORIGINS=["https://mini.example.org"]
```

При `MINI_APP_URL` бот на старте настраивает menu button и создаёт новые invite
links через `startapp`. Без переменной старые handlers, `/start` и обычные deep
links продолжают работать как раньше. Настройка Main Mini App, username и preview
в BotFather остаётся ручной операторской операцией.

Если статист недоступен: текущий вечер → «⚙️ Помощь» → «Проблема со статистом / Сменить статиста». Кандидат — активный зарегистрированный присутствующий Player. Два других текущих участника явно подтверждают; инициатор/кандидат не голосуют, повторный голос не учитывается. ADMIN может сменить сразу. Recovery confirmations живут в PostgreSQL; flow теряет актуальность при finish/смене статиста/уходе кандидата.

## Tests

Создайте **новую пустую disposable** DB с именем `_test`; задайте TEST_DATABASE_URL, отличный от DATABASE_URL и `.env`. Guard сравнивает host/port/database независимо от user/password. Не запускайте suite параллельно: committed concurrency/backup fixtures используют общую test DB и очищают её данные. Test role должен иметь CREATE DATABASE для двух фиксированных disposable recovery/install БД. `pg_dump`, `pg_restore` должны быть в PATH.

```bash
# TEST_DATABASE_URL передайте через доверенное environment, без секретов в истории shell.
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
git diff --check
```

Без TEST_DATABASE_URL integration tests пропускаются: такой результат не является полной проверкой. Infrastructure tests создают/удаляют исключительно `secreth_phase10_install_test` и `secreth_phase10_recovery_test`, проверяют fresh upgrade/downgrade base/upgrade, bootstrap, pg_dump/restore, stats/history equality. Эти имена должны быть свободны. После аварийного завершения проверьте оставшиеся **test** базы перед повторным запуском; production не трогайте.

Ruff lint работает по всему проекту. Formatter проверяет новые/уже совместимые модули; явный legacy baseline в pyproject сохраняет существующий стиль Phase 1–9 и исключает массовую косметическую перепись. Mypy пока не настроен.

## Import

```bash
.venv/bin/python scripts/import_legacy.py history.xlsx --dry-run
.venv/bin/python scripts/import_legacy.py history.xlsx --apply
# При нескольких клубах добавьте --club-id UUID.
```

В Docker: `docker compose run --rm -v "$PWD/import:/import:ro" app python scripts/import_legacy.py /import/history.xlsx --dry-run`. После проверки замените flag на `--apply`.

Поддерживается только [нормализованный XLSX v1](docs/IMPORT_FORMAT.md): Players, Sessions, Games, GamePlayers, Events. Формулы запрещены; все строки/ссылки/роли проверяются. Dry-run не меняет БД; apply атомарный. Legacy UNKNOWN допустим только здесь. Ambiguous names/duplicate IDs приводят к rejection; existing Players можно явно сослать по UUID без update. Новые historical Players не связываются с Telegram. Успех создаёт один IMPORT audit с nullable actor (доверенный CLI оператор).

## Export

ADMIN → «Управление клубом» → «📤 Экспорт»: Telegram document с Players, Sessions, Games, GamePlayers, Events. UUID, роли, Unicode, inactive/VOID сохраняются. Все sheets читаются из одной REPEATABLE READ snapshot. String cells — literal text, защищены от Excel formula injection без изменения текста. Workbook/document находится в памяти; после отправки приложение не оставляет export files.

XLSX — продуктовая история, **не полный технический backup**: без Telegram personal identity/memberships, invite tokens, club settings и старого audit. Export→import поддерживает semantic round-trip в чистую БД; в той же БД глобальные UUID конфликтуют. Для полного восстановления используйте dump.

## Backup

Host с PostgreSQL 16 client tools и project Python environment:

```bash
.venv/bin/python scripts/backup_db.py --directory backups
```

Берёт DATABASE_URL из environment/`.env`, password передаётся через subprocess environment, без shell interpolation/credentials в argv. Custom format (`pg_dump -Fc`), уникальное UTC filename, mode 0600. Failed dump удаляется; успешный сохраняется. `backups/`, dumps/XLSX/temp/cache ignored Git/Docker. Backup содержит персональные данные/токены БД: храните с ограниченным доступом, encryption/off-host copy.

Через Compose, без filesystem volume и permission проблем unprivileged user:

```bash
mkdir -p backups
(umask 077; docker compose exec -T db sh -c 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > backups/secret_hitler.dump)
docker compose exec -T db pg_restore --list < backups/secret_hitler.dump
```

Фиксированное имя в примере перезаписывает файл: для retention выбирайте уникальное имя. Проверяйте exit status и ненулевой размер; удаляйте failed partial dump. Script сам делает это безопасно. Настройте **ежедневный внешний cron/hosting-provider backup**, несколько последних snapshots и off-host copy; bot не содержит scheduler. Проверка `pg_restore --list` недостаточна: регулярно выполняйте actual restore в отдельную пустую DB.

## Restore

1. Проверьте dump (`pg_restore --list backup.dump`), сохраните исходную DB.
2. Остановите bot: `docker compose stop app`.
3. Создайте **новую пустую target DB**; задайте отдельную RESTORE_DATABASE_URL через доверенное environment.
4. Запустите:

```bash
.venv/bin/python scripts/restore_db.py backup.dump --target-env RESTORE_DATABASE_URL
```

Database name для tools: letters/digits/underscore/dash (без connection-string syntax). Tool печатает host/port/database **без credentials**, требует `Type RESTORE to continue`. Для automation нужен явный `--yes`. Не делает DROP/--clean; restore одной транзакцией в занятую DB отклоняется без частичной замены. Переключите DATABASE_URL на восстановленную DB только после проверки.

5. `alembic upgrade head` на target; проверьте `alembic current`, counts, stats/history.
6. Запустите bot, проверьте startup/logs и `/start`.

Через Compose можно восстановить dump в заранее созданную отдельную DB (`recovered_stats` — пример, не существующая production):

```bash
docker compose exec -T db sh -c 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec pg_restore --exit-on-error --single-transaction --no-owner --no-privileges -U "$POSTGRES_USER" -d recovered_stats' < backups/secret_hitler.dump
```

Raw pg_restore не задаёт вопрос: заранее проверьте target. Для interactive safety используйте restore script. Не удаляйте production DB или volume ради восстановления.

## Deployment/update старого bot stack

1. Сделайте и проверьте backup, сохраните off-host copy.
2. Остановите app: `docker compose stop app`.
3. Обновите код, пересоберите: `docker compose build app`.
4. Примените schema: `docker compose run --rm app alembic upgrade head`.
5. Запустите: `docker compose up -d app`.
6. Проверьте `docker compose logs --tail 100 app`, `alembic current`.
7. Выполните [go-live smoke checklist](docs/GO_LIVE.md).

Не запускайте старый bot одновременно с новым. Обновление image не удаляет named volume. Если migration несовместима со старым кодом, rollback plan определяется до deployment; не выполняйте destructive downgrade на рабочей БД.

## Troubleshooting

- **Bot does not start:** проверьте BOT_TOKEN, DATABASE_URL/driver, credentials, DB connectivity/health, schema revision. Не публикуйте config с secrets. Invalid config останавливает startup.
- **Database outdated/missing:** оператор запускает `alembic upgrade head`, затем bot restart; auto migration отсутствует.
- **Telegram buttons outdated:** «Эта кнопка больше не актуальна» ожидаемо; откройте текущий экран и начните flow заново.
- **Import rejected:** `--dry-run`, номера строк/errors, явные UUID mappings, enum codes, timezone timestamps; не обходите проверки.
- **Restore rejected:** target должен быть пустым, client version 16, правильные credentials и custom dump. Существующая DB не очищается автоматически.
- **Two pollers / Telegram conflict:** остановите лишний instance; оставьте ровно один.
- **Infrastructure error:** пользователь получает нейтральное сообщение; exception/context находятся в redacted logs. Domain errors остаются конкретными.

## Known limitations

- One bot instance only; no horizontal scaling.
- FSM drafts live in memory; unfinished wizard is lost on restart. Saved Games/Events/recovery confirmations остаются в PostgreSQL, draft не попадает в stats.
- Bot использует long polling без webhook; production Mini App stack описан
  отдельно в `compose.ministry.yml` и не развёртывается автоматически.
- Import только normalized XLSX; неизвестный legacy layout требует отдельного адаптера после получения файла. Нет CSV importer.
- XLSX не заменяет PostgreSQL dump и не переносит Telegram accounts автоматически.
- Backup schedule/retention/off-host copy настраивает оператор вне bot process.
- Полный реальный Telegram smoke и Docker deployment проверяются в целевой среде по GO_LIVE; unit/mock tests не означают live verification.
