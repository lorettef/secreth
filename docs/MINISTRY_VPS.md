# Развёртывание «Министерства культуры» на VPS

Эта инструкция описывает новый изолированный stack `compose.ministry.yml`. Она не
подключает и не изменяет старую production-базу SecretH. Команды выполняет оператор
на VPS; репозиторий сам не выполняет deployment, миграции или восстановление.

## 1. Подготовить Ubuntu 24.04 LTS

Создайте отдельного непривилегированного пользователя с доступом по SSH-ключу,
включите firewall и разрешите входящие TCP 22, 80, 443 и UDP 443. PostgreSQL и
uvicorn наружу не публикуются. Установите обновления безопасности и синхронизацию
времени.

## 2. Установить Docker Engine и Compose

Установите Docker Engine из официального Docker apt-репозитория и Compose plugin.
Добавьте оператора в группу `docker` только если принимаете, что это даёт права,
эквивалентные root. Проверьте:

```bash
docker version
docker compose version
```

## 3. Клонировать репозиторий

```bash
git clone https://github.com/lorettef/secreth.git
cd secreth
git switch main
git pull --ff-only origin main
```

Зафиксируйте развёртываемый commit через `git rev-parse HEAD`. Не запускайте старый
и новый polling-процессы с одним `BOT_TOKEN` одновременно.

## 4. Создать защищённый `.env`

```bash
cp .env.ministry.example .env
chmod 600 .env
```

Замените все placeholders. `POSTGRES_PASSWORD` должен быть длинным случайным
секретом; в `DATABASE_URL` тот же пароль должен быть URL-encoded. Не добавляйте
`.env` в Git и не передавайте его содержимое в issue, чат или логи. Для production
одного origin оставьте `MINI_APP_ALLOWED_ORIGINS=[]`.

Обязательные значения:

- `BOT_TOKEN` — токен BotFather;
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` — новая чистая база;
- `DATABASE_URL` — `postgresql+asyncpg://...@db:5432/...`;
- `MINI_APP_DOMAIN` — домен без схемы и пути;
- `MINI_APP_URL` — `https://` URL того же домена.

Проверьте итоговую конфигурацию без публикации вывода с секретами:

```bash
docker compose --env-file .env -f compose.ministry.yml config --quiet
```

Для CI-проверки без production `.env` допустимо передать
`MINISTRY_ENV_FILE=.env.ministry.example`; в обычной эксплуатации эта служебная
переменная не нужна.

## 5. Настроить DNS

Создайте A/AAAA-запись `MINI_APP_DOMAIN`, указывающую на VPS. До запуска проверьте,
что запись разрешается в правильный адрес, а порты 80/443 доступны извне. Caddy
сам запросит и обновит TLS-сертификат; его состояние хранится в отдельном volume.

## 6. Запустить новую PostgreSQL 16

```bash
docker compose --env-file .env -f compose.ministry.yml up -d db
docker compose --env-file .env -f compose.ministry.yml ps
```

Volume `ministry_of_culture_postgres_data` намеренно не совпадает с volume старого
compose. Не подключайте архивную БД SecretH к этому stack.

## 7. Собрать images и явно применить миграции

```bash
docker compose --env-file .env -f compose.ministry.yml build
docker compose --env-file .env -f compose.ministry.yml run --rm api alembic current
docker compose --env-file .env -f compose.ministry.yml run --rm api alembic upgrade head
docker compose --env-file .env -f compose.ministry.yml run --rm api alembic check
```

Миграции никогда не запускаются автоматически. Bot и API проверяют Alembic head
при старте и завершаются с ошибкой на отсутствующей или несовместимой схеме.

## 8. Создать клуб и bootstrap-приглашение

```bash
docker compose --env-file .env -f compose.ministry.yml run --rm bot \
  python -m app.bootstrap_club create-club \
  --club "Наш клуб" --timezone "Asia/Yakutsk"

docker compose --env-file .env -f compose.ministry.yml run --rm bot \
  python -m app.bootstrap_club create-invite \
  --club-id 00000000-0000-0000-0000-000000000000
```

Сохраните UUID клуба и одноразовый токен в защищённом месте. Не помещайте токен в
`.env`, логи или аналитику.

## 9. Запустить API, frontend/Caddy и единственный bot

