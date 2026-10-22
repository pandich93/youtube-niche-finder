/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, num, esc, toast, sectionHead, empty, notice, qmark, saturationChip } from '../ui.js';
import { view } from '../shared.js';
import { dict } from '../i18n.js';
import ru from '../i18n/ru/enter.js';
import en from '../i18n/en/enter.js';

const t = dict({ ru, en });

/* ----------------------------------------------- Где заходить (план 27) */

/* Подписи слагаемых переводятся при рендере: terms() вместо константы с текстом. */
const TERM_DEFS = [
  { key: 'demand', labelKey: 'termDemand', fmt: (v) => `×${v}` },
  { key: 'supply', labelKey: 'termSupply', fmt: (v) => `×${v}` },
  { key: 'newcomers', labelKey: 'termNewcomers', fmt: (v) => `${Math.round(v * 100)}%` },
  { key: 'rpm', labelKey: 'termRpm', fmt: (v) => `$${v}` },
  { key: 'template', labelKey: 'termTemplate', fmt: (v) => `${Math.round(v * 100)}%` },
  { key: 'policy', labelKey: 'termPolicy', fmt: (v) => `${Math.round(v * 100)}%` },
];
const terms = () => TERM_DEFS.map((d) => ({ ...d, label: t(d.labelKey) }));
const BAND_CLASS = { high: 'chip-good', medium: 'chip-warn', low: 'chip-bad' };
const REASON_KEY = {
  'insufficient-data': 'reasonInsufficientData',
  'few-signals': 'reasonFewSignals',
};

let ranking = null;
let selected = new Set();
let sort = { key: null, dir: 'desc' };
let comparison = null;

const term = (e, key) => e.breakdown.find((b) => b.key === key);

function sortValue(e, key) {
  if (key === 'score') return e.score;
  if (key === 'videos') return e.videos;
  return term(e, key)?.points ?? null;
}

function sorted(list) {
  if (!sort.key) return list;
  const sign = sort.dir === 'desc' ? -1 : 1;
  return [...list].sort((a, b) => {
    const x = sortValue(a, sort.key), y = sortValue(b, sort.key);
    if (x == null && y == null) return 0;
    if (x == null) return 1;                       // без значения -- всегда в конец
    if (y == null) return -1;
    return (x - y) * sign;
  });
}

function scoreCell(e) {
  if (e.score == null) {
    return `<span class="chip" data-tip="${esc(REASON_KEY[e.reason] ? t(REASON_KEY[e.reason]) : '')}">${t('noScore')}</span>`;
  }
  return `<span class="chip ${BAND_CLASS[e.band] || ''}"><b>${e.score}</b></span>`;
}

function termCell(e, def) {
  const tm = term(e, def.key);
  if (tm.value == null) return '<span class="section-sub" style="display:inline">—</span>';
  return `<span data-tip="${esc(t('termTip', { label: tm.label, value: def.fmt(tm.value), weight: tm.weight }))}">${tm.points}
    <span class="section-sub" style="display:inline">(${esc(def.fmt(tm.value))})</span></span>`;
}

