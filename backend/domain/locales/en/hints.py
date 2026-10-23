"""Texts of application/* that reach a person through API responses: hints,
notes, reasons and error messages (discovery, inspection, search, channel
tracking, collecting, metadata review, hooks, briefs, calendar, plans).
Keys are the exact Russian source passed to domain.i18n.tr()."""

MESSAGES = {
    # application/discovery.py -- empty results
    "Ни одного видео в окне {period} по '{period_by}'. Соберите больше "
    "(collect_channel дешевле всего), возьмите период шире, или "
    "period_by='discovered', если видео старые, а в базу попали недавно.":
        "No videos in the {period} window by '{period_by}'. Collect more "
        "(collect_channel is the cheapest), widen the period, or use "
        "period_by='discovered' if the videos are old but reached the database recently.",
    "Фильтр '{name}' отсёк все {before} видео. Понизьте {param} или уберите его (0 / None).":
        "The '{name}' filter removed all {before} videos. Lower {param} or drop it (0 / None).",
    "Осталось 0 после '{step}'.": "Nothing left after '{step}'.",
    "Ни одной категории с >= {min_videos} видео в окне {period}. В окне всего {total} "
    "видео — соберите больше, возьмите период шире, понизьте min_videos, или "
    "period_by='discovered'.":
        "No category has >= {min_videos} videos in the {period} window. The window holds "
        "only {total} videos — collect more, widen the period, lower min_videos, or use "
        "period_by='discovered'.",
    "В окне {period} по '{period_by}' нет видео вообще — сначала соберите корпус "
    "(collect_channel / collect_niche).":
        "There are no videos at all in the {period} window by '{period_by}' — collect "
        "some data first (collect_channel / collect_niche).",
    "Ни одной фразы, встречающейся в >= {min_videos} видео. В окне {total} видео — "
    "понизьте min_videos (до 2 или 1), возьмите период шире, или соберите больше.":
        "No phrase appears in >= {min_videos} videos. The window holds {total} videos — "
        "lower min_videos (to 2 or 1), widen the period, or collect more.",
    "В окне {period} по '{period_by}' нет видео — сначала соберите корпус.":
        "No videos in the {period} window by '{period_by}' — collect some data first.",
    "Ни одного тега, встречающегося в >= {min_videos} видео ни в одной категории. "
    "Понизьте min_videos или возьмите период шире.":
        "No tag appears in >= {min_videos} videos in any category. Lower min_videos "
        "or widen the period.",

    # application/channel_tracking.py
    "Ни один из {before} каналов в окне не дотянул до множителя {min_multiplier} — "
    "понизьте min_multiplier.":
        "None of the {before} channels in the window reached the {min_multiplier} "
        "multiplier — lower min_multiplier.",
    "В окне {period} по '{period_by}' нет видео — сначала соберите корпус. Множитель "
    "считается по медиане предыдущих загрузок канала, поэтому каналу нужно минимум "
    "4 видео в базе.":
        "No videos in the {period} window by '{period_by}' — collect some data first. "
        "The multiplier comes from the median of the channel's previous uploads, so a "
        "channel needs at least 4 videos in the database.",
    "Нужно минимум два снимка. Их пишет воркер: docker compose up -d worker":
        "At least two snapshots are needed. The background collector records them: "
        "docker compose up -d worker",

    # application/collecting.py
    "Дневной лимит поиска исчерпан ({calls_today}/{limit}). search.list сбрасывается "
    "в полночь по Тихоокеанскому времени. Ниша '{slug}' уже создана (пока пустая) -- "
    "запустите тот же поиск позже, или используйте collect_channel/collect_trending, "
    "которые не тратят эту квоту.":
        "Daily search limit reached ({calls_today}/{limit}). search.list resets at "
        "midnight Pacific Time. The niche '{slug}' has already been created (empty for "
        "now) -- run the same search later, or use collect_channel/collect_trending, "
        "which don't spend this quota.",

    # application/inspection.py
    "видео нет в локальной базе": "this video is not in the local database",
    "видео нет в локальной базе, а YOUTUBE_API_KEY не задан — дозагрузить неоткуда":
        "this video is not in the local database, and YOUTUBE_API_KEY is not set — "
        "there is nowhere to fetch it from",
    "видео нет в локальной базе (дозагрузка выключена в настройках расширения)":
        "this video is not in the local database (fetching is turned off in the "
        "extension settings)",
    "канала нет в локальной базе": "this channel is not in the local database",
    "канала нет в локальной базе, а YOUTUBE_API_KEY не задан — дозагрузить неоткуда":
        "this channel is not in the local database, and YOUTUBE_API_KEY is not set — "
        "there is nowhere to fetch it from",
    "канала нет в локальной базе (дозагрузка выключена в настройках расширения)":
        "this channel is not in the local database (fetching is turned off in the "
        "extension settings)",

    # application/search.py -- idea check
    "эмбеддинги недоступны в этом прогоне -- совпадения только по вхождению фразы "
    "в название, точность ниже, чем с семантическим поиском":
        "embeddings are unavailable in this run -- matches come only from the phrase "
        "appearing in the title, which is less accurate than semantic search",
    "совпадений не найдено": "no matches found",
    "последнее совпадение младше {recent_days} дней -- пропустить, недавно снимали":
        "the latest match is less than {recent_days} days old -- skip it, it was "
        "covered recently",
    "совпадения старше {recent_days} дней, лучший outlier >= {proven_outlier} "
    "-- спрос доказан":
        "matches are older than {recent_days} days, best outlier >= {proven_outlier} "
        "-- demand is proven",
    "совпадения старше {recent_days} дней и лучший outlier < {proven_outlier} "
    "(включая {flop_outlier}-{proven_outlier} -- сигнал слабый, не считаем "
    "доказанным без явного провала)":
        "matches are older than {recent_days} days and the best outlier is < "
        "{proven_outlier} (including {flop_outlier}-{proven_outlier} -- a weak signal, "
        "not treated as conclusive without a clear flop)",

    # application/metadata_review.py ("длина заголовка" is the label it hands to
    # MD.length_signal, which translates it)
    "длина заголовка": "title length",
    "{label}: lift ×{lift} у выбросов (выборка {sample}); в черновике есть, "
    "и это соответствует паттерну":
        "{label}: lift ×{lift} among outliers (sample {sample}); your draft has it, "
        "which fits the pattern",
    "{label}: lift ×{lift} у выбросов (выборка {sample}); в черновике есть, "
    "а паттерн ниши другой":
        "{label}: lift ×{lift} among outliers (sample {sample}); your draft has it, "
        "but the niche's pattern is different",
    "{label}: lift ×{lift} у выбросов (выборка {sample}); в черновике нет, "
    "и это соответствует паттерну":
        "{label}: lift ×{lift} among outliers (sample {sample}); your draft doesn't "
        "have it, which fits the pattern",
    "{label}: lift ×{lift} у выбросов (выборка {sample}); в черновике нет, "
    "а паттерн ниши другой":
        "{label}: lift ×{lift} among outliers (sample {sample}); your draft doesn't "
        "have it, but the niche's pattern is different",

    # application/hook_score.py, application/briefs.py
    "Для этого видео не сохранён транскрипт -- сначала вставьте его на экране "
    "«Транскрипты» (request_transcript / save_transcript).":
        "No transcript saved for this video -- paste one on the Transcripts screen "
        "(request_transcript / save_transcript) first.",
    "Для видео этой ниши нет сохранённых транскриптов -- сначала вставьте их на "
    "экране «Транскрипты».":
        "No saved transcripts for videos in this niche -- paste some on the "
        "Transcripts screen first.",
    "Вставьте транскрипт на экране «Транскрипты» — крючок добавится в бриф.":
        "Paste a transcript on the Transcripts screen and the hook will be added to "
        "the brief.",

    # application/plans.py -- errors of choosing, assigning and editing plans
    "такого тарифа нет": "no such plan",
    "оплата пока не подключена -- этот тариф назначает администратор":
        "payments aren't connected yet -- an admin assigns this plan",
    "expiresAt: нужна дата в формате ISO": "expiresAt: an ISO date is required",
    "нет такого пользователя": "no such user",
    "name: обязательно": "name: required",
    "slug: латиница в нижнем регистре, цифры и дефис":
        "slug: lowercase Latin letters, digits and hyphens",
    "price: число не меньше 0": "price: a number no less than 0",
    "currency: три латинские буквы, например USD": "currency: three Latin letters, e.g. USD",
    "periodDays: целое число дней от 1 или пусто":
        "periodDays: a whole number of days from 1, or empty",
    "price: базовый тариф бесплатный": "price: the base plan is free",
    "periodDays: у базового тарифа нет срока": "periodDays: the base plan has no term",
    "isPublic: базовый тариф всегда виден": "isPublic: the base plan is always visible",
    "sortOrder: целое число": "sortOrder: an integer",
    "slug: тариф {slug} уже есть": "slug: plan {slug} already exists",
    "isBase: сначала назначьте базовым другой тариф":
        "isBase: make another plan the base one first",
    "isBase: архивный тариф не может быть базовым": "isBase: an archived plan can't be the base one",
    "базовый тариф нельзя архивировать": "the base plan can't be archived",
}
