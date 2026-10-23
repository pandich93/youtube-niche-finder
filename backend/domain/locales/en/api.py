"""HTTP API texts a person sees: error details of interfaces/http/api.py.
Keys are the exact Russian source passed to domain.i18n.tr()."""

MESSAGES = {
    "нужно поле channel_id": "the channel_id field is required",
    "нужно поле channel": "the channel field is required",
    "нужно поле query": "the query field is required",
    "ids должен быть списком": "ids must be a list",
    "planId: нужен номер тарифа": "planId: a plan number is required",
    "канал не найден": "channel not found",
    "YOUTUBE_API_KEY не задан. Впишите его в .env рядом с docker-compose.yml и "
    "перезапустите: docker compose up -d web":
        "YOUTUBE_API_KEY is not set. Add it to .env next to docker-compose.yml and "
        "restart: docker compose up -d web",
    # middlewares: rate limit, sign-in, Host and cross-site guards
    "Больше {limit} запросов в минуту к /api. Повторите через {retry_after} с или "
    "поднимите RATE_LIMIT_PER_MINUTE в .env.":
        "More than {limit} requests per minute to /api. Retry in {retry_after} s or "
        "raise RATE_LIMIT_PER_MINUTE in .env.",
    "Sign in first: NF_MULTI_USER is on -- войдите в дашборде.":
        "Sign in first: NF_MULTI_USER is on.",
    "Недопустимый заголовок Host: {host}. API отвечает только на 127.0.0.1, localhost "
    "и [::1]; другое имя добавьте в NF_ALLOWED_HOSTS в .env.":
        "Host header not allowed: {host}. The API answers only on 127.0.0.1, localhost "
        "and [::1]; add any other name to NF_ALLOWED_HOSTS in .env.",
    "Изменяющий запрос без Content-Type: application/json и без заголовка X-NF-Client "
    "отклонён -- так выглядит запрос с чужого сайта. Добавьте заголовок "
    "X-NF-Client: <имя клиента>.":
        "A modifying request without Content-Type: application/json and without an "
        "X-NF-Client header was refused -- that is what a request from another site "
        "looks like. Add the header X-NF-Client: <client name>.",
    # notifications test
    "\U0001F9EA niche-finder: тестовое сообщение. Если вы это видите, уведомления "
    "настроены верно.":
        "\U0001F9EA niche-finder: test message. If you can see this, notifications are "
        "set up correctly.",
    # no frontend folder
    "Папка фронта не найдена: {path}": "Frontend folder not found: {path}",
    "Задайте FRONTEND_DIR или смонтируйте ./frontend в контейнер":
        "Set FRONTEND_DIR or mount ./frontend into the container",
    # multi-user plan P0-02: a shared transcript belongs to its first author
    "Транскрипт этого видео сохранил другой пользователь; пересчитать его может "
    "он или администратор.":
        "Another user saved this video's transcript; only they or an administrator "
        "can re-index it.",
    # multi-user plan P0-03: sign-in throttled after failed attempts
    "Слишком много неудачных попыток входа. Повторите через {retry_after} с.":
        "Too many failed sign-in attempts. Try again in {retry_after} s.",
}
