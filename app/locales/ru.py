NO_MEMBERSHIP = "Вы пока не состоите в клубе. Попросите администратора прислать приглашение."
CHOOSE_CLUB = "Выберите клуб:"
JOIN_BUTTON = "Вступить"
NEW_PLAYER_BUTTON = "Я новый игрок"
EXISTING_PLAYER_BUTTON = "Я уже есть в статистике"
ADMIN_BUTTON = "⚙️ Управление клубом"
PROFILE_BUTTON = "👤 Моя статистика"
CREATE_INVITE_BUTTON = "🔗 Создать приглашение"
CREATE_INVITE_ONCE_BUTTON = "🔗 Приглашение на 1 вход"
CREATE_INVITE_WEEK_BUTTON = "🔗 Приглашение на 7 дней"
PENDING_LINKS_BUTTON = "📋 Заявки на привязку"
PREVIOUS_BUTTON = "⬅️"
NEXT_BUTTON = "➡️"
BACK_BUTTON = "⬅️ Назад"
APPROVE_BUTTON = "✅ Подтвердить"
REJECT_BUTTON = "❌ Отклонить"
NO_UNLINKED_PLAYERS = "Свободных исторических игроков пока нет."
NO_PENDING_LINKS = "Ожидающих заявок нет."
PROFILE_UNAVAILABLE = "Профиль игрока пока не привязан."
LINK_REQUESTED = "Заявка отправлена администратору. После подтверждения история появится в вашем профиле."
LINK_APPROVED = "Игрок привязан к Telegram-пользователю."
LINK_REJECTED = "Заявка отклонена."
GENERIC_ERROR = "Не удалось выполнить действие. Попробуйте позже."
INVALID_ACTION = "Эта кнопка больше не действует. Откройте /start."
START_SESSION_BUTTON = "🍻 Начать игровой вечер"
OPEN_SESSION_BUTTON = "🍻 Текущий игровой вечер"
START_SELECTED_BUTTON = "🍻 Начать с выбранным составом"
RECORD_GAME_BUTTON = "➕ Записать игру"
CHANGE_ROSTER_BUTTON = "👥 Изменить состав"
SESSION_SUMMARY_BUTTON = "📊 Итоги вечера"
TRANSFER_BUTTON = "🔄 Передать ведение"
FINISH_BUTTON = "🏁 Завершить вечер"
ARRIVED_BUTTON = "➕ Кто-то пришёл"
LEFT_BUTTON = "🚪 Кто-то ушёл"
CONFIRM_BUTTON = "✅ Подтвердить"
CANCEL_BUTTON = "❌ Отмена"
NO_ROSTER_CHOICES = "Подходящих игроков нет."
SESSION_STALE = "Этот экран устарел. Откройте игровой вечер заново."
SESSION_HELP_BUTTON = "⚙️ Помощь"
RECOVERY_BUTTON = "⚙️ Проблема со статистом"
RECOVERY_START_BUTTON = "Сменить статиста"
RECOVERY_HELP = "Если статист недоступен, присутствующие участники могут выбрать нового. Нужны подтверждения двух других участников; администратор может сменить статиста сразу."
RECOVERY_CHOOSE = "Кто из присутствующих будет новым статистом?"
RECOVERY_CONFIRM_BUTTON = "✅ Подтвердить смену"
RECOVERY_REFRESH_BUTTON = "🔄 Обновить"
RECOVERY_SHARE = "Передайте эту ссылку двум другим присутствующим участникам. Инициатор и кандидат не подтверждают заявку."
SESSION_START_PROMPT = "Выберите присутствующих игроков. Вечер можно начать и с составом меньше 5 человек."
SESSION_SELECT_PROMPT = "Выберите присутствующих игроков:"
SESSION_PICK_PROMPT = "Выберите игрока:"
SESSION_CANCELLED = "Действие отменено. Откройте /start для главного меню."
SESSION_FINISH_PROMPT = "Завершить игровой вечер? После завершения статист больше не сможет добавлять игры."
SESSION_STARTED = "Игровой вечер начался.\n"
SESSION_ALREADY_STARTED = "Вечер уже начат.\n"
SESSION_CHOICE_TITLES = {"add": "Кто пришёл?", "remove": "Кто ушёл?", "transfer": "Кому передать ведение?", "recovery": RECOVERY_CHOOSE}
SESSION_ERRORS = {
    "NO_ACCESS": "Нет доступа к этому клубу.",
    "PLAYER_REQUIRED": "Сначала привяжите активного игрока к своему Telegram.",
    "SESSION_NOT_FOUND": "Игровой вечер не найден.",
    "SESSION_FINISHED": "Этот игровой вечер уже завершён.",
    "NOT_STATISTICIAN": "Управлять вечером может текущий статист или администратор.",
    "PLAYER_NOT_AVAILABLE": "Этот игрок недоступен для состава.",
    "ALREADY_PRESENT": "Игрок уже в текущем составе.",
    "NOT_PRESENT": "Игрока нет в текущем составе.",
    "INVALID_SELECTION": "Состав изменился. Выберите игроков заново.",
    "TRANSFER_NOT_AVAILABLE": "Этому игроку нельзя передать ведение.",
    "STALE": SESSION_STALE,
    "CONFIRMATION_NOT_ALLOWED": "Подтверждать могут только другие активные присутствующие участники этого вечера.",
}
GAME_ERRORS = {
    "NOT_FOUND": "Партия или игровой вечер не найдены.",
    "NO_ACCESS": "Нет доступа к этому клубу.",
    "SESSION_FINISHED": "Этот игровой вечер уже завершён.",
    "NOT_STATISTICIAN": "Записывать партии может текущий статист или администратор.",
    "TOO_FEW_PRESENT": "Для записи партии нужно минимум 5 присутствующих игроков.",
    "INVALID_PARTICIPANTS": "Выберите от 5 до 10 разных игроков этого клуба.",
    "INVALID_ROLES": "Проверьте выбор Hitler и обычных Fascists.",
    "INVALID_VICTORY": "Способ победы не соответствует победившей команде.",
    "DRAFT_CONFLICT": "Этот экран записи устарел. Начните запись партии заново.",
    "STALE": "Эта кнопка больше не актуальна.",
}

