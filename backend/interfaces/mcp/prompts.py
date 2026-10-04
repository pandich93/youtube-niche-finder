"""Ready-made scenarios (MCP prompts, plan 11): a Claude client lists them under
"+", and picking one puts a step-by-step instruction into the chat -- which of
our tools to call, in what order, what it costs in quota and what the answer
should look like. The client's own model runs the steps; the server spends
nothing on the prompt itself.

Texts are in Russian because they land in the chat as the user's message.
Tool names are written as `name(` so tests/test_mcp_prompts.py can check that
every tool a scenario names still exists.
"""

from typing import Annotated

from pydantic import Field

TOPIC = Annotated[str, Field(description="Тема будущего канала, как её искали бы на YouTube")]
PAGES = Annotated[str, Field(description="Сколько страниц поиска взять, 1–3 (каждая — 1 из 100 "
                                         "поисков в день)")]
CHANNEL = Annotated[str, Field(description="Канал: @handle, UC… id или ссылка")]
IDEA = Annotated[str, Field(description="Идея видео одной фразой")]
NICHE = Annotated[str, Field(description="Slug ниши из list_niches")]
VIDEO = Annotated[str, Field(description="ID выстрелившего видео (11 символов)")]
USE_LLM = Annotated[str, Field(description="yes / no — добавить разбор LLM сервера")]
PERIOD = Annotated[str, Field(description="Окно: 24h, 7d, 30d")]

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


