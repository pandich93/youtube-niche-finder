/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, num, esc, toast, sectionHead, empty, notice, qmark, saturationChip } from '../ui.js';
import { view } from '../shared.js';

/* ----------------------------------------------- Где заходить (план 27) */

const TERMS = [
  { key: 'demand', label: 'Спрос', fmt: (v) => `×${v}` },
  { key: 'supply', label: 'Предложение', fmt: (v) => `×${v}` },
  { key: 'newcomers', label: 'Новички', fmt: (v) => `${Math.round(v * 100)}%` },
  { key: 'rpm', label: 'RPM', fmt: (v) => `$${v}` },
  { key: 'template', label: 'Шаблонные', fmt: (v) => `${Math.round(v * 100)}%` },
  { key: 'policy', label: 'Сигналы', fmt: (v) => `${Math.round(v * 100)}%` },
];
const BAND_CLASS = { high: 'chip-good', medium: 'chip-warn', low: 'chip-bad' };
const REASON = {
  'insufficient-data': 'мало данных: тренд ниши не посчитать',
  'few-signals': 'мало известных слагаемых',
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
    return `<span class="chip" data-tip="${esc(REASON[e.reason] || '')}">без балла</span>`;
  }
  return `<span class="chip ${BAND_CLASS[e.band] || ''}"><b>${e.score}</b></span>`;
}

function termCell(e, def) {
  const t = term(e, def.key);
  if (t.value == null) return '<span class="section-sub" style="display:inline">—</span>';
  return `<span data-tip="${esc(`${t.label}: ${def.fmt(t.value)}, вес ${t.weight}`)}">${t.points}
    <span class="section-sub" style="display:inline">(${esc(def.fmt(t.value))})</span></span>`;
}

function rankingTable() {
  const head = (key, label, cls = '') =>
    `<th class="${cls} sortable" data-sort="${key}" style="cursor:pointer">${esc(label)}${
      sort.key === key ? (sort.dir === 'desc' ? ' ↓' : ' ↑') : ''}</th>`;
  const rows = sorted(ranking.niches).map((e) => `<tr>
    <td><input type="checkbox" class="enter-pick" data-slug="${esc(e.niche)}"${
      selected.has(e.niche) ? ' checked' : ''}></td>
    <td class="wrap"><a href="#/niche/${esc(e.niche)}">${esc(e.label || e.niche)}</a>
      <div class="section-sub">${esc(e.niche)} · ${num(e.videos)} видео</div></td>
    <td class="num">${scoreCell(e)}</td>
    <td>${saturationChip({ status: e.status, confidence: e.confidence })}</td>
    ${TERMS.map((d) => `<td class="num">${termCell(e, d)}</td>`).join('')}
  </tr>`).join('');
  return `<div class="table-wrap"><table>
    <thead><tr><th></th>${head('videos', 'Ниша / видео')}${head('score', 'Балл', 'num')}<th>Тренд</th>
      ${TERMS.map((d) => head(d.key, d.label, 'num')).join('')}</tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

function compareTable(c) {
  const cols = c.niches;
  const ov = (e, k, f = (v) => num(v)) => (e.overview && e.overview[k] != null ? f(e.overview[k]) : '—');
  const rows = [
    ['Балл', (e) => scoreCell(e)],
    ['Тренд', (e) => saturationChip({ status: e.status, confidence: e.confidence })],
    ['Видео в нише', (e) => ov(e, 'video_count')],
    ['Каналов', (e) => ov(e, 'channel_count')],
    ['Медиана подписчиков', (e) => ov(e, 'median_subscribers')],
    ['Медианный outlier', (e) => ov(e, 'median_outlier_score', (v) => `${v}x`)],
    ['Просмотров на видео (медиана)', (e) => ov(e, 'median_views_per_video')],
    ['Доля Shorts', (e) => ov(e, 'shorts_share_percent', (v) => `${v}%`)],
    ['Прорывов маленьких каналов', (e) => ov(e, 'small_channel_breakouts')],
    ...TERMS.map((d) => [`${d.label} (баллы · значение)`, (e) => termCell(e, d)]),
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
  return `<div class="card">${sectionHead('Сравнение ниш', 'все показатели рядом')}
    ${comparison.missing.length ? notice(`Нет в базе: ${comparison.missing.map(esc).join(', ')}`, 'warn') : ''}
    ${comparison.niches.length ? compareTable(comparison) : empty('нечего сравнивать')}</div>`;
}

function render() {
  const scored = ranking.niches.filter((n) => n.score != null).length;
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Где заходить', `${scored} ниш с баллом из ${ranking.niches.length} · клик по заголовку — сортировка`,
        `<button class="btn btn-ghost btn-sm" id="enterRefresh">Пересчитать</button> ${qmark('nicheRank')}`)}
      ${notice('Балл — взвешенная сумма шести слагаемых (в ячейках: баллы 0–100 и само значение). ' +
        'Веса — наше суждение, RPM — оценка, а не данные YouTube. Ниша без балла — слишком маленькая, ' +
        'чтобы судить: она не «плохая», а не оценена.')}
      ${ranking.niches.length ? rankingTable() : empty('ниш пока нет — соберите первую')}
      <div style="display:flex;gap:12px;align-items:center;margin-top:12px">
        <button class="btn" id="enterCompare">Сравнить выбранные</button>
        <span class="section-sub" id="enterPicked">выберите 2–3 ниши</span>
      </div>
    </div>
    <div id="enterComparison">${comparisonSection()}</div>`;
  const picked = () => {
    $('#enterPicked').textContent = selected.size ? `выбрано: ${selected.size}` : 'выберите 2–3 ниши';
  };
  picked();
  view.querySelectorAll('th.sortable').forEach((th) => th.addEventListener('click', () => {
    const key = th.dataset.sort;
    sort = sort.key === key ? { key, dir: sort.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: 'desc' };
    render();
  }));
  view.querySelectorAll('.enter-pick').forEach((box) => box.addEventListener('change', () => {
    if (box.checked && selected.size >= 3) { box.checked = false; toast('Не больше трёх ниш', 'err'); return; }
    if (box.checked) selected.add(box.dataset.slug); else selected.delete(box.dataset.slug);
    picked();
  }));
  $('#enterCompare').addEventListener('click', async () => {
    if (selected.size < 2) { toast('Выберите 2–3 ниши', 'err'); return; }
    const btn = $('#enterCompare');
    btn.disabled = true; btn.textContent = 'Сравниваю…';
    try {
      comparison = await api('/api/niches/compare' + q({ slugs: [...selected].join(',') }));
      $('#enterComparison').innerHTML = comparisonSection();
    } catch (e) {
      toast(e.message, 'err');
    }
    btn.disabled = false; btn.textContent = 'Сравнить выбранные';
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