EVENT_TYPE_LABELS = {
    "RUIN": "💥 Руина", "PALIVO": "🎭 Палево", "IQ_200": "🧠 200 IQ", "PROPHET": "🔮 Пророк",
}
RUIN_REASON_LABELS = {
    "WRONG_VOTE": "🗳 Неправильное голосование",
    "ROLE_REVEAL": "🎭 Спалил / неправильно отыграл роль",
    "BAD_CHANCELLOR": "👑 Неправильный выбор канцлера",
    "BAD_EXECUTION": "🔫 Неправильное убийство",
    "LEAKED_INFO": "🗣 Сказал лишнюю информацию",
    "TACTICAL_ERROR": "🧠 Тактическая ошибка", "OTHER": "❓ Другое",
}
EVENT_ERRORS = {
    "NOT_FOUND": "Партия или момент не найдены.",
    "NO_ACCESS": "Моменты текущего вечера может добавлять и исправлять статист или ADMIN; исторические изменения и удаление доступны ADMIN.",
    "INVALID_EVENT": "Проверьте игрока, тип события, причину Руины и комментарий (до 500 символов).",
    "DRAFT_CONFLICT": "Этот момент уже изменён или удалён. Начните новый ввод.",
    "STALE": "Эта кнопка больше не актуальна.",
}
EVENT_TYPE_PROMPT = "✨ Что произошло?"
EVENT_PLAYER_PROMPT = "👤 Кто? Выберите участника этой партии."
EVENT_REASON_PROMPT = "💥 Почему Руина?"
EVENT_ADMITTED_PROMPT = "🙋 Игрок сам признал Руину?"
EVENT_COMMENT_PROMPT = "💬 Добавить комментарий? Ответьте на запрос ниже или нажмите «Пропустить»."
EVENT_COMMENT_REPLY = "💬 Ответьте на это сообщение: комментарий до 500 символов."
EVENT_COMMENT_INVALID = "Комментарий должен содержать от 1 до 500 символов. Можно нажать «Пропустить»."
EVENT_COMMENT_STALE = "Ответьте на актуальный запрос комментария; этот текст не добавлен."
EVENT_REVIEW = "✅ Проверка момента"
EVENT_SAVED = "✅ Момент добавлен"
EVENT_CANCELLED = "Добавление момента отменено."
EVENT_ADD = "✨ Добавить момент"
EVENT_MORE = "✨ Добавить ещё"
EVENT_GAME = "🎲 К игре"
EVENT_HOME = "🏠 К вечеру"
EVENT_SKIP = "Пропустить"
EVENT_YES = "Да"
EVENT_NO = "Нет"
EVENT_SAVE = "✅ Сохранить"
GAME_VICTORY_UNKNOWN = "Способ победы неизвестен"


