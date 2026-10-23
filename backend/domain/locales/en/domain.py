"""Domain labels and explanations that reach the dashboard: metadata review
signals (domain/metadata.py), outlier traits (domain/outlier_traits.py),
language names and the thin-corpus hint (domain/language_gaps.py), the
where-to-enter terms (domain/niche_ranking.py). Keys are the exact Russian
source passed to domain.i18n.tr(), placeholders included."""

MESSAGES = {
    # ------------------------------------------------ domain/metadata.py
    "цифра в заголовке": "a digit in the title",
    "скобки в заголовке": "brackets in the title",
    "вопрос в заголовке": "a question in the title",
    "слово КАПСОМ": "a word in ALL CAPS",
    "эмодзи в заголовке": "emoji in the title",
    "длина заголовка": "title length",
    "{label}: {value} симв.; в базе только {sample} выбросов ниши для сравнения (нужно {min}+)":
        "{label}: {value} chars; your database has only {sample} niche outliers to compare "
        "with ({min}+ needed)",
    "{label}: {value} симв. против медианы {median} (p25-p75: {p25}-{p75}) у выбросов ниши, "
    "выборка {sample}":
        "{label}: {value} chars vs a median of {median} (p25-p75: {p25}-{p75}) among the niche's "
        "outliers, sample {sample}",
    "первые {n} символов: «{prefix}»": "first {n} characters: “{prefix}”",
    " -- фраза «{phrase}» попадает в видимую часть":
        " -- the phrase “{phrase}” is in the visible part",
    " -- фраза «{phrase}» НЕ попадает в видимую часть":
        " -- the phrase “{phrase}” is NOT in the visible part",
    "{tags} тегов, {chars}/{limit} символов": "{tags} tags, {chars}/{limit} characters",
    " -- ПРЕВЫШЕН лимит YouTube, лишние теги будут отброшены":
        " -- OVER YouTube's limit, the extra tags will be dropped",
    "; из {trending} трендовых фраз ниши совпало {matched}":
        "; {matched} of the niche's {trending} trending phrases match",
    "{n} симв.": "{n} chars",
    "; фраза «{phrase}» есть в первых {n} символах (видно до «ещё»)":
        "; the phrase “{phrase}” is in the first {n} characters (visible before “...more”)",
    "; фраза «{phrase}» отсутствует в первых {n} символах (видно до «ещё»)":
        "; the phrase “{phrase}” is missing from the first {n} characters (visible before "
        "“...more”)",
    "; таймкоды есть": "; has timestamps",
    "; таймкодов нет": "; no timestamps",

    # ------------------------------------------ domain/outlier_traits.py
    "Число в заголовке": "Number in the title",
    "Вопрос в заголовке": "Question in the title",
    "Скобки в заголовке": "Brackets in the title",
    "Слово ЗАГЛАВНЫМИ": "Word in ALL CAPS",
    "Эмодзи в заголовке": "Emoji in the title",
    "Публикация в выходные": "Published on a weekend",
    "Публикация 00–05 UTC": "Published 00–05 UTC",
    "Публикация 06–11 UTC": "Published 06–11 UTC",
    "Публикация 12–17 UTC": "Published 12–17 UTC",
    "Публикация 18–23 UTC": "Published 18–23 UTC",
    "Длина видео, секунды": "Video length, seconds",
    "Длина заголовка, символы": "Title length, characters",
    "Число тегов": "Number of tags",

    # ------------------------------------------ domain/language_gaps.py
    # lang_label(): the words after "на" in Russian, after "in" in English
    "английском языке": "English",
    "русском языке": "Russian",
    "узбекском языке": "Uzbek",
    "испанском языке": "Spanish",
    "хинди": "Hindi",
    "турецком языке": "Turkish",
    "корейском языке": "Korean",
    "арабском языке": "Arabic",
    "португальском языке": "Portuguese",
    "немецком языке": "German",
    "французском языке": "French",
    "японском языке": "Japanese",
    "индонезийском языке": "Indonesian",
    "итальянском языке": "Italian",
    "польском языке": "Polish",
    "украинском языке": "Ukrainian",
    "казахском языке": "Kazakh",
    "вьетнамском языке": "Vietnamese",
    "тайском языке": "Thai",
    "китайском языке": "Chinese",
    "персидском языке": "Persian",
    "бенгальском языке": "Bengali",
    "урду": "Urdu",
    "языке «{code}»": "the “{code}” language",
    "В базе только {videos} видео на {lang}: «открыто» может значить «не собрано». Соберите "
    "каналы этой ниши на этом языке и проверьте снова.":
        "Your database has only {videos} videos in {lang}: “open” may mean “not collected”. "
        "Collect this niche's channels in this language and check again.",

    # ------------------------------------------ domain/niche_ranking.py
    "Спрос": "Demand",
    "Предложение": "Supply",
    "Новички пробиваются": "Newcomers breaking through",
    "Шаблонные каналы": "Templated channels",
    "Сигналы по правилам": "Policy signals",
}
