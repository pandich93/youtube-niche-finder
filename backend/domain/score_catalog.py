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
from domain import niche_ranking as NRK
from domain import outlier_traits as OTR
from domain import policy_signals as PS
from domain import repeatability as RP
from domain import saturation as SAT
from domain import template_risk as TR
from domain.i18n import tr

YOUTUBE = "youtube"
ESTIMATE = "niche-finder"

_BANDS = ", ".join(f"<{t:g} {name}" for t, name in M.OUTLIER_BANDS if t != float("inf"))


class _T(str):
    """A text built from the code's thresholds: reads as the Russian string
    (CATALOG keeps showing exactly that), and keeps its template and params
    so catalog() can translate the template and put the same numbers back.
    A param may be a function of the translator (for words inside a param)."""

    def __new__(cls, template: str, /, **params):
        text = super().__new__(cls, template.format(**_resolve(params, str)))
        text.template, text.params = template, params
        return text


def _resolve(params: dict, say) -> dict:
    return {k: (v(say) if callable(v) else v) for k, v in params.items()}


def _say(value):
    """One CATALOG value in the reader's language (lists item by item)."""
    if isinstance(value, _T):
        return tr(value.template, **_resolve(value.params, tr))
    if isinstance(value, str):
        return tr(value)
    if isinstance(value, list):
        return [_say(v) for v in value]
    return value