def event_card(player_name, event_type, ruin_reason, self_admitted, comment) -> str:
    lines = [EVENT_TYPE_LABELS[event_type.value], f"Игрок: {player_name}"]
    if event_type.value == "RUIN":
        lines += [f"Причина: {RUIN_REASON_LABELS[ruin_reason.value]}",
                  f"Сам признал: {EVENT_YES.lower() if self_admitted else EVENT_NO.lower()}"]
    if comment:
        lines += ["", "Комментарий:", comment]
    return "\n".join(lines)


def session_message(view) -> str:
    roster = ", ".join(p.name for p in view.roster) or "никого нет"
    status = "Активен" if view.status.value == "ACTIVE" else "Завершён"
    return (
        f"🍻 {view.club_name} · {status}\n"
        f"Статист: {view.statistician_name}\n"
        f"Сейчас за столом ({len(view.roster)}): {roster}\n"
        f"Завершённых игр: {view.game_count}\n"
        f"Счёт Liberal/Fascist: {view.liberal_wins}:{view.fascist_wins}"
    )


def recovery_message(view) -> str:
    if view.status.value == "APPLIED":
        return f"📋 Новый статист: {view.candidate_name}"
    return f"⚙️ Смена статиста на {view.candidate_name}\nПодтверждений: {view.confirmations}/2."

ERRORS = {
    "USER_NOT_FOUND": "Сначала откройте /start.",
    "NOT_MEMBER": "Вы не состоите в этом клубе.",
    "BLOCKED": "Доступ к этому клубу ограничен.",
    "NOT_ADMIN": "Это действие доступно только администратору клуба.",
    "ADMIN_PLAYER_REQUIRED": "Сначала свяжите ваш Telegram с игроком, чтобы создавать приглашения.",
    "INVITE_INVALID": "Приглашение недействительно или отключено.",
    "INVITE_EXPIRED": "Срок действия приглашения истёк.",
    "INVITE_EXHAUSTED": "Приглашение больше нельзя использовать.",
    "INVALID_INVITE_OPTIONS": "Некорректные параметры приглашения.",
    "ALREADY_LINKED": "Ваш Telegram уже связан с игроком этого клуба.",
    "NAME_REQUIRED": "В Telegram не указано имя. Обратитесь к администратору.",
    "PENDING_EXISTS": "Ваша заявка уже ожидает решения администратора.",
    "PLAYER_NOT_AVAILABLE": "Этот игрок уже занят или недоступен для привязки.",
    "REQUEST_NOT_FOUND": "Заявка не найдена.",
    "ALREADY_REVIEWED": "Эта заявка уже обработана.",
}


def invite_message(club_name: str) -> str:
    return f"🎩 Secret Hitler Stats\nВас приглашают в клуб «{club_name}»."


def club_menu_message(club_name: str, player_name: str | None, has_pending_link: bool) -> str:
    if player_name:
        return f"🎩 {club_name}\n👤 {player_name}"
    if has_pending_link:
        return f"🎩 {club_name}\nПривязка игрока ожидает подтверждения администратора."
    return f"🎩 {club_name}\nВыберите, как добавить себя в статистику:"


def player_choices_message(club_name: str) -> str:
    return f"Клуб «{club_name}». Выберите себя среди игроков без Telegram-привязки:"


def admin_menu_message(club_name: str) -> str:
    return f"⚙️ Управление клубом «{club_name}»"


def profile_message(club_name: str, player_name: str) -> str:
    return f"👤 {player_name}\nКлуб: {club_name}"


def invite_created_message(link: str) -> str:
    return f"Приглашение создано:\n{link}"


