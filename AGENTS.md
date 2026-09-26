# AGENTS.md

## Project: Secret Hitler Stats

This repository contains a lightweight Telegram bot for tracking recurring Secret Hitler game nights, player statistics, roles, results, and a small set of memorable in-game events.

The product goal is **fast, reliable post-game entry with minimal friction**. The bot is not a full digital implementation of the board game and must not become a detailed turn-by-turn tracker.

---

## 1. Source of truth

The primary product specification is:

`docs/SPEC.md`

Before making any non-trivial change, read the relevant sections of `docs/SPEC.md` and inspect the current implementation and tests.

### Important rule

**Do not modify `docs/SPEC.md` on your own.**

If implementation and specification disagree:

1. treat `docs/SPEC.md` as the intended behavior;
2. report the conflict;
3. change the code to match the spec unless the user explicitly asks to change the product behavior;
4. only modify `docs/SPEC.md` when explicitly instructed.

Do not silently redefine product behavior in code.

---

## 2. Product principles

Every change should preserve these principles:

1. **Fast input.** A normal completed game should be recordable in roughly 20–30 seconds.
2. **Buttons first.** Normal users should not need to memorize bot commands.
3. **Compute what can be computed.** Do not ask users for values the bot can derive safely.
4. **One responsible editor per evening.** The statistician records the active game night; normal players are read-only.
5. **Admin is not required to start a game night.** Any active club player may start a session when none is active.
6. **Historical data must remain stable.** Changing the current roster must never mutate previously saved games.
7. **Saved games are the source of truth for statistics.** Do not maintain manual counters on players as authoritative state.
8. **Keep the MVP small.** Prefer a straightforward implementation over abstractions that do not solve a current requirement.

When deciding whether a feature belongs in the MVP, ask:

> Does this make game entry faster, safer, or make the accumulated statistics materially more useful?

If not, do not add it without explicit instruction.

---

## 3. Tech stack

Use the existing project stack unless explicitly instructed otherwise.

Expected stack:

- Python 3.12+
- aiogram 3.x
- PostgreSQL
- SQLAlchemy 2.x async
- asyncpg
- Alembic
- pydantic-settings
- pytest
- pytest-asyncio
- openpyxl for XLSX import/export
- Docker / Docker Compose

### Do not add without a demonstrated need

- Redis
- FastAPI
- Flask
- Django
- React / Vue / separate frontend
- Telegram Mini App
- Celery / task queues
- microservices
- Kubernetes
- event buses
- additional persistence layers
- complex caching systems

If one of these becomes necessary for a requested feature, explain the need before introducing it.

---

## 4. Architectural boundaries

Keep responsibilities separated.

### Telegram handlers

Handlers should:

- read Telegram input;
- validate basic interaction state;
- call application/service functions;
- render messages and keyboards.

Handlers should **not** contain substantial business logic, statistics formulas, permission policy, or direct multi-step database orchestration.

Bad pattern:

```python
@router.callback_query(...)
async def save_game(...):
    # 150 lines of role validation, permissions,
    # SQL operations, statistics updates, etc.
```

Preferred pattern:

```python
await game_service.create_game(...)
```

### Domain layer

Keep game rules and invariants in domain modules that do not depend on Telegram.

Examples:

- role distribution;
- role validation;
- victory validation;
- permission rules;
- reusable statistics formulas.

### Service/application layer

Services coordinate:

- permissions;
- domain validation;
- transactions;
- repositories/database operations;
- audit logging where required.

### Persistence layer

Database models and repositories handle persistence concerns only.

Do not duplicate the same business rule across handler, service, and repository layers.

---

## 5. Core domain invariants

These rules must be enforced on the backend even if the Telegram UI already prevents invalid input.

### Player count

A Secret Hitler game supports 5–10 players.

### Role distribution

Use one centralized source of truth for role counts:

| Players | Liberals | Fascists | Hitler |
|---:|---:|---:|---:|
| 5 | 3 | 1 | 1 |
| 6 | 4 | 1 | 1 |
| 7 | 4 | 2 | 1 |
| 8 | 5 | 2 | 1 |
| 9 | 5 | 3 | 1 |
| 10 | 6 | 3 | 1 |

`Fascists` in this table means ordinary Fascists and does **not** include Hitler.

### Game role validation

For every saved game:

- participants must be unique;
- all participants must belong to the same club;
- player count must be 5–10;
- exactly one Hitler exists;
- Hitler must be one of the participants;
- Hitler cannot also be an ordinary Fascist;
- ordinary Fascist count must match player count;
- Liberals are every remaining participant;
- roles must be derivable and internally consistent.

### Winner and victory type

Valid combinations:

