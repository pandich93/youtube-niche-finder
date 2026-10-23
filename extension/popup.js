// ------------------------------------------------------------- i18n
// Тексты -- из _locales через chrome.i18n; числа и даты -- Intl на языке браузера,
// если для него есть свои _locales, иначе на языке показанных текстов (см. content.js).
const MSG_LOCALE = chrome.i18n.getMessage('localeCode') || 'en';
const BROWSER_LANG = chrome.i18n.getUILanguage() || MSG_LOCALE;
const UI_LANG = BROWSER_LANG.toLowerCase().split(/[-_]/)[0] === MSG_LOCALE ? BROWSER_LANG : MSG_LOCALE;
const t = (key, subs) => chrome.i18n.getMessage(
  key, subs === undefined ? undefined : [].concat(subs).map(String)) || key;

// Статичные тексты попапа: data-i18n -> textContent, data-i18n-title -> title,
// data-i18n-placeholder -> placeholder. Английский в разметке -- запасной.
function applyI18n(root = document) {
  document.documentElement.lang = UI_LANG;
  const set = (attr, apply) => root.querySelectorAll(`[${attr}]`).forEach((el) => {
    const msg = chrome.i18n.getMessage(el.getAttribute(attr));
    if (msg) apply(el, msg);
  });
  set('data-i18n', (el, msg) => { el.textContent = msg; });
  set('data-i18n-title', (el, msg) => { el.title = msg; });
  set('data-i18n-placeholder', (el, msg) => { el.placeholder = msg; });
}
applyI18n();

const send = (msg) => new Promise((r) => chrome.runtime.sendMessage(msg, (res) => r(res || { ok: false, error: t('errNoResponse') })));
const $ = (id) => document.getElementById(id);
const COMPACT = new Intl.NumberFormat(UI_LANG, { notation: 'compact' });
const compact = (n) => n == null ? '—' : COMPACT.format(n);

const DEFAULT_BASE = 'http://127.0.0.1:8080';
let savedBase = DEFAULT_BASE;   // сохранённый адрес: доступ к прежнему удалённому хосту отпускаем
let tokenSaved = false;

async function load() {
  const s = (await send({ type: 'settings:get' })).data;
  savedBase = s.baseUrl;
  tokenSaved = !!s.apiToken;
  $('baseUrl').value = s.baseUrl;
  $('apiToken').placeholder = s.apiToken ? t('popupTokenSaved') : 'nf_…';
  $('autoFetch').checked = s.autoFetch;
  $('showBadges').checked = s.showBadges;
  $('onlyOutliers').checked = s.onlyOutliers;
  $('showVph').checked = s.showVph;
  $('dash').href = s.baseUrl;
  await updateAccessUi();
  checkHealth();
}

// ------------------------------------------- доступ к удалённому сервису (P1-16)
// Chrome выдаёт optional_host_permissions только из жеста пользователя, поэтому
// запрос уходит синхронно в обработчике изменения адреса или кнопки «Разрешить».
async function requestAccess(target) {
  try { return await chrome.permissions.request({ origins: [target.origin] }); } catch { return false; }
}

async function updateAccessUi() {
  const target = nfServiceTarget(savedBase);
  $('grant').hidden = !target || await nfHasHostAccess(target);
}

async function grantAccess() {
  const target = nfServiceTarget(savedBase);
  if (target && !target.local) await requestAccess(target);
  await send({ type: 'cache:drop' });
  await updateAccessUi();
  checkHealth();
}