def pending_label(request) -> str:
    return f"{request.requester_name} · ID {request.requester_telegram_id} → {request.player_name}"


def pending_details(request) -> str:
    username = f"@{request.requester_username}" if request.requester_username else "без username"
    return (
        f"Пользователь: {request.requester_name} ({username})\n"
        f"Telegram ID: {request.requester_telegram_id}\n"
        f"Исторический игрок: {request.player_name}\n"
        "Подтвердить привязку?"
    )

# Statistics: values remain numeric in the service; rendering is plain text.
STATS_MY_BUTTON = PROFILE_BUTTON
STATS_BUTTON = "📊 Статистика"
RATINGS_BUTTON = "🏆 Рейтинги"
STATS_SESSION_BUTTON = "📊 Статистика вечера"
STATS_SECTIONS = {
    "players": "👤 По игрокам", "roles": "🎭 По ролям", "ruin": "💥 Руины",
    "moments": "✨ Моменты", "sessions": "🍻 По вечерам", "club": "🌍 Общая статистика клуба",
}
STATS_BOARD_LABELS = {
    "overall": "🔥 Общий winrate", "liberal": "🔵 Liberal", "fascist": "😈 Fascist",
    "hitler": "👑 Hitler", "ruin": "💥 Руины", "iq": "🧠 200 IQ",
}
STATS_ROLE_LABELS = {"LIBERAL": "🔵 Liberal", "FASCIST": "😈 Fascist", "HITLER": "👑 Hitler"}
STATS_ERRORS = {
    "NO_ACCESS": "Статистика доступна только участникам этого клуба.",
    "NOT_FOUND": "Игрок или игровой вечер больше не доступен.",
    "NO_PLAYER": "Ваш Telegram ещё не связан с игроком клуба. Сначала создайте или привяжите игрока.",
    "INVALID_PAGE": "Эта кнопка больше не актуальна.",
    "INVALID_BOARD": "Эта кнопка больше не актуальна.",
}
STATS_NO_GAMES = "Пока нет сыгранных партий."
STATS_NO_SESSIONS = "Пока нет игровых вечеров."
STATS_NO_PLAYERS = "Пока нет игроков."
STATS_VICTORY_LABELS = {
    "LIBERAL_POLICIES": "5 либеральных законов", "HITLER_KILLED": "Hitler убит",
    "FASCIST_POLICIES": "6 фашистских законов", "HITLER_CHANCELLOR": "Hitler избран канцлером",
    "UNKNOWN": "Причина победы неизвестна",
}


def stats_percent(value: float) -> str:
    return f"{value:.1f}%"


def stats_eligibility(info) -> str:
    return "" if info.eligible else f"\nНедостаточно игр: {info.games}/{info.required_games}"


def stats_event_lines(counts) -> str:
    return "\n".join(f"{EVENT_TYPE_LABELS[k.value]}: {count}" for k, count in counts.items())


def stats_player_card(player) -> str:
    text = (f"👤 {player.display_name}\n\n🎲 Игр: {player.total_games}\n🏆 Побед: {player.total_wins}"
            f"\n📈 Winrate: {stats_percent(player.overall_winrate)}{stats_eligibility(player.overall_eligibility)}")
    for role, score in player.roles.items():
        text += (f"\n\n{STATS_ROLE_LABELS[role.value]}\n{score.games} игр / {score.wins} побед / "
                 f"{stats_percent(score.winrate)}{stats_eligibility(player.role_eligibility[role])}")
    text += (f"\n\n💥 Руины: {player.ruin_events} событий\n🎲 Игры с Руиной: {player.games_with_ruin}"
             f"\n💣 Ruin rate: {stats_percent(player.ruin_rate)}\n🙋 Сам признал: {player.self_admitted_ruins}"
             f"\n🎭 Палево: {player.palivo_count}\n🧠 200 IQ: {player.iq_200_count}\n🔮 Пророк: {player.prophet_count}")
    reasons = [f"{RUIN_REASON_LABELS[reason.value]}: {count}" for reason, count in player.ruin_reasons.items() if count]
    if reasons:
        text += "\n\nПричины Руины:\n" + "\n".join(reasons)
    return text


