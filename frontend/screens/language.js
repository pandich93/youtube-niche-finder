/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, compact, mult, esc, toast, sectionHead, empty, notice, table, qmark, state } from '../ui.js';
import { view } from '../shared.js';
import { dict } from '../i18n.js';
import ru from '../i18n/ru/language.js';
import en from '../i18n/en/language.js';

const t = dict({ ru, en });

/* ----------------------------------------------- Другой язык (план 26) */

let pair = { source: '', target: '' };
let filters = { minOutlier: 3, minSimilarity: 0.62 };
let langs = null;
let result = null;

/* Функция, а не константа: подписи переводятся при каждом рендере. */
const verdictLabel = (v) => ({ open: t('verdictOpen'), thin: t('verdictThin'), covered: t('verdictCovered') })[v];
const VERDICT_CLASS = { open: 'chip-good', thin: 'chip-warn', covered: 'chip-bad' };

function verdictChip(v) {
  return `<span class="chip ${VERDICT_CLASS[v] || ''}">${esc(verdictLabel(v) || v)}</span>`;
}

function videoLink(id, title) {
  return `<a href="https://www.youtube.com/watch?v=${esc(id)}" target="_blank" rel="noopener">${esc(title || id)}</a>`;
}

function targetCell(c) {
  if (c.matches.length) {
    return c.matches.map((m) => `<div>${videoLink(m.videoId, m.title)}
      <span class="section-sub" style="display:inline">· ${esc(m.channelTitle || m.channelId)} · ${mult(m.outlierScore)} · ${t('similarity', { value: m.similarity.toFixed(2) })}</span></div>`).join('');
  }
  if (c.nearest) {
    return `<span class="section-sub" style="display:inline">${t('nearestBelow', { value: c.nearest.similarity.toFixed(2) })}</span>`;
  }
  return `<span class="section-sub" style="display:inline">${t('nothingInTarget')}</span>`;
}

function resultSection(r) {
  if (!r) return '';
  const c = r.counts;
  const shown = r.gaps.length;
  return `
    ${r.targetCorpus.hint ? notice(esc(r.targetCorpus.hint), 'warn') : ''}
    <div class="card">
      ${sectionHead(`${r.source} → ${r.target}`,
        t('resultSub', { checked: r.sourceOutliers, found: r.outliersFound,
          videos: r.targetCorpus.videos, channels: r.targetCorpus.channels }), qmark('languageGap'))}
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">
        <span class="chip chip-good">${t('countOpen', { value: c.open })}</span>
        <span class="chip chip-warn">${t('countThin', { value: c.thin })}</span>
        <span class="chip chip-bad">${t('countCovered', { value: c.covered })}</span>
        ${shown < r.sourceOutliers ? `<span class="chip">${t('shown', { value: shown })}</span>` : ''}
      </div>
      ${shown ? table([
        { label: t('colSourceVideo'), wrap: true, render: (g) =>
          `${videoLink(g.videoId, g.title)}<div class="section-sub">${esc(g.channelTitle || g.channelId)} · ${t('views', { views: compact(g.views) })}</div>` },
        { label: 'Outlier', num: true, render: (g) => mult(g.outlierScore) },
        { label: t('colMoreChannels'), num: true, tip: t('moreChannelsTip'),
          render: (g) => g.demand.otherChannelsHit },
        { label: t('colTargetLang'), render: (g) => verdictChip(g.verdict) },
        { label: t('colWhatsThere'), wrap: true, render: targetCell },
      ], r.gaps) : empty(t('noOutliers'))}
      <div class="section-sub" style="margin-top:10px">${esc(r.note)}</div>
    </div>`;
}

function options(selected) {
  return langs.map((l) =>
    `<option value="${esc(l.code)}"${l.code === selected ? ' selected' : ''}>${t('langOption', { code: esc(l.code), n: l.videos })}</option>`).join('');
}

async function viewLanguage() {
  if (!langs) langs = (await api('/api/language-gaps/languages')).languages;
  if (langs.length < 2) {
    view.innerHTML = `<div class="card">${sectionHead(t('title'),
      t('sub'))}
      ${empty(t('singleLanguage'))}</div>`;
    return;
  }
  if (!pair.source || !langs.some((l) => l.code === pair.source)) pair.source = langs[0].code;
  if (!pair.target || pair.target === pair.source || !langs.some((l) => l.code === pair.target)) {
    pair.target = langs.find((l) => l.code !== pair.source).code;
  }
  view.innerHTML = `
    <div class="card">
      ${sectionHead(t('title'), t('sub') +
        (state.niche ? ` · ${t('sourceNiche', { niche: state.niche })}` : ''))}
      <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-end">
        <label style="font-size:12px;color:var(--muted)">${t('source')}<br>
          <select id="lgSource">${options(pair.source)}</select></label>
        <button class="btn btn-ghost btn-sm" id="lgSwap" title="${esc(t('swap'))}">⇄</button>
        <label style="font-size:12px;color:var(--muted)">${t('target')}<br>
          <select id="lgTarget">${options(pair.target)}</select></label>
        <label style="font-size:12px;color:var(--muted)">outlier ≥
          <input type="number" step="0.5" id="lgMinOutlier" value="${filters.minOutlier}" style="width:70px"></label>
        <label style="font-size:12px;color:var(--muted)">${t('minSimilarity')}
          <input type="number" step="0.01" min="0.3" max="0.95" id="lgMinSim" value="${filters.minSimilarity}" style="width:70px"></label>
        <button class="btn" id="lgRun">${t('find')}</button>
      </div>
    </div>
    <div id="lgResult">${resultSection(result)}</div>`;

  $('#lgSwap').addEventListener('click', () => {
    pair = { source: $('#lgTarget').value, target: $('#lgSource').value };
    result = null;
    viewLanguage();
  });
  $('#lgRun').addEventListener('click', async () => {
    pair = { source: $('#lgSource').value, target: $('#lgTarget').value };
    filters = {
      minOutlier: Number($('#lgMinOutlier').value) || 3,
      minSimilarity: Number($('#lgMinSim').value) || 0.62,
    };
    if (pair.source === pair.target) { toast(t('needTwoLangs'), 'err'); return; }
    const btn = $('#lgRun');
    btn.disabled = true; btn.textContent = t('searching');
    try {
      result = await api('/api/language-gaps' + q({
        source: pair.source, target: pair.target, niche: state.niche,
        min_outlier: filters.minOutlier, min_similarity: filters.minSimilarity,
      }));
      $('#lgResult').innerHTML = resultSection(result);
    } catch (e) {
      toast(e.message, 'err');
    }
    btn.disabled = false; btn.textContent = t('find');
  });
}

export { viewLanguage };
