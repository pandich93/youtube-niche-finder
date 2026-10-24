"""Two-factor sign-in (application/two_factor.py, interfaces/http/api.py,
the CLI's disable-2fa; multi-user plan P2-04). Keys are the exact Russian
source passed to domain.i18n.tr()."""

MESSAGES = {
    "Секрет двухфакторной защиты не читается на этом сервере (нет или сменился "
    "OWN_TOKENS_KEY). Войдите резервным кодом или попросите администратора выполнить "
    "cli.py disable-2fa.":
        "The two-factor secret cannot be read on this server (OWN_TOKENS_KEY is missing or "
        "has changed). Sign in with a backup code or ask the administrator to run "
        "cli.py disable-2fa.",
    "Двухфакторную защиту нельзя включить: на сервере не задан OWN_TOKENS_KEY "
    "(ключ шифрования секретов). Обратитесь к администратору.":
        "Two-factor sign-in cannot be turned on: the server has no OWN_TOKENS_KEY (the key "
        "that encrypts secrets). Ask the administrator.",
    "Двухфакторная защита уже включена.": "Two-factor sign-in is already on.",
    "Сначала начните настройку: получите секрет для приложения.":
        "Start the setup first: get the secret for the app.",
    "Неверный код. Проверьте время на телефоне и введите новый код.":
        "Wrong code. Check the time on your phone and enter a fresh code.",
    "Время на ввод кода вышло или вход уже завершён: войдите заново.":
        "The time to enter the code is over, or this sign-in is already finished: sign in again.",
    "Неверный код.": "Wrong code.",
    "Неверный пароль или код.": "Wrong password or code.",
    "Двухфакторная защита выключена.": "Two-factor sign-in is off.",
    "На этом сервере администраторам двухфакторная защита обязательна "
    "(NF_ADMIN_2FA=required).":
        "On this server two-factor sign-in is required for administrators "
        "(NF_ADMIN_2FA=required).",
    "Нет пользователя {email}.": "No user {email}.",
    "Администраторам этого сервера нужна двухфакторная защита: включите её на экране "
    "«Профиль».":
        "Administrators of this server need two-factor sign-in: turn it on in the Profile screen.",
    "Создать API-токен можно только из входа, прошедшего двухфакторную проверку: "
    "выйдите и войдите снова.":
        "An API token can be created only from a sign-in that passed the two-factor check: "
        "sign out and sign in again.",
    "снять двухфакторную защиту аккаунта (запись в журнал)":
        "turn an account's two-factor sign-in off (written to the action log)",
}
