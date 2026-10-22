/* Общий вид результата оценки вступления (план 10) -- для экранов транскриптов и заголовков. */
import { esc } from './ui.js';
import { dict, onLocaleChange } from './i18n.js';
import ru from './i18n/ru/hookView.js';
import en from './i18n/en/hookView.js';

const t = dict({ ru, en });

const LEVEL = {
  strong: ['levelStrong', 'chip-good'],
  ok: ['levelOk', ''],
  weak: ['levelWeak', 'chip-bad'],
  'insufficient-data': ['levelInsufficient', ''],
};

const FEATURE_KEY = {
  question: 'featQuestion',
  number: 'featNumber',
  promise: 'featPromise',
  intrigue: 'featIntrigue',
  secondPerson: 'featSecondPerson',
  firstSentence: 'featFirstSentence',
  pace: 'featPace',
};

/* Экспортируется как HOOK_FEATURE_LABEL (screens/transcripts.js читает его как
   объект): живая привязка, пересобирается при смене языка. */
let FEATURE_LABEL;
function buildFeatureLabels() {
  FEATURE_LABEL = Object.fromEntries(Object.entries(FEATURE_KEY).map(([k, key]) => [k, t(key)]));
}
buildFeatureLabels();
onLocaleChange(buildFeatureLabels);

function featureRow(name, f) {
  const label = FEATURE_LABEL[name] || name;
  if (!f) return `<li style="white-space:normal"><span class="row-sub">${t('notScored', { label: esc(label) })}</span></li>`;
  const evidence = f.evidence && f.evidence.length ? ` <span class="row-sub">(${f.evidence.map(esc).join(', ')})</span>` : '';
  return `<li style="white-space:normal">${t('featureRow', { hit: t(f.hit ? 'hitYes' : 'hitNo'), label: esc(label),
    points: f.points, max: f.max })}${evidence}</li>`;
}

function comparisonHtml(cmp) {
  if (!cmp) return '';
  if (cmp.benchmarkLevel === 'insufficient-data') {
    return `<div class="row-sub">${t('cmpInsufficient')}</div>`;
  }
  const gaps = cmp.featureGaps.length
    ? ` ${t('cmpGaps', { list: cmp.featureGaps.map((n) => esc(FEATURE_LABEL[n] || n)).join(', ') })}` : '';
  return `<div class="row-sub">${t('cmpLine', { your: cmp.yourScore, outlier: cmp.outlierMean, regular: cmp.regularMean })}${gaps}</div>`;
}

function llmHtml(result) {
  if (result.llm) {
    const l = result.llm;
    const list = (items) => `<ul class="digest-list">${(items || []).map((x) => `<li style="white-space:normal">${esc(x)}</li>`).join('')}</ul>`;
    return `<div style="margin-top:10px"><b>${t('llmTitle')}</b>${l.cached ? ` <span class="row-sub">${t('llmCached')}</span>` : ''}
      ${l.works?.length ? `<div class="row-sub">${t('llmWorks')}</div>${list(l.works)}` : ''}
      ${l.improve?.length ? `<div class="row-sub">${t('llmImprove')}</div>${list(l.improve)}` : ''}
      ${l.rewrite ? `<div class="row-sub"><b>${t('llmRewrite')}</b> ${esc(l.rewrite)}</div>` : ''}</div>`;
  }
  return result.llmHint ? `<div class="row-sub" style="margin-top:10px">${esc(result.llmHint)}</div>` : '';
}

/* result -- ответ GET /api/videos/{id}/hook или POST /api/hooks/score */
function hookResultHtml(result) {
  if (!result) return '';
  if (result.hasTranscript === false) return `<div class="row-sub">${esc(result.hint || t('noTranscript'))}</div>`;
  const [levelKey, levelCls] = LEVEL[result.level] || [null, ''];
  const levelLabel = levelKey ? t(levelKey) : result.level;
  if (result.score == null) return `<div class="row-sub">${t('tooLittleText')}</div>`;
  const features = Object.entries(result.features || {}).map(([n, f]) => featureRow(n, f)).join('');
  const penalties = (result.penalties || []).map((p) =>
    `<li style="white-space:normal">${p.points} — ${p.id === 'greeting' ? t('penaltyGreeting')
      : t('penaltyFiller', { text: esc(p.text.split(': ')[1] || '') })}</li>`).join('');
  const tips = (result.tips || []).map((tip) => `<li style="white-space:normal">${esc(tip.text)}</li>`).join('');
  return `
    <div class="hook-result">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <span class="chip ${levelCls}" style="font-size:14px">${result.score} / 100 · ${esc(levelLabel)}</span>
        ${result.hook ? `<span class="row-sub">${t(result.hook.mode === 'timed' ? 'wordsTimed' : 'wordsUntimed', { n: result.hook.words })}</span>` : ''}
      </div>
      ${result.hook?.warning ? `<div class="row-sub" style="color:var(--critical);margin-top:6px">${esc(result.hook.warning)}</div>` : ''}
      ${!result.punctuated ? `<div class="row-sub" style="margin-top:6px">${t('noPunctuation')}</div>` : ''}
      <ul class="digest-list" style="margin-top:8px">${features}</ul>
      ${penalties ? `<div class="row-sub">${t('fillerTitle')}</div><ul class="digest-list">${penalties}</ul>` : ''}
      ${tips ? `<div class="row-sub">${t('tipsTitle')}</div><ul class="digest-list">${tips}</ul>` : ''}
      ${comparisonHtml(result.comparison || result.nicheComparison)}
      ${llmHtml(result)}
      <div class="row-sub" style="margin-top:8px">${t('disclaimer')}</div>
    </div>`;
}

export { hookResultHtml, FEATURE_LABEL as HOOK_FEATURE_LABEL };
