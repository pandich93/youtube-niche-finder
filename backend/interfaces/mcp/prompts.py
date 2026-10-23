"""Ready-made scenarios (MCP prompts, plan 11): a Claude client lists them under
"+", and picking one puts a step-by-step instruction into the chat -- which of
our tools to call, in what order, what it costs in quota and what the answer
should look like. The client's own model runs the steps; the server spends
nothing on the prompt itself.

Texts land in the chat as the user's message, so they are in the user's
language (i18n plan): the Russian source goes through tr(), the English text is
in domain/locales/en/mcp.py. Titles, descriptions and argument hints are fixed
when the server starts (NF_DEFAULT_LOCALE); the scenario text itself follows
the language the user saved in the dashboard (auth.locale_middleware).
Tool names are written as `name(` in both languages so tests/test_mcp_prompts.py
can check that every tool a scenario names still exists.
"""

from typing import Annotated

from pydantic import Field

from domain.i18n import tr

ANSWER = "Отвечай на языке пользователя. Числа бери из ответов инструментов, не придумывай."
QUOTA_GUARD = ("Перед любым сбором вызови `db_stats()` и посмотри search_quota."
               "search_calls_left_today и unit_quota.units_left_today. Если остатка не хватает — "
               "остановись и скажи, сколько осталось и когда сбросится (полночь по Тихоокеанскому "
               "времени).")


def _pages(value) -> int:
    try:
        return max(1, min(int(value), 3))
    except (TypeError, ValueError):
        return 1


def _steps(*lines) -> str:
    return "\n".join(f"{i}. {line}" for i, line in enumerate(lines, 1))


def _guard(then: str) -> str:
    """QUOTA_GUARD followed by a sentence of the scenario's own."""
    return tr(QUOTA_GUARD) + " " + then


def _arg(description: str):
    return Annotated[str, Field(description=tr(description))]


