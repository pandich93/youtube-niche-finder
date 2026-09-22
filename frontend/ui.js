/* niche-finder dashboard — vanilla ES modules, без сборки.
   Каждый экран читает те же функции, что и MCP-инструменты, через backend/api.py. */

const $ = (sel, root = document) => root.querySelector(sel);

const state = {
  period: localStorage.getItem('nf.period') || '30d',
  niche: localStorage.getItem('nf.niche') || '',
  route: 'overview',
};

/* ------------------------------------------------------------------ api */

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'content-type': 'application/json' },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* пустой ответ */ }
  if (!res.ok) {
    const msg = data?.detail || data?.error || `HTTP ${res.status}`;
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return data;
}

function q(params) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') p.set(k, v);
  }
  const s = p.toString();
  return s ? `?${s}` : '';
}

/* ------------------------------------------------------------ форматтеры */

const nf = new Intl.NumberFormat('ru-RU');

function num(n) { return n === null || n === undefined ? '—' : nf.format(Math.round(n)); }

function compact(n) {
  if (n === null || n === undefined) return '—';
  const a = Math.abs(n);
  if (a >= 1e9) return (n / 1e9).toFixed(a >= 1e10 ? 0 : 1).replace('.0', '') + 'B';
  if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e7 ? 0 : 1).replace('.0', '') + 'M';
  if (a >= 1e3) return (n / 1e3).toFixed(a >= 1e4 ? 0 : 1).replace('.0', '') + 'K';
  return String(Math.round(n));
}

/* Русские склонения: «1 канал», «3 канала», «12 каналов». */
function plural(n, one, few, many) {
  const a = Math.abs(Math.round(n)) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  if (b === 1) return one;
  return many;
}

function pl(n, one, few, many) { return `${num(n)} ${plural(n, one, few, many)}`; }

function mult(x) { return x === null || x === undefined ? '—' : `${(+x).toFixed(1)}x`; }

function ago(iso) {
  if (!iso) return '—';
  const h = (Date.now() - new Date(iso).getTime()) / 36e5;
  if (!isFinite(h)) return '—';
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} мин назад`;
  if (h < 48) return `${Math.round(h)} ч назад`;
  const d = h / 24;
  if (d < 45) return `${Math.round(d)} дн назад`;
  if (d < 365) return `${Math.round(d / 30)} мес назад`;
  const y = d / 365;
  return `${y.toFixed(y < 2 ? 1 : 0)} г назад`;
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* Дельта всегда со знаком и словом — цвет никогда не единственный носитель смысла. */
function delta(v, unit = '%') {
  if (v === null || v === undefined) return '<span class="chip">нет данных</span>';
  const up = v > 0, flat = Math.abs(v) < 0.05;
  const cls = flat ? '' : up ? 'chip-good' : 'chip-bad';
  const sign = flat ? '' : up ? '▲ +' : '▼ ';
  return `<span class="chip ${cls}">${sign}${(+v).toFixed(1)}${unit}</span>`;
}

/* --------------------------------------------------------------- тултип */

const tip = $('#tooltip');

function showTip(evt, html) {
  tip.innerHTML = html;
  tip.hidden = false;
  const pad = 12, r = tip.getBoundingClientRect();
  let x = evt.clientX + pad, y = evt.clientY + pad;
  if (x + r.width > innerWidth - 8) x = evt.clientX - r.width - pad;
  if (y + r.height > innerHeight - 8) y = evt.clientY - r.height - pad;
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${Math.max(8, y)}px`;
}
function hideTip() { tip.hidden = true; }

/* Делегирование: любой элемент с data-tip получает ховер-подсказку. */
document.addEventListener('mousemove', (e) => {
  const el = e.target.closest?.('[data-tip]');
  if (el) showTip(e, el.dataset.tip); else hideTip();
});
document.addEventListener('mouseleave', hideTip);
document.addEventListener('scroll', hideTip, true);

/* ---------------------------------------------------------------- тосты */

function toast(msg, kind = '') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $('#toasts').append(el);
  setTimeout(() => el.remove(), kind === 'err' ? 9000 : 4500);
}

/* ------------------------------------------------------------ компоненты */

function tile(label, value, note) {
  return `<div class="tile">
    <div class="tile-label">${esc(label)}</div>
    <div class="tile-value">${value}</div>
    ${note ? `<div class="tile-note">${note}</div>` : ''}
  </div>`;
}

function sectionHead(title, sub, actions = '') {
  return `<div class="section-head">
    <div><div class="section-title">${esc(title)}</div>
    ${sub ? `<div class="section-sub">${esc(sub)}</div>` : ''}</div>
    ${actions ? `<div class="section-actions">${actions}</div>` : ''}
  </div>`;
}