async function save() {
  const baseUrl = $('baseUrl').value.trim() || DEFAULT_BASE;
  const target = nfServiceTarget(baseUrl);
  // запрос уходит синхронно, а адрес сохраняется параллельно: на время диалога
  // Chrome может закрыть попап, и адрес не должен потеряться вместе с ним
  const asked = target && !target.local && baseUrl !== savedBase ? requestAccess(target) : null;
  const res = await send({ type: 'settings:set', patch: {
    baseUrl,
    autoFetch: $('autoFetch').checked,
    showBadges: $('showBadges').checked,
    onlyOutliers: $('onlyOutliers').checked,
    showVph: $('showVph').checked,
    // пустое поле -- «не менять»; новый токен заменяет старый
    ...($('apiToken').value.trim() ? { apiToken: $('apiToken').value.trim() } : {}),
  }});
  // доступ держим только к одному удалённому хосту -- к тому, что настроен сейчас
  const old = nfServiceTarget(savedBase);
  if (old && !old.local && (!target || old.origin !== target.origin)) {
    chrome.permissions.remove({ origins: [old.origin] }).catch(() => {});
  }
  savedBase = baseUrl;
  if (asked) await asked;
  if (res.ok) tokenSaved = !!res.data.apiToken;
  $('apiToken').value = '';
  $('apiToken').placeholder = tokenSaved ? t('popupTokenSaved') : 'nf_…';
  $('dash').href = baseUrl;
  await updateAccessUi();
  checkHealth();
}

async function checkHealth() {
  const box = $('status');
  box.className = 'status';
  box.textContent = t('popupStatusChecking');
  const res = await send({ type: 'health' });
  if (!res.ok) {
    box.className = 'status err';
    box.textContent = res.error;
    return;
  }
  const h = res.data;
  box.className = 'status ok';
  box.textContent = t('healthOk', [compact(h.db.videos), compact(h.db.channels)])
    + ' · ' + (h.hasApiKey ? t('healthSearchLeft', h.searchQuota.callsLeft) : t('healthNoApiKey'));
  // /api/health открыт и без входа: про токен удалённому сервису скажем сами
  const target = nfServiceTarget(savedBase);
  const note = !target || target.local ? ''
    : !target.secure ? t('popupInsecureRemote')
    : !tokenSaved ? t('popupTokenNeeded') : '';
  if (note) {
    box.className = 'status err';
    box.textContent += ' · ' + note;
  }
}

// ------------------------------------------------------------ алерты (8.9)

const ALERT_KIND_LABEL = {
  outlier: t('alertKindOutlier'), acceleration: t('alertKindAcceleration'), title_change: t('alertKindTitleChange'),
  silence_break: t('alertKindSilenceBreak'), channel_gone: t('alertKindChannelGone'),
  video_gone: t('alertKindVideoGone'), milestone: t('alertKindMilestone'), topic_match: t('alertKindTopicMatch'),
};

function alertLine(ev) {
  const p = ev.payload || {};
  let text;
  if (ev.kind === 'outlier') text = t('alertOutlier', [p.title || p.videoId, p.outlierScore, compact(p.views)]);
  else if (ev.kind === 'acceleration') text = t('alertAcceleration', [p.title || p.videoId, p.acceleration]);
  else if (ev.kind === 'title_change') text = t('alertTitleChange', [p.oldTitle, p.newTitle]);
  else if (ev.kind === 'silence_break') text = t('alertSilenceBreak', [p.title || p.videoId, p.gapDays]);
  else if (ev.kind === 'channel_gone') text = t('alertChannelGone', [p.title || p.channelId, compact(p.subscribers)]);
  else if (ev.kind === 'topic_match') text = t('alertTopicMatch', [p.topic, p.title || p.videoId]);
  else if (ev.kind === 'milestone') text = t('alertMilestone', [p.title || p.channelId, compact(p.milestone)]);
  else if (ev.kind === 'video_gone') text = t('alertVideoGone', [p.title || p.videoId, p.outlierScore]);
  else text = JSON.stringify(p);
  return `<div class="md-sig ${ev.seenAt ? 'unreliable' : 'warn'}">${escHtml(ALERT_KIND_LABEL[ev.kind] || ev.kind)}: ${escHtml(text)}</div>`;
}

async function loadAlerts() {
  const res = await send({ type: 'events', unseenOnly: false });
  const box = $('alertsBody');
  const countEl = $('alertsCount');
  if (!res.ok) { box.innerHTML = `<div class="md-sig warn">${escHtml(res.error)}</div>`; return; }
  const { events, unseenCount } = res.data;
  countEl.textContent = unseenCount > 0 ? `(${unseenCount})` : '';
  box.innerHTML = events.length
    ? events.slice(0, 20).map(alertLine).join('')
    : `<div class="md-summary">${escHtml(t('alertsEmpty'))}</div>`;
}