- `LIBERAL + LIBERAL_POLICIES`
- `LIBERAL + HITLER_KILLED`
- `FASCIST + FASCIST_POLICIES`
- `FASCIST + HITLER_CHANCELLOR`

`UNKNOWN` is allowed only for legacy/imported historical data when the original victory type is unavailable.

For normal Telegram game entry, `UNKNOWN` must not be accepted.

---

## 6. Player identity and Telegram identity

`Player` and `TelegramUser` are separate concepts.

A historical player may exist with no Telegram account linked.

This is required for importing old Excel history.

Do not merge these entities into one model.

Do not create a duplicate Player automatically when a Telegram user may correspond to an existing historical Player.

Any linking flow must preserve historical statistics.

---

## 7. User roles and permissions

There are three conceptual access levels.

### PLAYER

May:

- view the current game night;
- view current roster;
- view statistics;
- view leaderboards;
- view history;
- start a new game night if no active session exists.

May not:

- record games unless they become the session statistician;
- modify rosters;
- edit game history;
- manage players or club settings.

### STATISTICIAN

A statistician role belongs to a specific active game session, not to the user globally.

May:

- record games in that active session;
- change the current roster;
- add game events;
- edit games in that active session;
- transfer statistician responsibility;
- finish the session.

May not:

- edit historical sessions;
- manage admins;
- perform unrestricted player administration;
- modify club-wide settings.

### ADMIN

May perform all club-level management operations defined in the spec.

### Security rule

Hiding a Telegram button is never sufficient authorization.

Every state-changing service function must verify permissions on the backend.

Do not trust callback payloads or UI state as proof of authorization.

---

## 8. Game sessions and roster behavior

A club may have at most one `ACTIVE` game session at a time.

Enforce this at database level where possible, not only in Python.

Starting a session:

- does not require an admin;
- may be done by any active club player when no active session exists;
- makes the creator the session statistician.

The session roster is a live list of currently present players.

Players may:

- arrive late;
- leave early;
- leave and return.

Historical games must not depend on the current session roster after they are saved.

Every saved game must contain its own participant snapshot through `GamePlayer` or equivalent records.

Never reconstruct old game participants from current session presence.

---

## 9. Recording a game

The standard happy path is:

1. choose/confirm participants;
2. choose Hitler;
3. choose the required number of Fascists;
4. derive Liberals automatically;
5. choose winning team;
6. choose victory type;
7. review;
8. save.

Do not add extra mandatory steps without explicit instruction.

### Normal roster behavior

If 5–10 players are currently present, use the current roster as the default game participant set.

If more than 10 are present, require selection of 5–10 actual participants for that game.

If fewer than 5 are present, block game creation.

### Idempotency

Double-clicking or replaying a Save callback must not create duplicate games.

Use a draft/idempotency mechanism.

Final game creation should happen transactionally.

### Sequence numbers

`sequence_number` is unique within a session.

Do not use an unsafe `MAX(sequence_number) + 1` implementation without locking or another concurrency-safe strategy.

---

## 10. Game events

The MVP supports exactly these event types:

- `RUIN`
- `PALIVO`
- `IQ_200`
- `PROPHET`

Do not add additional event types unless explicitly requested.

There is **no clown moment**.

### Ruin reasons

Use stable enum codes:

- `WRONG_VOTE`
- `ROLE_REVEAL`
- `BAD_CHANCELLOR`
- `BAD_EXECUTION`
- `LEAKED_INFO`
- `TACTICAL_ERROR`
- `OTHER`

### Event rules

- events belong to a completed game;
- event player must have participated in that game;
- multiple events per game are allowed;
- multiple events of the same type are allowed;
- one player may have multiple events in one game;
- `self_admitted` is meaningful only for `RUIN`;
- event comment is optional and limited to the configured/spec maximum;
- no voting/approval workflow for Ruin in MVP.

Do not infer a Ruin from a loss automatically.

---

## 11. Statistics rules

Saved, non-void games are the source of truth.

Do not manually increment/decrement permanent counters on `Player` when games are edited.

Statistics must update correctly after:

- game edits;
- game void;
- game restore;
- role corrections;
- winner corrections.

### Player win

- Liberal wins when `winner_team == LIBERAL`.
- Fascist wins when `winner_team == FASCIST`.
- Hitler wins when `winner_team == FASCIST`.

### Ruin metrics

Track conceptually:

- `ruin_events`: number of Ruin event rows;
- `games_with_ruin`: number of distinct games in which the player had at least one Ruin;
- `ruin_rate = games_with_ruin / total_games * 100`.

Do not calculate Ruin rate as `ruin_events / total_games`.

### Leaderboards

Use club-configured minimum game thresholds.

Do not implement Elo, TrueSkill, MMR, AI skill scores, or hidden composite ratings in MVP.

---

## 12. Soft deletion and audit