function notice(text, kind = '') {
  return `<div class="notice ${kind}">${text}</div>`;
}

function empty(text) { return `<div class="empty">${esc(text)}</div>`; }

/* Горизонтальные бары — одна серия, поэтому легенда не нужна: заголовок называет её. */
function barList(items) {
  if (!items.length) return empty('нет данных');
  const max = Math.max(...items.map((i) => i.value || 0), 1);
  return `<div class="barlist">${items.map((i) => `
    <div class="barrow" data-tip="${esc(i.tip || `${i.name}: ${num(i.value)}`)}">
      <div class="barrow-name">${esc(i.name)}</div>
      <div class="barrow-value">${esc(i.display ?? num(i.value))}</div>
      <div class="bartrack"><div class="barfill" style="width:${Math.max(2, (i.value / max) * 100)}%"></div></div>
    </div>`).join('')}</div>`;
}

function strengthBar(level) {
  return `<div class="strength">${[0, 1, 2, 3]
    .map((i) => `<i class="${i < (level || 0) ? 'on' : ''}"></i>`).join('')}</div>`;
}

function channelRow(c) {
  const subs = c.subscribers === null ? 'подписчики скрыты' : `${compact(c.subscribers)} подписчиков`;
  return `<div class="row">
    <div class="avatar">${esc((c.channelTitle || '?').slice(0, 1).toUpperCase())}</div>
    <div class="row-main">
      <div class="row-title"><a href="#/channel/${esc(c.channelId)}">${esc(c.channelTitle || c.channelId)}</a></div>
      <div class="row-sub">${subs}${c.category ? ` · ${esc(c.category)}` : ''}${
        c.videosInWindow ? ` · ${c.videosInWindow} видео в окне` : ''}</div>
    </div>
    <div class="row-metrics">
      <div class="metric" data-tip="${c.multiplierBasis === 'lifetime-mean'
        ? 'Посчитан против СРЕДНЕГО за всю жизнь канала (формула NexLev).&lt;br&gt;В базе слишком мало его видео для медианной базы — нужно 4+.&lt;br&gt;Такое число завышается одним виральным роликом: соберите канал целиком.'
        : 'Лучший возрастно-нормированный множитель среди видео канала в окне.&lt;br&gt;Считается против медианы предыдущих загрузок этого же канала.'}">
        <div class="metric-value">${mult(c.multiplier)}${
          c.multiplierBasis === 'lifetime-mean' ? '<span class="approx">≈</span>' : ''}</div>
        ${strengthBar(c.strength)}
      </div>
    </div>
  </div>`;
}

function videoCard(v) {
  const thumb = v.thumbnail
    ? `<img src="${esc(v.thumbnail)}" alt="" loading="lazy">`
    : `<div class="thumb-fallback">без обложки</div>`;
  const vph = v.vph24h ?? v.vphLifetime;
  const vphLabel = v.vph24h != null ? 'VPH за 24ч' : 'VPH за всё время';
  return `<article class="vcard">
    <a class="thumb" href="https://www.youtube.com/watch?v=${esc(v.videoId)}" target="_blank" rel="noopener">
      ${thumb}
      ${vph != null ? `<span class="badge" data-tip="${esc(vphLabel)}: просмотров в час">${num(vph)} VPH</span>` : ''}
      ${v.outlierScore != null ? `<span class="badge badge-right" data-tip="Множитель против медианы предыдущих загрузок канала">${mult(v.outlierScore)}</span>` : ''}
    </a>
    <div class="vcard-title">${esc(v.title)}</div>
    <div class="vcard-meta">${compact(v.views)} просмотров · ${ago(v.publishedAt)}</div>
    <div class="vcard-meta"><a href="#/channel/${esc(v.channelId)}">${esc(v.channelTitle || '')}</a>
      · ${compact(v.channelSubscribers)} подп.</div>
    <div class="vcard-chips">
      <span class="chip chip-accent" data-tip="Просмотров на одного подписчика — насколько видео вышло за пределы своей аудитории">VSR ${(+v.viewsPerSubscriber).toFixed(1)}</span>
      ${v.outlierBand ? `<span class="chip">${esc(v.outlierBand)}</span>` : ''}
      ${v.estimatedRpm != null ? `<span class="chip" data-tip="Оценка RPM по категории видео, не измеренная выплата">~$${v.estimatedRpm} RPM</span>` : ''}
      ${v.acceleration != null ? `<span class="chip ${v.acceleration > 1.2 ? 'chip-good' : v.acceleration < 0.8 ? 'chip-bad' : ''}"
        data-tip="Ускорение: VPH сегодня против вчера">${v.acceleration > 1.2 ? '▲' : v.acceleration < 0.8 ? '▼' : '='} ${v.acceleration}</span>` : ''}
    </div>
  </article>`;
}

