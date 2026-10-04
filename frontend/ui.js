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
  if (res.status === 401 && path !== '/api/auth/login') {
    // План 15: сессия кончилась или её нет -- app.js покажет экран входа.
    dispatchEvent(new CustomEvent('nf:signin-required'));
  }
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

/* RPM-вилка (plan 06): оценка без проверяемого источника, поэтому всегда диапазон. */
function usd(x) { return x == null ? '—' : x >= 10 ? String(Math.round(x)) : (+x).toFixed(1).replace('.0', ''); }
function rpmRange(r) { return r ? `$${usd(r.low)}–${usd(r.high)}` : '—'; }
const RPM_TIP = 'Оценка RPM по категории, не измеренная выплата: середина — модель ниши, '
  + 'вилка ÷2…×2, потому что публичные оценки для одной ниши расходятся до 7 раз';

/* Риск шаблонности (plan 01): эвристика по загрузкам, не вердикт YouTube. */
const RISK_LEVEL = { low: ['низкий', ''], medium: ['средний', 'chip-warn'], high: ['высокий', 'chip-bad'],
                     'insufficient-data': ['мало данных', ''] };
const RISK_REASON = {
  similarity: (v) => `заголовки очень похожи друг на друга (близость ${(+v).toFixed(2)})`,
  templateShare: (v) => `${Math.round(v * 100)}% заголовков повторяют одно начало или конец`,
  durationCv: (v) => `длительность видео почти не меняется (разброс ${(+v).toFixed(2)})`,
  cadenceCv: (v) => `видео выходят с ровным ритмом (разброс интервалов ${(+v).toFixed(2)})`,
};
const RISK_NOTE = 'Эвристика по публичным паттернам загрузок, не решение YouTube: серии, подкасты '
  + 'и музыкальные каналы тоже могут получить высокий балл.';

function riskChip(level) {
  const [label, cls] = RISK_LEVEL[level] || RISK_LEVEL['insufficient-data'];
  return `<span class="chip ${cls}">${label}</span>`;
}

function templateRiskBlock(r) {
  if (!r || !r.found) return '';
  const head = sectionHead('Риск шаблонности', 'насколько последние загрузки похожи на один повторяемый шаблон', qmark('templateRisk'));
  if (r.level === 'insufficient-data') {
    return `<div class="card">${head}<div class="section-sub">Мало данных: нужно не меньше 10 свежих
      видео с эмбеддингами, сейчас ${num(r.videosAnalysed)}.</div></div>`;
  }
  return `<div class="card">${head}
    <div class="tiles">
      ${tile('Балл', `${r.score}<span class="tile-unit"> / 100</span>`, riskChip(r.level))}
      ${tile('Видео в оценке', num(r.videosAnalysed), r.format === 'long-form' ? 'без Shorts' : 'все форматы')}
    </div>
    ${r.reasons.length ? `<ul class="digest-list">${r.reasons.map((x) =>
      `<li style="white-space:normal">${esc((RISK_REASON[x.signal] || (() => x.text))(x.value))}</li>`).join('')}</ul>`
      : '<div class="section-sub" style="margin-top:8px">Ни один сигнал шаблонности не выражен.</div>'}
    <div class="section-sub" style="margin-top:8px">${RISK_NOTE}</div></div>`;
}

/* План 22: сигналы по трём категориям «неаутентичного» контента из правил
   монетизации YouTube. Уровни, причины и текст правила -- без общего «процента
   риска»: решение принимает YouTube, вручную. */
const POLICY_CAT = {
  generic_repetitive: 'Шаблонный, повторяющийся контент',
  unsatisfying: 'Шок и эмоциональные манипуляции',
  ai_persona_sensitive: 'ИИ-персона в чувствительной теме',
};
const POLICY_LEVEL = { high: ['chip-bad', 'заметные сигналы'], watch: ['', 'стоит присмотреться'],
  none: ['chip-good', 'сигналов нет'], 'insufficient-data': ['', 'мало данных'] };
/* Пересказ правил YouTube по-русски; оригинал -- по ссылке «справка YouTube». */
const POLICY_TEXT = {
  generic_repetitive: 'видео, которые кажутся взаимозаменяемыми; сделанные по типовому или неоригинальному шаблону',
  unsatisfying: 'контент, который держится на эмоциональных манипуляциях или сделан ради шока',
  ai_persona_sensitive: 'ИИ-персоны, которые дают советы по здоровью, праву, финансам или политике как эксперты',
};
const POLICY_TOPIC = { health: 'здоровье', legal: 'право', finance: 'финансы', politics: 'политику' };
const POLICY_REASON = {
  thumbSimilarity: (x) => `обложки похожи друг на друга (близость ${(+x.value).toFixed(2)})`,
  shockShare: (x) => `${Math.round(x.value * 100)}% свежих заголовков с шок-маркерами`,
  sensitiveShare: (x) => `${Math.round(x.value * 100)}% свежих видео про ${POLICY_TOPIC[x.topic] || x.topic}`,
  syntheticShare: (x) => `${Math.round(x.value * 100)}% видео с раскрытым ИИ-контентом`,
  faceless: () => 'канал размечен как безликий',
};
function policyReason(x) {
  if (POLICY_REASON[x.signal]) return POLICY_REASON[x.signal](x);
  if (RISK_REASON[x.signal]) return RISK_REASON[x.signal](x.value);
  return x.text || '';
}
const POLICY_NOTE = 'Это сигналы, видимые по открытым данным, а не решение YouTube: монетизацию проверяют люди. '
  + 'ИИ-персону по открытым данным не увидеть, поэтому эта категория не бывает выше «стоит присмотреться».';

