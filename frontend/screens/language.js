/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, compact, mult, esc, toast, sectionHead, empty, notice, table, qmark, state } from '../ui.js';
import { view } from '../shared.js';

/* ----------------------------------------------- Другой язык (план 26) */

let pair = { source: '', target: '' };
let filters = { minOutlier: 3, minSimilarity: 0.62 };
let langs = null;
let result = null;

const VERDICT_LABEL = { open: 'не снято', thin: 'снимали слабо', covered: 'уже есть хит' };
const VERDICT_CLASS = { open: 'chip-good', thin: 'chip-warn', covered: 'chip-bad' };

function verdictChip(v) {
  return `<span class="chip ${VERDICT_CLASS[v] || ''}">${esc(VERDICT_LABEL[v] || v)}</span>`;
}

function videoLink(id, title) {
  return `<a href="https://www.youtube.com/watch?v=${esc(id)}" target="_blank" rel="noopener">${esc(title || id)}</a>`;
}

function targetCell(c) {
  if (c.matches.length) {
    return c.matches.map((m) => `<div>${videoLink(m.videoId, m.title)}
      <span class="section-sub" style="display:inline">· ${esc(m.channelTitle || m.channelId)} · ${mult(m.outlierScore)} · сходство ${m.similarity.toFixed(2)}</span></div>`).join('');
  }
  if (c.nearest) {
    return `<span class="section-sub" style="display:inline">ближайшее — сходство ${c.nearest.similarity.toFixed(2)}, ниже порога</span>`;
  }
  return '<span class="section-sub" style="display:inline">на этом языке ничего нет</span>';
}

function resultSection(r) {
  if (!r) return '';
  const c = r.counts;
  const shown = r.gaps.length;
  return `
    ${r.targetCorpus.hint ? notice(esc(r.targetCorpus.hint), 'warn') : ''}
    <div class="card">
      ${sectionHead(`${r.source} → ${r.target}`,
        `${r.sourceOutliers} outlier'ов проверено из ${r.outliersFound} найденных · на целевом языке в базе ` +
        `${r.targetCorpus.videos} видео у ${r.targetCorpus.channels} каналов`, qmark('languageGap'))}
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">
        <span class="chip chip-good">не снято: ${c.open}</span>
        <span class="chip chip-warn">снимали слабо: ${c.thin}</span>
        <span class="chip chip-bad">уже есть хит: ${c.covered}</span>
        ${shown < r.sourceOutliers ? `<span class="chip">показано ${shown}</span>` : ''}
      </div>
      ${shown ? table([
        { label: 'Видео на исходном языке', wrap: true, render: (g) =>
          `${videoLink(g.videoId, g.title)}<div class="section-sub">${esc(g.channelTitle || g.channelId)} · ${compact(g.views)} просмотров</div>` },
        { label: 'Outlier', num: true, render: (g) => mult(g.outlierScore) },
        { label: 'Ещё каналов', num: true, tip: 'Сколько других каналов на исходном языке получили outlier ≥ ×2 с похожим видео',
          render: (g) => g.demand.otherChannelsHit },
        { label: 'Целевой язык', render: (g) => verdictChip(g.verdict) },
        { label: 'Что там', wrap: true, render: targetCell },
      ], r.gaps) : empty('На исходном языке нет outlier\'ов выше порога — снизьте порог или соберите ниши.')}
      <div class="section-sub" style="margin-top:10px">${esc(r.note)}</div>
    </div>`;
}

function options(selected) {
  return langs.map((l) =>
    `<option value="${esc(l.code)}"${l.code === selected ? ' selected' : ''}>${esc(l.code)} · ${l.videos} видео</option>`).join('');
}

async function viewLanguage() {
  if (!langs) langs = (await api('/api/language-gaps/languages')).languages;
  if (langs.length < 2) {
    view.innerHTML = `<div class="card">${sectionHead('Другой язык',
      'формат выстрелил на одном языке — снят ли он на другом')}
      ${empty('В базе видео только на одном языке: соберите каналы на втором языке, и экран заработает.')}</div>`;
    return;
  }
  if (!pair.source || !langs.some((l) => l.code === pair.source)) pair.source = langs[0].code;
  if (!pair.target || pair.target === pair.source || !langs.some((l) => l.code === pair.target)) {
    pair.target = langs.find((l) => l.code !== pair.source).code;
  }
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Другой язык', 'формат выстрелил на одном языке — снят ли он на другом' +
        (state.niche ? ` · ниша исходных видео: ${state.niche}` : ''))}
      <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-end">
        <label style="font-size:12px;color:var(--muted)">Где выстрелило<br>
          <select id="lgSource">${options(pair.source)}</select></label>
        <button class="btn btn-ghost btn-sm" id="lgSwap" title="Поменять местами">⇄</button>
        <label style="font-size:12px;color:var(--muted)">Где проверить<br>
          <select id="lgTarget">${options(pair.target)}</select></label>
        <label style="font-size:12px;color:var(--muted)">outlier ≥
          <input type="number" step="0.5" id="lgMinOutlier" value="${filters.minOutlier}" style="width:70px"></label>
        <label style="font-size:12px;color:var(--muted)">сходство ≥
          <input type="number" step="0.01" min="0.3" max="0.95" id="lgMinSim" value="${filters.minSimilarity}" style="width:70px"></label>
        <button class="btn" id="lgRun">Найти</button>
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
    if (pair.source === pair.target) { toast('Выберите два разных языка', 'err'); return; }
    const btn = $('#lgRun');
    btn.disabled = true; btn.textContent = 'Ищу…';
    try {
      result = await api('/api/language-gaps' + q({
        source: pair.source, target: pair.target, niche: state.niche,
        min_outlier: filters.minOutlier, min_similarity: filters.minSimilarity,
      }));
      $('#lgResult').innerHTML = resultSection(result);
    } catch (e) {
      toast(e.message, 'err');
    }
    btn.disabled = false; btn.textContent = 'Найти';
  });
}

export { viewLanguage };
