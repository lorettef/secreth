# Secret Hitler Stats

Минимальный Telegram-бот для будущего учёта партий. На этом этапе доступны только запуск и команда `/start`.

## Требования

- Docker с Compose plugin
- Токен бота от BotFather

## Запуск через Docker Compose

1. Скопируйте `.env.example` в `.env`.
2. Заполните `BOT_TOKEN` и задайте свой `POSTGRES_PASSWORD`.
3. В `DATABASE_URL` укажите тот же пароль; хост `db` нужен для запуска в Compose.
4. Запустите сервисы:

```bash
docker compose up --build -d
docker compose logs -f app
```

При запуске бот проверяет подключение к PostgreSQL, затем начинает long polling. Команда `/start` отвечает: «Secret Hitler Stats запущен.»

Остановить сервисы без удаления данных:

```bash
docker compose down
```

## Миграции

Alembic настроен, но в Phase 1 таблиц и миграций ещё нет. Запуск текущей ревизии:

```bash
docker compose run --rm app alembic upgrade head
```

## Тесты

На хосте с Python 3.12+:

```bash
python -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```

Через Docker без установки Python на хосте:

```bash
docker compose run --rm --build -v "$PWD/tests:/app/tests:ro" app sh -c "pip install '.[test]' && pytest"
```

Реальные токен и пароль держите только в `.env`; файл исключён из Git.
