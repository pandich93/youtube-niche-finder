"""Blocking and deleting accounts (application/users_admin.py, multi-user plan
P0-04). Keys are the exact Russian source passed to domain.i18n.tr()."""

MESSAGES = {
    "Это последний активный администратор: сначала назначьте другого.":
        "This is the last active administrator: make someone else an admin first.",
    "Пользователя local удалить нельзя: у него данные, собранные до "
    "включения учётных записей. Его можно заблокировать.":
        "The local user cannot be deleted: it holds the data collected before accounts "
        "were turned on. It can be blocked.",
    "Со своей учётной записью так нельзя: удалить её можно на "
    "странице «Тариф».":
        "You cannot do this to your own account; you can delete it on the Plan page.",
    "Для удаления введите email этого пользователя.":
        "To delete, type this user's email.",
    "Неверный пароль.": "Wrong password.",
}