function rankingTable() {
  const defs = terms();
  const head = (key, label, cls = '') =>
    `<th class="${cls} sortable" data-sort="${key}" style="cursor:pointer">${esc(label)}${
      sort.key === key ? (sort.dir === 'desc' ? ' ↓' : ' ↑') : ''}</th>`;
  const rows = sorted(ranking.niches).map((e) => `<tr>
    <td><input type="checkbox" class="enter-pick" data-slug="${esc(e.niche)}"${
      selected.has(e.niche) ? ' checked' : ''}></td>
    <td class="wrap"><a href="#/niche/${esc(e.niche)}">${esc(e.label || e.niche)}</a>
      <div class="section-sub">${esc(e.niche)} · ${t('videosCount', { n: e.videos })}</div></td>
    <td class="num">${scoreCell(e)}</td>
    <td>${saturationChip({ status: e.status, confidence: e.confidence })}</td>
    ${defs.map((d) => `<td class="num">${termCell(e, d)}</td>`).join('')}
  </tr>`).join('');
  return `<div class="table-wrap"><table>
    <thead><tr><th></th>${head('videos', t('colNicheVideos'))}${head('score', t('colScore'), 'num')}<th>${t('colTrend')}</th>
      ${defs.map((d) => head(d.key, d.label, 'num')).join('')}</tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

function compareTable(c) {
  const cols = c.niches;
  const ov = (e, k, f = (v) => num(v)) => (e.overview && e.overview[k] != null ? f(e.overview[k]) : '—');
  const rows = [
    [t('rowScore'), (e) => scoreCell(e)],
    [t('rowTrend'), (e) => saturationChip({ status: e.status, confidence: e.confidence })],
    [t('rowVideosInNiche'), (e) => ov(e, 'video_count')],
    [t('rowChannels'), (e) => ov(e, 'channel_count')],
    [t('rowMedianSubs'), (e) => ov(e, 'median_subscribers')],
    [t('rowMedianOutlier'), (e) => ov(e, 'median_outlier_score', (v) => `${v}x`)],
    [t('rowMedianViews'), (e) => ov(e, 'median_views_per_video')],
    [t('rowShortsShare'), (e) => ov(e, 'shorts_share_percent', (v) => `${v}%`)],
    [t('rowBreakouts'), (e) => ov(e, 'small_channel_breakouts')],
    ...terms().map((d) => [t('rowTerm', { label: d.label }), (e) => termCell(e, d)]),
  ];
  return `<div class="table-wrap"><table>
    <thead><tr><th></th>${cols.map((e) =>
      `<th class="num">${esc(e.label || e.niche)}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(([label, f]) => `<tr><td>${esc(label)}</td>${
      cols.map((e) => `<td class="num">${f(e)}</td>`).join('')}</tr>`).join('')}</tbody>
  </table></div>`;
}

function comparisonSection() {
  if (!comparison) return '';
  return `<div class="card">${sectionHead(t('compareTitle'), t('compareSub'))}
    ${comparison.missing.length ? notice(t('missing', { list: comparison.missing.map(esc).join(', ') }), 'warn') : ''}
    ${comparison.niches.length ? compareTable(comparison) : empty(t('nothingToCompare'))}</div>`;
}

function render() {
  const scored = ranking.niches.filter((n) => n.score != null).length;
  view.innerHTML = `
    <div class="card">
      ${sectionHead(t('title'), t('sub', { scored, total: ranking.niches.length }),
        `<button class="btn btn-ghost btn-sm" id="enterRefresh">${t('recompute')}</button> ${qmark('nicheRank')}`)}
      ${notice(t('explainer'))}
      ${ranking.niches.length ? rankingTable() : empty(t('emptyNiches'))}
      <div style="display:flex;gap:12px;align-items:center;margin-top:12px">
        <button class="btn" id="enterCompare">${t('compareSelected')}</button>
        <span class="section-sub" id="enterPicked">${t('pickHint')}</span>
      </div>
    </div>
    <div id="enterComparison">${comparisonSection()}</div>`;
  const picked = () => {
    $('#enterPicked').textContent = selected.size ? t('picked', { n: selected.size }) : t('pickHint');
  };
  picked();
  view.querySelectorAll('th.sortable').forEach((th) => th.addEventListener('click', () => {
    const key = th.dataset.sort;
    sort = sort.key === key ? { key, dir: sort.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: 'desc' };
    render();
  }));
  view.querySelectorAll('.enter-pick').forEach((box) => box.addEventListener('change', () => {
    if (box.checked && selected.size >= 3) { box.checked = false; toast(t('maxThree'), 'err'); return; }
    if (box.checked) selected.add(box.dataset.slug); else selected.delete(box.dataset.slug);
    picked();
  }));
  $('#enterCompare').addEventListener('click', async () => {
    if (selected.size < 2) { toast(t('pickToast'), 'err'); return; }
    const btn = $('#enterCompare');
    btn.disabled = true; btn.textContent = t('comparing');
    try {
      comparison = await api('/api/niches/compare' + q({ slugs: [...selected].join(',') }));
      $('#enterComparison').innerHTML = comparisonSection();
    } catch (e) {
      toast(e.message, 'err');
    }
    btn.disabled = false; btn.textContent = t('compareSelected');
  });
  $('#enterRefresh').addEventListener('click', async () => {
    $('#enterRefresh').disabled = true;
    ranking = await api('/api/niches/ranking' + q({ refresh: 'true' }));
    render();
  });
}

async function viewEnter() {
  ranking = await api('/api/niches/ranking');
  selected = new Set([...selected].filter((s) => ranking.niches.some((n) => n.niche === s)));
  render();
}

export { viewEnter };