/* Бейдж AI-разметки канала (этап 03) -- "faceless · voiceover_stock".
   Пусто, если канал ещё не размечен (aiLabels отсутствует/null). */
function aiLabelsBadge(ai) {
  if (!ai || !ai.contentFormat) return '';
  const parts = [ai.isFaceless ? 'faceless' : 'on-camera', ai.contentFormat];
  const tip = `AI-разметка${ai.topic ? ` · тема: ${ai.topic}` : ''}${ai.labeledAt ? ` · ${ago(ai.labeledAt)}` : ''}`;
  return `<span class="chip" data-tip="${esc(tip)}">${esc(parts.join(' · '))}</span>`;
}

function commentList(comments) {
  if (!comments.length) return empty('нет комментариев');
  return `<div class="comments-list">${comments.map((c) => `
    <div class="comment-row">
      <div class="comment-meta">${esc(c.author || '—')} · ${ago(c.publishedAt)} · 👍 ${num(c.likeCount || 0)}</div>
      <div class="comment-text">${esc(c.text || '')}</div>
    </div>`).join('')}</div>`;
}

function table(cols, rows) {
  if (!rows.length) return empty('нет данных');
  return `<div class="table-wrap"><table>
    <thead><tr>${cols.map((c) => `<th class="${c.num ? 'num' : ''}">${esc(c.label)}</th>`).join('')}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${cols.map((c) => {
      const v = c.render ? c.render(r) : r[c.key];
      return `<td class="${c.num ? 'num' : ''}${c.wrap ? ' wrap' : ''}">${v ?? '—'}</td>`;
    }).join('')}</tr>`).join('')}</tbody>
  </table></div>`;
}

/* Линейный график с перекрестием — одна серия, поэтому без легенды. */
function lineChart(points, { height = 180, valueLabel = 'значение' } = {}) {
  if (!points || points.length < 2) return empty('нужно минимум две точки истории');
  const W = 720, H = height, m = { t: 12, r: 14, b: 22, l: 48 };
  const xs = points.map((p) => new Date(p.t).getTime());
  const ys = points.map((p) => p.v);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pad = (y1 - y0) * 0.1 || Math.max(1, y1 * 0.05);
  const lo = Math.max(0, y0 - pad), hi = y1 + pad;
  const px = (t) => m.l + ((t - x0) / (x1 - x0 || 1)) * (W - m.l - m.r);
  const py = (v) => m.t + (1 - (v - lo) / (hi - lo || 1)) * (H - m.t - m.b);
  const d = points.map((p, i) => `${i ? 'L' : 'M'}${px(xs[i]).toFixed(1)},${py(p.v).toFixed(1)}`).join('');
  const area = `${d}L${px(x1).toFixed(1)},${py(lo).toFixed(1)}L${px(x0).toFixed(1)},${py(lo).toFixed(1)}Z`;
  const ticks = [lo, (lo + hi) / 2, hi];
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(valueLabel)} во времени">
    ${ticks.map((t) => `<line class="gridline" x1="${m.l}" x2="${W - m.r}" y1="${py(t).toFixed(1)}" y2="${py(t).toFixed(1)}"/>
      <text x="${m.l - 8}" y="${(py(t) + 4).toFixed(1)}" text-anchor="end">${compact(t)}</text>`).join('')}
    <line class="axis" x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}"/>
    <path class="area" d="${area}"/>
    <path class="line" d="${d}"/>
    ${points.map((p, i) => `<circle class="dot" cx="${px(xs[i]).toFixed(1)}" cy="${py(p.v).toFixed(1)}" r="4"
      data-tip="${esc(new Date(p.t).toLocaleString('ru-RU'))}&lt;br&gt;&lt;b&gt;${num(p.v)}&lt;/b&gt; ${esc(valueLabel)}"/>`).join('')}
    <text x="${m.l}" y="${H - 6}">${esc(new Date(x0).toLocaleDateString('ru-RU'))}</text>
    <text x="${W - m.r}" y="${H - 6}" text-anchor="end">${esc(new Date(x1).toLocaleDateString('ru-RU'))}</text>
  </svg>`;
}

/* Стабильный цвет на channelId -- один и тот же канал везде одного цвета
   без палитры/легенды (в scatter'е их может быть десятки). */
function _channelColor(id) {
  let h = 0;
  for (const c of String(id)) h = (h * 31 + c.charCodeAt(0)) % 360;
  return `hsl(${h} 65% 55%)`;
}