function policyBlock(r) {
  if (!r || !r.found) return '';
  const rows = Object.entries(r.categories).map(([k, c]) => {
    const [cls, label] = POLICY_LEVEL[c.level] || ['', c.level];
    return `<div class="row"><div class="row-main">
      <div class="row-title">${esc(POLICY_CAT[k] || k)} <span class="chip ${cls}">${esc(label)}</span></div>
      <div class="row-sub">${c.reasons.length ? esc(c.reasons.map(policyReason).join('; ')) : ''}${
        c.examples.length && c.level !== 'none' ? `${c.reasons.length ? ' · ' : ''}например: «${esc(c.examples[0])}»` : ''}</div>
      <div class="row-sub">Правило: ${esc(POLICY_TEXT[k] || c.policy)} · <a href="${esc(c.policyUrl)}" target="_blank" rel="noopener">справка YouTube</a></div>
    </div></div>`;
  }).join('');
  return `<div class="card">${sectionHead('Сигналы по правилам монетизации', 'три категории «неаутентичного» контента YouTube', qmark('policySignals'))}
    <div class="rows">${rows}</div><div class="section-sub" style="margin-top:8px">${esc(POLICY_NOTE)}</div></div>`;
}

function nichePolicyBlock(n) {
  if (!n || !n.found) return '';
  return `<div class="card">${sectionHead('Сигналы по правилам монетизации в нише', 'сколько каналов ниши показывают сигналы каждой категории', qmark('policySignals'))}
    ${table([
      { label: 'Категория', wrap: true, render: (r) => esc(POLICY_CAT[r.key] || r.key) },
      { label: 'Заметные', num: true, render: (r) => num(r.high) },
      { label: 'Присмотреться', num: true, render: (r) => num(r.watch) },
      { label: 'Доля', num: true, render: (r) => (r.shareFlagged == null ? '—' : `${Math.round(r.shareFlagged * 100)}%`) },
      { label: 'Каналы', wrap: true, render: (r) => r.channels.map((c) =>
        `<a href="#/channel/${esc(c.channelId)}">${esc(c.channelTitle || c.channelId)}</a>`).join(', ') || '—' },
    ], Object.entries(n.categories).map(([key, c]) => ({ key, ...c })))}
    <div class="section-sub" style="margin-top:8px">${esc(POLICY_NOTE)}</div></div>`;
}

function nicheTemplateRiskBlock(n) {
  if (!n || !n.found) return '';
  const head = sectionHead('Риск шаблонности каналов ниши',
    'какая доля каналов выглядит как конвейер — там, где их много, копировать формат опасно');
  if (!n.channelsAnalysed) {
    return `<div class="card">${head}<div class="section-sub">Мало данных: ни у одного канала нет 10+ свежих видео.</div></div>`;
  }
  return `<div class="card">${head}
    <div class="tiles">
      ${tile('Высокий риск', `${n.highRiskSharePercent}%`, `${num(n.levels.high)} из ${num(n.channelsAnalysed)} каналов`)}
      ${tile('Средний', num(n.levels.medium))}
      ${tile('Низкий', num(n.levels.low))}
      ${tile('Мало данных', num(n.channelsInsufficient), 'меньше 10 видео')}
    </div>
    ${table([
      { label: 'Самые шаблонные каналы', wrap: true, render: (c) => `<a href="#/channel/${esc(c.channelId)}">${esc(c.title || c.channelId)}</a>` },
      { label: 'Подписчиков', num: true, render: (c) => compact(c.subscribers) },
      { label: 'Балл', num: true, render: (c) => num(c.score) },
      { label: 'Риск', render: (c) => riskChip(c.level) },
    ], n.mostTemplated)}
    <div class="section-sub" style="margin-top:8px">${RISK_NOTE}</div></div>`;
}

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

/* data-tip -- атрибут: браузер раскодирует &lt; обратно в «<», и название видео
   вида <img onerror=…> стало бы разметкой. Поэтому всё экранируется заново, а
   разрешены только <br> и <b>, которыми подсказки оформлены. */
function safeTip(text) {
  return esc(text).replace(/&lt;(\/?)(br|b)\s*\/?&gt;/g, '<$1$2>');
}