def register(mcp):
    @mcp.prompt(name="find_niche", title="Найти нишу",
                description="Оценить тему как нишу: соберёт видео (если их ещё нет), покажет "
                            "насыщенность, тренд, вирусные видео мелких каналов и риск шаблонности. "
                            "Квота: 1 поиск из 100 в день на страницу, если данных ещё нет.")
    def find_niche(topic: TOPIC, pages: PAGES = "1") -> str:
        n = _pages(pages)
        steps = _steps(
            "Вызови `list_niches()`. Если ниша с таким запросом уже есть и собрана за последние 7 дней — "
            "пропусти сбор и используй её slug.",
            QUOTA_GUARD + f" Для сбора нужно, чтобы search_calls_left_today было не меньше {n + 5}.",
            f"Если данных нет: `collect_niche(query=\"{topic}\", pages={n})`. Больше {n} страниц не бери.",
            "`niche_overview(niche=<slug>)` — размер каналов, медианы, и отдельно saturation_v2 (тренд: "
            "растёт / держится / остывает / забита / мало данных, с причинами и confidence).",
            "`viral_videos_small_channels(niche=<slug>, period=\"30d\")` — что выстреливает "
            "у каналов до 10k.",
            "`niche_template_risk(niche=<slug>)` — какая доля каналов похожа на шаблонный конвейер.",
        )
        return f"""Оцени тему «{topic}» как нишу для нового YouTube-канала.

Квота: до {n} поисковых запросов из 100 в день (pages={n}) и примерно {n} units, только если ниша
ещё не собрана. Остальные шаги бесплатны.

{steps}

Ответ: короткая таблица «показатель — значение — что это значит» (каналы, медиана просмотров,
тренд и его причины, пробития мелких каналов, доля шаблонных каналов), затем 3–5 примеров
вирусных видео мелких каналов и вывод в 2–3 предложениях: стоит ли входить и с каким форматом.
Если тренд «мало данных» или confidence low — так и скажи, не делай вывод сильнее данных.
{ANSWER}"""

    @mcp.prompt(name="analyze_competitor", title="Разобрать конкурента",
                description="Полный разбор канала: рост, лучшие видео, фразы в заголовках, лучшее "
                            "время публикации, похожие каналы. Квота: ~2–3 units, поиск не тратит.")
    def analyze_competitor(channel: CHANNEL) -> str:
        steps = _steps(
            QUOTA_GUARD + " Для этого сценария хватит 10 units.",
            f"`collect_channel(channel=\"{channel}\", max_videos=100)` — возьми channel_id из ответа.",
            "`channel_analytics(channel_id=<id>)` — рост, каденс, медиана против среднего, лучшие outlier'ы.",
            "`title_patterns(channel_id=<id>)` — какие фразы в заголовках связаны с выстрелами.",
            "`best_time_to_publish(channel_id=<id>)` — лучшие дни и часы (UTC).",
            "`similar_channels(channel_id=<id>, limit=5)` — с кем он делит аудиторию в нашей базе.",
        )
        return f"""Разбери канал {channel} как конкурента.

Квота: сбор канала берёт 1 unit за 50 видео (около 2–3 units на 100 видео) и не тратит
поисковые запросы. Остальные шаги бесплатны.

{steps}

Ответ: профиль канала (подписчики, частота, медиана просмотров), 3–5 лучших видео с множителем,
фразы-паттерны, лучшее время, похожие каналы, и 3 конкретных вывода: что у него стоит взять и
где он слаб. Поля роста пустые, пока нет снимков за несколько дней — скажи об этом, если так.
{ANSWER}"""

    @mcp.prompt(name="validate_idea", title="Проверить идею",
                description="Есть ли уже такие видео в нише и как они сработали, оценка вариантов "
                            "заголовка и кто скоро станет конкурентом. Квота: 0.")
    def validate_idea(idea: IDEA, niche: NICHE) -> str:
        steps = _steps(
            f"`check_ideas(ideas=[\"{idea}\"], niche=\"{niche}\")` — вердикт free / recent / proven / "
            "flopped и похожие видео.",
            "Придумай 5 вариантов заголовка под эту идею и оцени их: "
            f"`score_titles(candidates=[...], niche=\"{niche}\")`.",
            f"`high_future_competition(niche=\"{niche}\", period=\"30d\")` — молодые быстрые каналы, "
            "которые могут занять эту тему.",
        )
        return f"""Проверь идею видео «{idea}» для ниши {niche}.

Квота: 0 — все шаги читают только локальную базу.

{steps}

Ответ: вердикт одной строкой (снимать / снимать с другим углом / не снимать) и почему, таблица
заголовков с оценкой (лучший первым), 2–3 похожих видео, если есть, и кто из молодых каналов
рядом. {ANSWER}"""

    @mcp.prompt(name="outlier_to_video", title="Outlier → своё видео",
                description="Бриф для своего ролика из одного выстрелившего видео: крючок, "
                            "паттерны ниши, занята ли тема, заголовки, референсы превью. Квота: 0.")
    def outlier_to_video(video_id: VIDEO, use_llm: USE_LLM = "yes") -> str:
        llm = str(use_llm).strip().lower() not in ("no", "нет", "false", "0")
        steps = _steps(
            f"`build_brief(video_id=\"{video_id}\", use_llm={str(llm).lower()}, save=false)`.",
            f"`format_repeatability(video_id=\"{video_id}\")` — повторялся ли формат у других каналов. "
            "Если 'one_off' — честно скажи, что это может быть разовая удача; 'unknown' значит "
            "«мало собрано», а не «не работает».",
            "Посмотри skipped: всё, что не удалось собрать, назови прямо (например, нет транскрипта — "
            "предложи вставить его на экране «Транскрипты»).",
            "Если бриф полезен, спроси, сохранить ли его в черновики, и только после согласия вызови "
            f"`build_brief(video_id=\"{video_id}\", use_llm={str(llm).lower()}, save=true)`.",
        )
        return f"""Сделай бриф для моего видео по выстрелившему видео {video_id}.

Квота: 0. С use_llm=true тратится бюджет LLM сервера (если он настроен).

{steps}

Ответ: почему видео выстрелило, повторяем ли формат, крючок, занята ли тема (что уже снято),
3–5 заголовков, и мой угол — чем моё видео должно отличаться, а не как скопировать. {ANSWER}"""

    @mcp.prompt(name="weekly_review", title="Обзор недели",
                description="Что случилось за период: новые outlier'ы, ускорения, перепаковки, "
                            "пропавшие каналы, новые сильные каналы. Квота: 0.")
    def weekly_review(period: PERIOD = "7d") -> str:
        steps = _steps(
            f"`daily_digest(period=\"{period}\")` — сводка: outlier'ы, ускорения, растущие каналы.",
            "`list_events(unseen_only=true)` — непросмотренные алерты.",
            f"`packaging_changes(period=\"{period}\")` — кто сменил заголовок или превью и что стало с "
            "просмотрами.",
            f"`recently_added_outlier_channels(period=\"{period}\")` — новые каналы, которые сразу "
            "обгоняют свой уровень.",
        )
        return f"""Сделай обзор за период {period} по моим отслеживаемым каналам и базе.

Квота: 0 — все шаги читают только локальную базу.

{steps}

Ответ: 5–7 пунктов «что произошло → что с этим делать», сначала самое важное. Если данных
за период нет — скажи, что воркер, возможно, не запущен. {ANSWER}"""

    @mcp.prompt(name="find_content_gaps", title="Пробелы в контенте",
                description="Вопросы зрителей из комментариев, на которые в нише ещё нет видео, "
                            "и бриф под выбранный. Квота: 0 из кэша; чтение комментариев — "
                            "1 unit на видео и только с вашего согласия.")
    def find_content_gaps(niche: NICHE) -> str:
        steps = _steps(
            f"`content_gaps(niche=\"{niche}\", fetch=false)` — только кэш.",
            "Если в skippedVideos есть видео с reason=not-fetched — скажи, сколько их и сколько units "
            "это стоит, и спроси, читать ли комментарии. Только если пользователь согласился: "
            + QUOTA_GUARD + f" Затем `content_gaps(niche=\"{niche}\", fetch=true)`.",
            "Покажи пробелы и спроси, под какой сделать бриф. Для выбранного: "
            "`build_brief(video_id=<первое из sourceVideos>, gap_topic=<topic>, save=false)`.",
        )
        return f"""Найди пробелы в контенте ниши {niche}: о чём зрители спрашивают, а видео нет.

Квота: 0, пока читается кэш. Чтение комментариев — 1 unit на каждое непрочитанное видео (обычно
до 10), и только после согласия пользователя.

{steps}

Ответ: таблица пробелов (вопрос, статус, сколько раз спросили, ближайшее существующее видео),
пометка режима (LLM или правила — без LLM шума больше) и размер базы из coverageBase: пробел
реален настолько, насколько полна база. {ANSWER}"""

    @mcp.prompt(name="niche_health", title="Здоровье ниши",
                description="Проверка ниши перед входом: тренд, шаблонность каналов, спонсоры, "
                            "крючки outlier'ов. Квота: 0.")
    def niche_health(niche: NICHE) -> str:
        steps = _steps(
            f"`niche_overview(niche=\"{niche}\")` — возьми saturation_v2: статус, причины, confidence.",
            f"`niche_template_risk(niche=\"{niche}\")` — доля шаблонных каналов (риск для всей ниши).",
            f"`sponsor_map(niche=\"{niche}\")` — платят ли бренды авторам ниши и кто.",
            f"`niche_hook_benchmark(niche=\"{niche}\")` — чем крючки outlier'ов отличаются (если хватает "
            "транскриптов).",
        )
        return f"""Проверь здоровье ниши {niche} перед тем, как в неё входить.

Квота: 0 — все шаги читают только локальную базу.

{steps}

Ответ: светофор по четырём пунктам (тренд, шаблонность, деньги, крючки) с числами и итог в 2–3
предложениях. «Мало данных» не превращай в вывод — подскажи, что собрать. {ANSWER}"""
