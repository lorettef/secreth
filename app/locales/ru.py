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
