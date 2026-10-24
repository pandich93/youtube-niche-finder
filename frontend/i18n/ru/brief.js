export default {
  // вердикт по теме
  verdictFree: 'тема свободна',
  verdictRecent: 'недавно снимали',
  verdictProven: 'спрос доказан',
  verdictFlopped: 'уже проваливалась',

  // части брифа
  partSource: 'Исходное видео',
  partHook: 'Крючок',
  partWhyViral: 'Почему выстрелило',
  partTitles: 'Заголовки',
  partAngle: 'Паттерны ниши',
  partOverlap: 'Занята ли тема',
  partThumbnailReferences: 'Референсы превью',

  skippedTitle: 'Что не удалось собрать',
  skippedSub: 'каждая пропущенная часть названа честно, шаблоны вместо неё не подставляются',

  briefGapTitle: 'Бриф под пробел',
  briefOutlierTitle: 'Бриф из outlier',
  briefSub: 'исследование, не сценарий: выберите СВОЙ угол, а не копию',
  gapQuestion: 'Вопрос зрителей: <b>{topic}</b>. Проверка темы и заголовки — про него, видео ниже — источник, под которым спрашивали.',
  tileMultiplier: 'Множитель',
  tileViews: 'Просмотры',
  tileFormat: 'Формат',
  formatLong: 'длинное',
  seconds: '{s} с',
  tileTopic: 'Тема',
  similarInDb: '{count} похожих в базе',
  nicheMeta: ' · ниша {niche}',

  hookTitle: 'Крючок',
  hookSub: 'первые ~75 слов вставленного транскрипта (около 30 секунд)',

  whyTitle: 'Почему выстрелило',
  whySub: 'разбор LLM по данным канала',
  whyFormula: 'Формула: {text}',
  whyTiming: 'Время: {text}',

  titlesTitle: 'Заголовки',
  titlesLlm: 'кандидаты LLM с оценкой',
  titlesNoLlm: 'новые заголовки без LLM не генерируются',
  patternSkeletons: 'Фразы, которые у outlier\'ов ниши встречаются чаще: {chips}',
  matchedInTitle: 'В этом заголовке уже есть: {chips}',
  bestTime: 'Лучшее время публикации в нише (по времени браузера): {times}',

  overlapGapTitle: 'Уже снято по этому вопросу',
  overlapTopicTitle: 'Уже снято по этой теме',
  overlapSub: 'без самого исходного видео',

  thumbRefsTitle: 'Референсы превью',
  thumbRefsSub: 'похожие видео из вашей базы',
  noThumb: 'без обложки',
  viewsSimilarity: '{views} просмотров · сходство {pct}%',

  savedDraft: 'Сохранено в черновики (№{id}). Дальше — <a href="#/metadata">разбор метаданных</a> и <a href="#/titles">проверка заголовков</a>.',
  saveDraft: 'Сохранить в черновики',
  addLlm: 'Добавить LLM-разбор (может стоить денег)',
  saveNote: 'Сохранение создаёт черновик со ссылкой на это видео и ставит в очередь недостающий транскрипт. Квота YouTube не тратится.',

  similarThumbsTitle: 'Похожие по картинке',
  similarThumbsSub: 'чьи превью выглядят так же — стоит ли повторять этот вид',
  channelSimilarity: '{channel} · сходство {pct}%',
  clipNote: 'CLIP сравнивает стиль и содержимое картинки, а не надписи.',

  // повторяемость формата
  repeatRepeatable: 'формат повторяем',
  repeatMixed: 'сработал не у всех',
  repeatOneOff: 'похоже на разовую удачу',
  repeatUnknown: 'мало данных',
  repeatNoEmbedding: 'у видео ещё нет эмбеддинга — воркер посчитает его в течение часа',
  repeatFewSimilar: 'похожих видео у других каналов собрано мало ({count})',
  repeatStats: 'outlier ≥ ×{hitScore} у {hit} из {total} каналов с похожими видео; медиана лучшего видео канала ×{median}',
  repeatTitle: 'Формат повторяем?',
  repeatSub: 'похожие по смыслу видео других каналов — у кого ещё это сработало',
  similarityPct: 'сходство {pct}%',
  titleOpening: 'Так же («{opening}…») начинают заголовки ещё {count} каналов.',
  repeatCaveat: 'Оценка по собранной базе: «разовая удача» может значить, что каналы, повторившие формат, просто не собраны.',

  trajectoryTitle: 'Как набирало просмотры',
  trajectorySub: 'по снимкам воркера; пунктир — обычное видео этого канала',
  compareOthers: 'Сравнить с другими',

  briefSaved: 'Бриф сохранён в черновики',
  computing: 'Считаю…',
  briefFailed: '<b>Бриф не собрался.</b><br>{error}',
  briefFailedEmpty: 'видео нет в базе — соберите канал и откройте бриф снова',
};