/* Точечный график ниши (этап 15): X — дата публикации, Y — просмотры (лог.
   шкала, иначе один вирусный ролик сплющивает всё остальное в одну линию).
   Полые точки — моложе 30 дней (только выросли, сравнивать рано). Крупный
   контур — аномалия по periodScore (или rolling, если period нет). Свой SVG,
   без сторонних библиотек графиков -- та же техника viewBox, что и lineChart:
   ширина тянется по контейнеру через CSS (.chart{width:100%}), без JS на
   resize. */
function scatterChart(videos, { height = 340, outlierThreshold = 3 } = {}) {
  if (!videos.length) return empty('нет видео в нише за этот период');
  const W = 720, H = height, m = { t: 12, r: 14, b: 26, l: 52 };

  const xs = videos.map((v) => new Date(v.publishedAt).getTime()).filter(Number.isFinite);
  if (!xs.length) return empty('у видео нет дат публикации');
  const x0 = Math.min(...xs), x1 = Math.max(Math.max(...xs), x0 + 86400000);
  const ys = videos.map((v) => Math.log10(Math.max(1, v.views || 0)));
  const y0 = Math.min(...ys), y1 = Math.max(Math.max(...ys), Math.min(...ys) + 1);
  const px = (t) => m.l + ((t - x0) / (x1 - x0 || 1)) * (W - m.l - m.r);
  const py = (logV) => m.t + (1 - (logV - y0) / (y1 - y0 || 1)) * (H - m.t - m.b);

  const points = videos.map((v) => {
    const t = new Date(v.publishedAt).getTime();
    const views = Math.max(1, v.views || 0);
    const score = v.outlierScorePeriod ?? v.outlierScoreRolling ?? v.outlierScore;
    const isNew = (v.ageDays ?? Infinity) < 30;
    const isOutlier = (score || 0) >= outlierThreshold;
    const color = _channelColor(v.channelId);
    const tip = `${esc(v.title)}&lt;br&gt;${esc(v.channelTitle || v.channelId)}&lt;br&gt;`
      + `${num(views)} просмотров · outlier ${score != null ? mult(score) : '—'}&lt;br&gt;`
      + `${Math.round((v.durationSeconds || 0) / 60)} мин${isNew ? ' · моложе 30 дней' : ''}`;
    return { cx: px(t), cy: py(Math.log10(views)), color, isNew, isOutlier, tip };
  }).filter((p) => Number.isFinite(p.cx) && Number.isFinite(p.cy));

  const yTicks = [y0, (y0 + y1) / 2, y1];

  return `<svg class="chart chart-scatter" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="Просмотры видео ниши по датам публикации, логарифмическая шкала">
    ${yTicks.map((t) => `<line class="gridline" x1="${m.l}" x2="${W - m.r}"
        y1="${py(t).toFixed(1)}" y2="${py(t).toFixed(1)}"/>
      <text x="${m.l - 8}" y="${(py(t) + 4).toFixed(1)}" text-anchor="end">${compact(10 ** t)}</text>`).join('')}
    <line class="axis" x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}"/>
    ${points.map((p) => `<circle r="${p.isOutlier ? 6 : 4}" cx="${p.cx.toFixed(1)}" cy="${p.cy.toFixed(1)}"
        fill="${p.isNew ? 'none' : p.color}" stroke="${p.color}" stroke-width="${p.isNew ? 2 : 1}"
        opacity="${p.isOutlier ? 1 : 0.75}" data-tip="${p.tip}"/>`).join('')}
    <text x="${m.l}" y="${H - 6}">${esc(new Date(x0).toLocaleDateString('ru-RU'))}</text>
    <text x="${W - m.r}" y="${H - 6}" text-anchor="end">${esc(new Date(x1).toLocaleDateString('ru-RU'))}</text>
  </svg>`;
}

function funnelBlock(res) {
  if (!res.funnel) return '';
  const rows = res.funnel.map((f) =>
    `<div class="funnel-step"><span>${esc(f.step)}</span><b>${num(f.remaining)}</b></div>`).join('');
  return `<div class="card"><div class="section-sub">Воронка фильтров</div>
    <div class="funnel">${rows}</div>
    ${res.hint ? `<div style="margin-top:12px">${notice(esc(res.hint))}</div>` : ''}</div>`;
}

export { $, api, q, num, compact, mult, ago, esc, delta, plural, pl, toast, tile, sectionHead,
         notice, empty, barList, strengthBar, channelRow, videoCard, table, commentList,
         lineChart, funnelBlock, aiLabelsBadge, scatterChart, state };
