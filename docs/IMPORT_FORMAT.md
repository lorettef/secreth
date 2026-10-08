# Нормализованный XLSX import v1

Импорт принимает **только** описанный формат, а не произвольный старый Excel. Адаптер реальной таблицы нужно написать после её получения. CLI выполняется доверенным оператором с доступом к БД, не через Telegram.

```bash
python scripts/import_legacy.py history.xlsx --dry-run --club-id CLUB_UUID
python scripts/import_legacy.py history.xlsx --apply --club-id CLUB_UUID
```

`DATABASE_URL` читается из environment или `.env`. При ровно одном клубе `--club-id` можно опустить. Клуб должен существовать. Другие клубы не меняются. Dry-run только читает БД: без INSERT/UPDATE, audit или изменения sequence. Apply проверяет весь набор и сохраняет его одной транзакцией, блокируя строку клуба. При любой ошибке нет частичного импорта. Ошибки содержат sheet и номер строки; CLI завершается с кодом 2. Успех — код 0. Исправьте все ошибки до apply; warnings требуют проверки человеком.

## Sheets и колонки

Все пять sheets обязательны, включая пустые `Events`. Первая строка — уникальные имена колонок. Используйте точные коды enum, не русские labels.

| Sheet | Обязательные колонки | Необязательные колонки |
|---|---|---|
| Players | display_name | player_id, is_active, created_at, updated_at, telegram_linked |
| Sessions | session_id, started_at, created_by_player_id, statistician_player_id | finished_at, status |
| Games | game_id, session_id, sequence_number, winner_team, played_at, created_by_player_id | victory_type, status, created_at, updated_at |
| GamePlayers | game_id, player_id, role | player_name |
| Events | event_id, game_id, player_id, event_type, created_by_player_id | ruin_reason, self_admitted, comment, created_at, updated_at, player_name |

Export создаёт эти sheets со всеми колонками. UUID — текст. `session_id`, `game_id`, `event_id` обязательны и уникальны. `player_id` может отсутствовать: создаётся UUID, ссылки на такого игрока должны содержать его **точное уникальное display_name** в колонках `player_id`, `created_by_player_id`, `statistician_player_id`. Предпочтительны UUID для всех ссылок. Все ссылки должны разрешаться внутри файла; запись существующего Player должна присутствовать в Players.

Имена не объединяются автоматически. Одинаковые имена без UUID — ошибка с номерами строк. С явно заданными разными UUID одинаковые имена допускаются с warning; ссылки тогда только UUID. Новое имя, совпадающее с существующим в целевом клубе (без учёта регистра), требует явного mapping UUID. Совпадения без учёта регистра также считаются ambiguous. Похожие соседние по алфавиту имена дают warning; это подсказка для ручной проверки, а не полный fuzzy search. Бот не угадывает и не объединяет `Макс == Максим`.

Существующий Player UUID допускается **только в том же клубе** при точном совпадении display_name и is_active: это reference без изменения игрока, с warning. Все новые Players имеют `telegram_user_id = NULL`; `telegram_linked` и `player_name` — информационные колонки, импорт их игнорирует. Account linking выполняется отдельно через ADMIN approval.

У Sessions/Games/Events существующие UUID отклоняются. Повторный набор без UUID Players также отклоняется по fingerprint нормализованного содержимого, сохранённому в IMPORT AuditLog. Fingerprint не предназначен для угадывания похожих, вручную изменённых наборов. Глобальные UUID нельзя копировать в другой клуб той же БД; для такого переноса нужен явный mapping новых UUID. В чистой БД исходные UUID сохраняются.

## Значения

- Dates: ISO 8601 с часовым поясом, например `2025-04-12T19:30:00+09:00`. Excel dates без timezone отклоняются. Export пишет UTC ISO 8601. Обязательны started_at и played_at; отсутствующие created_at/updated_at получают DB timestamps.
- Boolean: TRUE/FALSE, 1/0; is_active default TRUE, self_admitted default FALSE.
- Session status: ACTIVE / FINISHED / VOID; default FINISHED. Не более одного ACTIVE на клуб. Live roster не импортируется: snapshot Games сохраняется независимо от него. Для продолжения экспортированного ACTIVE вечера оператор должен восстановить roster и явно связать Telegram нового статиста или выполнить ADMIN transfer.
- Game status: ACTIVE / VOID; default ACTIVE. VOID сохраняется, но не входит в stats.
- winner_team: LIBERAL / FASCIST.
- victory_type: LIBERAL_POLICIES / HITLER_KILLED / FASCIST_POLICIES / HITLER_CHANCELLOR / UNKNOWN. Пустое значение становится UNKNOWN **только при legacy import**. Runtime wizard по-прежнему не принимает UNKNOWN. Способ победы должен соответствовать команде.
- GamePlayers role: LIBERAL / FASCIST / HITLER. На Game 5–10 уникальных игроков, ровно один Hitler и правильное число Fascists; проверяется общий domain validator.
- Event type: RUIN / PALIVO / IQ_200 / PROPHET. Участник должен быть в GamePlayers соответствующей Game. Повторные Events допустимы.
- RUIN требует ruin_reason: WRONG_VOTE / ROLE_REVEAL / BAD_CHANCELLOR / BAD_EXECUTION / LEAKED_INFO / TACTICAL_ERROR / OTHER. Для остальных типов ruin_reason очищается, self_admitted становится FALSE.
- Comment: до 500 символов, optional; пустой/пробельный — NULL. Unicode поддерживается.

## Безопасность и границы

Файл должен существовать, быть обычным `.xlsx`; предел 20 MiB compressed / 100 MiB expanded, 100000 строк / 64 колонки на sheet. Формулы и error cells отклоняются, не исполняются и не читаются как trusted cached values. Export записывает все строки как literal text cells, включая начальные `=`, `+`, `-`, `@`; текст не меняется при round-trip.

После успешного apply создаётся один `IMPORT` AuditLog (actor NULL: доверенный CLI оператор), с counts и fingerprint. Никакого audit на dry-run.

XLSX — переносимый **продуктовый export**, не полный технический backup: нет Telegram identities/memberships, invite tokens, заявок recovery/link, club settings и старого AuditLog. Их сохраняет PostgreSQL dump. Export сохраняет Players, Sessions, Games, roles и Events, включая inactive/VOID. Не импортируйте файл поверх действующей истории для её обновления; corrections идут через services/ADMIN UI.