function showTip(evt, html) {
  tip.innerHTML = safeTip(html);
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

/* План 20: «сравнить» на карточке видео — в корзину сравнения и на её экран. */
document.addEventListener('click', (e) => {
  const el = e.target.closest?.('[data-compare]');
  if (!el) return;
  e.preventDefault();
  location.hash = compareHref(el.dataset.compare);
});

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
      ${vph != null ? `<span class="badge" data-tip="${scoreTip('vph', vphLabel)}">${num(vph)} VPH</span>` : ''}
      ${v.outlierScore != null ? `<span class="badge badge-right" data-tip="${scoreTip('outlierScore')}">${mult(v.outlierScore)}</span>` : ''}
    </a>
    <div class="vcard-title">${esc(v.title)}</div>
    <div class="vcard-meta">${compact(v.views)} просмотров · ${ago(v.publishedAt)}
      · <a href="#/brief/${esc(v.videoId)}" data-tip="Собрать бриф для своего видео из этого outlier">бриф</a>
      · <a href="#" data-compare="${esc(v.videoId)}" data-tip="Сравнить траекторию просмотров с другими видео (до 5)">сравнить</a></div>
    <div class="vcard-meta"><a href="#/channel/${esc(v.channelId)}">${esc(v.channelTitle || '')}</a>
      · ${compact(v.channelSubscribers)} подп.</div>
    <div class="vcard-chips">
      <span class="chip chip-accent est" data-tip="${scoreTip('vsr')}">VSR ${(+v.viewsPerSubscriber).toFixed(1)}</span>
      ${v.outlierBand ? `<span class="chip">${esc(v.outlierBand)}</span>` : ''}
      ${v.estimatedRpmRange ? `<span class="chip" data-tip="${RPM_TIP}">~${rpmRange(v.estimatedRpmRange)} RPM</span>`
        : v.estimatedRpm != null ? `<span class="chip" data-tip="${RPM_TIP}">~$${v.estimatedRpm} RPM</span>` : ''}
      ${v.acceleration != null ? `<span class="chip ${v.acceleration > 1.2 ? 'chip-good' : v.acceleration < 0.8 ? 'chip-bad' : ''}"
        data-tip="${scoreTip('acceleration')}">${v.acceleration > 1.2 ? '▲' : v.acceleration < 0.8 ? '▼' : '='} ${v.acceleration}</span>` : ''}
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
    <thead><tr>${cols.map((c) => `<th class="${c.num ? 'num' : ''}">${esc(c.label)}${c.tip ? ` ${qmark(c.tip)}` : ''}</th>`).join('')}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${cols.map((c) => {
      const v = c.render ? c.render(r) : r[c.key];
      return `<td class="${c.num ? 'num' : ''}${c.wrap ? ' wrap' : ''}">${v ?? '—'}</td>`;
    }).join('')}</tr>`).join('')}</tbody>
  </table></div>`;
}

/* С 24.08.2026 YouTube засчитывает просмотр с первого кадра (план 18): снимки
   по обе стороны этой даты считались по разным правилам. */
const VIEW_COUNT_CHANGE = { t: '2026-08-24T00:00:00Z', label: 'YouTube изменил подсчёт просмотров' };

/* Линейный график с перекрестием — одна серия, поэтому без легенды.
   marks: [{t, label}] — вертикальные отметки дат, рисуются только внутри оси X. */
function lineChart(points, { height = 180, valueLabel = 'значение', marks = [] } = {}) {
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
    ${marks.map((mk) => ({ ...mk, x: new Date(mk.t).getTime() }))
      .filter((mk) => mk.x > x0 && mk.x < x1)
      .map((mk) => `<line class="mark" x1="${px(mk.x).toFixed(1)}" x2="${px(mk.x).toFixed(1)}" y1="${m.t}" y2="${H - m.b}"
        data-tip="${esc(new Date(mk.x).toLocaleDateString('ru-RU'))}&lt;br&gt;${esc(mk.label)}"/>`).join('')}
    ${points.map((p, i) => `<circle class="dot" cx="${px(xs[i]).toFixed(1)}" cy="${py(p.v).toFixed(1)}" r="4"
      data-tip="${esc(new Date(p.t).toLocaleString('ru-RU'))}&lt;br&gt;&lt;b&gt;${num(p.v)}&lt;/b&gt; ${esc(valueLabel)}"/>`).join('')}
    <text x="${m.l}" y="${H - 6}">${esc(new Date(x0).toLocaleDateString('ru-RU'))}</text>
    <text x="${W - m.r}" y="${H - 6}" text-anchor="end">${esc(new Date(x1).toLocaleDateString('ru-RU'))}</text>
  </svg>`;
}

/* План 20: траектории видео — просмотры по возрасту (часы с публикации) из снимков
   воркера. У каждого видео свой цвет; пунктир того же цвета — ожидаемая кривая канала
   (медиана канала × кривая взросления). Ось X — возраст, а не дата: так видео разных
   дней сравниваются в одном возрасте. Отметки — смена заголовка/обложки. */
const SERIES = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)', 'var(--series-5)'];
const MARK_LABEL = { title: 'сменили заголовок', thumbnail: 'сменили обложку',
  thumbnail_image: 'сменили обложку', view_count_change: 'YouTube изменил подсчёт просмотров' };

function ageLabel(h) {
  return h < 48 ? `${Math.round(h)} ч` : `${Math.round(h / 24)} дн`;
}

function trajectoryChart(videos, { height = 260 } = {}) {
  const withPts = (videos || []).filter((v) => v.points.length >= 2);
  if (!withPts.length) return empty('нужно минимум два снимка — воркер снимает видео каждые 3 часа первую неделю');
  const W = 720, H = height, m = { t: 12, r: 14, b: 26, l: 52 };
  const all = withPts.flatMap((v) => [...v.points, ...v.expected]);
  const x1 = Math.max(...withPts.flatMap((v) => v.points.map((p) => p.ageHours)), 1);
  const y1 = Math.max(...all.filter((p) => p.ageHours <= x1 * 1.05).map((p) => p.views), 1) * 1.08;
  const px = (h) => m.l + (Math.min(h, x1) / x1) * (W - m.l - m.r);
  const py = (v) => m.t + (1 - v / y1) * (H - m.t - m.b);
  const path = (pts) => pts.filter((p) => p.ageHours <= x1).map((p, i) =>
    `${i ? 'L' : 'M'}${px(p.ageHours).toFixed(1)},${py(p.views).toFixed(1)}`).join('');
  const ticks = [0, y1 / 2, y1];
  const xticks = [0, x1 / 2, x1];
  const series = withPts.map((v, i) => {
    const c = SERIES[i % SERIES.length];
    return `${v.expected.length ? `<path class="tline expected" d="${path(v.expected)}" style="stroke:${c}"/>` : ''}
      <path class="tline" d="${path(v.points)}" style="stroke:${c}"/>
      ${v.points.map((p) => `<circle class="tdot" cx="${px(p.ageHours).toFixed(1)}" cy="${py(p.views).toFixed(1)}" r="3"
        style="fill:${c}" data-tip="${esc(v.title || v.videoId)}&lt;br&gt;${esc(ageLabel(p.ageHours))}: &lt;b&gt;${num(p.views)}&lt;/b&gt; просмотров"/>`).join('')}
      ${v.marks.filter((mk) => mk.ageHours >= 0 && mk.ageHours <= x1).map((mk) => `<line class="mark" x1="${px(mk.ageHours).toFixed(1)}"
        x2="${px(mk.ageHours).toFixed(1)}" y1="${m.t}" y2="${H - m.b}" style="stroke:${c}"
        data-tip="${esc(v.title || v.videoId)}&lt;br&gt;${esc(ageLabel(mk.ageHours))}: ${esc(MARK_LABEL[mk.kind] || mk.kind)}"/>`).join('')}`;
  }).join('');
  const legend = withPts.map((v, i) => `<span><i style="background:${SERIES[i % SERIES.length]}"></i>${esc(v.title || v.videoId)}${
    v.observedFromHours > 24 ? ` · наблюдаем с ${esc(ageLabel(v.observedFromHours))}` : ''}</span>`).join('');
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="просмотры по возрасту видео">
    ${ticks.map((t) => `<line class="gridline" x1="${m.l}" x2="${W - m.r}" y1="${py(t).toFixed(1)}" y2="${py(t).toFixed(1)}"/>
      <text x="${m.l - 8}" y="${(py(t) + 4).toFixed(1)}" text-anchor="end">${compact(t)}</text>`).join('')}
    <line class="axis" x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}"/>
    ${xticks.map((h, i) => `<text x="${px(h).toFixed(1)}" y="${H - 8}" text-anchor="${['start', 'middle', 'end'][i]}">${esc(ageLabel(h))}</text>`).join('')}
    ${series}
  </svg>
  <div class="chart-legend">${legend}${withPts.some((v) => v.expected.length)
    ? '<span><i style="background:var(--muted)"></i>пунктир — ожидание по каналу (оценка)</span>'
    : '<span>ожидания по каналу нет: у канала собрано меньше 4 видео</span>'}</div>`;
}

