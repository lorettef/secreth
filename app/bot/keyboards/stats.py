"""Stateless statistics screens. Every object is reauthorized by the service."""

from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks import ClubAction, StatsPage, StatsPlayer, StatsSession
from app.locales import ru
from app.services.stats import Page


def button(text: str, payload) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=payload.pack())


def menu(club_id: UUID, *, ratings: bool = False, roles: bool = False) -> InlineKeyboardMarkup:
    labels = ru.STATS_BOARD_LABELS if ratings or roles else ru.STATS_SECTIONS
    if roles:
        labels = {k: v for k, v in labels.items() if k in ("liberal", "fascist", "hitler")}
    prefix = "r_" if roles else "b_" if ratings else ""
    rows = [[button(label, StatsPage(club_id=club_id, section=prefix + code))]
            for code, label in labels.items()]
    rows.append([button(ru.BACK_BUTTON, ClubAction(club_id=club_id, action="menu"))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pagination(page: Page, payload) -> list[InlineKeyboardButton]:
    if page.pages == 1:
        return []
    buttons = []
    if page.page > 0:
        buttons.append(button(ru.PREVIOUS_BUTTON, payload(page.page - 1)))
    buttons.append(button(f"{page.page + 1}/{page.pages}", payload(page.page)))
    if page.page + 1 < page.pages:
        buttons.append(button(ru.NEXT_BUTTON, payload(page.page + 1)))
    return buttons


def page_view(club_id: UUID, section: str, page: Page, *, players: bool = False,
              sessions: bool = False, date_label=None, back: str = "menu") -> InlineKeyboardMarkup:
    rows = []
    for item in page.items:
        if players:
            rows.append([button(item.display_name, StatsPlayer(player_id=item.player_id))])
        elif sessions:
            rows.append([button(date_label(item), StatsSession(session_id=item.id))])
    nav = pagination(page, lambda n: StatsPage(club_id=club_id, section=section, page=n))
    if nav:
        rows.append(nav)
    rows.append([button(ru.BACK_BUTTON, StatsPage(club_id=club_id, section=back))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back(club_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[button(ru.BACK_BUTTON, StatsPage(club_id=club_id, section="menu"))]])


def session_view(stats) -> InlineKeyboardMarkup:
    nav = pagination(stats.players, lambda n: StatsSession(session_id=stats.session_id, page=n))
    rows = [nav] if nav else []
    rows.append([button(ru.BACK_BUTTON, StatsPage(club_id=stats.club_id, section="sessions"))])
    return InlineKeyboardMarkup(inline_keyboard=rows)
