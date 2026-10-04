"""What every number on the screen is (plan 23): YouTube data or an estimate
of niche-finder, how it is computed, from what, and from how much data it
starts to mean something. One place for the dashboard's "?" tips, the help
page and the explain_scores MCP tool.

YouTube's derived-metrics policy asks API clients to keep their own metrics
visibly apart from API data and never present them as YouTube's
(developers.google.com/youtube/terms/derived-metrics-policy) -- `source`
is that line. Thresholds come from the modules that use them, so the text
cannot drift from the code.
"""
from domain import hook_scoring as HS
from domain import language_gaps as LG
from domain import metrics as M
from domain import milestones as MS
from domain import monetization as MZ
from domain import policy_signals as PS
from domain import repeatability as RP
from domain import saturation as SAT
from domain import template_risk as TR

YOUTUBE = "youtube"
ESTIMATE = "niche-finder"

_BANDS = ", ".join(f"<{t:g} {name}" for t, name in M.OUTLIER_BANDS if t != float("inf"))

CATALOG = {
    # ------------------------------------------------------- YouTube data
    "views": {"name": "Просмотры", "source": YOUTUBE,
              "formula": "viewCount из YouTube Data API на момент последнего снимка",
              "inputs": ["videos.list / videos.batchGetStats"], "minSample": None},
    "subscribers": {"name": "Подписчики", "source": YOUTUBE,
                    "formula": "subscriberCount из YouTube Data API; YouTube округляет его до трёх "
                               "значащих цифр, а скрытое число не отдаёт",
                    "inputs": ["channels.list"], "minSample": None},
    "likesComments": {"name": "Лайки и комментарии", "source": YOUTUBE,
                      "formula": "likeCount / commentCount из YouTube Data API",
                      "inputs": ["videos.list"], "minSample": None},
    # ------------------------------------------------- estimates of ours
    "outlierScore": {
        "name": "Множитель (outlier)", "source": ESTIMATE,
        "formula": f"просмотры видео / медиана просмотров {M.DEFAULT_BASELINE_N} предыдущих "
                   "загрузок канала того же формата (Shorts и длинные отдельно); полосы: " + _BANDS,
        "inputs": ["просмотры видео", "просмотры прошлых видео канала из базы"],
        "minSample": "4 собранных видео канала; меньше — множитель против среднего за жизнь (≈)"},
    "outlierScoreAgeAdjusted": {
        "name": "Множитель с поправкой на возраст", "source": ESTIMATE,
        "formula": "просмотры / (медиана канала × кривая взросления(возраст)) — молодое видео "
                   "сравнивается с тем, сколько обычное видео набирает к тому же возрасту",
        "inputs": ["множитель", "кривая взросления (своя, если откалибрована)"],
        "minSample": "как у множителя"},
    "vsr": {"name": "VSR", "source": ESTIMATE,
            "formula": "просмотры / подписчики канала — насколько видео вышло за свою аудиторию",
            "inputs": ["просмотры", "подписчики"], "minSample": None},
    "vph": {"name": "VPH", "source": ESTIMATE,
            "formula": "прирост просмотров между снимками / часы между ними; без истории — "
                       "просмотры / часы с публикации. Пары снимков через 24.08.2026 не берутся",
            "inputs": ["снимки воркера"], "minSample": "2 снимка"},
    "acceleration": {"name": "Ускорение", "source": ESTIMATE,
                     "formula": "VPH сегодня / VPH вчера; > 1.5 — разгоняется, < 0.7 — остывает",
                     "inputs": ["снимки воркера за 48 ч"], "minSample": "снимки за 2 суток"},
    "momentumGrade": {"name": "Импульс и грейд", "source": ESTIMATE,
                      "formula": "просмотров в день за 30 дней / в среднем за жизнь канала; "
                                 "грейд — буква по log2 этого отношения (идея Social Blade)",
                      "inputs": ["снимки канала"], "minSample": "снимки за 30 дней"},
    "revenueRange": {"name": "Доход и RPM", "source": ESTIMATE,
                     "formula": "просмотры за месяц / 1000 × RPM ниши × "
                                f"{M.MONETISATION_DISCOUNT}; показывается вилкой ÷{M.RPM_SPREAD:g}…"
                                f"×{M.RPM_SPREAD:g}: публичные оценки одной ниши расходятся до 7 раз. "
                                "Только AdSense, без спонсоров",
                     "inputs": ["просмотры", "категория YouTube видео"], "minSample": None},
    "trendScore": {"name": "trendScore, lift, momentum фраз", "source": ESTIMATE,
                   "formula": "lift = P(outlier | фраза в заголовке) / P(outlier); momentum = доля "
                              "фразы сейчас / в прошлом окне; trendScore = ln(1 + видео) × lift × "
                              "momentum. Объёма поиска в API нет — его здесь и нет",
                   "inputs": ["заголовки и теги собранных видео"], "minSample": "фраза в 3+ видео"},
    "nicheTrend": {"name": "Тренд ниши", "source": ESTIMATE,
                   "formula": f"последние {SAT.RECENT_DAYS} дней против {SAT.BASE_DAYS} до них: "
                              "предложение (видео), спрос (медиана прогноза просмотров), новые каналы, "
                              f"доля новичков с outlier ≥ ×{SAT.BREAKOUT:g}",
                   "inputs": ["видео ниши за 120 дней"],
                   "minSample": f"{SAT.MIN_VIDEOS} видео в каждом окне"},
    "templateRisk": {"name": "Риск шаблонности", "source": ESTIMATE,
                     "formula": "взвешенное среднее четырёх сигналов: похожесть заголовков "
                                f"({TR.WEIGHTS['similarity']:g}), общий скелет заголовка "
                                f"({TR.WEIGHTS['templateShare']:g}), одинаковая длина видео "
                                f"({TR.WEIGHTS['durationCv']:g}), ровный график загрузок "
                                f"({TR.WEIGHTS['cadenceCv']:g}); эвристика, не вердикт YouTube",
                     "inputs": ["последние загрузки канала"],
                     "minSample": f"{TR.MIN_VIDEOS} загрузок"},
    "hookScore": {"name": "Оценка вступления", "source": ESTIMATE,
                  "formula": "баллы за признаки текста первых ~30 секунд: "
                             + ", ".join(f"{k} {v}" for k, v in HS.MAX_POINTS.items())
                             + f"; слабое < {HS.LEVEL_WEAK_BELOW}, сильное ≥ {HS.LEVEL_STRONG_FROM}. "
                             "Только текст, не картинка; корреляция, не причина",
                  "inputs": ["вставленный транскрипт"], "minSample": None},
    "titleScore": {"name": "Оценка заголовка", "source": ESTIMATE,
                   "formula": "50 ± длина в норме (±10), число (+5), шаблоны outlier'ов ниши "
                              "(до +20), почти дубль уже снятого (−30); с LLM — его оценка рядом",
                   "inputs": ["заголовок", "шаблоны ниши"], "minSample": None},
    "ideaVerdict": {"name": "Вердикт идеи", "source": ESTIMATE,
                    "formula": "совпадения идеи в базе (смысл и вхождение в заголовок): нет — "
                               "свободна; свежие — недавно снимали; старые с outlier ≥ ×2 — спрос "
                               "доказан; иначе — провал",
                    "inputs": ["эмбеддинги и заголовки собранных видео"], "minSample": None},
    "ypp": {"name": "Пороги YPP", "source": ESTIMATE,
            "formula": "какие пороги партнёрской программы канал видимо проходит: подписчики, "
                       "загрузки и просмотры Shorts за 90 дней по собранным видео (нижняя граница); "
                       f"с {MZ.RULES_2027_FROM.isoformat()} — правила 2027. Часы просмотра через API "
                       "не узнать. Не статус монетизации",
            "inputs": ["подписчики", "собранные видео за 90 дней"], "minSample": None},
    "milestones": {"name": "Рубежи подписчиков", "source": ESTIMATE,
                   "formula": "прямая по темпу роста подписчиков за 30 и 90 дней до следующего "
                              "круглого числа",
                   "inputs": ["снимки канала"],
                   "minSample": f"{MS.MIN_POINTS} снимков за {MS.MIN_SPAN_DAYS}+ дней"},
    "repeatability": {"name": "Формат повторяем?", "source": ESTIMATE,
                      "formula": "похожие по смыслу видео других каналов: сколько каналов получили "
                                 f"outlier ≥ ×{RP.HIT_SCORE:g} (канал считается один раз); "
                                 f"{RP.MIN_HIT_CHANNELS}+ — повторяем",
                      "inputs": ["эмбеддинги", "множители"],
                      "minSample": f"{RP.MIN_VIDEOS} похожих видео у {RP.MIN_CHANNELS}+ каналов"},
    "languageGap": {"name": "Другой язык", "source": ESTIMATE,
                    "formula": "outlier на исходном языке и ближайшие по смыслу видео на целевом "
                               f"(сходство ≥ {LG.MIN_SIMILARITY}): нет — не снято; есть, но ни одно не "
                               f"outlier ≥ ×{LG.HIT_SCORE:g} — снимали слабо; есть outlier — уже есть "
                               "хит. Рядом: сколько других каналов на исходном языке повторили формат",
                    "inputs": ["эмбеддинги", "множители", "язык видео"],
                    "minSample": f"{LG.THIN_CORPUS_VIDEOS}+ видео на целевом языке, иначе «не снято» "
                                 "может значить «не собрано»"},
    "expectedCurve": {"name": "Ожидаемая траектория", "source": ESTIMATE,
                      "formula": "медиана просмотров канала × кривая взросления(возраст)",
                      "inputs": ["медиана канала", "кривая взросления"],
                      "minSample": "4 собранных видео канала"},
    "policySignals": {"name": "Сигналы по правилам монетизации", "source": ESTIMATE,
                      "formula": "три категории «неаутентичного» контента: шаблонность (риск шаблонности, "
                                 "похожие обложки), шок (доля заголовков с шок-маркерами: высокая от "
                                 f"{round(PS.SHOCK_HIGH * 100)}%), ИИ-персона (чувствительная тема в "
                                 f"{round(PS.SENSITIVE_SHARE * 100)}%+ видео и раскрытый ИИ или безликий "
                                 "канал — не выше «присмотреться»). Без общего процента, не решение YouTube",
                      "inputs": ["заголовки", "темы YouTube", "раскрытие ИИ", "обложки", "ИИ-разметка"],
                      "minSample": f"{PS.MIN_TITLES} заголовков; шаблонность — {TR.MIN_VIDEOS} загрузок"},
    "ownFormats": {"name": "Форматы своего канала", "source": ESTIMATE,
                   "formula": "из ваших данных YouTube Analytics по creatorContentType: доля просмотров и "
                              "подписчиков на 1000 просмотров по формату; r Пирсона между недельными "
                              "просмотрами Shorts и длинных; часы длинных и трансляций за 365 дней и дата "
                              "порога по темпу 30 дней (не «qualified watch hours» YouTube)",
                   "inputs": ["ваш канал через OAuth"], "minSample": "8 недель для связи форматов"},
    "topicMatch": {"name": "Совпадение с темой", "source": ESTIMATE,
                   "formula": "косинус эмбеддингов темы и заголовка с описанием видео ≥ порога темы",
                   "inputs": ["эмбеддинги"], "minSample": None},
}


def catalog(key: str = None) -> dict:
    if key is None:
        return {"scores": CATALOG, "sources": {YOUTUBE: "данные YouTube API",
                                               ESTIMATE: "оценка niche-finder, не данные YouTube"}}
    if key not in CATALOG:
        raise KeyError(key)
    return {key: CATALOG[key]}