async function scanAlertsNow() {
  $('alertsBody').innerHTML = `<div class="md-summary">${escHtml(t('checking'))}</div>`;
  const res = await send({ type: 'eventsScan' });
  if (!res.ok) { $('alertsBody').innerHTML = `<div class="md-sig warn">${escHtml(res.error)}</div>`; return; }
  await loadAlerts();
}

async function markAlertsSeen() {
  await send({ type: 'eventsSeen', all: true });
  await loadAlerts();
}

// ------------------------------------------------------- разбор метаданных

const escHtml = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

let mdLastReview = null;

function mdReadForm() {
  return {
    title: $('mdTitle').value.trim(),
    description: $('mdDesc').value,
    tags: $('mdTags').value.split(',').map((t) => t.trim()).filter(Boolean),
    niche: $('mdNiche').value.trim() || null,
    channelId: $('mdChannel').value.trim() || null,
    isShort: $('mdShort').checked,
  };
}

function mdRenderResult(res) {
  mdLastReview = res;
  const box = $('mdResult');
  if (res.hint) {
    box.innerHTML = `<div class="md-summary">${escHtml(res.hint)}</div>`;
    return;
  }
  const s = res.summary;
  const sigHtml = res.signals.map((sig) => `<div class="md-sig ${sig.verdict}">${escHtml(sig.explanation)}</div>`).join('');
  const dupHtml = res.nearDuplicates.near && res.nearDuplicates.near.length
    ? `<div class="md-summary">${escHtml(t('mdSimilarTo', res.nearDuplicates.near.slice(0, 3)
        .map((d) => d.title || d.videoId).join('; ')))}</div>`
    : '';
  box.innerHTML = `
    <div class="md-summary">${escHtml(t('mdSampleSummary', [res.sample.videosAnalysed, res.sample.outliersInSample,
      s.ok, s.warn, s.unreliable]))}</div>
    ${sigHtml}${dupHtml}`;
}

async function mdReview() {
  const body = mdReadForm();
  if (!body.title) { $('mdResult').innerHTML = `<div class="md-summary">${escHtml(t('mdEnterTitle'))}</div>`; return; }
  $('mdResult').innerHTML = `<div class="md-summary">${escHtml(t('checking'))}</div>`;
  const res = await send({ type: 'metadataReview', payload: body });
  if (!res.ok) { $('mdResult').innerHTML = `<div class="md-sig warn">${escHtml(res.error)}</div>`; return; }
  mdRenderResult(res.data);
}

async function mdSaveDraft() {
  const body = mdReadForm();
  if (!body.title) { $('mdResult').innerHTML = `<div class="md-summary">${escHtml(t('mdEnterTitle'))}</div>`; return; }
  body.review = mdLastReview;
  const res = await send({ type: 'saveDraft', payload: body });
  if (!res.ok) { $('mdResult').innerHTML = `<div class="md-sig warn">${escHtml(res.error)}</div>`; return; }
  $('mdResult').innerHTML = `<div class="md-summary">${escHtml(t('mdDraftSaved', res.data.id))}</div>`;
}

document.addEventListener('DOMContentLoaded', load);
['baseUrl', 'autoFetch', 'showBadges', 'onlyOutliers', 'showVph', 'apiToken'].forEach((id) =>
  document.getElementById(id).addEventListener('change', save));
document.getElementById('check').addEventListener('click', checkHealth);
document.getElementById('grant').addEventListener('click', grantAccess);
document.getElementById('mdReview').addEventListener('click', mdReview);
document.getElementById('mdSave').addEventListener('click', mdSaveDraft);
document.getElementById('alertsScan').addEventListener('click', scanAlertsNow);
document.getElementById('alertsMarkSeen').addEventListener('click', markAlertsSeen);
document.getElementById('alertsDetails').addEventListener('toggle', function onToggle() {
  if (this.open) loadAlerts();
});