/* Корзина сравнения (план 20): до 5 видео в localStorage; ссылка «сравнить»
   открывает #/compare с этим видео и теми, что уже в корзине. */
function compareHref(videoId) {
  let ids = [];
  try { ids = JSON.parse(localStorage.getItem('nf.compare') || '[]'); } catch { ids = []; }
  ids = [...ids.filter((x) => x !== videoId), videoId].slice(-5);
  return `#/compare/${ids.map(encodeURIComponent).join(',')}`;
}

/* План 23: каталог оценок -- что каждое число: данные YouTube или оценка
   niche-finder, формула и с какой выборки она что-то значит. Грузится один раз
   при старте (/api/scores); до загрузки подсказка -- запасной текст. */
let SCORES = null;

async function loadScores() {
  try { SCORES = (await api('/api/scores')).scores; } catch { SCORES = {}; }
}

/* «?» рядом с числом: подсказка из каталога (что это, данные YouTube или оценка). */
function qmark(key) {
  return `<span class="qmark" data-tip="${scoreTip(key)}" aria-label="как считается">?</span>`;
}

function scoreTip(key, lead = '') {
  const e = SCORES?.[key];
  if (!e) return esc(lead);
  const src = e.source === 'youtube' ? 'данные YouTube' : 'оценка niche-finder, не данные YouTube';
  return [lead ? esc(lead) : '', `&lt;b&gt;${esc(e.name)}&lt;/b&gt; — ${esc(src)}`, esc(e.formula),
    e.minSample ? `нужно: ${esc(e.minSample)}` : ''].filter(Boolean).join('&lt;br&gt;');
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

/* Карта спонсоров (plan 09) -- один блок для ниши и канала. Данные из
   /api/niches/{slug}/sponsors и /api/channels/{id}/sponsors. Всегда подписан
   как нижняя граница: видно только то, что указано в описании видео. */
const SPONSOR_FLOOR = 'Нижняя граница: только то, что указано в описании видео. '
  + 'Интеграция, которую автор лишь произносит в ролике, здесь не видна.';

function sponsorBrandBars(brands) {
  return barList((brands || []).map((b) => ({
    name: b.brand, value: b.videos,
    display: `${num(b.videos)} ${plural(b.videos, 'видео', 'видео', 'видео')}`
      + (b.channels > 1 ? ` · ${num(b.channels)} ${plural(b.channels, 'канал', 'канала', 'каналов')}` : ''),
    tip: `${esc(b.brand)}: ${num(b.videos)} видео, ${num(b.channels)} кан.`
      + (b.lastSeen ? `<br>последнее: ${esc(String(b.lastSeen).slice(0, 10))}` : '')
      + (b.examples || []).slice(0, 2).map((e) =>
        `<br>· ${esc(String(e.title || e.videoId).slice(0, 60))}`).join(''),
  })));
}

function sponsorBlock(d, title, sub) {
  const head = sectionHead(title, sub);
  if (!d) return `<div class="card">${head}${empty('не удалось загрузить данные о спонсорах')}</div>`;
  if (d.found === false) return `<div class="card">${head}${empty(d.hint || 'данных пока нет')}</div>`;
  if (!d.videos) return `<div class="card">${head}${empty('в выбранном окне нет видео')}
    <div class="section-sub" style="margin-top:10px">${esc(SPONSOR_FLOOR)}</div></div>`;
  const pct = (x) => `${Math.round((x || 0) * 100)}%`;
  const cover = d.scanCoverage < 1
    ? notice(`Описания просканированы у ${pct(d.scanCoverage)} видео — воркер дочитает остальные `
      + 'при следующем запуске шага sponsors. Доли считаются по просканированным.') : '';
  const brands = d.topBrands || [];
  const aff = d.affiliateBrands || [];
  return `<div class="card">${head}${cover}
    <div class="tiles">
      ${tile('Видео со спонсором', pct(d.sponsorShare),
        `${num(d.videosWithSponsor)} из ${num(d.videos)}`)}
      ${tile('Просмотры со спонсором', compact(d.avgViewsWithSponsor),
        `среднее · медиана ${compact(d.medianViewsWithSponsor)}`)}
      ${tile('Просмотры без спонсора', compact(d.avgViewsWithout),
        `среднее · медиана ${compact(d.medianViewsWithout)}`)}
      ${tile('Партнёрские ссылки', pct(d.affiliateShare),
        `${num(d.videosWithAffiliate)} видео`)}
    </div>
    <div class="grid-2" style="margin-top:16px">
      <div><div class="section-sub" style="margin-bottom:8px">Бренды-спонсоры и промокоды</div>
        ${brands.length ? sponsorBrandBars(brands) : empty('спонсоров в описаниях не нашлось')}</div>
      <div><div class="section-sub" style="margin-bottom:8px">Партнёрские ссылки (не спонсорство)</div>
        ${aff.length ? sponsorBrandBars(aff) : empty('партнёрских ссылок не нашлось')}</div>
    </div>
    <div class="section-sub" style="margin-top:12px">${esc(SPONSOR_FLOOR)}</div>
  </div>`;
}

/* Тренд насыщенности ниши (план 08): последние 30 дней против 90 до них.
   Две точки -- это «было → стало» в плитке с изменением, а не график. Статус --
   бейдж с подписью, не один цвет. */
const SAT_STATUS = {
  growing: ['растёт', 'chip-good'],
  stable: ['держится', ''],
  cooling: ['остывает', 'chip-warn'],
  saturated: ['забита', 'chip-bad'],
  'insufficient-data': ['мало данных', ''],
};
const SAT_REASON = {
  'supply-up': 'видео выходит больше, чем раньше',
  'supply-down': 'видео выходит меньше, чем раньше',
  'supply-flat': 'видео выходит столько же',
  'demand-up': 'просмотры новых видео выше, чем раньше',
  'demand-down': 'просмотры новых видео ниже, чем раньше',
  'demand-flat': 'просмотры новых видео на прежнем уровне',
  'demand-unknown': 'просмотры сравнить не по чему',
  'entrants-up': 'новых каналов появляется больше',
  'entrants-down': 'новых каналов появляется меньше',
  'entrants-flat': 'новые каналы появляются с прежней скоростью',
  'entrants-unknown': 'даты создания каналов неизвестны',
  'newcomers-break-out': 'новички часто выстреливают',
  'newcomers-some': 'новички выстреливают иногда',
  'newcomers-rarely-break-out': 'новички почти не выстреливают',
  'newcomers-unknown': 'молодых каналов слишком мало для вывода',
  'supply-unknown': 'объём сравнить не по чему',
  'few-videos': 'меньше 20 видео в одном из окон — тренд не считаем',
  'low-coverage': 'большинство видео мы не застали молодыми — тренд может быть артефактом сбора',
};

function saturationChip(s) {
  if (!s) return '';
  const [label, cls] = SAT_STATUS[s.status] || [s.status, ''];
  const low = s.confidence === 'low' && s.status !== 'insufficient-data';
  return `<span class="chip ${cls}" data-tip="${scoreTip('nicheTrend', low ? 'низкая уверенность: мало видео застали молодыми' : '')}">${esc(label)}${low ? ' ?' : ''}</span>`;
}

function saturationBlock(s) {
  if (!s) return '';
  const sig = s.signals;
  const x = (r) => (r == null ? '' : ` · ×${r}`);
  const reasons = (s.reasons || []).map((r) => SAT_REASON[r.code] || r.code);
  return `<div class="card">
    ${sectionHead('Тренд ниши', `последние 30 дней против 90 до них · ${s.format === 'short' ? 'Shorts' : 'длинные видео'}`,
      saturationChip(s))}
    <div class="tiles">
      ${tile('Видео за 30 дней', `${num(sig.supply.basePer30d)} → ${num(sig.supply.recent)}`,
        `было в среднем → сейчас${x(sig.supply.ratio)}`)}
      ${tile('Просмотры новых видео', `${compact(sig.demand.baseMedian)} → ${compact(sig.demand.recentMedian)}`,
        `медиана прогноза на 30-й день${x(sig.demand.ratio)}`)}
      ${tile('Новые каналы за 30 дней', `${num(sig.entrants.basePer30d)} → ${num(sig.entrants.recent)}`,
        sig.entrants.unknownAge ? `без даты создания: ${num(sig.entrants.unknownAge)}` : 'созданы в окне')}
      ${tile('Новички выстреливают', sig.newcomers.share == null ? '—' : `${Math.round(sig.newcomers.share * 100)}%`,
        `${num(sig.newcomers.brokeOut)} из ${num(sig.newcomers.channels)} каналов младше 180 дней, множитель ≥ 2`)}
    </div>
    <ul class="digest-list" style="margin-top:10px">${reasons.map((r) => `<li style="white-space:normal">${esc(r)}</li>`).join('')}</ul>
    <div class="section-sub">Видео: ${num(s.counts.recent)} за 30 дней и ${num(s.counts.base)} за 90 до них.
      ${sig.coverage.caughtYoungShare != null ? `Застали молодыми: ${Math.round(sig.coverage.caughtYoungShare * 100)}%.` : ''}
      Считается только по собранному здесь.</div>
  </div>`;
}

/* Пороги YPP (план 12): что канал видимо проходит по открытым данным. Это не
   статус монетизации -- YouTube его не публикует, и это сказано рядом. */
function yppText(e) {
  if (!e) return '';
  const full = e.tiers?.full || {};
  const exp = e.tiers?.expanded || {};
  const shortsSeen = compact(full.shortsViews90d?.seenAtLeast || 0);
  return {
    'below-threshold': 'не достигнут: меньше 500 подписчиков, ни в одну программу YPP канал попасть не может',
    'subscribers-met': `по подписчикам достигнут (${full.subscribers ? 'от 1000' : 'от 500'}); часы просмотра через API не узнать, `
      + `загрузок за 90 дней видно ${exp.uploads90d?.seen ?? 0}, просмотров Shorts — не меньше ${shortsSeen}`,
    'shorts-path-met': `достигнут через Shorts: не меньше ${shortsSeen} просмотров Shorts за 90 дней`,
    unknown: 'неизвестно: число подписчиков скрыто',
  }[e.status] || e.status;
}

/* План 17: с 01.02.2027 полный уровень YPP — 8 000 часов за 365 дней или
   20 млн просмотров Shorts за 90 дней (1 000 подписчиков остаётся). */
const YPP_2027 = 'с 01.02.2027 полный уровень потребует 8 000 часов за 365 дней или 20 млн просмотров Shorts за 90 дней';

function yppLine(e) {
  if (!e) return '';
  const next = e.upcoming
    ? `<br><b>Правила 2027:</b> ${YPP_2027}; по видимым данным — ${
      e.upcoming.status === e.status ? 'то же самое' : esc(yppText(e.upcoming))}.`
    : e.rules === '2027' ? ' Действуют правила YPP с 01.02.2027.' : '';
  return `<div class="section-sub" style="margin-top:8px"><b>Порог YPP</b> ${qmark('ypp')}: ${esc(yppText(e))}.
    Это не статус монетизации — YouTube его не публикует; загрузки и Shorts считаются по собранным видео.${next}</div>`;
}

/* Рубежи подписчиков (план 17): прямая по темпу наших снимков — оценка, не данные YouTube. */
const MILESTONE_REASON = {
  'few-snapshots': 'мало снимков', 'short-history': 'история короче недели',
  'no-growth': 'роста нет', 'too-slow': 'дольше 10 лет при таком темпе',
};

function milestoneText(f) {
  if (f.reached) return `${num(f.target)} — уже есть`;
  const d = (iso) => new Date(iso).toLocaleDateString('ru-RU');
  if (f.eta30 || f.eta90) {
    const parts = [];
    if (f.eta30) parts.push(`≈ ${d(f.eta30)} по темпу 30 дней (+${num(f.pace30PerDay)}/день)`);
    if (f.eta90) parts.push(`≈ ${d(f.eta90)} по темпу 90 дней`);
    return `${num(f.target)}: ${parts.join(', ')}`;
  }
  return `${num(f.target)}: нельзя оценить — ${MILESTONE_REASON[f.reason] || f.reason}`;
}

function milestonesLine(ms) {
  if (!ms || !ms.forecasts?.length) return '';
  const ypp = ms.ypp1000BeforeRules2027 === true ? ' 1 000 подписчиков — до смены правил YPP 01.02.2027.'
    : ms.ypp1000BeforeRules2027 === false ? ' 1 000 подписчиков — уже после смены правил YPP 01.02.2027.' : '';
  return `<div class="section-sub" style="margin-top:8px"><b>Рубежи</b> ${qmark('milestones')}:
    ${ms.forecasts.map((f) => esc(milestoneText(f))).join('; ')}.${ypp}
    Оценка niche-finder по темпу наших снимков, не данные YouTube.</div>`;
}

/* Фильтр поиска по порогам YPP (plan 12): пороги, которые канал видимо прошёл. */
const YPP_TIP = 'Не статус монетизации: YouTube его не публикует. Только пороги YPP, видимые по публичным данным; каналы со скрытым числом подписчиков не проходят';

function yppSelect(id, cur) {
  const opt = (v, l) => `<option value="${v}"${(cur || '') === v ? ' selected' : ''}>${l}</option>`;
  return `<label class="field" data-tip="${YPP_TIP}"><span class="field-label">Пороги YPP</span>
    <select id="${id}">${opt('', 'не важно')}${opt('subscribers-met', 'подписчики набраны')}${
      opt('shorts-path-met', 'порог пройден по Shorts')}</select></label>`;
}

/* Партнёры для коллабораций (план 31): похожие каналы вашего размера, активные и
   не шаблонные. Контактов YouTube не даёт -- только ссылки на каналы. */
const COLLAB_EXCLUDED = { 'off-topic': 'не по теме', 'size-unknown': 'размер скрыт', 'too-small': 'меньше',
  'too-big': 'крупнее', inactive: 'не выпускали', templated: 'шаблонные' };

async function mountCollabs(box, channelId, { bare = false } = {}) {
  if (!box) return;
  // bare: без своей карточки и заголовка -- когда их уже рисует экран (Мои каналы)
  const wrap = (head, body) => (bare ? body : `<div class="card">${head}${body}</div>`);
  box.innerHTML = wrap(sectionHead('Партнёры для коллабораций', 'похожие каналы вашего размера, активные и не шаблонные'),
    '<div class="section-sub">Ищу…</div>');
  let d;
  try { d = await api(`/api/channels/${encodeURIComponent(channelId)}/collabs`); }
  catch (e) { box.innerHTML = ''; return; }
  const ex = Object.entries(d.excluded || {}).map(([k, n]) => `${COLLAB_EXCLUDED[k] || k}: ${n}`).join(' · ');
  const rows = (d.candidates || []).map((c) => `<tr>
    <td class="wrap"><a href="#/channel/${esc(c.channelId)}">${esc(c.title || c.channelId)}</a>
      <a class="section-sub" href="https://www.youtube.com/channel/${esc(c.channelId)}" target="_blank" rel="noopener">YouTube ↗</a></td>
    <td class="num">${compact(c.subscribers)} <span class="section-sub" style="display:inline">×${c.sizeRatio}</span></td>
    <td class="num">${Math.round(c.similarity * 100)}%</td>
    <td class="num">${c.daysSinceUpload == null ? '—' : `${Math.round(c.daysSinceUpload)} дн. назад`}</td>
    <td class="num">${c.growth30dPct == null ? '—' : `${c.growth30dPct > 0 ? '+' : ''}${c.growth30dPct}%`}</td>
    <td class="num"><button class="btn btn-ghost btn-sm js-collab-track" data-id="${esc(c.channelId)}">В трекер</button></td>
  </tr>`).join('');
  box.innerHTML = wrap(sectionHead('Партнёры для коллабораций',
      `похожая тема, ×${d.minRatio}–×${d.maxRatio} ваших подписчиков, загрузка за ${d.activeDays} дней, не шаблонные`), `
    ${rows ? `<div class="table-wrap"><table><thead><tr><th>Канал</th><th class="num">Подписчики</th>
      <th class="num">Похожесть</th><th class="num">Последнее видео</th><th class="num">Рост за 30 дн.</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table></div>`
      : empty(d.hint || 'подходящих каналов среди собранных нет — соберите нишу шире')}
    <div class="section-sub" style="margin-top:8px">${ex ? `Отсеяно из ${num(d.poolSize)} похожих: ${esc(ex)}. ` : ''}Только собранные каналы;
      контактов YouTube не даёт — пишите через ссылки на канале.</div>`);
  box.querySelectorAll('.js-collab-track').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true;
    try { await api('/api/channels/track', { method: 'POST', body: { channel_id: b.dataset.id, note: 'коллаборация' } });
      b.textContent = 'В трекере'; toast('Канал добавлен в трекер', 'ok'); }
    catch (e) { b.disabled = false; toast(e.message, 'err'); }
  }));
}

