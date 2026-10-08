"""History lists and screen-bound corrections."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from app.bot.callbacks import ClubAction, EventStart, GameCard, HistoryMutation, HistoryPage, HistoryPick, HistoryStep
from app.bot.keyboards.stats import button, pagination
from app.locales import ru


def menu(club_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        *[[button(label, HistoryPage(scope_id=club_id, section=code))] for code, label in ru.HISTORY_SECTIONS.items()],
        [button(ru.BACK_BUTTON, ClubAction(club_id=club_id, action="menu"))],
    ])


def listing(scope_id, section, page, choices, *, club_id):
    rows = [[button(label, payload)] for label, payload in choices]
    nav = pagination(page, lambda n: HistoryPage(scope_id=scope_id, section=section, page=n))
    if nav:
        rows.append(nav)
    rows.append([button(ru.BACK_BUTTON, HistoryPage(scope_id=club_id, section="menu"))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def card(view):
    rows = []
    if view.can_edit:
        rows.append([button(ru.HISTORY_EDIT, HistoryMutation(entity_id=view.game.id, action="ge", revision=view.revision))])
    if view.is_admin:
        action = "gr" if view.game.status.value == "VOID" else "gv"
        rows.append([button(ru.HISTORY_RESTORE if action == "gr" else ru.HISTORY_VOID,
                            HistoryMutation(entity_id=view.game.id, action=action, revision=view.revision))])
    if view.can_add_event:
        rows.append([button(ru.EVENT_ADD, EventStart(game_id=view.game.id))])
    rows.append([button(ru.EVENT_HOME, HistoryPage(scope_id=view.game.session_id, section="session"))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def events(view):
    choices = [(ru.EVENT_TYPE_LABELS[e.event_type.value] + " · " + e.player_name[:80],
                HistoryPage(scope_id=e.id, section="event")) for e in view.events.items]
    return listing(view.game.id, "game", view.events, choices, club_id=view.club_id)


def event(view, rev, can_edit, admin):
    rows = []
    if can_edit:
        rows.append([button(ru.HISTORY_EVENT_EDIT, HistoryMutation(entity_id=view.id, action="ee", revision=rev))])
    if admin:
        rows.append([button(ru.HISTORY_EVENT_DELETE, HistoryMutation(entity_id=view.id, action="ed", revision=rev))])
    rows.append([button(ru.BACK_BUTTON, GameCard(game_id=view.game_id))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def player(p, club_id):
    rows = [[button(ru.HISTORY_RENAME, HistoryMutation(entity_id=p.id, action="pn", revision=p.revision))]]
    code = "pd" if p.is_active else "pr"
    rows.append([button(ru.HISTORY_DEACTIVATE if p.is_active else ru.HISTORY_REACTIVATE,
                        HistoryMutation(entity_id=p.id, action=code, revision=p.revision))])
    rows.append([button(ru.BACK_BUTTON, HistoryPage(scope_id=club_id, section="manage"))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def step(label, code, flow):
    return button(label, HistoryStep(flow=flow, action=code))


def edit_menu(flow):
    return InlineKeyboardMarkup(inline_keyboard=[
        *[[step(label, code, flow)] for code, label in ru.HISTORY_EDIT_FIELDS.items()],
        [step(ru.HISTORY_SAVE, "save", flow)], [step(ru.CANCEL_BUTTON, "cancel", flow)],
    ])


def picks(people, selected, flow, page=0, *, multiple=True):
    rows = [[button(("✅ " if p.id in selected else "▫️ ") + p.name, HistoryPick(flow=flow, player_id=p.id))]
            for p in people[page * 10: (page + 1) * 10]]
    nav = []
    if page:
        nav.append(step(ru.PREVIOUS_BUTTON, "prev", flow))
    if (page + 1) * 10 < len(people):
        nav.append(step(ru.NEXT_BUTTON, "next", flow))
    if nav:
        rows.append(nav)
    rows.append([step(ru.HISTORY_DONE if multiple else ru.BACK_BUTTON, "back", flow), step(ru.CANCEL_BUTTON, "cancel", flow)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def choices(labels, flow):
    return InlineKeyboardMarkup(inline_keyboard=[
        *[[step(label, code, flow)] for code, label in labels.items()],
        [step(ru.BACK_BUTTON, "back", flow), step(ru.CANCEL_BUTTON, "cancel", flow)],
    ])


def confirm(flow):
    return InlineKeyboardMarkup(inline_keyboard=[
        [step(ru.CONFIRM_BUTTON, "confirm", flow)], [step(ru.CANCEL_BUTTON, "cancel", flow)],
    ])