def stats_player_line(player) -> str:
    return f"{player.display_name} — {player.total_games} игр / {player.total_wins} побед / {stats_percent(player.overall_winrate)}"


def stats_players(page, *, moments: bool = False) -> str:
    heading = STATS_SECTIONS["moments" if moments else "players"]
    lines = []
    for player in page.items:
        if moments:
            lines.append(f"{player.display_name} — 💥 {player.ruin_events} · 🎭 {player.palivo_count} · 🧠 {player.iq_200_count} · 🔮 {player.prophet_count}")
        else:
            lines.append(stats_player_line(player))
    return heading + "\n\n" + ("\n".join(lines) or STATS_NO_PLAYERS)


def stats_role_players(page, role_code: str) -> str:
    lines = []
    for player in page.items:
        score = next(s for role, s in player.roles.items() if role.value == role_code)
        lines.append(f"{player.display_name} — {score.games} игр / {score.wins} побед / {stats_percent(score.winrate)}")
    return STATS_ROLE_LABELS[role_code] + "\n\n" + ("\n".join(lines) or STATS_NO_PLAYERS)


def stats_ruin_players(page) -> str:
    lines = [f"{p.display_name} — {stats_percent(p.ruin_rate)} · {p.games_with_ruin}/{p.total_games} игр · {p.ruin_events} событий"
             for p in page.items]
    return STATS_SECTIONS["ruin"] + "\n\n" + ("\n".join(lines) or STATS_NO_PLAYERS)


def stats_team_lines(score) -> str:
    text = (f"🎲 Партий: {score.games_count}\n🔵 Liberals: {score.liberal_wins} — {stats_percent(score.liberal_winrate)}"
            f"\n🔴 Fascists: {score.fascist_wins} — {stats_percent(score.fascist_winrate)}")
    return text if score.games_count else text + "\n" + STATS_NO_GAMES


def stats_session_card(stats, date: str) -> str:
    lines = "\n".join(stats_player_line(player) for player in stats.players.items)
    return (f"🍻 Итоги вечера · {date}\n\n{stats_team_lines(stats.score)}\n\n"
            f"{stats_event_lines(stats.event_counts)}\n\n{lines or STATS_NO_PLAYERS}")


def stats_club_card(stats) -> str:
    text = (f"🌍 {stats.club_name}\n🍻 Вечеров: {stats.total_sessions}\n\n{stats_team_lines(stats.score)}"
            f"\n\n{stats_event_lines(stats.event_counts)}\n\nПричины победы:\n")
    text += "\n".join(f"{STATS_VICTORY_LABELS[k.value]}: {v}" for k, v in stats.victory_types.items())
    text += "\n\nПартии по числу игроков:\n" + "\n".join(
        f"👥 {size}: {s.games_count} игр · 🔵 {s.liberal_wins} ({stats_percent(s.liberal_winrate)}) · 🔴 {s.fascist_wins} ({stats_percent(s.fascist_winrate)})"
        for size, s in stats.games_by_player_count.items())
    return text


def stats_leaderboard(board) -> str:
    text = f"{STATS_BOARD_LABELS[board.kind.value]}\nМинимум игр: {board.required_games}\n\n"
    if not board.entries.items:
        return text + f"Пока никто не набрал минимум {board.required_games} игр для этого рейтинга."
    role_codes = {"liberal": "LIBERAL", "fascist": "FASCIST", "hitler": "HITLER"}
    lines = []
    for index, player in enumerate(board.entries.items, board.entries.page * 10 + 1):
        if board.kind.value == "ruin":
            result = f"{stats_percent(player.ruin_rate)} · {player.games_with_ruin}/{player.total_games} игр · {player.ruin_events} событий"
        elif board.kind.value == "iq":
            result = f"{player.iq_200_count} событий · {player.total_games} игр"
        elif board.kind.value in role_codes:
            score = next(s for r, s in player.roles.items() if r.value == role_codes[board.kind.value])
            result = f"{stats_percent(score.winrate)} · {score.wins}/{score.games} побед"
        else:
            result = f"{stats_percent(player.overall_winrate)} · {player.total_wins}/{player.total_games} побед"
        lines.append(f"{index}. {player.display_name} — {result}")
    return text + "\n".join(lines)
