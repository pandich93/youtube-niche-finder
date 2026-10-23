"""MCP scenarios (interfaces/mcp/prompts.py): titles, descriptions, argument
hints and the step-by-step text a Claude client puts into the chat.
Keys are the exact Russian source passed to domain.i18n.tr(); tool names and
their arguments stay as they are so the client's model calls the same tools."""

MESSAGES = {
    # shared pieces
    "Отвечай на языке пользователя. Числа бери из ответов инструментов, не придумывай.":
        "Answer in the user's language. Take numbers from the tools' answers, do not make them up.",
    "Перед любым сбором вызови `db_stats()` и посмотри search_quota."
    "search_calls_left_today и unit_quota.units_left_today. Если остатка не хватает — "
    "остановись и скажи, сколько осталось и когда сбросится (полночь по Тихоокеанскому "
    "времени).":
        "Before any collection call `db_stats()` and look at search_quota."
        "search_calls_left_today and unit_quota.units_left_today. If what is left is not enough, "
        "stop and say how much is left and when it resets (midnight Pacific time).",
    "Квота: 0 — все шаги читают только локальную базу.":
        "Quota: 0 — every step only reads the local database.",

    # argument hints
    "Тема будущего канала, как её искали бы на YouTube":
        "Topic of the future channel, as people would search for it on YouTube",
    "Сколько страниц поиска взять, 1–3 (каждая — 1 из 100 поисков в день)":
        "How many search pages to take, 1–3 (each is 1 of 100 searches a day)",
    "Канал: @handle, UC… id или ссылка": "Channel: @handle, UC… id or link",
    "Идея видео одной фразой": "Video idea in one sentence",
    "Slug ниши из list_niches": "Niche slug from list_niches",
    "ID выстрелившего видео (11 символов)": "ID of the video that took off (11 characters)",
    "yes / no — добавить разбор LLM сервера": "yes / no — add the server's LLM analysis",
    "Окно: 24h, 7d, 30d": "Window: 24h, 7d, 30d",

    # find_niche
    "Найти нишу": "Find a niche",
    "Оценить тему как нишу: соберёт видео (если их ещё нет), покажет "
    "насыщенность, тренд, вирусные видео мелких каналов и риск шаблонности. "
    "Квота: 1 поиск из 100 в день на страницу, если данных ещё нет.":
        "Assess a topic as a niche: collects videos (if there are none yet), shows saturation, "
        "trend, viral videos of small channels and the templated-content risk. "
        "Quota: 1 of 100 searches a day per page, if there is no data yet.",
    "Вызови `list_niches()`. Если ниша с таким запросом уже есть и собрана за последние "
    "7 дней — пропусти сбор и используй её slug.":
        "Call `list_niches()`. If a niche with this query already exists and was collected in "
        "the last 7 days, skip collection and use its slug.",
    "Для сбора нужно, чтобы search_calls_left_today было не меньше {need}.":
        "Collection needs search_calls_left_today to be at least {need}.",
    "Если данных нет: `collect_niche(query=\"{topic}\", pages={n})`. Больше {n} страниц "
    "не бери.":
        "If there is no data: `collect_niche(query=\"{topic}\", pages={n})`. Do not take more "
        "than {n} pages.",
    "`niche_overview(niche=<slug>)` — размер каналов, медианы, и отдельно saturation_v2 "
    "(тренд: растёт / держится / остывает / забита / мало данных, с причинами и "
    "confidence).":
        "`niche_overview(niche=<slug>)` — channel sizes, medians, and separately saturation_v2 "
        "(trend: growing / holding / cooling / crowded / too little data, with reasons and "
        "confidence).",
    "`viral_videos_small_channels(niche=<slug>, period=\"30d\")` — что выстреливает "
    "у каналов до 10k.":
        "`viral_videos_small_channels(niche=<slug>, period=\"30d\")` — what takes off "
        "for channels under 10k.",
    "`niche_template_risk(niche=<slug>)` — какая доля каналов похожа на шаблонный конвейер.":
        "`niche_template_risk(niche=<slug>)` — what share of channels looks like a templated "
        "content mill.",
    "Оцени тему «{topic}» как нишу для нового YouTube-канала.":
        "Assess the topic “{topic}” as a niche for a new YouTube channel.",
    "Квота: до {n} поисковых запросов из 100 в день (pages={n}) и примерно {n} units, "
    "только если ниша\nещё не собрана. Остальные шаги бесплатны.":
        "Quota: up to {n} search requests of 100 a day (pages={n}) and about {n} units, only if "
        "the niche\nis not collected yet. The other steps are free.",
    "Ответ: короткая таблица «показатель — значение — что это значит» (каналы, медиана "
    "просмотров,\nтренд и его причины, пробития мелких каналов, доля шаблонных каналов), "
    "затем 3–5 примеров\nвирусных видео мелких каналов и вывод в 2–3 предложениях: стоит "
    "ли входить и с каким форматом.\nЕсли тренд «мало данных» или confidence low — так и "
    "скажи, не делай вывод сильнее данных.":
        "Answer: a short table “metric — value — what it means” (channels, median views,\n"
        "trend and its reasons, breakouts of small channels, share of templated channels), "
        "then 3–5 examples\nof viral videos of small channels and a conclusion in 2–3 sentences: "
        "whether to enter and with what format.\nIf the trend is “too little data” or confidence "
        "is low, say so; do not make the conclusion stronger than the data.",

    # analyze_competitor
    "Разобрать конкурента": "Analyze a competitor",
    "Полный разбор канала: рост, лучшие видео, фразы в заголовках, лучшее "
    "время публикации, похожие каналы. Квота: ~2–3 units, поиск не тратит.":
        "Full channel breakdown: growth, best videos, title phrases, best time to publish, "
        "similar channels. Quota: ~2–3 units, no searches.",
    "Для этого сценария хватит 10 units.": "10 units are enough for this scenario.",
    "`collect_channel(channel=\"{channel}\", max_videos=100)` — возьми channel_id из ответа.":
        "`collect_channel(channel=\"{channel}\", max_videos=100)` — take channel_id from the answer.",
    "`channel_analytics(channel_id=<id>)` — рост, каденс, медиана против среднего, лучшие "
    "outlier'ы.":
        "`channel_analytics(channel_id=<id>)` — growth, cadence, median vs mean, best outliers.",
    "`title_patterns(channel_id=<id>)` — какие фразы в заголовках связаны с выстрелами.":
        "`title_patterns(channel_id=<id>)` — which title phrases go with hits.",
    "`best_time_to_publish(channel_id=<id>)` — лучшие дни и часы (UTC).":
        "`best_time_to_publish(channel_id=<id>)` — best days and hours (UTC).",
    "`similar_channels(channel_id=<id>, limit=5)` — с кем он делит аудиторию в нашей базе.":
        "`similar_channels(channel_id=<id>, limit=5)` — who it shares an audience with in our "
        "database.",
    "Разбери канал {channel} как конкурента.": "Analyze the channel {channel} as a competitor.",
    "Квота: сбор канала берёт 1 unit за 50 видео (около 2–3 units на 100 видео) и не "
    "тратит\nпоисковые запросы. Остальные шаги бесплатны.":
        "Quota: collecting a channel takes 1 unit per 50 videos (about 2–3 units per 100 videos) "
        "and spends\nno search requests. The other steps are free.",
    "Ответ: профиль канала (подписчики, частота, медиана просмотров), 3–5 лучших видео с "
    "множителем,\nфразы-паттерны, лучшее время, похожие каналы, и 3 конкретных вывода: что "
    "у него стоит взять и\nгде он слаб. Поля роста пустые, пока нет снимков за несколько "
    "дней — скажи об этом, если так.":
        "Answer: channel profile (subscribers, frequency, median views), 3–5 best videos with "
        "their multiplier,\npattern phrases, best time, similar channels, and 3 concrete "
        "takeaways: what is worth borrowing and\nwhere it is weak. Growth fields stay empty "
        "until there are snapshots over several days — say so if that is the case.",

    # validate_idea
    "Проверить идею": "Validate an idea",
    "Есть ли уже такие видео в нише и как они сработали, оценка вариантов "
    "заголовка и кто скоро станет конкурентом. Квота: 0.":
        "Whether the niche already has such videos and how they did, a score for title "
        "options and who will soon compete. Quota: 0.",
    "`check_ideas(ideas=[\"{idea}\"], niche=\"{niche}\")` — вердикт free / recent / "
    "proven / flopped и похожие видео.":
        "`check_ideas(ideas=[\"{idea}\"], niche=\"{niche}\")` — verdict free / recent / "
        "proven / flopped and similar videos.",
    "Придумай 5 вариантов заголовка под эту идею и оцени их: "
    "`score_titles(candidates=[...], niche=\"{niche}\")`.":
        "Come up with 5 title options for this idea and score them: "
        "`score_titles(candidates=[...], niche=\"{niche}\")`.",
    "`high_future_competition(niche=\"{niche}\", period=\"30d\")` — молодые быстрые "
    "каналы, которые могут занять эту тему.":
        "`high_future_competition(niche=\"{niche}\", period=\"30d\")` — young fast channels "
        "that may take this topic.",
    "Проверь идею видео «{idea}» для ниши {niche}.":
        "Validate the video idea “{idea}” for the niche {niche}.",
    "Ответ: вердикт одной строкой (снимать / снимать с другим углом / не снимать) и "
    "почему, таблица\nзаголовков с оценкой (лучший первым), 2–3 похожих видео, если "
    "есть, и кто из молодых каналов\nрядом.":
        "Answer: a one-line verdict (make it / make it from another angle / don't make it) and "
        "why, a table\nof titles with scores (best first), 2–3 similar videos if any, and which "
        "young channels\nare close.",

    # outlier_to_video
    "Outlier → своё видео": "Outlier → your video",
    "Бриф для своего ролика из одного выстрелившего видео: крючок, "
    "паттерны ниши, занята ли тема, заголовки, референсы превью. Квота: 0.":
        "A brief for your own video from one video that took off: hook, niche patterns, "
        "whether the topic is taken, titles, thumbnail references. Quota: 0.",
    "`format_repeatability(video_id=\"{video_id}\")` — повторялся ли формат у других "
    "каналов. Если 'one_off' — честно скажи, что это может быть разовая удача; 'unknown' "
    "значит «мало собрано», а не «не работает».":
        "`format_repeatability(video_id=\"{video_id}\")` — whether the format repeated on "
        "other channels. If 'one_off', say honestly it may be a one-time fluke; 'unknown' "
        "means “too little collected”, not “doesn't work”.",
    "Посмотри skipped: всё, что не удалось собрать, назови прямо (например, нет "
    "транскрипта — предложи вставить его на экране «Транскрипты»).":
        "Look at skipped: name plainly everything that could not be collected (for example, "
        "no transcript — suggest pasting it on the Transcripts screen).",
    "Если бриф полезен, спроси, сохранить ли его в черновики, и только после согласия "
    "вызови `build_brief(video_id=\"{video_id}\", use_llm={llm}, save=true)`.":
        "If the brief is useful, ask whether to save it to drafts, and only after a yes "
        "call `build_brief(video_id=\"{video_id}\", use_llm={llm}, save=true)`.",
    "Сделай бриф для моего видео по выстрелившему видео {video_id}.":
        "Make a brief for my video based on the video that took off, {video_id}.",
    "Квота: 0. С use_llm=true тратится бюджет LLM сервера (если он настроен).":
        "Quota: 0. With use_llm=true the server's LLM budget is spent (if it is set up).",
    "Ответ: почему видео выстрелило, повторяем ли формат, крючок, занята ли тема (что уже "
    "снято),\n3–5 заголовков, и мой угол — чем моё видео должно отличаться, а не как "
    "скопировать.":
        "Answer: why the video took off, whether the format repeats, the hook, whether the "
        "topic is taken (what is already made),\n3–5 titles, and my angle — how my video should "
        "differ, not how to copy it.",

    # weekly_review
    "Обзор недели": "Weekly review",
    "Что случилось за период: новые outlier'ы, ускорения, перепаковки, "
    "пропавшие каналы, новые сильные каналы. Квота: 0.":
        "What happened over the period: new outliers, accelerations, repackagings, "
        "channels that disappeared, strong new channels. Quota: 0.",
    "`daily_digest(period=\"{period}\")` — сводка: outlier'ы, ускорения, растущие каналы.":
        "`daily_digest(period=\"{period}\")` — summary: outliers, accelerations, growing channels.",
    "`list_events(unseen_only=true)` — непросмотренные алерты.":
        "`list_events(unseen_only=true)` — unseen alerts.",
    "`packaging_changes(period=\"{period}\")` — кто сменил заголовок или превью и что "
    "стало с просмотрами.":
        "`packaging_changes(period=\"{period}\")` — who changed a title or thumbnail and what "
        "happened to views.",
    "`recently_added_outlier_channels(period=\"{period}\")` — новые каналы, которые сразу "
    "обгоняют свой уровень.":
        "`recently_added_outlier_channels(period=\"{period}\")` — new channels that outperform "
        "their level right away.",
    "Сделай обзор за период {period} по моим отслеживаемым каналам и базе.":
        "Review the period {period} for my tracked channels and the database.",
    "Ответ: 5–7 пунктов «что произошло → что с этим делать», сначала самое важное. Если "
    "данных\nза период нет — скажи, что воркер, возможно, не запущен.":
        "Answer: 5–7 points “what happened → what to do about it”, most important first. If "
        "there is no data\nfor the period, say that the background collector may not be running.",

    # find_content_gaps
    "Пробелы в контенте": "Content gaps",
    "Вопросы зрителей из комментариев, на которые в нише ещё нет видео, "
    "и бриф под выбранный. Квота: 0 из кэша; чтение комментариев — "
    "1 unit на видео и только с вашего согласия.":
        "Viewer questions from comments that no video in the niche answers yet, and a brief "
        "for the one you pick. Quota: 0 from the cache; reading comments costs "
        "1 unit per video and only with your consent.",
    "`content_gaps(niche=\"{niche}\", fetch=false)` — только кэш.":
        "`content_gaps(niche=\"{niche}\", fetch=false)` — cache only.",
    "Если в skippedVideos есть видео с reason=not-fetched — скажи, сколько их и сколько "
    "units это стоит, и спроси, читать ли комментарии. Только если пользователь "
    "согласился: ":
        "If skippedVideos has videos with reason=not-fetched, say how many and how many units "
        "that costs, and ask whether to read the comments. Only if the user agreed: ",
    "Затем `content_gaps(niche=\"{niche}\", fetch=true)`.":
        "Then `content_gaps(niche=\"{niche}\", fetch=true)`.",
    "Покажи пробелы и спроси, под какой сделать бриф. Для выбранного: "
    "`build_brief(video_id=<первое из sourceVideos>, gap_topic=<topic>, save=false)`.":
        "Show the gaps and ask which one to brief. For the chosen one: "
        "`build_brief(video_id=<first of sourceVideos>, gap_topic=<topic>, save=false)`.",
    "Найди пробелы в контенте ниши {niche}: о чём зрители спрашивают, а видео нет.":
        "Find content gaps in the niche {niche}: what viewers ask about that has no video.",
    "Квота: 0, пока читается кэш. Чтение комментариев — 1 unit на каждое непрочитанное "
    "видео (обычно\nдо 10), и только после согласия пользователя.":
        "Quota: 0 while the cache is read. Reading comments costs 1 unit per unread video "
        "(usually\nup to 10), and only after the user agrees.",
    "Ответ: таблица пробелов (вопрос, статус, сколько раз спросили, ближайшее "
    "существующее видео),\nпометка режима (LLM или правила — без LLM шума больше) и "
    "размер базы из coverageBase: пробел\nреален настолько, насколько полна база.":
        "Answer: a table of gaps (question, status, how many times asked, nearest existing "
        "video),\na note on the mode (LLM or rules — without an LLM there is more noise) and "
        "the database size from coverageBase: a gap\nis only as real as the database is complete.",

    # niche_health
    "Здоровье ниши": "Niche health",
    "Проверка ниши перед входом: тренд, шаблонность каналов, спонсоры, "
    "крючки outlier'ов. Квота: 0.":
        "Check a niche before entering: trend, templated channels, sponsors, "
        "outlier hooks. Quota: 0.",
    "`niche_overview(niche=\"{niche}\")` — возьми saturation_v2: статус, причины, "
    "confidence.":
        "`niche_overview(niche=\"{niche}\")` — take saturation_v2: status, reasons, confidence.",
    "`niche_template_risk(niche=\"{niche}\")` — доля шаблонных каналов (риск для всей "
    "ниши).":
        "`niche_template_risk(niche=\"{niche}\")` — share of templated channels (a risk for the "
        "whole niche).",
    "`sponsor_map(niche=\"{niche}\")` — платят ли бренды авторам ниши и кто.":
        "`sponsor_map(niche=\"{niche}\")` — whether brands pay creators in the niche, and who.",
    "`niche_hook_benchmark(niche=\"{niche}\")` — чем крючки outlier'ов отличаются (если "
    "хватает транскриптов).":
        "`niche_hook_benchmark(niche=\"{niche}\")` — how outlier hooks differ (if there are "
        "enough transcripts).",
    "Проверь здоровье ниши {niche} перед тем, как в неё входить.":
        "Check the health of the niche {niche} before entering it.",
    "Ответ: светофор по четырём пунктам (тренд, шаблонность, деньги, крючки) с числами и "
    "итог в 2–3\nпредложениях. «Мало данных» не превращай в вывод — подскажи, что "
    "собрать.":
        "Answer: a traffic light on four points (trend, templating, money, hooks) with numbers "
        "and a summary in 2–3\nsentences. Do not turn “too little data” into a conclusion — "
        "suggest what to collect.",
}