```bash
docker compose --env-file .env -f compose.ministry.yml up -d api web bot
docker compose --env-file .env -f compose.ministry.yml ps
docker compose --env-file .env -f compose.ministry.yml logs --tail 100 api web bot
```

`bot` — ровно один long-polling process. `api:8000` доступен только внутри Docker
network. Наружу опубликованы только 80/443 Caddy; React assets встроены в image
`web`, а runtime secrets туда не передаются.

## 10. Настроить Main Mini App в BotFather

Вручную выберите бота в BotFather, настройте Main Mini App и укажите точный
`MINI_APP_URL`. Пригласительная ссылка имеет вид
`https://t.me/<bot_username>?startapp=<invite_token>`. Эти действия нельзя надёжно
автоматизировать из приложения.

## 11. Проверить HTTPS и health

```bash
curl --fail --show-error https://mini.example.org/health
curl --fail --show-error --head https://mini.example.org/
docker compose --env-file .env -f compose.ministry.yml ps
```

Замените пример своим доменом. `/health` должен вернуть `{"status":"ok"}`; SPA
должна открываться по HTTPS. Затем выполните реальный Telegram smoke: запуск из
menu button, регистрация по новой ссылке и повторное открытие без приглашения.

## 12. Зарегистрировать первого OWNER

Сначала будущий владелец регистрируется через bootstrap-приглашение. После этого
оператор назначает OWNER отдельно:

```bash
docker compose --env-file .env -f compose.ministry.yml run --rm bot \
  python -m app.bootstrap_club assign-owner \
  --club-id 00000000-0000-0000-0000-000000000000 \
  --telegram-id 123456789
```

Регистрация сама никогда не назначает OWNER или ADMIN.

## 13. Безопасное обновление и откат

Перед каждым обновлением сохраните текущий commit/image tag, создайте backup и
проверьте его список. Изучите новые миграции и совместимость отката, затем:

```bash
docker compose --env-file .env -f compose.ministry.yml stop bot api web
git fetch origin main
git switch --detach <проверенный-новый-commit>
docker compose --env-file .env -f compose.ministry.yml build
docker compose --env-file .env -f compose.ministry.yml run --rm api alembic upgrade head
docker compose --env-file .env -f compose.ministry.yml up -d api web bot
```

Проверьте health, logs и Telegram smoke. Для code rollback вернитесь на заранее
зафиксированный commit и пересоберите images. Не выполняйте destructive downgrade
на production. Если старая версия несовместима с новой схемой, оставьте сервисы
остановленными и восстановите предмиграционный dump в **новую** БД по проверенному
плану, затем переключите `DATABASE_URL`.

Обычная остановка — `docker compose ... down` или `stop`; volumes сохраняются.
**`docker compose ... down -v` удаляет PostgreSQL и Caddy volumes вместе с данными
и сертификатами. Не используйте `-v` при обновлении или обычной остановке.**

## 14. Backup и проверка восстановления

Создавайте custom-format dump с закрытыми правами и копируйте его off-host:

```bash
mkdir -p backups
chmod 700 backups
(umask 077; docker compose --env-file .env -f compose.ministry.yml exec -T db \
  sh -c 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > backups/ministry-YYYYMMDDTHHMMSSZ.dump)
docker compose --env-file .env -f compose.ministry.yml exec -T db \
  pg_restore --list < backups/ministry-YYYYMMDDTHHMMSSZ.dump
```

Проверка списка не заменяет restore rehearsal. Создайте отдельную disposable базу,
восстановите dump одной транзакцией и проверьте Alembic revision и данные:

```bash
docker compose --env-file .env -f compose.ministry.yml exec -T db \
  sh -c 'export PGPASSWORD="$POSTGRES_PASSWORD"; createdb -U "$POSTGRES_USER" ministry_restore_test'
docker compose --env-file .env -f compose.ministry.yml exec -T db \
  sh -c 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec pg_restore --exit-on-error --single-transaction --no-owner --no-privileges -U "$POSTGRES_USER" -d ministry_restore_test' \
  < backups/ministry-YYYYMMDDTHHMMSSZ.dump
```

Затем временно задайте отдельный `RESTORE_DATABASE_URL` для
`ministry_restore_test`, выполните `alembic current`, контрольные queries и
application smoke. Удалять тестовую базу можно только после подтверждения, что это
именно disposable target и что production к ней не подключён. Production volume
не очищается и не пересоздаётся. Автоматического web-restore нет.
