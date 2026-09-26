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

Миграция `0001_initial_models` создаёт таблицы и ограничения. Применить её:

```bash
docker compose run --rm app alembic upgrade head
```

Для локального Alembic задайте `DATABASE_URL` в окружении процесса. Команды `alembic downgrade base` и повторный `alembic upgrade head` проверяйте только на отдельной тестовой базе: downgrade удаляет таблицы.

## Тесты

На хосте с Python 3.12+:

```bash
python -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```

Интеграционные тесты требуют отдельную PostgreSQL-базу с именем, оканчивающимся на `_test`. Создайте её и задайте `TEST_DATABASE_URL` в формате `postgresql+asyncpg://user:password@host:port/database_test` перед запуском pytest. Тесты применяют миграцию к этой базе и откатывают данные каждого теста транзакцией. Если переменная не задана, интеграционные тесты пропускаются.

Через Docker без установки Python на хосте:

```bash
docker compose run --rm --build -v "$PWD/tests:/app/tests:ro" app sh -c "pip install '.[test]' && pytest"
```

Реальные токен и пароль держите только в `.env`; файл исключён из Git.
