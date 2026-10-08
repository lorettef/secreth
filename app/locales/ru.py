NO_MEMBERSHIP = "Вы пока не состоите в клубе. Попросите администратора прислать приглашение."
CHOOSE_CLUB = "Выберите клуб:"
JOIN_BUTTON = "Вступить"
NEW_PLAYER_BUTTON = "Я новый игрок"
EXISTING_PLAYER_BUTTON = "Я уже есть в статистике"
ADMIN_BUTTON = "⚙️ Управление клубом"
PROFILE_BUTTON = "👤 Мой профиль"
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
}


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