def register(mcp):
    TOPIC = _arg("Тема будущего канала, как её искали бы на YouTube")
    PAGES = _arg("Сколько страниц поиска взять, 1–3 (каждая — 1 из 100 поисков в день)")
    CHANNEL = _arg("Канал: @handle, UC… id или ссылка")
    IDEA = _arg("Идея видео одной фразой")
    NICHE = _arg("Slug ниши из list_niches")
    VIDEO = _arg("ID выстрелившего видео (11 символов)")
    USE_LLM = _arg("yes / no — добавить разбор LLM сервера")
    PERIOD = _arg("Окно: 24h, 7d, 30d")

    @mcp.prompt(name="find_niche", title=tr("Найти нишу"),
                description=tr("Оценить тему как нишу: соберёт видео (если их ещё нет), покажет "
                               "насыщенность, тренд, вирусные видео мелких каналов и риск шаблонности. "
                               "Квота: 1 поиск из 100 в день на страницу, если данных ещё нет."))
    def find_niche(topic: TOPIC, pages: PAGES = "1") -> str:
        n = _pages(pages)
        steps = _steps(
            tr("Вызови `list_niches()`. Если ниша с таким запросом уже есть и собрана за последние "
               "7 дней — пропусти сбор и используй её slug."),
            _guard(tr("Для сбора нужно, чтобы search_calls_left_today было не меньше {need}.",
                      need=n + 5)),
            tr("Если данных нет: `collect_niche(query=\"{topic}\", pages={n})`. Больше {n} страниц "
               "не бери.", topic=topic, n=n),
            tr("`niche_overview(niche=<slug>)` — размер каналов, медианы, и отдельно saturation_v2 "
               "(тренд: растёт / держится / остывает / забита / мало данных, с причинами и "
               "confidence)."),
            tr("`viral_videos_small_channels(niche=<slug>, period=\"30d\")` — что выстреливает "
               "у каналов до 10k."),
            tr("`niche_template_risk(niche=<slug>)` — какая доля каналов похожа на шаблонный конвейер."),
        )
        return "\n\n".join([
            tr("Оцени тему «{topic}» как нишу для нового YouTube-канала.", topic=topic),
            tr("Квота: до {n} поисковых запросов из 100 в день (pages={n}) и примерно {n} units, "
               "только если ниша\nещё не собрана. Остальные шаги бесплатны.", n=n),
            steps,
            tr("Ответ: короткая таблица «показатель — значение — что это значит» (каналы, медиана "
               "просмотров,\nтренд и его причины, пробития мелких каналов, доля шаблонных каналов), "
               "затем 3–5 примеров\nвирусных видео мелких каналов и вывод в 2–3 предложениях: стоит "
               "ли входить и с каким форматом.\nЕсли тренд «мало данных» или confidence low — так и "
               "скажи, не делай вывод сильнее данных.") + "\n" + tr(ANSWER),
        ])

    @mcp.prompt(name="analyze_competitor", title=tr("Разобрать конкурента"),
                description=tr("Полный разбор канала: рост, лучшие видео, фразы в заголовках, лучшее "
                               "время публикации, похожие каналы. Квота: ~2–3 units, поиск не тратит."))
    def analyze_competitor(channel: CHANNEL) -> str:
        steps = _steps(
            _guard(tr("Для этого сценария хватит 10 units.")),
            tr("`collect_channel(channel=\"{channel}\", max_videos=100)` — возьми channel_id из ответа.",
               channel=channel),
            tr("`channel_analytics(channel_id=<id>)` — рост, каденс, медиана против среднего, лучшие "
               "outlier'ы."),
            tr("`title_patterns(channel_id=<id>)` — какие фразы в заголовках связаны с выстрелами."),
            tr("`best_time_to_publish(channel_id=<id>)` — лучшие дни и часы (UTC)."),
            tr("`similar_channels(channel_id=<id>, limit=5)` — с кем он делит аудиторию в нашей базе."),
        )
        return "\n\n".join([
            tr("Разбери канал {channel} как конкурента.", channel=channel),
            tr("Квота: сбор канала берёт 1 unit за 50 видео (около 2–3 units на 100 видео) и не "
               "тратит\nпоисковые запросы. Остальные шаги бесплатны."),
            steps,
            tr("Ответ: профиль канала (подписчики, частота, медиана просмотров), 3–5 лучших видео с "
               "множителем,\nфразы-паттерны, лучшее время, похожие каналы, и 3 конкретных вывода: что "
               "у него стоит взять и\nгде он слаб. Поля роста пустые, пока нет снимков за несколько "
               "дней — скажи об этом, если так.") + "\n" + tr(ANSWER),
        ])

    @mcp.prompt(name="validate_idea", title=tr("Проверить идею"),
                description=tr("Есть ли уже такие видео в нише и как они сработали, оценка вариантов "
                               "заголовка и кто скоро станет конкурентом. Квота: 0."))
    def validate_idea(idea: IDEA, niche: NICHE) -> str:
        steps = _steps(
            tr("`check_ideas(ideas=[\"{idea}\"], niche=\"{niche}\")` — вердикт free / recent / "
               "proven / flopped и похожие видео.", idea=idea, niche=niche),
            tr("Придумай 5 вариантов заголовка под эту идею и оцени их: "
               "`score_titles(candidates=[...], niche=\"{niche}\")`.", niche=niche),
            tr("`high_future_competition(niche=\"{niche}\", period=\"30d\")` — молодые быстрые "
               "каналы, которые могут занять эту тему.", niche=niche),
        )
        return "\n\n".join([
            tr("Проверь идею видео «{idea}» для ниши {niche}.", idea=idea, niche=niche),
            tr("Квота: 0 — все шаги читают только локальную базу."),
            steps,
            tr("Ответ: вердикт одной строкой (снимать / снимать с другим углом / не снимать) и "
               "почему, таблица\nзаголовков с оценкой (лучший первым), 2–3 похожих видео, если "
               "есть, и кто из молодых каналов\nрядом.") + " " + tr(ANSWER),
        ])

    @mcp.prompt(name="outlier_to_video", title=tr("Outlier → своё видео"),
                description=tr("Бриф для своего ролика из одного выстрелившего видео: крючок, "
                               "паттерны ниши, занята ли тема, заголовки, референсы превью. Квота: 0."))
    def outlier_to_video(video_id: VIDEO, use_llm: USE_LLM = "yes") -> str:
        llm = str(str(use_llm).strip().lower() not in ("no", "нет", "false", "0")).lower()
        steps = _steps(
            f"`build_brief(video_id=\"{video_id}\", use_llm={llm}, save=false)`.",
            tr("`format_repeatability(video_id=\"{video_id}\")` — повторялся ли формат у других "
               "каналов. Если 'one_off' — честно скажи, что это может быть разовая удача; 'unknown' "
               "значит «мало собрано», а не «не работает».", video_id=video_id),
            tr("Посмотри skipped: всё, что не удалось собрать, назови прямо (например, нет "
               "транскрипта — предложи вставить его на экране «Транскрипты»)."),
            tr("Если бриф полезен, спроси, сохранить ли его в черновики, и только после согласия "
               "вызови `build_brief(video_id=\"{video_id}\", use_llm={llm}, save=true)`.",
               video_id=video_id, llm=llm),
        )
        return "\n\n".join([
            tr("Сделай бриф для моего видео по выстрелившему видео {video_id}.", video_id=video_id),
            tr("Квота: 0. С use_llm=true тратится бюджет LLM сервера (если он настроен)."),
            steps,
            tr("Ответ: почему видео выстрелило, повторяем ли формат, крючок, занята ли тема (что уже "
               "снято),\n3–5 заголовков, и мой угол — чем моё видео должно отличаться, а не как "
               "скопировать.") + " " + tr(ANSWER),
        ])

    @mcp.prompt(name="weekly_review", title=tr("Обзор недели"),
                description=tr("Что случилось за период: новые outlier'ы, ускорения, перепаковки, "
                               "пропавшие каналы, новые сильные каналы. Квота: 0."))
    def weekly_review(period: PERIOD = "7d") -> str:
        steps = _steps(
            tr("`daily_digest(period=\"{period}\")` — сводка: outlier'ы, ускорения, растущие каналы.",
               period=period),
            tr("`list_events(unseen_only=true)` — непросмотренные алерты."),
            tr("`packaging_changes(period=\"{period}\")` — кто сменил заголовок или превью и что "
               "стало с просмотрами.", period=period),
            tr("`recently_added_outlier_channels(period=\"{period}\")` — новые каналы, которые сразу "
               "обгоняют свой уровень.", period=period),
        )
        return "\n\n".join([
            tr("Сделай обзор за период {period} по моим отслеживаемым каналам и базе.", period=period),
            tr("Квота: 0 — все шаги читают только локальную базу."),
            steps,
            tr("Ответ: 5–7 пунктов «что произошло → что с этим делать», сначала самое важное. Если "
               "данных\nза период нет — скажи, что воркер, возможно, не запущен.") + " " + tr(ANSWER),
        ])

    @mcp.prompt(name="find_content_gaps", title=tr("Пробелы в контенте"),
                description=tr("Вопросы зрителей из комментариев, на которые в нише ещё нет видео, "
                               "и бриф под выбранный. Квота: 0 из кэша; чтение комментариев — "
                               "1 unit на видео и только с вашего согласия."))
    def find_content_gaps(niche: NICHE) -> str:
        steps = _steps(
            tr("`content_gaps(niche=\"{niche}\", fetch=false)` — только кэш.", niche=niche),
            tr("Если в skippedVideos есть видео с reason=not-fetched — скажи, сколько их и сколько "
               "units это стоит, и спроси, читать ли комментарии. Только если пользователь "
               "согласился: ") + _guard(tr("Затем `content_gaps(niche=\"{niche}\", fetch=true)`.",
                                           niche=niche)),
            tr("Покажи пробелы и спроси, под какой сделать бриф. Для выбранного: "
               "`build_brief(video_id=<первое из sourceVideos>, gap_topic=<topic>, save=false)`."),
        )
        return "\n\n".join([
            tr("Найди пробелы в контенте ниши {niche}: о чём зрители спрашивают, а видео нет.",
               niche=niche),
            tr("Квота: 0, пока читается кэш. Чтение комментариев — 1 unit на каждое непрочитанное "
               "видео (обычно\nдо 10), и только после согласия пользователя."),
            steps,
            tr("Ответ: таблица пробелов (вопрос, статус, сколько раз спросили, ближайшее "
               "существующее видео),\nпометка режима (LLM или правила — без LLM шума больше) и "
               "размер базы из coverageBase: пробел\nреален настолько, насколько полна база.")
            + " " + tr(ANSWER),
        ])

    @mcp.prompt(name="niche_health", title=tr("Здоровье ниши"),
                description=tr("Проверка ниши перед входом: тренд, шаблонность каналов, спонсоры, "
                               "крючки outlier'ов. Квота: 0."))
    def niche_health(niche: NICHE) -> str:
        steps = _steps(
            tr("`niche_overview(niche=\"{niche}\")` — возьми saturation_v2: статус, причины, "
               "confidence.", niche=niche),
            tr("`niche_template_risk(niche=\"{niche}\")` — доля шаблонных каналов (риск для всей "
               "ниши).", niche=niche),
            tr("`sponsor_map(niche=\"{niche}\")` — платят ли бренды авторам ниши и кто.", niche=niche),
            tr("`niche_hook_benchmark(niche=\"{niche}\")` — чем крючки outlier'ов отличаются (если "
               "хватает транскриптов).", niche=niche),
        )
        return "\n\n".join([
            tr("Проверь здоровье ниши {niche} перед тем, как в неё входить.", niche=niche),
            tr("Квота: 0 — все шаги читают только локальную базу."),
            steps,
            tr("Ответ: светофор по четырём пунктам (тренд, шаблонность, деньги, крючки) с числами и "
               "итог в 2–3\nпредложениях. «Мало данных» не превращай в вывод — подскажи, что "
               "собрать.") + " " + tr(ANSWER),
        ])
