# Go-live: ручная проверка перед использованием

Автоматические тесты не заменяют реальные Telegram interaction. Выполните сначала на отдельном smoke-клубе/базе; не публикуйте token и приглашения в logs/скриншотах.

## Инфраструктура

- [ ] `.env` заполнен, не tracked; пароль отличается от примера.
- [ ] Работает ровно один bot application instance, long polling.
- [ ] `docker compose build app` успешен.
- [ ] PostgreSQL `docker compose ps` healthy, named volume подключён.
- [ ] Fresh installation: `alembic upgrade head`; `alembic current` показывает `0004_statistician_recovery`.
- [ ] `alembic heads` — одна head, `alembic check` clean.
- [ ] Первый ADMIN создан; повторный bootstrap безопасно отклоняется.
- [ ] Backup через pg_dump работает; dump доступен только оператору.
- [ ] Restore в **другую пустую disposable БД** выполнен; counts/stats/history совпадают.
- [ ] Настроен ежедневный внешний backup (cron/provider), retention и off-host copy.
- [ ] `docker compose down` → `up` сохраняет fixture данные; **не используйте `down -v`**.

## Реальный Telegram smoke test

- [ ] `/start`: меню клуба, отображение имён и Unicode корректное.
- [ ] ADMIN создаёт одноразовое invite; другой user вступает, создаёт Player либо запрашивает historical linking; ADMIN подтверждает.
- [ ] PLAYER начинает вечер и становится statistician; выбирает минимум 5 присутствующих.
- [ ] Другой PLAYER видит read-only вечер и не может менять данные.
- [ ] Roster: пришёл / ушёл / вернулся; старые Games не меняются.
- [ ] Game: Hitler → Fascists → winner → victory → review → Save; двойной Save даёт одну Game.
- [ ] Events: RUIN с причиной/self-admitted/comment; PALIVO, IQ_200, PROPHET; повторный Save даёт одно событие.
- [ ] Stats: Player, роли, вечер, клуб; все шесть leaderboards; empty/threshold states.
- [ ] History: вечера → Game Card; Player games; pagination.
- [ ] Statistician transfer и emergency recovery с двумя другими подтверждающими работают.
- [ ] Finish session лишает прежнего статиста права historical correction.
- [ ] ADMIN исправляет winner/роли; stats меняются без ручного пересчёта.
- [ ] ADMIN VOID → stats исключают Game; RESTORE → stats возвращаются; AuditLog содержит действия.
- [ ] ADMIN редактирует/удаляет Event и деактивирует/реактивирует Player; история сохраняется.
- [ ] ADMIN экспортирует XLSX document; все пять sheets, Unicode, VOID и UUID присутствуют.
- [ ] Старые Back/Cancel/Save/confirmation кнопки отвечают «больше не актуальна» и не меняют новый flow.
- [ ] Restart посреди wizard теряет только draft; сохранённые данные целы, flow можно начать заново.
- [ ] Проверка чужого club callback не открывает его private данные.
- [ ] Logs содержат startup/shutdown и операции; нет tracebacks, секретов, unclosed resources.

## После обновления

- [ ] Backup создан **до** изменения code/schema.
- [ ] Bot остановлен на время migration, image обновлён, `alembic upgrade head` выполнен оператором.
- [ ] Bot запущен; logs/schema/startup проверены.
- [ ] Повторены `/start`, запись тестовой Game/Event, stats/history/export.

Только после живой проверки можно вводить MVP в работу. Не отмечайте пункты выполненными только по чтению исходников.
