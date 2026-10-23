"""Command line (interfaces/cli/cli.py): help, the doctor report, the test
notification. Keys are the exact Russian source passed to domain.i18n.tr();
the language is NF_DEFAULT_LOCALE (from the environment or .env)."""

MESSAGES = {
    # the module docstring, shown by --help
    """Командная строка к niche-finder -- чтобы проверить и погонять всё
без подключения к Claude Desktop.

    python cli.py doctor                      проверка ключа, сети, базы
    python cli.py collect-channel @handle     собрать канал (дёшево)
    python cli.py collect "ai automation" --period 24h   [--no-embed]
    python cli.py refresh                     обновить счётчики (история)
    python cli.py embed-videos                досчитать эмбеддинги (0 quota)
    python cli.py fix-tracked [--apply]       почистить watchlist от handle/url вместо id
    python cli.py viral --period 24h
    python cli.py categories --period 7d --rank-by channels
    python cli.py keywords --period 24h
    python cli.py channels --period 24h       outlier-каналы
    python cli.py stats
    python cli.py notify-test                 тестовое сообщение в Telegram/webhook
    python cli.py digest-send                 отправить дайджест за сутки прямо сейчас
    python cli.py export-niche my-niche --format csv --out out.csv
    python cli.py seed                        синтетические данные для примера

В Docker:  docker compose run --rm mcp python cli.py doctor
""":
    """Command line for niche-finder -- to check and try everything
without connecting Claude Desktop.

    python cli.py doctor                      check the key, network, database
    python cli.py collect-channel @handle     collect a channel (cheap)
    python cli.py collect "ai automation" --period 24h   [--no-embed]
    python cli.py refresh                     refresh counters (history)
    python cli.py embed-videos                compute missing embeddings (0 quota)
    python cli.py fix-tracked [--apply]       clean handles/URLs out of the watchlist
    python cli.py viral --period 24h
    python cli.py categories --period 7d --rank-by channels
    python cli.py keywords --period 24h
    python cli.py channels --period 24h       outlier channels
    python cli.py stats
    python cli.py notify-test                 test message to Telegram/webhook
    python cli.py digest-send                 send the daily digest right now
    python cli.py export-niche my-niche --format csv --out out.csv
    python cli.py seed                        synthetic data to try things on

In Docker:  docker compose run --rm mcp python cli.py doctor
""",

    # subcommand help
    "проверить ключ, сеть, базу": "check the key, network, database",
    "дополнительно пингануть настроенный LLM-провайдер (тратит бюджет)":
        "also ping the configured LLM provider (spends budget)",
    "что в базе": "what is in the database",
    "тестовое сообщение в Telegram/webhook": "test message to Telegram/webhook",
    "отправить дайджест за сутки прямо сейчас": "send the daily digest right now",
    "залить синтетические данные": "load synthetic data",
    "создать пользователя (вход при NF_MULTI_USER=1)": "create a user (sign-in with NF_MULTI_USER=1)",
    "читать пароль из stdin": "read the password from stdin",
    "задать или сменить пароль (local -- ваши данные)":
        "set or change a password (local -- your own data)",
    "список пользователей": "list users",
    # multi-user plan P0-04: blocking and deleting accounts
    "заблокировать: сессии и токены перестают работать":
        "block: sessions and tokens stop working at once",
    "разблокировать": "unblock",
    "удалить учётную запись и все её данные": "delete an account and all of its data",
    "подтвердить: удаление необратимо": "confirm: deleting cannot be undone",
    "Удаление необратимо: учётная запись и все её данные. Повторите с --yes.":
        "Deleting cannot be undone: the account and all of its data. Repeat with --yes.",
    "личный токен для расширения и MCP по HTTP": "personal token for the extension and MCP over HTTP",
    "досчитать эмбеддинги для уже собранных видео (0 quota)":
        "compute embeddings for videos already collected (0 quota)",
    "экспорт видео ниши в TSV/CSV": "export a niche's videos to TSV/CSV",
    "путь файла (по умолчанию <slug>_videos_ДАТА.EXT)": "file path (default <slug>_videos_DATE.EXT)",
    "почистить watchlist от handle/url вместо channel_id":
        "clean handles/URLs stored instead of channel_id out of the watchlist",
    "применить изменения (по умолчанию — только просмотр)":
        "apply the changes (by default only show them)",
    "собрать канал (1 unit / 50 видео)": "collect a channel (1 unit / 50 videos)",
    "считать эмбеддинги сразу (по умолчанию нет)": "compute embeddings right away (default: no)",
    "собрать нишу поиском (1 из 100 поисков в сутки)":
        "collect a niche via search (1 of 100 searches a day)",
    "считать эмбеддинги сразу (по умолчанию да)": "compute embeddings right away (default: yes)",
    "обновить счётчики и дописать историю": "refresh counters and extend the history",
    "вирусные видео у маленьких каналов": "viral videos of small channels",
    "популярные категории": "popular categories",
    "трендовые ключевые слова": "trending keywords",
    "недавно добавленные outlier-каналы": "recently added outlier channels",

    # doctor
    "1. Ключ": "1. Key",
    "2. Сеть и сам ключ": "2. Network and the key itself",
    "3. База": "3. Database",
    "4. LLM (опционально)": "4. LLM (optional)",
    "5. Покрытие окна 24h": "5. 24h window coverage",
    "ОТСУТСТВУЕТ": "MISSING",
    "есть": "present",
    "Впишите YOUTUBE_API_KEY в .env рядом с docker-compose.yml":
        "Put YOUTUBE_API_KEY into .env next to docker-compose.yml",
    "подозрительная форма: длина {length}, начало {start}":
        "suspicious shape: length {length}, starts with {start}",
    "Ключ Google обычно 39 символов и начинается на AIza -- "
    "похоже, скопирован не полностью":
        "A Google key is usually 39 characters and starts with AIza -- "
        "looks like it was not copied in full",
    "есть, длина {length}, начинается на {start}…": "present, length {length}, starts with {start}…",
    "ОК -- API ответил, {count} категорий (потрачен 1 unit)":
        "OK -- the API answered, {count} categories (1 unit spent)",
    "ОШИБКА: {error}": "ERROR: {error}",
    "В этом проекте Google Cloud не включён YouTube Data "
    "API v3: APIs & Services -> Library -> YouTube Data "
    "API v3 -> Enable":
        "YouTube Data API v3 is not enabled in this Google Cloud project: "
        "APIs & Services -> Library -> YouTube Data API v3 -> Enable",
    "Ключ недействителен -- скопирован не целиком или "
    "из другого проекта":
        "The key is not valid -- copied only in part or from another project",
    "У ключа стоит ограничение по HTTP referrer / IP -- "
    "из контейнера так не работает. Credentials -> ключ "
    "-> Application restrictions -> None":
        "The key is restricted by HTTP referrer / IP -- that does not work from a container. "
        "Credentials -> the key -> Application restrictions -> None",
    "Квота на сегодня исчерпана, сбросится в полночь "
    "по тихоокеанскому времени":
        "Today's quota is used up; it resets at midnight Pacific time",
    "Сеть до googleapis.com не проходит или ключ отклонён "
    "-- см. текст ошибки выше":
        "googleapis.com cannot be reached or the key was rejected -- see the error above",
    "пропущено (нет ключа)": "skipped (no key)",
    "каналов {channels}, видео {videos}, ниш {niches}, снапшотов {snapshots}":
        "channels {channels}, videos {videos}, niches {niches}, snapshots {snapshots}",
    "База пустая -- начните с "
    "`python cli.py collect-channel @какой-нибудь-канал`":
        "The database is empty -- start with "
        "`python cli.py collect-channel @some-channel`",
    "Истории нет -- запустите воркер "
    "(`docker compose up -d worker`), иначе vph24h, "
    "ускорение и рост каналов останутся null":
        "No history -- start the background collector "
        "(`docker compose up -d worker`), otherwise vph24h, "
        "acceleration and channel growth stay null",
    "База не открывается: {error}": "The database does not open: {error}",
    "выключен (LLM_PROVIDER=none) -- это по умолчанию, ничего чинить не нужно":
        "off (LLM_PROVIDER=none) -- the default, nothing to fix",
    "Ollama: {msg} -- запустите `ollama serve` и "
    "`ollama pull <модель>`, либо проверьте OLLAMA_URL":
        "Ollama: {msg} -- run `ollama serve` and "
        "`ollama pull <model>`, or check OLLAMA_URL",
    "сервер и модель на месте, пинг пропущен (передайте --llm)":
        "server and model are there, ping skipped (pass --llm)",
    "LLM_PROVIDER=openrouter, но OPENROUTER_API_KEY не задан -- "
    "используется NullProvider":
        "LLM_PROVIDER=openrouter, but OPENROUTER_API_KEY is not set -- "
        "NullProvider is used",
    "ключ есть, пинг пропущен (передайте --llm, чтобы проверить и "
    "потратить немного бюджета)":
        "key present, ping skipped (pass --llm to check it and "
        "spend a little budget)",
    "ОК -- провайдер ответил": "OK -- the provider answered",
    "ОШИБКА или бюджет исчерпан -- см. лог выше": "ERROR or budget used up -- see the log above",
    "Пинг LLM-провайдера не удался -- проверьте ключ/модель/URL "
    "и LLM_DAILY_BUDGET_USD":
        "The LLM provider ping failed -- check the key/model/URL "
        "and LLM_DAILY_BUDGET_USD",
    "видео за 24ч: {published} из {total}; метрики скорости {velocity}":
        "videos in 24h: {published} of {total}; velocity metrics {velocity}",
    "доступны": "available",
    "НЕТ (нужна история)": "NO (history needed)",
    "ЧТО ПОЧИНИТЬ:": "WHAT TO FIX:",
    "Всё в порядке.": "All good.",

    # notify-test
    "\U0001F9EA niche-finder: тестовое сообщение. Если вы это видите, "
    "алерты настроены верно.":
        "\U0001F9EA niche-finder: test message. If you can see this, "
        "alerts are set up correctly.",
}
