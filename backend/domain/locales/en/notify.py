"""Telegram/webhook texts: instant alerts (application/alerts.py), the daily
digest (application/digest.py) and draft reminders (application/content_calendar.py).
Keys are the exact Russian source passed
to domain.i18n.tr(), Telegram HTML and {placeholders} included; the HTML stays
exactly as in the source."""

MESSAGES = {
    # ---------------------------------------------------- alerts, one per event
    "\U0001F680 <b>Outlier</b>: {title}\n×{score} · {views} просмотров":
        "\U0001F680 <b>Outlier</b>: {title}\n×{score} · {views} views",
    "⚡ <b>Ускорение</b>: {title}\n×{acceleration} · {vph} VPH за 24ч":
        "⚡ <b>Acceleration</b>: {title}\n×{acceleration} · {vph} VPH over 24h",
    "✏️ <b>Смена заголовка</b>\n«{old}» → «{new}»":
        "✏️ <b>Title changed</b>\n“{old}” → “{new}”",
    "\U0001F514 <b>Вернулись после паузы</b>: {title}\nмолчали {days} дней":
        "\U0001F514 <b>Back after a break</b>: {title}\nsilent for {days} days",
    "\U0001F6AB <b>Канал больше не доступен</b>: {title}\n"
    "было {subscribers} подписчиков · {views} просмотров · {videos} видео\n"
    "не отвечает API с {since}":
        "\U0001F6AB <b>Channel no longer available</b>: {title}\n"
        "had {subscribers} subscribers · {views} views · {videos} videos\n"
        "missing from the API since {since}",
    "\U0001F3C1 <b>Рубеж</b>: {title}\n{milestone} подписчиков (сейчас {subscribers})":
        "\U0001F3C1 <b>Milestone</b>: {title}\n{milestone} subscribers (now {subscribers})",
    "\U0001F3AF <b>Тема «{topic}»</b>: {title}\n{channel} · похожесть {similarity}":
        "\U0001F3AF <b>Topic “{topic}”</b>: {title}\n{channel} · similarity {similarity}",
    '\U0001F4C5 <b>Черновик</b>: {title}\n{when}\n<a href="{url}/#/calendar">календарь</a>':
        '\U0001F4C5 <b>Draft</b>: {title}\n{when}\n<a href="{url}/#/calendar">calendar</a>',
    "\U0001F6AB <b>Видео больше не доступно</b>: {title}\n"
    "было outlier ×{score} · {views} просмотров":
        "\U0001F6AB <b>Video no longer available</b>: {title}\n"
        "was an outlier ×{score} · {views} views",
    "дашборд": "dashboard",
    "\U0001F4EC И ещё {n} алертов:": "\U0001F4EC And {n} more alerts:",
    "открыть дашборд": "open the dashboard",

    # ---------------------------------------------- draft reminders (plan 33)
    "выпуск в ближайшие сутки": "goes live within 24 hours",
    "пора выпускать": "time to publish",

    # -------------------------------------------------------------- digest
    "\U0001F5DE <b>niche-finder — за сутки</b>": "\U0001F5DE <b>niche-finder — last 24 hours</b>",
    "\U0001F680 <b>Новые outlier-видео</b>": "\U0001F680 <b>New outlier videos</b>",
    "⚡ <b>Ускоряются</b>": "⚡ <b>Accelerating</b>",
    "\U0001F4C8 <b>Растущие каналы</b>": "\U0001F4C8 <b>Rising channels</b>",
    "✏️ <b>Перепаковки</b>": "✏️ <b>Repackaging</b>",
    "\U0001F4C5 <b>Календарь</b>": "\U0001F4C5 <b>Calendar</b>",
    "\U0001F3AF <b>Ваши темы</b>": "\U0001F3AF <b>Your topics</b>",
    "\U0001F3C1 <b>Рубежи</b>": "\U0001F3C1 <b>Milestones</b>",
    "\U0001F6AB <b>Пропали</b>": "\U0001F6AB <b>Gone</b>",
    "…и ещё {n}": "…and {n} more",
    "• канал {title}": "• channel {title}",
    "• видео {title}": "• video {title}",
    "• {title} — {n} подп.": "• {title} — {n} subs",
    "• {title} — ×{multiplier}, {subscribers} подп.": "• {title} — ×{multiplier}, {subscribers} subs",
    "• заголовок: «{old}» → «{new}»": "• title: “{old}” → “{new}”",
    "• обложка: {title}": "• thumbnail: {title}",
    # the notification test message (api.py) is in locales/en/api.py
}