Games should not normally be physically deleted.

Use `VOID` or equivalent soft-delete state.

Void games:

- remain stored;
- are excluded from statistics;
- are excluded from session score;
- may be restored by admin.

Administrative changes that affect historical data should produce audit records where required by the spec.

Do not store secrets or credentials in audit logs.

---

## 13. Database rules

Prefer constraints for invariants that the database can enforce safely.

Examples:

- unique Telegram user id;
- one player per game;
- one sequence number per session;
- one active session per club;
- appropriate foreign keys;
- useful indexes.

When changing database schema:

1. modify the SQLAlchemy models;
2. create an Alembic migration;
3. inspect the generated migration manually;
4. test `upgrade`;
5. when practical, test `downgrade` and re-upgrade;
6. update tests.

Never rely on `create_all()` as a production migration strategy.

---

## 14. Transactions and concurrency

Use transactions for operations that change related state.

Pay special attention to:

- starting an active game session;
- allocating game sequence numbers;
- saving a game and its roles;
- transferring statistician rights;
- player linking;
- void/restore operations;
- imports.

Handle race conditions with database constraints and, where appropriate, row locking or transaction isolation.

Do not solve concurrency only with in-memory flags.

---

## 15. FSM and drafts

A partially completed game wizard is not official data.

It may live in aiogram FSM / in-memory storage during MVP.

If the bot restarts mid-wizard, losing the unfinished draft is acceptable.

Persisted game data must never be half-complete.

Do not introduce Redis solely to preserve unfinished wizard state.

If multi-instance deployment becomes a real requirement later, revisit FSM storage then.

---

## 16. Telegram UX rules

The bot should feel lightweight.

### Prefer

- inline buttons;
- concise messages;
- clear Back/Cancel paths where a wizard can branch;
- human-readable validation errors;
- automatic derivation of roles/counts;
- stable callback data objects.

### Avoid

- requiring users to type commands for normal flows;
- exposing raw IDs unnecessarily;
- giant messages with hundreds of rows;
- raw exceptions;
- ambiguous stale buttons;
- asking the user to manually select Liberals.

### Stale callbacks

Users may press an old Telegram button days later.

Handlers must re-check current state and respond safely, for example:

> Этот игровой вечер уже завершён.

Do not assume a callback is valid because it was valid when the message was created.

---

## 17. Callback data

Use structured callback data, preferably aiogram `CallbackData`.

Do not store large JSON payloads in callback data.

Do not put untrusted state in a callback and then trust it without checking the database.

Keep within Telegram callback size limits.

---

## 18. User-generated text

Event comments and names are user-controlled input.

When using Telegram HTML or Markdown parse mode:

- escape user-provided content correctly;
- do not allow formatting injection;
- do not expose raw exceptions caused by malformed text.

Respect comment length limits.

---

## 19. Import and export

Legacy import is important but must not guess the structure of unknown spreadsheets.

The importer should:

- support dry-run;
- report invalid rows;
- avoid silent duplicate player creation;
- preserve historical players without Telegram bindings;
- use transactions;
- support `UNKNOWN` victory type for incomplete legacy data.

Do not adapt the importer to a specific legacy spreadsheet until the actual spreadsheet is available.

Export should create a usable XLSX backup/reference with stable identifiers.

---

## 20. Time handling

Store timestamps as timezone-aware values (`TIMESTAMPTZ`).

Store the club timezone explicitly.

Render dates/times in the club timezone.

Do not rely on the application server's local timezone for business behavior.

---

## 21. Secrets and configuration

Never commit:

- bot tokens;
- database passwords;
- production credentials;
- real `.env` files.

Use environment variables and `.env.example`.

Ensure `.env` is ignored by Git.

Never print the bot token in logs or error reports.

---

## 22. Logging

Log meaningful system events such as:

- startup/shutdown;
- Telegram exceptions;
- database exceptions;
- game session creation;
- game creation;
- historical edits;
- void/restore;
- statistician transfer;
- import/export.

Logs should help diagnose failures without leaking secrets.

---

## 23. Tests are mandatory

Do not consider a feature complete without tests appropriate to its risk.

### Unit tests should cover

- role distribution for 5–10 players;
- invalid player counts;
- role validation;
- winner/victory validation;
- permission rules;
- statistics formulas.

### Integration tests should cover

- database constraints;
- session lifecycle;
- game creation;
- role persistence;
- duplicate-save protection;
- roster changes;
- game snapshots;
- void/restore behavior;
- player linking;
- audit generation where applicable.

### Important regression cases

Always protect these behaviors:

1. changing roster after Game 1 does not change Game 1 participants;
2. two Ruin events in one game count as two events but one `games_with_ruin`;
3. a void game disappears from statistics;
4. restoring it returns the previous statistics;
5. double Save creates one game;
6. two simultaneous session-start attempts create at most one active session;
7. an unauthorized callback cannot bypass service permissions.

