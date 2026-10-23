"""Plans (domain/plans.py, application/plans.py): limit and feature labels,
units, the plan-limit / feature-off messages and plan validation. Keys are the
exact Russian source passed to domain.i18n.tr(), placeholders included."""

MESSAGES = {
    # ------------------------------------------------------ limit labels
    "Квота YouTube в день": "YouTube quota per day",
    "Поисковые запросы YouTube в день": "YouTube searches per day",
    "Расход на LLM в день": "LLM spend per day",
    "Свои каналы": "Own channels",
    "Каналы в отслеживании": "Tracked channels",
    "Темы для алертов": "Alert topics",
    "Сохранённое": "Saved items",
    "Черновики и брифы": "Drafts and briefs",
    "API-токены": "API tokens",

    # ------------------------------------------------------------- units
    "запросов": "searches",
    "шт.": "items",

    # --------------------------------------------------- feature labels
    "Мои каналы": "My channels",
    "Доход своего канала": "Your channel's revenue",
    "Функции с LLM": "LLM features",
    "MCP и браузерное расширение": "MCP and browser extension",
    "Выгрузка ниш": "Niche export",
    "Поиск тем по всему YouTube": "Topic search across all of YouTube",
    "Алерты и дайджесты": "Alerts and digests",

    # -------------------------------------------------------- plan names
    "Без ограничений": "Unlimited",

    # ---------------------------------------------- limit / feature off
    "ваш тариф позволяет не больше {limit} ({what}) -- сменить тариф можно на странице «Тариф»":
        "your plan allows at most {limit} ({what}) -- you can change your plan on the Plan page",
    "«{feature}» не входит в ваш тариф -- сменить тариф можно на странице «Тариф»":
        "“{feature}” is not included in your plan -- you can change your plan on the Plan page",

    # -------------------------------------------------------- validation
    "limits: ожидается объект": "limits: an object is expected",
    "features: ожидается объект": "features: an object is expected",
    "limits.{key}: неизвестный лимит": "limits.{key}: unknown limit",
    "limits.{key}: нужно число или null": "limits.{key}: a number or null is required",
    "limits.{key}: не может быть меньше 0": "limits.{key}: cannot be less than 0",
    "limits.{key}: нужно целое число": "limits.{key}: a whole number is required",
    "features.{key}: неизвестная функция": "features.{key}: unknown feature",
    "features.{key}: нужно true или false": "features.{key}: true or false is required",
    # multi-user plan P0-02: shared corpus and the server's own work
    "Это делает фоновый сборщик по расписанию; вручную это "
    "запускает только администратор сервера.":
        "The background collector does this on schedule; only the server's administrator "
        "can run it by hand.",
}