def _entry(entry: dict) -> dict:
    return {k: (v if k == "source" else _say(v)) for k, v in entry.items()}

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
        "formula": _T("просмотры видео / медиана просмотров {n} предыдущих "
                      "загрузок канала того же формата (Shorts и длинные отдельно); полосы: {bands}",
                      n=M.DEFAULT_BASELINE_N, bands=_BANDS),
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
                     "formula": _T("просмотры за месяц / 1000 × RPM ниши × "
                                   "{discount}; показывается вилкой ÷{spread}…"
                                   "×{spread}: публичные оценки одной ниши расходятся до 7 раз. "
                                   "Только AdSense, без спонсоров",
                                   discount=M.MONETISATION_DISCOUNT, spread=f"{M.RPM_SPREAD:g}"),
                     "inputs": ["просмотры", "категория YouTube видео"], "minSample": None},
    "trendScore": {"name": "trendScore, lift, momentum фраз", "source": ESTIMATE,
                   "formula": "lift = P(outlier | фраза в заголовке) / P(outlier); momentum = доля "
                              "фразы сейчас / в прошлом окне; trendScore = ln(1 + видео) × lift × "
                              "momentum. Объёма поиска в API нет — его здесь и нет",
                   "inputs": ["заголовки и теги собранных видео"], "minSample": "фраза в 3+ видео"},
    "nicheTrend": {"name": "Тренд ниши", "source": ESTIMATE,
                   "formula": _T("последние {recent} дней против {base} до них: "
                                 "предложение (видео), спрос (медиана прогноза просмотров), новые каналы, "
                                 "доля новичков с outlier ≥ ×{breakout}",
                                 recent=SAT.RECENT_DAYS, base=SAT.BASE_DAYS,
                                 breakout=f"{SAT.BREAKOUT:g}"),
                   "inputs": ["видео ниши за 120 дней"],
                   "minSample": _T("{n} видео в каждом окне", n=SAT.MIN_VIDEOS)},
    "templateRisk": {"name": "Риск шаблонности", "source": ESTIMATE,
                     "formula": _T("взвешенное среднее четырёх сигналов: похожесть заголовков "
                                   "({similarity}), общий скелет заголовка "
                                   "({template}), одинаковая длина видео "
                                   "({duration}), ровный график загрузок "
                                   "({cadence}); эвристика, не вердикт YouTube",
                                   similarity=f"{TR.WEIGHTS['similarity']:g}",
                                   template=f"{TR.WEIGHTS['templateShare']:g}",
                                   duration=f"{TR.WEIGHTS['durationCv']:g}",
                                   cadence=f"{TR.WEIGHTS['cadenceCv']:g}"),
                     "inputs": ["последние загрузки канала"],
                     "minSample": _T("{n} загрузок", n=TR.MIN_VIDEOS)},
    "hookScore": {"name": "Оценка вступления", "source": ESTIMATE,
                  "formula": _T("баллы за признаки текста первых ~30 секунд: {points}"
                                "; слабое < {weak}, сильное ≥ {strong}. "
                                "Только текст, не картинка; корреляция, не причина",
                                points=", ".join(f"{k} {v}" for k, v in HS.MAX_POINTS.items()),
                                weak=HS.LEVEL_WEAK_BELOW, strong=HS.LEVEL_STRONG_FROM),
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
            "formula": _T("какие пороги партнёрской программы канал видимо проходит: подписчики, "
                          "загрузки и просмотры Shorts за 90 дней по собранным видео (нижняя граница); "
                          "с {rules_from} — правила 2027. Часы просмотра через API "
                          "не узнать. Не статус монетизации",
                          rules_from=MZ.RULES_2027_FROM.isoformat()),
            "inputs": ["подписчики", "собранные видео за 90 дней"], "minSample": None},
    "milestones": {"name": "Рубежи подписчиков", "source": ESTIMATE,
                   "formula": "прямая по темпу роста подписчиков за 30 и 90 дней до следующего "
                              "круглого числа",
                   "inputs": ["снимки канала"],
                   "minSample": _T("{points} снимков за {days}+ дней",
                                   points=MS.MIN_POINTS, days=MS.MIN_SPAN_DAYS)},
    "repeatability": {"name": "Формат повторяем?", "source": ESTIMATE,
                      "formula": _T("похожие по смыслу видео других каналов: сколько каналов получили "
                                    "outlier ≥ ×{hit} (канал считается один раз); "
                                    "{min_hit}+ — повторяем",
                                    hit=f"{RP.HIT_SCORE:g}", min_hit=RP.MIN_HIT_CHANNELS),
                      "inputs": ["эмбеддинги", "множители"],
                      "minSample": _T("{videos} похожих видео у {channels}+ каналов",
                                      videos=RP.MIN_VIDEOS, channels=RP.MIN_CHANNELS)},
    "languageGap": {"name": "Другой язык", "source": ESTIMATE,
                    "formula": _T("outlier на исходном языке и ближайшие по смыслу видео на целевом "
                                  "(сходство ≥ {similarity}): нет — не снято; есть, но ни одно не "
                                  "outlier ≥ ×{hit} — снимали слабо; есть outlier — уже есть "
                                  "хит. Рядом: сколько других каналов на исходном языке повторили формат",
                                  similarity=LG.MIN_SIMILARITY, hit=f"{LG.HIT_SCORE:g}"),
                    "inputs": ["эмбеддинги", "множители", "язык видео"],
                    "minSample": _T("{n}+ видео на целевом языке, иначе «не снято» "
                                    "может значить «не собрано»", n=LG.THIN_CORPUS_VIDEOS)},
    "profit": {"name": "Чистая прибыль", "source": ESTIMATE,
               "formula": "выручка (просмотры / 1000 × RPM: низкий / средний / высокий) минус расходы "
                          "профиля: за видео + за минуту × длина, за месяц — ещё постоянные расходы. "
                          "«Прибыльно» — если в плюсе даже нижняя граница, «убыток» — если в минусе даже "
                          "верхняя, иначе «не ясно». Только AdSense; у подключённого канала — реальный RPM",
               "inputs": ["просмотры", "RPM категории или ваш реальный", "профиль расходов"],
               "minSample": None},
    "outlierTraits": {"name": "Что общего у выстреливших", "source": ESTIMATE,
                      "formula": _T("outlier'ы (≥ ×{outlier_min}) против обычных видео "
                                    "(≤ ×{ordinary_max}) внутри формата (Shorts и длинные отдельно): "
                                    "доля с признаком (заметно — разница от {diff} п.п.) или "
                                    "медиана (заметно — в {ratio} раза и больше). Корреляция, "
                                    "не причина; при дюжине признаков один-два сходятся случайно",
                                    outlier_min=f"{OTR.OUTLIER_MIN:g}",
                                    ordinary_max=f"{OTR.ORDINARY_MAX:g}",
                                    diff=OTR.MIN_DIFF_PP, ratio=f"{OTR.MIN_RATIO:g}"),
                      "inputs": ["заголовки", "длительность", "теги", "время публикации", "множители"],
                      "minSample": _T("{n}+ видео в каждой группе", n=OTR.MIN_GROUP)},
    "nicheRank": {"name": "Где заходить", "source": ESTIMATE,
                  "formula": _T("0–100 из шести слагаемых с весами: {weights}"
                                ". Каждое слагаемое — 0–100 баллов по линейной шкале, итог — взвешенное "
                                "среднее известных; веса — суждение, RPM — оценка. Без балла, если тренд "
                                "ниши «мало данных»",
                                weights=lambda say: ", ".join(
                                    f"{say(NRK.LABELS[k]).lower()} {w}" for k, w in NRK.WEIGHTS.items())),
                  "inputs": ["тренд ниши", "новички", "RPM категории", "шаблонность каналов",
                             "сигналы по правилам"],
                  "minSample": _T("тренд: {videos}+ видео в каждом окне; известно "
                                  "{coverage}%+ веса",
                                  videos=SAT.MIN_VIDEOS, coverage=round(NRK.MIN_COVERAGE * 100))},
    "expectedCurve": {"name": "Ожидаемая траектория", "source": ESTIMATE,
                      "formula": "медиана просмотров канала × кривая взросления(возраст)",
                      "inputs": ["медиана канала", "кривая взросления"],
                      "minSample": "4 собранных видео канала"},
    "policySignals": {"name": "Сигналы по правилам монетизации", "source": ESTIMATE,
                      "formula": _T("три категории «неаутентичного» контента: шаблонность (риск шаблонности, "
                                    "похожие обложки), шок (доля заголовков с шок-маркерами: высокая от "
                                    "{shock}%), ИИ-персона (чувствительная тема в "
                                    "{sensitive}%+ видео и раскрытый ИИ или безликий "
                                    "канал — не выше «присмотреться»). Без общего процента, "
                                    "не решение YouTube",
                                    shock=round(PS.SHOCK_HIGH * 100),
                                    sensitive=round(PS.SENSITIVE_SHARE * 100)),
                      "inputs": ["заголовки", "темы YouTube", "раскрытие ИИ", "обложки", "ИИ-разметка"],
                      "minSample": _T("{titles} заголовков; шаблонность — {uploads} загрузок",
                                      titles=PS.MIN_TITLES, uploads=TR.MIN_VIDEOS)},
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
    """The catalog in the reader's language (domain.i18n); CATALOG itself stays Russian."""
    if key is None:
        return {"scores": {k: _entry(e) for k, e in CATALOG.items()},
                "sources": {YOUTUBE: tr("данные YouTube API"),
                            ESTIMATE: tr("оценка niche-finder, не данные YouTube")}}
    if key not in CATALOG:
        raise KeyError(key)
    return {key: _entry(CATALOG[key])}