/* Чистая прибыль (план 30): вилка выручки минус расходы выбранного профиля.
   target: { channel_id } или { niche }. Профили редактируются на экране «Данные». */
const PROFIT_VERDICT = { profitable: ['chip-good', 'в плюсе'], loss: ['chip-bad', 'в минусе'],
  uncertain: ['chip-warn', 'не ясно: зависит от RPM'] };

const UPLOADS_BASIS = (b) => (b === 'set by you' ? 'заданы вами'
  : b.startsWith('long videos') ? 'длинные видео за 30 дней' : 'по умолчанию');

function money(v) {
  if (v == null) return '—';
  const s = `$${Math.abs(Number(v)).toLocaleString('ru-RU', { maximumFractionDigits: 2 })}`;
  return v < 0 ? `−${s}` : s;
}
function moneyRange(r) { return `${money(r.low)} … ${money(r.high)}`; }

function profitResult(d) {
  if (!d.found) return empty(d.hint || 'нет данных');
  const part = d.month || d.video;
  const [cls, label] = PROFIT_VERDICT[part.verdict] || ['', part.verdict];
  const per = d.month ? 'в месяц' : 'за видео';
  const be = d.video?.breakEvenViews;
  return `
    ${d.yppWarning ? notice('Канал не проходит полный уровень YPP (1000 подписчиков): доход с рекламы начинается с него, до того реальная выручка может быть нулевой.', 'warn') : ''}
    ${d.hint ? notice('Профиля расходов нет — расходы считаются нулевыми. Добавьте профиль на экране «Данные».') : ''}
    <div class="tiles">
      ${tile(`Выручка ${per}`, moneyRange(part.revenue), `середина ${money(part.revenue.mid)}`)}
      ${tile(`Расходы ${per}`, money(part.cost), d.month ? `${num(d.month.uploads)} видео × ${money(d.month.costPerVideo)} + ${money(d.month.overhead)}` : '')}
      ${tile(`Прибыль ${per}`, moneyRange(part.profit), `середина ${money(part.profit.mid)}`)}
      ${tile('Итог', `<span class="chip ${cls}">${esc(label)}</span>`)}
      ${be ? tile('Окупается с', `${num(be.mid)} просм.`, `от ${num(be.high)} до ${num(be.low)} — по RPM`) : ''}
    </div>
    <div class="section-sub" style="margin-top:8px">RPM: ${money(d.rpm.low)} … ${money(d.rpm.high)} (${d.rpmBasis.includes('real') ? 'ваш реальный RPM' : 'оценка по категории'})${
      d.uploadsBasis ? ` · видео в месяц: ${num(d.month?.uploads)} (${esc(UPLOADS_BASIS(d.uploadsBasis))})` : ''}${
      d.typicalVideo ? ' · типичное видео ниши: медиана просмотров и длины' : ''}. Только AdSense, без спонсоров.</div>`;
}

