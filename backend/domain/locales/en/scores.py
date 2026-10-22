"""The score catalog (domain/score_catalog.py): names, formulas, inputs and
minimum samples behind the dashboard's "?" tips. Keys are the exact Russian
source -- for texts built from thresholds, the template with its
{placeholders}, so a changed threshold never orphans a translation."""

MESSAGES = {
    # ------------------------------------------------------------ sources
    "данные YouTube API": "YouTube API data",
    "оценка niche-finder, не данные YouTube": "niche-finder estimate, not YouTube data",

    # -------------------------------------------------------------- names
    "Просмотры": "Views",
    "Подписчики": "Subscribers",
    "Лайки и комментарии": "Likes and comments",
    "Множитель (outlier)": "Multiplier (outlier)",
    "Множитель с поправкой на возраст": "Age-adjusted multiplier",
    "Ускорение": "Acceleration",
    "Импульс и грейд": "Momentum and grade",
    "Доход и RPM": "Revenue and RPM",
    "trendScore, lift, momentum фраз": "trendScore, lift, phrase momentum",
    "Тренд ниши": "Niche trend",
    "Риск шаблонности": "Templated-content risk",
    "Оценка вступления": "Hook score",
    "Оценка заголовка": "Title score",
    "Вердикт идеи": "Idea verdict",
    "Пороги YPP": "YPP thresholds",
    "Рубежи подписчиков": "Subscriber milestones",
    "Формат повторяем?": "Is the format repeatable?",
    "Другой язык": "Language gaps",
    "Чистая прибыль": "Net profit",
    "Что общего у выстреливших": "What the hits have in common",
    "Где заходить": "Where to enter",
    "Ожидаемая траектория": "Expected trajectory",
    "Сигналы по правилам монетизации": "Monetization policy signals",
    "Форматы своего канала": "Your channel's formats",
    "Совпадение с темой": "Topic match",

    # ----------------------------------------------------------- formulas
    "viewCount из YouTube Data API на момент последнего снимка":
        "viewCount from the YouTube Data API as of the latest snapshot",
    "subscriberCount из YouTube Data API; YouTube округляет его до трёх значащих цифр, "
    "а скрытое число не отдаёт":
        "subscriberCount from the YouTube Data API; YouTube rounds it to three significant "
        "figures and returns nothing when the count is hidden",
    "likeCount / commentCount из YouTube Data API":
        "likeCount / commentCount from the YouTube Data API",
    "просмотры видео / медиана просмотров {n} предыдущих загрузок канала того же формата "
    "(Shorts и длинные отдельно); полосы: {bands}":
        "video views / median views of the channel's previous {n} uploads of the same format "
        "(Shorts and long-form separately); bands: {bands}",
    "просмотры / (медиана канала × кривая взросления(возраст)) — молодое видео сравнивается "
    "с тем, сколько обычное видео набирает к тому же возрасту":
        "views / (channel median × maturity curve(age)) — a young video is compared with what "
        "a typical video has gained by the same age",
    "просмотры / подписчики канала — насколько видео вышло за свою аудиторию":
        "views / channel subscribers — how far the video reached beyond its own audience",
    "прирост просмотров между снимками / часы между ними; без истории — просмотры / часы с "
    "публикации. Пары снимков через 24.08.2026 не берутся":
        "view gain between snapshots / hours between them; without history — views / hours "
        "since publishing. Snapshot pairs spanning 24.08.2026 are skipped",
    "VPH сегодня / VPH вчера; > 1.5 — разгоняется, < 0.7 — остывает":
        "VPH today / VPH yesterday; > 1.5 — speeding up, < 0.7 — cooling down",
    "просмотров в день за 30 дней / в среднем за жизнь канала; грейд — буква по log2 этого "
    "отношения (идея Social Blade)":
        "views per day over 30 days / the channel's lifetime average; the grade is a letter "
        "by log2 of that ratio (an idea from Social Blade)",
    "просмотры за месяц / 1000 × RPM ниши × {discount}; показывается вилкой ÷{spread}…×{spread}: "
    "публичные оценки одной ниши расходятся до 7 раз. Только AdSense, без спонсоров":
        "monthly views / 1000 × niche RPM × {discount}; shown as a range ÷{spread}…×{spread}: "
        "public estimates for one niche differ by up to 7×. AdSense only, no sponsors",
    "lift = P(outlier | фраза в заголовке) / P(outlier); momentum = доля фразы сейчас / в "
    "прошлом окне; trendScore = ln(1 + видео) × lift × momentum. Объёма поиска в API нет — "
    "его здесь и нет":
        "lift = P(outlier | phrase in the title) / P(outlier); momentum = the phrase's share now "
        "/ in the previous window; trendScore = ln(1 + videos) × lift × momentum. The API has no "
        "search volume, so neither does this",
    "последние {recent} дней против {base} до них: предложение (видео), спрос (медиана прогноза "
    "просмотров), новые каналы, доля новичков с outlier ≥ ×{breakout}":
        "the last {recent} days vs the {base} before them: supply (videos), demand (median "
        "projected views), new channels, share of newcomers with an outlier ≥ ×{breakout}",
    "взвешенное среднее четырёх сигналов: похожесть заголовков ({similarity}), общий скелет "
    "заголовка ({template}), одинаковая длина видео ({duration}), ровный график загрузок "
    "({cadence}); эвристика, не вердикт YouTube":
        "weighted average of four signals: title similarity ({similarity}), a shared title "
        "skeleton ({template}), uniform video length ({duration}), an even upload schedule "
        "({cadence}); a heuristic, not YouTube's verdict",
    "баллы за признаки текста первых ~30 секунд: {points}; слабое < {weak}, сильное ≥ {strong}. "
    "Только текст, не картинка; корреляция, не причина":
        "points for features of the text of the first ~30 seconds: {points}; weak < {weak}, "
        "strong ≥ {strong}. Text only, not visuals; a correlation, not a cause",
    "50 ± длина в норме (±10), число (+5), шаблоны outlier'ов ниши (до +20), почти дубль уже "
    "снятого (−30); с LLM — его оценка рядом":
        "50 ± length within the norm (±10), a number (+5), the niche's outlier patterns (up to "
        "+20), a near-duplicate of an existing video (−30); with an LLM, its score is shown "
        "alongside",
    "совпадения идеи в базе (смысл и вхождение в заголовок): нет — свободна; свежие — недавно "
    "снимали; старые с outlier ≥ ×2 — спрос доказан; иначе — провал":
        "matches for the idea in your database (by meaning and in titles): none — open; recent "
        "ones — covered lately; older ones with an outlier ≥ ×2 — proven demand; otherwise — "
        "a flop",
    "какие пороги партнёрской программы канал видимо проходит: подписчики, загрузки и просмотры "
    "Shorts за 90 дней по собранным видео (нижняя граница); с {rules_from} — правила 2027. Часы "
    "просмотра через API не узнать. Не статус монетизации":
        "which YouTube Partner Program thresholds the channel appears to meet: subscribers, "
        "uploads and Shorts views over 90 days from collected videos (a lower bound); from "
        "{rules_from} — the 2027 rules. Watch hours are not available through the API. Not the "
        "monetization status",
    "прямая по темпу роста подписчиков за 30 и 90 дней до следующего круглого числа":
        "a straight line at the subscriber growth rate of the last 30 and 90 days to the next "
        "round number",
    "похожие по смыслу видео других каналов: сколько каналов получили outlier ≥ ×{hit} (канал "
    "считается один раз); {min_hit}+ — повторяем":
        "videos with a similar meaning on other channels: how many channels got an outlier "
        "≥ ×{hit} (each channel counts once); {min_hit}+ — repeatable",
    "outlier на исходном языке и ближайшие по смыслу видео на целевом (сходство ≥ {similarity}): "
    "нет — не снято; есть, но ни одно не outlier ≥ ×{hit} — снимали слабо; есть outlier — уже "
    "есть хит. Рядом: сколько других каналов на исходном языке повторили формат":
        "an outlier in the source language and the closest videos by meaning in the target "
        "language (similarity ≥ {similarity}): none — not covered; some, but none an outlier "
        "≥ ×{hit} — weakly covered; an outlier — there is already a hit. Alongside: how many "
        "other channels in the source language repeated the format",
    "выручка (просмотры / 1000 × RPM: низкий / средний / высокий) минус расходы профиля: за "
    "видео + за минуту × длина, за месяц — ещё постоянные расходы. «Прибыльно» — если в плюсе "
    "даже нижняя граница, «убыток» — если в минусе даже верхняя, иначе «не ясно». Только "
    "AdSense; у подключённого канала — реальный RPM":
        "revenue (views / 1000 × RPM: low / mid / high) minus the profile's costs: per video + "
        "per minute × length, plus fixed costs per month. “Profitable” — if even the lower bound "
        "is positive, “loss” — if even the upper one is negative, otherwise “unclear”. AdSense "
        "only; a connected channel uses its real RPM",
    "outlier'ы (≥ ×{outlier_min}) против обычных видео (≤ ×{ordinary_max}) внутри формата "
    "(Shorts и длинные отдельно): доля с признаком (заметно — разница от {diff} п.п.) или "
    "медиана (заметно — в {ratio} раза и больше). Корреляция, не причина; при дюжине признаков "
    "один-два сходятся случайно":
        "outliers (≥ ×{outlier_min}) vs ordinary videos (≤ ×{ordinary_max}) within a format "
        "(Shorts and long-form separately): the share with a feature (notable — a gap of {diff} "
        "pp or more) or the median (notable — {ratio}× apart or more). A correlation, not a "
        "cause; with a dozen features, one or two match by chance",
    "0–100 из шести слагаемых с весами: {weights}. Каждое слагаемое — 0–100 баллов по линейной "
    "шкале, итог — взвешенное среднее известных; веса — суждение, RPM — оценка. Без балла, если "
    "тренд ниши «мало данных»":
        "0–100 from six weighted terms: {weights}. Each term is 0–100 points on a linear scale; "
        "the total is the weighted average of the known ones; the weights are a judgement, RPM "
        "is an estimate. No score when the niche trend is “not enough data”",
    "медиана просмотров канала × кривая взросления(возраст)":
        "channel median views × maturity curve(age)",
    "три категории «неаутентичного» контента: шаблонность (риск шаблонности, похожие обложки), "
    "шок (доля заголовков с шок-маркерами: высокая от {shock}%), ИИ-персона (чувствительная "
    "тема в {sensitive}%+ видео и раскрытый ИИ или безликий канал — не выше «присмотреться»). "
    "Без общего процента, не решение YouTube":
        "three categories of “inauthentic” content: templated content (templated-content risk, "
        "similar thumbnails), shock (share of titles with shock markers: high from {shock}%), AI "
        "persona (a sensitive topic in {sensitive}%+ of videos plus disclosed AI or a faceless "
        "channel — never above “worth a look”). No overall percentage; not YouTube's decision",
    "из ваших данных YouTube Analytics по creatorContentType: доля просмотров и подписчиков на "
    "1000 просмотров по формату; r Пирсона между недельными просмотрами Shorts и длинных; часы "
    "длинных и трансляций за 365 дней и дата порога по темпу 30 дней (не «qualified watch "
    "hours» YouTube)":
        "from your YouTube Analytics data by creatorContentType: share of views and subscribers "
        "per 1000 views by format; Pearson's r between weekly Shorts and long-form views; "
        "long-form and live watch hours over 365 days and the date of the threshold at the "
        "30-day pace (not YouTube's “qualified watch hours”)",
    "косинус эмбеддингов темы и заголовка с описанием видео ≥ порога темы":
        "cosine similarity of the embeddings of the topic and the video's title with "
        "description ≥ the topic's threshold",

    # ------------------------------------------------------------- inputs
    "просмотры видео": "video views",
    "просмотры прошлых видео канала из базы": "views of the channel's earlier videos in your database",
    "множитель": "multiplier",
    "кривая взросления (своя, если откалибрована)": "maturity curve (your own, if calibrated)",
    "просмотры": "views",
    "подписчики": "subscribers",
    "снимки воркера": "background collector snapshots",
    "снимки воркера за 48 ч": "background collector snapshots over 48 h",
    "снимки канала": "channel snapshots",
    "категория YouTube видео": "the video's YouTube category",
    "заголовки и теги собранных видео": "titles and tags of collected videos",
    "видео ниши за 120 дней": "niche videos over 120 days",
    "последние загрузки канала": "the channel's latest uploads",
    "вставленный транскрипт": "the pasted transcript",
    "заголовок": "title",
    "шаблоны ниши": "niche patterns",
    "эмбеддинги и заголовки собранных видео": "embeddings and titles of collected videos",
    "собранные видео за 90 дней": "collected videos over 90 days",
    "эмбеддинги": "embeddings",
    "множители": "multipliers",
    "язык видео": "video language",
    "RPM категории или ваш реальный": "category RPM or your real one",
    "профиль расходов": "cost profile",
    "заголовки": "titles",
    "длительность": "duration",
    "теги": "tags",
    "время публикации": "publish time",
    "тренд ниши": "niche trend",
    "новички": "newcomers",
    "RPM категории": "category RPM",
    "шаблонность каналов": "templated channels",
    "сигналы по правилам": "policy signals",
    "медиана канала": "channel median",
    "кривая взросления": "maturity curve",
    "темы YouTube": "YouTube topics",
    "раскрытие ИИ": "AI disclosure",
    "обложки": "thumbnails",
    "ИИ-разметка": "AI labels",
    "ваш канал через OAuth": "your channel via OAuth",

    # -------------------------------------------------------- min samples
    "4 собранных видео канала; меньше — множитель против среднего за жизнь (≈)":
        "4 collected videos of the channel; with fewer, the multiplier is against the lifetime "
        "average (≈)",
    "как у множителя": "same as the multiplier",
    "2 снимка": "2 snapshots",
    "снимки за 2 суток": "snapshots over 2 days",
    "снимки за 30 дней": "snapshots over 30 days",
    "фраза в 3+ видео": "phrase in 3+ videos",
    "{n} видео в каждом окне": "{n} videos in each window",
    "{n} загрузок": "{n} uploads",
    "{points} снимков за {days}+ дней": "{points} snapshots over {days}+ days",
    "{videos} похожих видео у {channels}+ каналов": "{videos} similar videos across {channels}+ channels",
    "{n}+ видео на целевом языке, иначе «не снято» может значить «не собрано»":
        "{n}+ videos in the target language; otherwise “not covered” may mean “not collected”",
    "{n}+ видео в каждой группе": "{n}+ videos in each group",
    "тренд: {videos}+ видео в каждом окне; известно {coverage}%+ веса":
        "trend: {videos}+ videos in each window; {coverage}%+ of the weight known",
    "4 собранных видео канала": "4 collected videos of the channel",
    "{titles} заголовков; шаблонность — {uploads} загрузок":
        "{titles} titles; templated content — {uploads} uploads",
    "8 недель для связи форматов": "8 weeks for the format correlation",
}
