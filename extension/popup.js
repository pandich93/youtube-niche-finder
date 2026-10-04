const send = (msg) => new Promise((r) => chrome.runtime.sendMessage(msg, (res) => r(res || { ok: false, error: 'нет ответа' })));
const $ = (id) => document.getElementById(id);
const compact = (n) => n == null ? '—' : new Intl.NumberFormat('ru-RU', { notation: 'compact' }).format(n);

async function load() {
  const s = (await send({ type: 'settings:get' })).data;
  $('baseUrl').value = s.baseUrl;
  $('apiToken').placeholder = s.apiToken ? 'токен сохранён — введите новый, чтобы заменить' : 'nf_…';
  $('autoFetch').checked = s.autoFetch;
  $('showBadges').checked = s.showBadges;
  $('onlyOutliers').checked = s.onlyOutliers;
  $('showVph').checked = s.showVph;
  $('dash').href = s.baseUrl;
  checkHealth();
}

async function save() {
  await send({ type: 'settings:set', patch: {
    baseUrl: $('baseUrl').value.trim() || 'http://127.0.0.1:8080',
    autoFetch: $('autoFetch').checked,
    showBadges: $('showBadges').checked,
    onlyOutliers: $('onlyOutliers').checked,
    showVph: $('showVph').checked,
    // пустое поле -- «не менять»; новый токен заменяет старый
    ...($('apiToken').value.trim() ? { apiToken: $('apiToken').value.trim() } : {}),
  }});
  $('apiToken').value = '';
  $('dash').href = $('baseUrl').value.trim();
  checkHealth();
}

async function checkHealth() {
  const box = $('status');
  box.className = 'status';
  box.textContent = 'проверяю связь…';
  const res = await send({ type: 'health' });
  if (!res.ok) {
    box.className = 'status err';
    box.textContent = res.error;
    return;
  }
  const h = res.data;
  box.className = 'status ok';
  box.textContent = `бэкенд на связи · ${compact(h.db.videos)} видео, ${compact(h.db.channels)} каналов в базе`
    + (h.hasApiKey ? ` · поисковых вызовов осталось ${h.searchQuota.callsLeft}` : ' · ключ YouTube API не задан');
}

// ------------------------------------------------------------ алерты (8.9)

const ALERT_KIND_LABEL = {
  outlier: 'новый выброс', acceleration: 'ускоряется', title_change: 'сменил заголовок',
  silence_break: 'вернулся после паузы', channel_gone: 'канал пропал',
  video_gone: 'видео пропало', milestone: 'рубеж подписчиков',
};

function alertLine(ev) {
  const p = ev.payload || {};
  let text;
  if (ev.kind === 'outlier') text = `«${p.title || p.videoId}» -- ×${p.outlierScore}, ${compact(p.views)} просмотров`;
  else if (ev.kind === 'acceleration') text = `«${p.title || p.videoId}» -- ускорение ×${p.acceleration}`;
  else if (ev.kind === 'title_change') text = `«${p.oldTitle}» → «${p.newTitle}»`;
  else if (ev.kind === 'silence_break') text = `«${p.title || p.videoId}» -- пауза ${p.gapDays} дн.`;
  else if (ev.kind === 'channel_gone') text = `«${p.title || p.channelId}» -- был ${compact(p.subscribers)} подписчиков`;
  else if (ev.kind === 'milestone') text = `«${p.title || p.channelId}» -- ${compact(p.milestone)} подписчиков`;
  else if (ev.kind === 'video_gone') text = `«${p.title || p.videoId}» -- был outlier ×${p.outlierScore}`;
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
    : '<div class="md-summary">пока нет событий -- нужен хотя бы один отслеживаемый канал и время для воркера</div>';
}

async function scanAlertsNow() {
  $('alertsBody').innerHTML = '<div class="md-summary">проверяю…</div>';
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
    ? `<div class="md-summary">Похоже на: ${res.nearDuplicates.near.slice(0, 3)
        .map((d) => escHtml(d.title || d.videoId)).join('; ')}</div>`
    : '';
  box.innerHTML = `
    <div class="md-summary">${res.sample.videosAnalysed} видео в выборке, ${res.sample.outliersInSample} выбросов
      · ок ${s.ok} / поправить ${s.warn} / ненадёжно ${s.unreliable}</div>
    ${sigHtml}${dupHtml}`;
}

async function mdReview() {
  const body = mdReadForm();
  if (!body.title) { $('mdResult').innerHTML = '<div class="md-summary">Введите заголовок</div>'; return; }
  $('mdResult').innerHTML = '<div class="md-summary">проверяю…</div>';
  const res = await send({ type: 'metadataReview', payload: body });
  if (!res.ok) { $('mdResult').innerHTML = `<div class="md-sig warn">${escHtml(res.error)}</div>`; return; }
  mdRenderResult(res.data);
}

async function mdSaveDraft() {
  const body = mdReadForm();
  if (!body.title) { $('mdResult').innerHTML = '<div class="md-summary">Введите заголовок</div>'; return; }
  body.review = mdLastReview;
  const res = await send({ type: 'saveDraft', payload: body });
  if (!res.ok) { $('mdResult').innerHTML = `<div class="md-sig warn">${escHtml(res.error)}</div>`; return; }
  $('mdResult').innerHTML = `<div class="md-summary">Черновик сохранён (id ${res.data.id}) -- привязать к video ID можно из дашборда после публикации.</div>`;
}

document.addEventListener('DOMContentLoaded', load);
['baseUrl', 'autoFetch', 'showBadges', 'onlyOutliers', 'showVph', 'apiToken'].forEach((id) =>
  document.getElementById(id).addEventListener('change', save));
document.getElementById('check').addEventListener('click', checkHealth);
document.getElementById('mdReview').addEventListener('click', mdReview);
document.getElementById('mdSave').addEventListener('click', mdSaveDraft);
document.getElementById('alertsScan').addEventListener('click', scanAlertsNow);
document.getElementById('alertsMarkSeen').addEventListener('click', markAlertsSeen);
document.getElementById('alertsDetails').addEventListener('toggle', function onToggle() {
  if (this.open) loadAlerts();
});