async function mountProfit(box, target) {
  if (!box) return;
  let profiles = [];
  try { profiles = (await api('/api/cost-profiles')).profiles; } catch { /* без профилей тоже считаем */ }
  const opts = profiles.length
    ? profiles.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join('')
    : '<option value="">без расходов</option>';
  box.innerHTML = `<div class="card">${sectionHead('Чистая прибыль', 'выручка минус расходы на производство', `
      <select class="js-profit-profile">${opts}</select>
      <input type="number" min="0" class="js-profit-vpm" placeholder="видео/мес" style="width:90px">
      ${qmark('profit')}`)}
    <div class="js-profit-body"><div class="section-sub">Считаю…</div></div>
    <div class="section-sub" style="margin-top:6px">Профили расходов — на экране <a href="#/data">«Данные»</a>.</div></div>`;
  const load = async () => {
    const body = box.querySelector('.js-profit-body');
    const vpm = box.querySelector('.js-profit-vpm').value;
    try {
      const d = await api('/api/profit' + q({ ...target, profile: box.querySelector('.js-profit-profile').value,
        videos_per_month: vpm === '' ? undefined : vpm }));
      body.innerHTML = profitResult(d);
    } catch (e) { body.innerHTML = empty(e.message); }
  };
  box.querySelector('.js-profit-profile').addEventListener('change', load);
  box.querySelector('.js-profit-vpm').addEventListener('change', load);
  load();
}

