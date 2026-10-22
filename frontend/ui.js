/* niche-finder dashboard — vanilla ES modules, без сборки.
   Каждый экран читает те же функции, что и MCP-инструменты, через backend/api.py. */

import { getLocale, intlLocale, dict, fmtNum, fmtDate, fmtDateTime, onLocaleChange } from './i18n.js';
import ru from './i18n/ru/ui.js';
import en from './i18n/en/ui.js';

const t = dict({ ru, en });

const $ =(sel, root = document) => root.querySelector(sel);

const state = {
  period: localStorage.getItem('nf.period') || '30d',
  niche: localStorage.getItem('nf.niche') || '',
  route: 'overview',
};

/* ------------------------------------------------------------------ api */

async function api(path, opts = {}) {
  const res = await fetch(path, {
    // X-NF-Locale: сервер отвечает текстами (подсказки, ошибки, каталог оценок) на этом языке
    headers: { 'content-type': 'application/json', 'x-nf-locale': getLocale() },
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
  if (opts.method && opts.method !== 'GET') dispatchEvent(new CustomEvent('nf:api-write'));
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

// формат чисел текущего языка; пересоздаётся при смене языка
let nf = null;
let nfLocale = null;

function num(n) {
  if (n === null || n === undefined) return '—';
  if (nfLocale !== intlLocale()) { nfLocale = intlLocale(); nf = new Intl.NumberFormat(nfLocale); }
  return nf.format(Math.round(n));
}

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
/* Текстовые константы, которые импортируют экраны (RPM_TIP, VIEW_COUNT_CHANGE):
   живые привязки, пересчитываются при смене языка (onLocaleChange ниже). */
let RPM_TIP;
let VIEW_COUNT_CHANGE;

/* Риск шаблонности (plan 01): эвристика по загрузкам, не вердикт YouTube. */
const RISK_LEVEL = { low: ['riskLow', ''], medium: ['riskMedium', 'chip-warn'], high: ['riskHigh', 'chip-bad'],
                     'insufficient-data': ['riskInsufficient', ''] };
const RISK_REASON = {
  similarity: (v) => t('riskSimilarity', { v: (+v).toFixed(2) }),
  templateShare: (v) => t('riskTemplateShare', { pct: Math.round(v * 100) }),
  durationCv: (v) => t('riskDurationCv', { v: (+v).toFixed(2) }),
  cadenceCv: (v) => t('riskCadenceCv', { v: (+v).toFixed(2) }),
};

function riskChip(level) {
  const [key, cls] = RISK_LEVEL[level] || RISK_LEVEL['insufficient-data'];
  return `<span class="chip ${cls}">${t(key)}</span>`;
}

function templateRiskBlock(r) {
  if (!r || !r.found) return '';
  const head = sectionHead(t('riskTitle'), t('riskSub'), qmark('templateRisk'));
  if (r.level === 'insufficient-data') {
    return `<div class="card">${head}<div class="section-sub">${t('riskNotEnough', { videos: num(r.videosAnalysed) })}</div></div>`;
  }
  return `<div class="card">${head}
    <div class="tiles">
      ${tile(t('tileScore'), `${r.score}<span class="tile-unit"> / 100</span>`, riskChip(r.level))}
      ${tile(t('tileVideosAnalysed'), num(r.videosAnalysed), r.format === 'long-form' ? t('formatNoShorts') : t('formatAll'))}
    </div>
    ${r.reasons.length ? `<ul class="digest-list">${r.reasons.map((x) =>
      `<li style="white-space:normal">${esc((RISK_REASON[x.signal] || (() => x.text))(x.value))}</li>`).join('')}</ul>`
      : `<div class="section-sub" style="margin-top:8px">${t('riskNoSignals')}</div>`}
    <div class="section-sub" style="margin-top:8px">${t('riskNote')}</div></div>`;
}

/* План 22: сигналы по трём категориям «неаутентичного» контента из правил
   монетизации YouTube. Уровни, причины и текст правила -- без общего «процента
   риска»: решение принимает YouTube, вручную. */
const POLICY_CAT = {
  generic_repetitive: 'policyCatGeneric',
  unsatisfying: 'policyCatUnsatisfying',
  ai_persona_sensitive: 'policyCatAiPersona',
};
const policyCat = (k) => (POLICY_CAT[k] ? t(POLICY_CAT[k]) : k);
const POLICY_LEVEL = { high: ['chip-bad', 'policyLevelHigh'], watch: ['', 'policyLevelWatch'],
  none: ['chip-good', 'policyLevelNone'], 'insufficient-data': ['', 'policyLevelInsufficient'] };
/* Пересказ правил YouTube; оригинал -- по ссылке «справка YouTube». */
const POLICY_TEXT = {
  generic_repetitive: 'policyTextGeneric',
  unsatisfying: 'policyTextUnsatisfying',
  ai_persona_sensitive: 'policyTextAiPersona',
};
const POLICY_TOPIC = { health: 'topicHealth', legal: 'topicLegal', finance: 'topicFinance', politics: 'topicPolitics' };
const POLICY_REASON = {
  thumbSimilarity: (x) => t('policyThumbSimilarity', { v: (+x.value).toFixed(2) }),
  shockShare: (x) => t('policyShockShare', { pct: Math.round(x.value * 100) }),
  sensitiveShare: (x) => t('policySensitiveShare', { pct: Math.round(x.value * 100),
    topic: POLICY_TOPIC[x.topic] ? t(POLICY_TOPIC[x.topic]) : x.topic }),
  syntheticShare: (x) => t('policySyntheticShare', { pct: Math.round(x.value * 100) }),
  faceless: () => t('policyFaceless'),
};
function policyReason(x) {
  if (POLICY_REASON[x.signal]) return POLICY_REASON[x.signal](x);
  if (RISK_REASON[x.signal]) return RISK_REASON[x.signal](x.value);
  return x.text || '';
}

function policyBlock(r) {
  if (!r || !r.found) return '';
  const rows = Object.entries(r.categories).map(([k, c]) => {
    const [cls, levelKey] = POLICY_LEVEL[c.level] || ['', null];
    const label = levelKey ? t(levelKey) : c.level;
    return `<div class="row"><div class="row-main">
      <div class="row-title">${esc(policyCat(k))} <span class="chip ${cls}">${esc(label)}</span></div>
      <div class="row-sub">${c.reasons.length ? esc(c.reasons.map(policyReason).join('; ')) : ''}${
        c.examples.length && c.level !== 'none' ? `${c.reasons.length ? ' · ' : ''}${t('policyExample', { text: esc(c.examples[0]) })}` : ''}</div>
      <div class="row-sub">${t('policyRule', { text: esc(POLICY_TEXT[k] ? t(POLICY_TEXT[k]) : c.policy) })} · <a href="${esc(c.policyUrl)}" target="_blank" rel="noopener">${t('policyHelpLink')}</a></div>
    </div></div>`;
  }).join('');
  return `<div class="card">${sectionHead(t('policyTitle'), t('policySub'), qmark('policySignals'))}
    <div class="rows">${rows}</div><div class="section-sub" style="margin-top:8px">${esc(t('policyNote'))}</div></div>`;
}

function nichePolicyBlock(n) {
  if (!n || !n.found) return '';
  return `<div class="card">${sectionHead(t('nichePolicyTitle'), t('nichePolicySub'), qmark('policySignals'))}
    ${table([
      { label: t('colCategory'), wrap: true, render: (r) => esc(policyCat(r.key)) },
      { label: t('colNoticeable'), num: true, render: (r) => num(r.high) },
      { label: t('colWatch'), num: true, render: (r) => num(r.watch) },
      { label: t('colShare'), num: true, render: (r) => (r.shareFlagged == null ? '—' : `${Math.round(r.shareFlagged * 100)}%`) },
      { label: t('colChannels'), wrap: true, render: (r) => r.channels.map((c) =>
        `<a href="#/channel/${esc(c.channelId)}">${esc(c.channelTitle || c.channelId)}</a>`).join(', ') || '—' },
    ], Object.entries(n.categories).map(([key, c]) => ({ key, ...c })))}
    <div class="section-sub" style="margin-top:8px">${esc(t('policyNote'))}</div></div>`;
}

function nicheTemplateRiskBlock(n) {
  if (!n || !n.found) return '';
  const head = sectionHead(t('nicheRiskTitle'), t('nicheRiskSub'));
  if (!n.channelsAnalysed) {
    return `<div class="card">${head}<div class="section-sub">${t('nicheRiskNotEnough')}</div></div>`;
  }
  return `<div class="card">${head}
    <div class="tiles">
      ${tile(t('tileHighRisk'), `${n.highRiskSharePercent}%`, t('nicheRiskOfChannels', { high: num(n.levels.high), total: num(n.channelsAnalysed) }))}
      ${tile(t('tileMedium'), num(n.levels.medium))}
      ${tile(t('tileLow'), num(n.levels.low))}
      ${tile(t('tileInsufficient'), num(n.channelsInsufficient), t('fewerThan10Videos'))}
    </div>
    ${table([
      { label: t('colMostTemplated'), wrap: true, render: (c) => `<a href="#/channel/${esc(c.channelId)}">${esc(c.title || c.channelId)}</a>` },
      { label: t('colSubscribers'), num: true, render: (c) => compact(c.subscribers) },
      { label: t('colScore'), num: true, render: (c) => num(c.score) },
      { label: t('colRisk'), render: (c) => riskChip(c.level) },
    ], n.mostTemplated)}
    <div class="section-sub" style="margin-top:8px">${t('riskNote')}</div></div>`;
}

function mult(x) { return x === null || x === undefined ? '—' : `${(+x).toFixed(1)}x`; }

function ago(iso) {
  if (!iso) return '—';
  const h = (Date.now() - new Date(iso).getTime()) / 36e5;
  if (!isFinite(h)) return '—';
  if (h < 1) return t('agoMin', { n: Math.max(1, Math.round(h * 60)) });
  if (h < 48) return t('agoHours', { n: Math.round(h) });
  const d = h / 24;
  if (d < 45) return t('agoDays', { n: Math.round(d) });
  if (d < 365) return t('agoMonths', { n: Math.round(d / 30) });
  const y = d / 365;
  return t('agoYears', { y: y.toFixed(y < 2 ? 1 : 0) });
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* Дельта всегда со знаком и словом — цвет никогда не единственный носитель смысла. */
function delta(v, unit = '%') {
  if (v === null || v === undefined) return `<span class="chip">${t('noData')}</span>`;
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
  if (!items.length) return empty(t('noData'));
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
  const subs = c.subscribers === null ? t('subsHidden') : t('subscribers', { subs: compact(c.subscribers) });
  return `<div class="row">
    <div class="avatar">${esc((c.channelTitle || '?').slice(0, 1).toUpperCase())}</div>
    <div class="row-main">
      <div class="row-title"><a href="#/channel/${esc(c.channelId)}">${esc(c.channelTitle || c.channelId)}</a></div>
      <div class="row-sub">${subs}${c.category ? ` · ${esc(c.category)}` : ''}${
        c.videosInWindow ? ` · ${t('videosInWindow', { n: c.videosInWindow })}` : ''}</div>
    </div>
    <div class="row-metrics">
      <div class="metric" data-tip="${c.multiplierBasis === 'lifetime-mean'
        ? t('multLifetimeTip')
        : t('multMedianTip')}">
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
    : `<div class="thumb-fallback">${t('noThumb')}</div>`;
  const vph = v.vph24h ?? v.vphLifetime;
  const vphLabel = v.vph24h != null ? t('vph24h') : t('vphLifetime');
  return `<article class="vcard">
    <a class="thumb" href="https://www.youtube.com/watch?v=${esc(v.videoId)}" target="_blank" rel="noopener">
      ${thumb}
      ${vph != null ? `<span class="badge" data-tip="${scoreTip('vph', vphLabel)}">${num(vph)} VPH</span>` : ''}
      ${v.outlierScore != null ? `<span class="badge badge-right" data-tip="${scoreTip('outlierScore')}">${mult(v.outlierScore)}</span>` : ''}
    </a>
    <div class="vcard-title">${esc(v.title)}</div>
    <div class="vcard-meta">${t('cardViews', { views: compact(v.views) })} · ${ago(v.publishedAt)}
      · <a href="#/brief/${esc(v.videoId)}" data-tip="${t('briefTip')}">${t('briefLink')}</a>
      · <a href="#" data-compare="${esc(v.videoId)}" data-tip="${t('compareTip')}">${t('compareLink')}</a></div>
    <div class="vcard-meta"><a href="#/channel/${esc(v.channelId)}">${esc(v.channelTitle || '')}</a>
      · ${t('subsShort', { subs: compact(v.channelSubscribers) })}</div>
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
  const tip = `${t('aiLabels')}${ai.topic ? ` · ${t('aiTopic', { topic: ai.topic })}` : ''}${ai.labeledAt ? ` · ${ago(ai.labeledAt)}` : ''}`;
  return `<span class="chip" data-tip="${esc(tip)}">${esc(parts.join(' · '))}</span>`;
}

function commentList(comments) {
  if (!comments.length) return empty(t('noComments'));
  return `<div class="comments-list">${comments.map((c) => `
    <div class="comment-row">
      <div class="comment-meta">${esc(c.author || '—')} · ${ago(c.publishedAt)} · 👍 ${num(c.likeCount || 0)}</div>
      <div class="comment-text">${esc(c.text || '')}</div>
    </div>`).join('')}</div>`;
}

function table(cols, rows) {
  if (!rows.length) return empty(t('noData'));
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
function applyLocaleTexts() {
  RPM_TIP = t('rpmTip');
  VIEW_COUNT_CHANGE = { t: '2026-08-24T00:00:00Z', label: t('viewCountChange') };
}
applyLocaleTexts();
onLocaleChange(applyLocaleTexts);

/* Линейный график с перекрестием — одна серия, поэтому без легенды.
   marks: [{t, label}] — вертикальные отметки дат, рисуются только внутри оси X. */
function lineChart(points, { height = 180, valueLabel = t('chartValue'), marks = [] } = {}) {
  if (!points || points.length < 2) return empty(t('chartNeedTwoPoints'));
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
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${t('chartAria', { label: esc(valueLabel) })}">
    ${ticks.map((tk) => `<line class="gridline" x1="${m.l}" x2="${W - m.r}" y1="${py(tk).toFixed(1)}" y2="${py(tk).toFixed(1)}"/>
      <text x="${m.l - 8}" y="${(py(tk) + 4).toFixed(1)}" text-anchor="end">${compact(tk)}</text>`).join('')}
    <line class="axis" x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}"/>
    <path class="area" d="${area}"/>
    <path class="line" d="${d}"/>
    ${marks.map((mk) => ({ ...mk, x: new Date(mk.t).getTime() }))
      .filter((mk) => mk.x > x0 && mk.x < x1)
      .map((mk) => `<line class="mark" x1="${px(mk.x).toFixed(1)}" x2="${px(mk.x).toFixed(1)}" y1="${m.t}" y2="${H - m.b}"
        data-tip="${esc(fmtDate(new Date(mk.x)))}&lt;br&gt;${esc(mk.label)}"/>`).join('')}
    ${points.map((p, i) => `<circle class="dot" cx="${px(xs[i]).toFixed(1)}" cy="${py(p.v).toFixed(1)}" r="4"
      data-tip="${esc(fmtDateTime(new Date(p.t)))}&lt;br&gt;&lt;b&gt;${num(p.v)}&lt;/b&gt; ${esc(valueLabel)}"/>`).join('')}
    <text x="${m.l}" y="${H - 6}">${esc(fmtDate(new Date(x0)))}</text>
    <text x="${W - m.r}" y="${H - 6}" text-anchor="end">${esc(fmtDate(new Date(x1)))}</text>
  </svg>`;
}

/* План 20: траектории видео — просмотры по возрасту (часы с публикации) из снимков
   воркера. У каждого видео свой цвет; пунктир того же цвета — ожидаемая кривая канала
   (медиана канала × кривая взросления). Ось X — возраст, а не дата: так видео разных
   дней сравниваются в одном возрасте. Отметки — смена заголовка/обложки. */
const SERIES = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)', 'var(--series-5)'];
const MARK_LABEL = { title: 'markTitle', thumbnail: 'markThumbnail',
  thumbnail_image: 'markThumbnail', view_count_change: 'viewCountChange' };

function ageLabel(h) {
  return h < 48 ? t('ageHours', { n: Math.round(h) }) : t('ageDays', { n: Math.round(h / 24) });
}

function trajectoryChart(videos, { height = 260 } = {}) {
  const withPts = (videos || []).filter((v) => v.points.length >= 2);
  if (!withPts.length) return empty(t('trajNeedTwo'));
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
        style="fill:${c}" data-tip="${esc(v.title || v.videoId)}&lt;br&gt;${t('trajPointTip', { age: esc(ageLabel(p.ageHours)), views: num(p.views) })}"/>`).join('')}
      ${v.marks.filter((mk) => mk.ageHours >= 0 && mk.ageHours <= x1).map((mk) => `<line class="mark" x1="${px(mk.ageHours).toFixed(1)}"
        x2="${px(mk.ageHours).toFixed(1)}" y1="${m.t}" y2="${H - m.b}" style="stroke:${c}"
        data-tip="${esc(v.title || v.videoId)}&lt;br&gt;${esc(ageLabel(mk.ageHours))}: ${esc(MARK_LABEL[mk.kind] ? t(MARK_LABEL[mk.kind]) : mk.kind)}"/>`).join('')}`;
  }).join('');
  const legend = withPts.map((v, i) => `<span><i style="background:${SERIES[i % SERIES.length]}"></i>${esc(v.title || v.videoId)}${
    v.observedFromHours > 24 ? ` · ${t('trajObservedFrom', { age: esc(ageLabel(v.observedFromHours)) })}` : ''}</span>`).join('');
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${t('trajAria')}">
    ${ticks.map((tk) => `<line class="gridline" x1="${m.l}" x2="${W - m.r}" y1="${py(tk).toFixed(1)}" y2="${py(tk).toFixed(1)}"/>
      <text x="${m.l - 8}" y="${(py(tk) + 4).toFixed(1)}" text-anchor="end">${compact(tk)}</text>`).join('')}
    <line class="axis" x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}"/>
    ${xticks.map((h, i) => `<text x="${px(h).toFixed(1)}" y="${H - 8}" text-anchor="${['start', 'middle', 'end'][i]}">${esc(ageLabel(h))}</text>`).join('')}
    ${series}
  </svg>
  <div class="chart-legend">${legend}${withPts.some((v) => v.expected.length)
    ? `<span><i style="background:var(--muted)"></i>${t('trajExpectedLegend')}</span>`
    : `<span>${t('trajNoExpected')}</span>`}</div>`;
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
  return `<span class="qmark" data-tip="${scoreTip(key)}" aria-label="${t('qmarkAria')}">?</span>`;
}

function scoreTip(key, lead = '') {
  const e = SCORES?.[key];
  if (!e) return esc(lead);
  const src = e.source === 'youtube' ? t('srcYoutube') : t('srcEstimate');
  return [lead ? esc(lead) : '', `&lt;b&gt;${esc(e.name)}&lt;/b&gt; — ${esc(src)}`, esc(e.formula),
    e.minSample ? t('scoreNeeds', { what: esc(e.minSample) }) : ''].filter(Boolean).join('&lt;br&gt;');
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
  if (!videos.length) return empty(t('scatterNoVideos'));
  const W = 720, H = height, m = { t: 12, r: 14, b: 26, l: 52 };

  const xs = videos.map((v) => new Date(v.publishedAt).getTime()).filter(Number.isFinite);
  if (!xs.length) return empty(t('scatterNoDates'));
  const x0 = Math.min(...xs), x1 = Math.max(Math.max(...xs), x0 + 86400000);
  const ys = videos.map((v) => Math.log10(Math.max(1, v.views || 0)));
  const y0 = Math.min(...ys), y1 = Math.max(Math.max(...ys), Math.min(...ys) + 1);
  const px = (t) => m.l + ((t - x0) / (x1 - x0 || 1)) * (W - m.l - m.r);
  const py = (logV) => m.t + (1 - (logV - y0) / (y1 - y0 || 1)) * (H - m.t - m.b);

  const points = videos.map((v) => {
    const ts = new Date(v.publishedAt).getTime();
    const views = Math.max(1, v.views || 0);
    const score = v.outlierScorePeriod ?? v.outlierScoreRolling ?? v.outlierScore;
    const isNew = (v.ageDays ?? Infinity) < 30;
    const isOutlier = (score || 0) >= outlierThreshold;
    const color = _channelColor(v.channelId);
    const tip = `${esc(v.title)}&lt;br&gt;${esc(v.channelTitle || v.channelId)}&lt;br&gt;`
      + `${t('scatterViews', { views: num(views), score: score != null ? mult(score) : '—' })}&lt;br&gt;`
      + `${t('scatterMinutes', { min: Math.round((v.durationSeconds || 0) / 60) })}${isNew ? ` · ${t('youngerThan30')}` : ''}`;
    return { cx: px(ts), cy: py(Math.log10(views)), color, isNew, isOutlier, tip };
  }).filter((p) => Number.isFinite(p.cx) && Number.isFinite(p.cy));

  const yTicks = [y0, (y0 + y1) / 2, y1];

  return `<svg class="chart chart-scatter" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="${t('scatterAria')}">
    ${yTicks.map((tk) => `<line class="gridline" x1="${m.l}" x2="${W - m.r}"
        y1="${py(tk).toFixed(1)}" y2="${py(tk).toFixed(1)}"/>
      <text x="${m.l - 8}" y="${(py(tk) + 4).toFixed(1)}" text-anchor="end">${compact(10 ** tk)}</text>`).join('')}
    <line class="axis" x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}"/>
    ${points.map((p) => `<circle r="${p.isOutlier ? 6 : 4}" cx="${p.cx.toFixed(1)}" cy="${p.cy.toFixed(1)}"
        fill="${p.isNew ? 'none' : p.color}" stroke="${p.color}" stroke-width="${p.isNew ? 2 : 1}"
        opacity="${p.isOutlier ? 1 : 0.75}" data-tip="${p.tip}"/>`).join('')}
    <text x="${m.l}" y="${H - 6}">${esc(fmtDate(new Date(x0)))}</text>
    <text x="${W - m.r}" y="${H - 6}" text-anchor="end">${esc(fmtDate(new Date(x1)))}</text>
  </svg>`;
}

function funnelBlock(res) {
  if (!res.funnel) return '';
  const rows = res.funnel.map((f) =>
    `<div class="funnel-step"><span>${esc(f.step)}</span><b>${num(f.remaining)}</b></div>`).join('');
  return `<div class="card"><div class="section-sub">${t('funnelTitle')}</div>
    <div class="funnel">${rows}</div>
    ${res.hint ? `<div style="margin-top:12px">${notice(esc(res.hint))}</div>` : ''}</div>`;
}

/* Карта спонсоров (plan 09) -- один блок для ниши и канала. Данные из
   /api/niches/{slug}/sponsors и /api/channels/{id}/sponsors. Всегда подписан
   как нижняя граница: видно только то, что указано в описании видео. */
function sponsorBrandBars(brands) {
  return barList((brands || []).map((b) => ({
    name: b.brand, value: b.videos,
    display: t('sponsorVideos', { n: b.videos })
      + (b.channels > 1 ? ` · ${t('sponsorChannels', { n: b.channels })}` : ''),
    tip: t('sponsorTip', { brand: esc(b.brand), videos: num(b.videos), channels: num(b.channels) })
      + (b.lastSeen ? `<br>${t('sponsorLastSeen', { date: esc(String(b.lastSeen).slice(0, 10)) })}` : '')
      + (b.examples || []).slice(0, 2).map((e) =>
        `<br>· ${esc(String(e.title || e.videoId).slice(0, 60))}`).join(''),
  })));
}

function sponsorBlock(d, title, sub) {
  const head = sectionHead(title, sub);
  if (!d) return `<div class="card">${head}${empty(t('sponsorLoadFailed'))}</div>`;
  if (d.found === false) return `<div class="card">${head}${empty(d.hint || t('sponsorNoDataYet'))}</div>`;
  if (!d.videos) return `<div class="card">${head}${empty(t('sponsorNoVideos'))}
    <div class="section-sub" style="margin-top:10px">${esc(t('sponsorFloor'))}</div></div>`;
  const pct = (x) => `${Math.round((x || 0) * 100)}%`;
  const cover = d.scanCoverage < 1
    ? notice(t('sponsorCoverage', { pct: pct(d.scanCoverage) })) : '';
  const brands = d.topBrands || [];
  const aff = d.affiliateBrands || [];
  return `<div class="card">${head}${cover}
    <div class="tiles">
      ${tile(t('tileWithSponsor'), pct(d.sponsorShare),
        t('xOfY', { x: num(d.videosWithSponsor), y: num(d.videos) }))}
      ${tile(t('tileViewsWithSponsor'), compact(d.avgViewsWithSponsor),
        t('avgMedian', { median: compact(d.medianViewsWithSponsor) }))}
      ${tile(t('tileViewsWithout'), compact(d.avgViewsWithout),
        t('avgMedian', { median: compact(d.medianViewsWithout) }))}
      ${tile(t('tileAffiliate'), pct(d.affiliateShare),
        t('videosCount', { n: d.videosWithAffiliate }))}
    </div>
    <div class="grid-2" style="margin-top:16px">
      <div><div class="section-sub" style="margin-bottom:8px">${t('sponsorBrandsTitle')}</div>
        ${brands.length ? sponsorBrandBars(brands) : empty(t('sponsorNoBrands'))}</div>
      <div><div class="section-sub" style="margin-bottom:8px">${t('affiliateTitle')}</div>
        ${aff.length ? sponsorBrandBars(aff) : empty(t('affiliateNone'))}</div>
    </div>
    <div class="section-sub" style="margin-top:12px">${esc(t('sponsorFloor'))}</div>
  </div>`;
}

/* Тренд насыщенности ниши (план 08): последние 30 дней против 90 до них.
   Две точки -- это «было → стало» в плитке с изменением, а не график. Статус --
   бейдж с подписью, не один цвет. */
const SAT_STATUS = {
  growing: ['satGrowing', 'chip-good'],
  stable: ['satStable', ''],
  cooling: ['satCooling', 'chip-warn'],
  saturated: ['satSaturated', 'chip-bad'],
  'insufficient-data': ['satInsufficient', ''],
};
const SAT_REASON = {
  'supply-up': 'satSupplyUp',
  'supply-down': 'satSupplyDown',
  'supply-flat': 'satSupplyFlat',
  'demand-up': 'satDemandUp',
  'demand-down': 'satDemandDown',
  'demand-flat': 'satDemandFlat',
  'demand-unknown': 'satDemandUnknown',
  'entrants-up': 'satEntrantsUp',
  'entrants-down': 'satEntrantsDown',
  'entrants-flat': 'satEntrantsFlat',
  'entrants-unknown': 'satEntrantsUnknown',
  'newcomers-break-out': 'satNewcomersBreakOut',
  'newcomers-some': 'satNewcomersSome',
  'newcomers-rarely-break-out': 'satNewcomersRarely',
  'newcomers-unknown': 'satNewcomersUnknown',
  'supply-unknown': 'satSupplyUnknown',
  'few-videos': 'satFewVideos',
  'low-coverage': 'satLowCoverage',
};

function saturationChip(s) {
  if (!s) return '';
  const [key, cls] = SAT_STATUS[s.status] || [null, ''];
  const label = key ? t(key) : s.status;
  const low = s.confidence === 'low' && s.status !== 'insufficient-data';
  return `<span class="chip ${cls}" data-tip="${scoreTip('nicheTrend', low ? t('satLowConfidence') : '')}">${esc(label)}${low ? ' ?' : ''}</span>`;
}

function saturationBlock(s) {
  if (!s) return '';
  const sig = s.signals;
  const x = (r) => (r == null ? '' : ` · ×${r}`);
  const reasons = (s.reasons || []).map((r) => (SAT_REASON[r.code] ? t(SAT_REASON[r.code]) : r.code));
  return `<div class="card">
    ${sectionHead(t('trendTitle'), t('trendSub', { format: s.format === 'short' ? 'Shorts' : t('formatLong') }),
      saturationChip(s))}
    <div class="tiles">
      ${tile(t('tileVideos30d'), `${num(sig.supply.basePer30d)} → ${num(sig.supply.recent)}`,
        t('trendWasNow', { ratio: x(sig.supply.ratio) }))}
      ${tile(t('tileNewVideoViews'), `${compact(sig.demand.baseMedian)} → ${compact(sig.demand.recentMedian)}`,
        t('trendForecastMedian', { ratio: x(sig.demand.ratio) }))}
      ${tile(t('tileNewChannels30d'), `${num(sig.entrants.basePer30d)} → ${num(sig.entrants.recent)}`,
        sig.entrants.unknownAge ? t('trendNoCreatedDate', { count: num(sig.entrants.unknownAge) }) : t('trendCreatedInWindow'))}
      ${tile(t('tileNewcomersBreakOut'), sig.newcomers.share == null ? '—' : `${Math.round(sig.newcomers.share * 100)}%`,
        t('trendNewcomersNote', { x: num(sig.newcomers.brokeOut), y: num(sig.newcomers.channels) }))}
    </div>
    <ul class="digest-list" style="margin-top:10px">${reasons.map((r) => `<li style="white-space:normal">${esc(r)}</li>`).join('')}</ul>
    <div class="section-sub">${t('trendCounts', { recent: num(s.counts.recent), base: num(s.counts.base) })}
      ${sig.coverage.caughtYoungShare != null ? t('trendCaughtYoung', { pct: Math.round(sig.coverage.caughtYoungShare * 100) }) : ''}
      ${t('trendOnlyCollected')}</div>
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
    'below-threshold': t('yppBelow'),
    'subscribers-met': t('yppSubsMet', { tier: full.subscribers ? t('yppFrom1000') : t('yppFrom500'),
      uploads: exp.uploads90d?.seen ?? 0, shorts: shortsSeen }),
    'shorts-path-met': t('yppShortsMet', { shorts: shortsSeen }),
    unknown: t('yppUnknown'),
  }[e.status] || e.status;
}

/* План 17: с 01.02.2027 полный уровень YPP — 8 000 часов за 365 дней или
   20 млн просмотров Shorts за 90 дней (1 000 подписчиков остаётся). Текст -- ключ ypp2027. */

function yppLine(e) {
  if (!e) return '';
  const next = e.upcoming
    ? t('ypp2027Next', { rules: t('ypp2027'),
      verdict: e.upcoming.status === e.status ? t('yppSame') : esc(yppText(e.upcoming)) })
    : e.rules === '2027' ? t('ypp2027Active') : '';
  return `<div class="section-sub" style="margin-top:8px">${t('yppLine', { qmark: qmark('ypp'), text: esc(yppText(e)), next })}</div>`;
}

/* Рубежи подписчиков (план 17): прямая по темпу наших снимков — оценка, не данные YouTube. */
const MILESTONE_REASON = {
  'few-snapshots': 'msFewSnapshots', 'short-history': 'msShortHistory',
  'no-growth': 'msNoGrowth', 'too-slow': 'msTooSlow',
};

function milestoneText(f) {
  if (f.reached) return t('msReached', { target: num(f.target) });
  const d = (iso) => fmtDate(iso);
  if (f.eta30 || f.eta90) {
    const parts = [];
    if (f.eta30) parts.push(t('msEta30', { date: d(f.eta30), pace: num(f.pace30PerDay) }));
    if (f.eta90) parts.push(t('msEta90', { date: d(f.eta90) }));
    return `${num(f.target)}: ${parts.join(', ')}`;
  }
  return t('msCannot', { target: num(f.target), reason: MILESTONE_REASON[f.reason] ? t(MILESTONE_REASON[f.reason]) : f.reason });
}

function milestonesLine(ms) {
  if (!ms || !ms.forecasts?.length) return '';
  const ypp = ms.ypp1000BeforeRules2027 === true ? t('msYppBefore')
    : ms.ypp1000BeforeRules2027 === false ? t('msYppAfter') : '';
  return `<div class="section-sub" style="margin-top:8px">${t('msLine', {
    qmark: qmark('milestones'), list: ms.forecasts.map((f) => esc(milestoneText(f))).join('; '), ypp })}</div>`;
}

/* Фильтр поиска по порогам YPP (plan 12): пороги, которые канал видимо прошёл. */
function yppSelect(id, cur) {
  const opt = (v, l) => `<option value="${v}"${(cur || '') === v ? ' selected' : ''}>${l}</option>`;
  return `<label class="field" data-tip="${t('yppTip')}"><span class="field-label">${t('yppFilterLabel')}</span>
    <select id="${id}">${opt('', t('yppAny'))}${opt('subscribers-met', t('yppOptSubs'))}${
      opt('shorts-path-met', t('yppOptShorts'))}</select></label>`;
}

/* Партнёры для коллабораций (план 31): похожие каналы вашего размера, активные и
   не шаблонные. Контактов YouTube не даёт -- только ссылки на каналы. */
const COLLAB_EXCLUDED = { 'off-topic': 'collabOffTopic', 'size-unknown': 'collabSizeUnknown', 'too-small': 'collabTooSmall',
  'too-big': 'collabTooBig', inactive: 'collabInactive', templated: 'collabTemplated' };

async function mountCollabs(box, channelId, { bare = false } = {}) {
  if (!box) return;
  // bare: без своей карточки и заголовка -- когда их уже рисует экран (Мои каналы)
  const wrap = (head, body) => (bare ? body : `<div class="card">${head}${body}</div>`);
  box.innerHTML = wrap(sectionHead(t('collabTitle'), t('collabSubShort')),
    `<div class="section-sub">${t('collabSearching')}</div>`);
  let d;
  try { d = await api(`/api/channels/${encodeURIComponent(channelId)}/collabs`); }
  catch (e) { box.innerHTML = ''; return; }
  const ex = Object.entries(d.excluded || {}).map(([k, n]) => `${COLLAB_EXCLUDED[k] ? t(COLLAB_EXCLUDED[k]) : k}: ${n}`).join(' · ');
  const rows = (d.candidates || []).map((c) => `<tr>
    <td class="wrap"><a href="#/channel/${esc(c.channelId)}">${esc(c.title || c.channelId)}</a>
      <a class="section-sub" href="https://www.youtube.com/channel/${esc(c.channelId)}" target="_blank" rel="noopener">YouTube ↗</a></td>
    <td class="num">${compact(c.subscribers)} <span class="section-sub" style="display:inline">×${c.sizeRatio}</span></td>
    <td class="num">${Math.round(c.similarity * 100)}%</td>
    <td class="num">${c.daysSinceUpload == null ? '—' : t('collabDaysAgo', { n: Math.round(c.daysSinceUpload) })}</td>
    <td class="num">${c.growth30dPct == null ? '—' : `${c.growth30dPct > 0 ? '+' : ''}${c.growth30dPct}%`}</td>
    <td class="num"><button class="btn btn-ghost btn-sm js-collab-track" data-id="${esc(c.channelId)}">${t('collabTrackBtn')}</button></td>
  </tr>`).join('');
  box.innerHTML = wrap(sectionHead(t('collabTitle'),
      t('collabSub', { min: d.minRatio, max: d.maxRatio, days: d.activeDays })), `
    ${rows ? `<div class="table-wrap"><table><thead><tr><th>${t('colChannel')}</th><th class="num">${t('colSubscribersShort')}</th>
      <th class="num">${t('colSimilarity')}</th><th class="num">${t('colLastVideo')}</th><th class="num">${t('colGrowth30d')}</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table></div>`
      : empty(d.hint || t('collabEmpty'))}
    <div class="section-sub" style="margin-top:8px">${ex ? `${t('collabFiltered', { pool: num(d.poolSize), list: esc(ex) })} ` : ''}${t('collabOnlyCollected')}</div>`);
  box.querySelectorAll('.js-collab-track').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true;
    // note уходит на сервер и хранится как данные -- не переводится
    try { await api('/api/channels/track', { method: 'POST', body: { channel_id: b.dataset.id, note: t('collabNote') } });
      b.textContent = t('collabTracked'); toast(t('collabTrackedToast'), 'ok'); }
    catch (e) { b.disabled = false; toast(e.message, 'err'); }
  }));
}

/* Чистая прибыль (план 30): вилка выручки минус расходы выбранного профиля.
   target: { channel_id } или { niche }. Профили редактируются на экране «Данные». */
const PROFIT_VERDICT = { profitable: ['chip-good', 'profitProfitable'], loss: ['chip-bad', 'profitLoss'],
  uncertain: ['chip-warn', 'profitUncertain'] };

// b -- значение с сервера (английский код), сравнение не меняется
const UPLOADS_BASIS = (b) => (b === 'set by you' ? t('basisSetByYou')
  : b.startsWith('long videos') ? t('basisLongVideos') : t('basisDefault'));

function money(v) {
  if (v == null) return '—';
  const s = `$${fmtNum(Math.abs(Number(v)), { maximumFractionDigits: 2 })}`;
  return v < 0 ? `−${s}` : s;
}
function moneyRange(r) { return `${money(r.low)} … ${money(r.high)}`; }

function profitResult(d) {
  if (!d.found) return empty(d.hint || t('noData'));
  const part = d.month || d.video;
  const [cls, verdictKey] = PROFIT_VERDICT[part.verdict] || ['', null];
  const label = verdictKey ? t(verdictKey) : part.verdict;
  const per = d.month ? t('perMonth') : t('perVideo');
  const be = d.video?.breakEvenViews;
  return `
    ${d.yppWarning ? notice(t('profitYppWarning'), 'warn') : ''}
    ${d.hint ? notice(t('profitNoProfile')) : ''}
    <div class="tiles">
      ${tile(t('profitRevenue', { per }), moneyRange(part.revenue), t('midpoint', { v: money(part.revenue.mid) }))}
      ${tile(t('profitCost', { per }), money(part.cost), d.month ? t('profitCostFormula', {
        uploads: num(d.month.uploads), cost: money(d.month.costPerVideo), overhead: money(d.month.overhead) }) : '')}
      ${tile(t('profitProfit', { per }), moneyRange(part.profit), t('midpoint', { v: money(part.profit.mid) }))}
      ${tile(t('profitVerdict'), `<span class="chip ${cls}">${esc(label)}</span>`)}
      ${be ? tile(t('profitBreakEven'), t('profitBreakEvenViews', { views: num(be.mid) }),
        t('profitBreakEvenRange', { from: num(be.high), to: num(be.low) })) : ''}
    </div>
    <div class="section-sub" style="margin-top:8px">${t('profitRpm', { low: money(d.rpm.low), high: money(d.rpm.high),
      basis: d.rpmBasis.includes('real') ? t('profitRealRpm') : t('profitCategoryRpm') })}${
      d.uploadsBasis ? ` · ${t('profitUploads', { uploads: num(d.month?.uploads), basis: esc(UPLOADS_BASIS(d.uploadsBasis)) })}` : ''}${
      d.typicalVideo ? ` · ${t('profitTypical')}` : ''}. ${t('profitAdsenseOnly')}</div>`;
}

async function mountProfit(box, target) {
  if (!box) return;
  let profiles = [];
  try { profiles = (await api('/api/cost-profiles')).profiles; } catch { /* без профилей тоже считаем */ }
  const opts = profiles.length
    ? profiles.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join('')
    : `<option value="">${t('profitNoCosts')}</option>`;
  box.innerHTML = `<div class="card">${sectionHead(t('profitTitle'), t('profitSub'), `
      <select class="js-profit-profile">${opts}</select>
      <input type="number" min="0" class="js-profit-vpm" placeholder="${t('profitVpmPlaceholder')}" style="width:90px">
      ${qmark('profit')}`)}
    <div class="js-profit-body"><div class="section-sub">${t('profitCalculating')}</div></div>
    <div class="section-sub" style="margin-top:6px">${t('profitProfilesHint')}</div></div>`;
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
function traitValue(tr, v) {
  if (tr.kind === 'share') return `${v}%`;
  return tr.key === 'duration' ? t('seconds', { v }) : `${v}`;
}

function traitLine(tr) {
  const n = t('traitN', { a: num(tr.nOutliers), b: num(tr.nOrdinary) });
  if (tr.kind === 'share') {
    const pp = Math.abs(tr.diff);
    return `${t('traitShare', { label: esc(tr.label), outliers: tr.outliers, ordinary: tr.ordinary,
      diff: `${tr.diff > 0 ? '+' : '−'}${pp}` })} <span class="section-sub" style="display:inline">· ${n}</span>`;
  }
  return `${t('traitMedian', { label: esc(tr.label), outliers: traitValue(tr, tr.outliers),
    ordinary: traitValue(tr, tr.ordinary), diff: tr.diff })} <span class="section-sub" style="display:inline">· ${n}</span>`;
}

function outlierTraitsBlock(d, only = null) {
  if (!d || !d.formats) return '';
  const names = { long: t('traitsLong'), short: 'Shorts' };
  const parts = Object.keys(names).filter((k) => !only || k === only).map((k) => {
    const f = d.formats[k];
    if (!f || (only === null && !f.videos)) return '';
    const head = t('traitsHead', { outliers: f.outliers, minOutlier: d.minOutlier, ordinary: f.ordinary, maxOrdinary: d.maxOrdinary });
    if (!f.reliable) {
      return `<div>${t('traitsUnreliable', { name: names[k], minGroup: d.minGroup, head })}</div>`;
    }
    const sig = f.traits.filter((tr) => tr.significant);
    const rest = f.traits.filter((tr) => !tr.significant && tr.diff !== null);
    return `<div style="margin-bottom:12px"><b>${names[k]}</b> <span class="section-sub" style="display:inline">· ${head}</span>
      ${f.concentrated ? notice(t('traitsConcentrated', { pct: Math.round(f.topChannelShare * 100) }), 'warn') : ''}
      ${sig.length ? `<ul class="digest-list">${sig.map((tr) => `<li style="white-space:normal">${traitLine(tr)}</li>`).join('')}</ul>`
        : `<div class="section-sub">${t('traitsNoDiff')}</div>`}
      ${rest.length ? `<details><summary class="section-sub" style="cursor:pointer">${t('traitsNoNoticeable', { n: rest.length })}</summary>
        <ul class="digest-list">${rest.map((tr) => `<li style="white-space:normal">${traitLine(tr)}</li>`).join('')}</ul></details>` : ''}
    </div>`;
  }).join('');
  if (!parts) return '';
  return `<div class="card">${sectionHead(t('traitsTitle'), t('traitsSub'), qmark('outlierTraits'))}
    ${parts}<div class="section-sub">${t('traitsFooter', { minDiffPp: d.minDiffPp, minRatio: d.minRatio })}</div></div>`;
}

export { $, api, q, num, compact, mult, ago, esc, delta, plural, pl, toast, tile, sectionHead,
         notice, empty, barList, strengthBar, channelRow, videoCard, table, commentList,
         lineChart, VIEW_COUNT_CHANGE, trajectoryChart, compareHref, loadScores, scoreTip, qmark, policyBlock, nichePolicyBlock, funnelBlock, aiLabelsBadge, scatterChart, state, rpmRange, RPM_TIP, templateRiskBlock,
         nicheTemplateRiskBlock, outlierTraitsBlock, mountProfit, mountCollabs, sponsorBlock, saturationChip, saturationBlock, yppLine, milestonesLine, yppSelect };
