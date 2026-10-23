"""Sign-up, password reset and profile changes (application/signup.py,
multi-user plan P1-06): the emails and the errors. Keys are the exact Russian
source passed to domain.i18n.tr()."""

MESSAGES = {
    "Нужен правильный адрес электронной почты.": "Enter a valid email address.",
    # interfaces/http/api.py
    "Не удалось отправить письмо. Попробуйте позже.":
        "The email could not be sent. Try again later.",
    "Если адрес верный, письмо со ссылкой уже в пути. Проверьте почту, в том числе "
    "папку «Спам».":
        "If the address is right, an email with a link is on its way. Check your inbox, "
        "including the spam folder.",
    "Слишком много запросов писем с вашего адреса. Попробуйте через час.":
        "Too many emails asked for from your address. Try again in an hour.",
    "Ссылка недействительна или устарела. Запросите новую.":
        "This link is invalid or has expired. Ask for a new one.",
    "Регистрация на этом сервере выключена.": "Sign-up is turned off on this server.",
    "У вас уже есть аккаунт в niche-finder": "You already have a niche-finder account",
    "Здравствуйте!\n\nКто-то (возможно, вы) хотел зарегистрироваться в "
    "niche-finder с этим адресом, но аккаунт с ним уже есть. Войдите со своим "
    "паролем или задайте новый по ссылке:\n{link}\n\nСсылка действует {hours} ч "
    "и сработает один раз. Если это были не вы, ничего делать не нужно.":
        "Hello!\n\nSomeone (perhaps you) tried to sign up for niche-finder with this address, "
        "but it already has an account. Sign in with your password, or set a new one with "
        "this link:\n{link}\n\nThe link works once, for {hours} h. If it was not you, there is "
        "nothing to do.",
    "Подтвердите регистрацию в niche-finder": "Confirm your niche-finder sign-up",
    "Здравствуйте!\n\nЧтобы закончить регистрацию в niche-finder, откройте "
    "ссылку и задайте пароль:\n{link}\n\nСсылка действует {hours} ч и сработает "
    "один раз. Если вы не регистрировались, просто удалите это письмо.":
        "Hello!\n\nTo finish signing up for niche-finder, open this link and choose a "
        "password:\n{link}\n\nThe link works once, for {hours} h. If you did not sign up, "
        "just delete this email.",
    "Аккаунт с этим адресом уже есть: войдите или сбросьте пароль.":
        "An account with this address already exists: sign in or reset the password.",
    "Сброс пароля на этом сервере не настроен: обратитесь к администратору.":
        "Password reset is not set up on this server: ask the administrator.",
    "Сброс пароля в niche-finder": "Reset your niche-finder password",
    "Здравствуйте!\n\nЧтобы задать новый пароль в niche-finder, откройте ссылку:\n{link}\n\n"
    "Ссылка действует {hours} ч и сработает один раз. После смены пароля все входы и "
    "API-токены этого аккаунта завершатся. Если вы не просили сброс, просто удалите это "
    "письмо.":
        "Hello!\n\nTo set a new niche-finder password, open this link:\n{link}\n\n"
        "The link works once, for {hours} h. Changing the password signs the account out "
        "everywhere and revokes its API tokens. If you did not ask for a reset, just delete "
        "this email.",
    "Сменить email можно, только когда на сервере настроена почта (SMTP).":
        "The email can only be changed when the server has mail (SMTP) set up.",
    "Это уже ваш адрес.": "This is already your address.",
    "На этот адрес уже отправлено много писем. Попробуйте через час.":
        "Many emails have already been sent to this address. Try again in an hour.",
    "Этот адрес уже занят другим аккаунтом.": "This address belongs to another account.",
    "Подтвердите новый email в niche-finder": "Confirm your new niche-finder email",
    "Здравствуйте!\n\nЧтобы этот адрес стал адресом вашего аккаунта в niche-finder, "
    "откройте ссылку:\n{link}\n\nСсылка действует {hours} ч и сработает один раз. Если вы "
    "ничего не меняли, просто удалите это письмо.":
        "Hello!\n\nTo make this the email of your niche-finder account, open this link:\n"
        "{link}\n\nThe link works once, for {hours} h. If you changed nothing, just delete "
        "this email.",
}
