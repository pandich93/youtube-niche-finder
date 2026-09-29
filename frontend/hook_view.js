/* Общий вид результата оценки вступления (план 10) -- для экранов транскриптов и заголовков. */
import { esc } from './ui.js';

const LEVEL = {
  strong: ['сильное', 'chip-good'],
  ok: ['среднее', ''],
  weak: ['слабое', 'chip-bad'],
  'insufficient-data': ['мало текста', ''],
};

const FEATURE_LABEL = {
  question: 'вопрос зрителю',
  number: 'цифра или сумма',
  promise: 'обещание результата',
  intrigue: 'интрига или конфликт',
  secondPerson: 'обращение «ты / вы»',
  firstSentence: 'короткая первая фраза',
  pace: 'темп речи',
};

function featureRow(name, f) {
  const label = FEATURE_LABEL[name] || name;
  if (!f) return `<li style="white-space:normal"><span class="row-sub">${esc(label)}: не оценивается</span></li>`;
  const evidence = f.evidence && f.evidence.length ? ` <span class="row-sub">(${f.evidence.map(esc).join(', ')})</span>` : '';
  return `<li style="white-space:normal">${f.hit ? '✓ есть' : '✗ нет'} — ${esc(label)}: ${f.points} из ${f.max}${evidence}</li>`;
}

function comparisonHtml(cmp) {
  if (!cmp) return '';
  if (cmp.benchmarkLevel === 'insufficient-data') {
    return `<div class="row-sub">Сравнение с нишей: мало данных (нужно 10 outliers и 10 обычных видео с транскриптами).</div>`;
  }
  const gaps = cmp.featureGaps.length
    ? ` У outliers чаще встречается то, чего нет у вас: ${cmp.featureGaps.map((n) => esc(FEATURE_LABEL[n] || n)).join(', ')}.` : '';
  return `<div class="row-sub">Сравнение с нишей: ваш балл ${cmp.yourScore}, у outliers в среднем ${cmp.outlierMean},
    у обычных видео ${cmp.regularMean}.${gaps}</div>`;
}

function llmHtml(result) {
  if (result.llm) {
    const l = result.llm;
    const list = (items) => `<ul class="digest-list">${(items || []).map((x) => `<li style="white-space:normal">${esc(x)}</li>`).join('')}</ul>`;
    return `<div style="margin-top:10px"><b>LLM-разбор</b>${l.cached ? ' <span class="row-sub">(из кэша)</span>' : ''}
      ${l.works?.length ? `<div class="row-sub">Что работает</div>${list(l.works)}` : ''}
      ${l.improve?.length ? `<div class="row-sub">Что улучшить</div>${list(l.improve)}` : ''}
      ${l.rewrite ? `<div class="row-sub"><b>Вариант вступления:</b> ${esc(l.rewrite)}</div>` : ''}</div>`;
  }
  return result.llmHint ? `<div class="row-sub" style="margin-top:10px">${esc(result.llmHint)}</div>` : '';
}

/* result -- ответ GET /api/videos/{id}/hook или POST /api/hooks/score */
function hookResultHtml(result) {
  if (!result) return '';
  if (result.hasTranscript === false) return `<div class="row-sub">${esc(result.hint || 'нет транскрипта')}</div>`;
  const [levelLabel, levelCls] = LEVEL[result.level] || [result.level, ''];
  if (result.score == null) return `<div class="row-sub">Слишком мало текста для оценки.</div>`;
  const features = Object.entries(result.features || {}).map(([n, f]) => featureRow(n, f)).join('');
  const penalties = (result.penalties || []).map((p) =>
    `<li style="white-space:normal">${p.points} — ${p.id === 'greeting' ? 'приветствие в начале текста'
      : `фразы-вода: ${esc(p.text.split(': ')[1] || '')}`}</li>`).join('');
  const tips = (result.tips || []).map((t) => `<li style="white-space:normal">${esc(t.text)}</li>`).join('');
  return `
    <div class="hook-result">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <span class="chip ${levelCls}" style="font-size:14px">${result.score} / 100 · ${esc(levelLabel)}</span>
        ${result.hook ? `<span class="row-sub">${result.hook.words} слов${result.hook.mode === 'timed' ? ', с таймкодами' : ', без таймкодов'}</span>` : ''}
      </div>
      ${result.hook?.warning ? `<div class="row-sub" style="color:var(--critical);margin-top:6px">${esc(result.hook.warning)}</div>` : ''}
      ${!result.punctuated ? '<div class="row-sub" style="margin-top:6px">В тексте почти нет знаков препинания — вопросы по «?» и длина первой фразы не оцениваются.</div>' : ''}
      <ul class="digest-list" style="margin-top:8px">${features}</ul>
      ${penalties ? `<div class="row-sub">Вода в начале</div><ul class="digest-list">${penalties}</ul>` : ''}
      ${tips ? `<div class="row-sub">Что можно попробовать</div><ul class="digest-list">${tips}</ul>` : ''}
      ${comparisonHtml(result.comparison || result.nicheComparison)}
      ${llmHtml(result)}
      <div class="row-sub" style="margin-top:8px">Оценка текста вступления, не визуального крючка; корреляция, не причинность.</div>
    </div>`;
}

export { hookResultHtml, FEATURE_LABEL as HOOK_FEATURE_LABEL };