/* «Что общего у выстреливших» (план 29): признаки, по которым outlier'ы ниши заметно
   отличаются от обычных видео. Корреляция, не причина. only: 'long' | 'short' | null. */
function traitValue(t, v) {
  if (t.kind === 'share') return `${v}%`;
  return t.key === 'duration' ? `${v} с` : `${v}`;
}

function traitLine(t) {
  const n = `${num(t.nOutliers)} и ${num(t.nOrdinary)} видео`;
  if (t.kind === 'share') {
    const pp = Math.abs(t.diff);
    return `<b>${esc(t.label)}</b>: у outlier'ов ${t.outliers}% против ${t.ordinary}% у обычных
      (${t.diff > 0 ? '+' : '−'}${pp} п.п.) <span class="section-sub" style="display:inline">· ${n}</span>`;
  }
  return `<b>${esc(t.label)}</b>: медиана ${traitValue(t, t.outliers)} против ${traitValue(t, t.ordinary)}
    (×${t.diff}) <span class="section-sub" style="display:inline">· ${n}</span>`;
}

function outlierTraitsBlock(d, only = null) {
  if (!d || !d.formats) return '';
  const names = { long: 'длинные видео', short: 'Shorts' };
  const parts = Object.keys(names).filter((k) => !only || k === only).map((k) => {
    const f = d.formats[k];
    if (!f || (only === null && !f.videos)) return '';
    const head = `${f.outliers} outlier'ов (≥ ×${d.minOutlier}) против ${f.ordinary} обычных (≤ ×${d.maxOrdinary})`;
    if (!f.reliable) {
      return `<div><b>${names[k]}</b>: мало данных — нужно ${d.minGroup}+ видео в каждой группе, сейчас ${head}.</div>`;
    }
    const sig = f.traits.filter((t) => t.significant);
    const rest = f.traits.filter((t) => !t.significant && t.diff !== null);
    return `<div style="margin-bottom:12px"><b>${names[k]}</b> <span class="section-sub" style="display:inline">· ${head}</span>
      ${f.concentrated ? notice(`${Math.round(f.topChannelShare * 100)}% outlier'ов — один канал: это привычки канала, а не ниши.`, 'warn') : ''}
      ${sig.length ? `<ul class="digest-list">${sig.map((t) => `<li style="white-space:normal">${traitLine(t)}</li>`).join('')}</ul>`
        : '<div class="section-sub">Заметных отличий нет: ни один признак не разошёлся достаточно.</div>'}
      ${rest.length ? `<details><summary class="section-sub" style="cursor:pointer">Без заметной разницы (${rest.length})</summary>
        <ul class="digest-list">${rest.map((t) => `<li style="white-space:normal">${traitLine(t)}</li>`).join('')}</ul></details>` : ''}
    </div>`;
  }).join('');
  if (!parts) return '';
  return `<div class="card">${sectionHead('Что общего у выстреливших',
    'чем outlier\'ы отличаются от обычных видео ниши — по числам, без ИИ', qmark('outlierTraits'))}
    ${parts}<div class="section-sub">Корреляция, не причина. Заметно — разница от ${d.minDiffPp} п.п. (доли) или в ${d.minRatio} раза (медианы);
      признаков проверено много, один-два могут сойтись случайно. Время — UTC. Оценка по собранной базе.</div></div>`;
}

export { $, api, q, num, compact, mult, ago, esc, delta, plural, pl, toast, tile, sectionHead,
         notice, empty, barList, strengthBar, channelRow, videoCard, table, commentList,
         lineChart, VIEW_COUNT_CHANGE, trajectoryChart, compareHref, loadScores, scoreTip, qmark, policyBlock, nichePolicyBlock, funnelBlock, aiLabelsBadge, scatterChart, state, rpmRange, RPM_TIP, templateRiskBlock,
         nicheTemplateRiskBlock, outlierTraitsBlock, mountProfit, mountCollabs, sponsorBlock, saturationChip, saturationBlock, yppLine, milestonesLine, yppSelect };
