"""The admin's read-only support view (application/impersonation.py and
interfaces/http/api.py, multi-user plan P2-05). Keys are the exact Russian
source passed to domain.i18n.tr()."""

MESSAGES = {
    "Укажите причину просмотра: она попадёт в журнал действий.":
        "Give a reason for the view: it goes into the action log.",
    "Смотреть под самим собой не нужно.":
        "There is no need to view your own account as yourself.",
    "Такого пользователя нет.":
        "No such user.",
    "Под другим администратором смотреть нельзя.":
        "Another administrator's account cannot be viewed.",
    "Аккаунт заблокирован: под ним смотреть нельзя.":
        "The account is blocked and cannot be viewed.",
    "Эта сессия уже в режиме просмотра: сначала выйдите из него.":
        "This session is already in a support view: leave it first.",
    "Режим просмотра под пользователем: только чтение. Чтобы что-то изменить, выйдите из режима.":
        "Viewing as a user: read-only. To change anything, leave the view.",
    "Режим просмотра под пользователем: новые данные из YouTube не запрашиваются, "
    "показано только уже собранное.":
        "Viewing as a user: nothing new is requested from YouTube, only data already "
        "collected is shown.",
}
