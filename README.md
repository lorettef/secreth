# Secret Hitler Stats

Telegram-бот для учёта партий Secret Hitler. Сейчас доступны вступление в клуб по приглашению и привязка игрока к Telegram.

## Требования

- Docker с Compose plugin
- Токен бота от BotFather

## Запуск через Docker Compose

1. Скопируйте `.env.example` в `.env`.
2. Заполните `BOT_TOKEN` и задайте свой `POSTGRES_PASSWORD`.
3. В `DATABASE_URL` укажите тот же пароль; хост `db` нужен для запуска в Compose.
4. Запустите PostgreSQL, примените миграции и создайте первого администратора:

```bash
docker compose up -d db
docker compose build app
docker compose run --rm app alembic upgrade head
docker compose run --rm app python -m app.bootstrap_admin \
  --telegram-id 123456789 --club "Название клуба" \
  --timezone "Asia/Yakutsk" --player "Имя администратора"
```

Замените `123456789` реальным Telegram ID администратора. Bootstrap разрешён только пока в базе нет ни одного клуба. Повторный запуск не создаст второй клуб или второго администратора.

5. Запустите бота:

```bash
docker compose up --build -d
docker compose logs -f app
```

При запуске бот проверяет PostgreSQL, затем начинает long polling. Администратор открывает `/start`, нажимает «Управление клубом» → «Создать приглашение» и отправляет ссылку игроку. Игрок открывает ссылку, вступает в клуб и выбирает «Я новый игрок» либо исторического игрока без Telegram-привязки. Привязку исторического игрока администратор подтверждает в «Заявках на привязку».

Остановить сервисы без удаления данных:

```bash
docker compose down
```

## Миграции

Миграции `0001_initial_models` и `0002_player_link_requests` создают таблицы и ограничения. Применить их:

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