When modifying a bug-prone behavior, add a regression test first or alongside the fix.

---

## 24. Test execution

Before declaring work complete, run the relevant test suite.

For substantial changes, run the full suite.

Typical commands may include:

```bash
pytest
```

or project-specific Docker/test commands documented in `README.md`.

Do not claim tests pass unless they were actually executed successfully.

If a test cannot be run due to environment limitations, state exactly what could not be verified.

---

## 25. Code quality

Prefer clear, boring code over clever abstractions.

Guidelines:

- small focused functions;
- explicit names;
- type hints for application/domain code;
- avoid duplicate business logic;
- avoid magic numbers for role counts;
- avoid UI strings as database/business enum values;
- avoid unnecessary inheritance;
- avoid generic repository/service frameworks that obscure simple operations;
- avoid premature optimization.

A small amount of duplication is preferable to a complicated abstraction that has only one real use case.

Refactor when there is an actual repeated concept, not in anticipation of hypothetical future needs.

---

## 26. Dependency policy

Before adding a package, ask:

1. Is this required by the current task?
2. Can the standard library or an existing dependency solve it simply?
3. Does the package materially reduce complexity?
4. Will this create extra deployment or maintenance burden?

Do not add dependencies for tiny convenience functions.

When adding a dependency, update project metadata and relevant documentation.

---

## 27. Scope discipline

Do not implement future phases while working on a current scoped task.

Examples:

- while building DB models, do not start building statistics UI;
- while building game recording, do not add Elo;
- while building events, do not create an achievements engine;
- while fixing a callback bug, do not redesign the entire architecture.

If you notice a useful future improvement, mention it in the completion note instead of implementing it silently.

---

## 28. Explicit non-goals for MVP

Unless the user changes the specification, do not implement:

- turn-by-turn voting history;
- president history;
- chancellor history;
- policy-card history by round;
- nomination history;
- election tracker history;
- digital role distribution during a live game;
- Elo/MMR/TrueSkill;
- AI player ratings;
- public global leaderboards;
- betting or gambling features;
- Telegram Mini App;
- separate website;
- elaborate achievements system;
- additional event categories beyond the four specified;
- original board-game assets copied into the repository.

---

## 29. Documentation expectations

Update `README.md` when a change affects:

- setup;
- environment variables;
- migrations;
- Docker commands;
- admin bootstrap;
- backup/restore;
- import/export;
- operational troubleshooting.

Do not duplicate the full product spec into the README.

`docs/SPEC.md` remains the product behavior source of truth.

---

## 30. How to approach a task

For every non-trivial task:

1. read the user's request;
2. read relevant sections of `docs/SPEC.md`;
3. inspect the current implementation;
4. inspect relevant tests;
5. identify the smallest coherent change;
6. implement domain/service logic first where appropriate;
7. add or update tests;
8. add migrations if schema changes;
9. run tests;
10. review the diff for accidental scope creep;
11. report what changed and what was verified.

Do not begin by rewriting working code.

---

## 31. Pre-completion self-review

Before saying a task is finished, check:

### Product

- Does behavior match `docs/SPEC.md`?
- Did the change preserve fast game entry?
- Did I add anything the user did not request?

### Architecture

- Is business logic outside Telegram handlers?
- Is permission enforcement server-side?
- Are domain rules centralized?
- Is saved game history independent of the live roster?

### Database

- Are constraints present where appropriate?
- Is the operation transactional where needed?
- Was a migration added and inspected for schema changes?

### Reliability

- Is repeated callback handling safe?
- Are stale buttons safe?
- Are race conditions handled for critical operations?
- Could this accidentally duplicate a game or session?

### Statistics

- Are stats derived from active saved games?
- Do edits/void/restore automatically affect stats correctly?

### Security

- Are permissions rechecked on the backend?
- Is user text escaped?
- Are credentials absent from source/logs?

### Testing

- Are new behaviors tested?
- Did the relevant test suite actually run?

If any answer is uncertain, investigate before declaring completion.

---

## 32. Completion report format

When finishing a task, keep the report concise and factual.

Include:

1. what was implemented;
2. files/modules materially changed;
3. migrations added, if any;
4. tests run and result;
5. any known limitation or item intentionally left out of scope.

Do not claim production readiness solely because code compiles.

---

## 33. Final guiding rule

This project should remain a **small, dependable Telegram bot for a real group of friends**.

The most valuable asset will eventually be the accumulated history of games, not the cleverness of the codebase.

Favor:

**data integrity + fast UX + understandable code**

over
**complex architecture + speculative features + unnecessary abstractions**.
