/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, num, compact, ago, esc, toast, tile, sectionHead, notice, empty, table, mountCollabs } from '../ui.js';
import { view } from '../shared.js';
import { dict, fmtDate } from '../i18n.js';
import ru from '../i18n/ru/own.js';
import en from '../i18n/en/own.js';

const t = dict({ ru, en });

/* ------------------------------------------------ Мои каналы (план 14)
   Реальные цифры своих каналов из YouTube Analytics через OAuth. Токен
   хранится зашифрованным на сервере и сюда никогда не приходит. */

/* Функция, а не константа: подписи переводятся при каждом рендере. */
const position = () => ({
  below: [t('posBelow'), 'chip-warn'],
  inside: [t('posInside'), 'chip-good'],
  above: [t('posAbove'), 'chip-good'],
  unknown: [t('posUnknown'), ''],
});

function money(x) { return x == null ? '—' : `$${num(Math.round(x * 100) / 100)}`; }
function pct(x) { return x == null ? '—' : `${num(x)}%`; }

/* План 25: клиенту сервиса -- только «ещё не настроено»; администратору в режиме
   web -- шаги для одного OAuth-клиента сервиса типа Web application. */
function setupHtml(st) {
  if (st.adminOnly) {
    return `<div class="card">${sectionHead(t('adminOnlyTitle'), '')}
      <div class="section-sub">${t('adminOnlyText')}</div></div>`;
  }
  if (st.mode === 'web') return webSetupHtml(st);
  return `<div class="card">
    ${sectionHead(t('setupTitle'), t('setupSub'))}
    <div class="prose">
      <p>${t('missingVars', { vars: st.missing.map((m) => `<code>${esc(m)}</code>`).join(', ') })}</p>
      <ol>
        <li>${t('setupStep1')}</li>
        <li>${t('setupStep2')}</li>
        <li>${t('setupStep3')}</li>
        <li>${t('setupStep4')}
          <pre><code>docker compose run --rm web python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"</code></pre>
          ${t('setupStep4Keep')}</li>
        <li>${t('setupStep5')}</li>
      </ol>
      <p>${t('setupAfter')}</p>
    </div></div>`;
}

function webSetupHtml(st) {
  const list = (xs) => xs.map((m) => `<code>${esc(m)}</code>`).join(', ');
  return `<div class="card">
    ${sectionHead(t('webSetupTitle'), t('webSetupSub'))}
    <div class="prose">
      ${st.missing?.length ? `<p>${t('missingVars', { vars: list(st.missing) })}</p>` : ''}
      ${st.problems?.length ? `<p>${st.problems.map(esc).join('<br>')}</p>` : ''}
      <ol>
        <li>${t('webStep1')}</li>
        <li>${t('webStep2')}</li>
        <li>${t('webStep3', { uri: esc(st.redirectUri || t('redirectUriFallback')) })}</li>
        <li>${t('webStep4')}</li>
        <li>${t('webStep5')}</li>
      </ol>
      <p>${t('webClientsNote')}</p>
    </div></div>`;
}

function channelsHtml(d, cal) {
  const byId = Object.fromEntries((cal.channels || []).map((c) => [c.channelId, c]));
  const positions = position();
  return `<div class="card">
    ${sectionHead(t('channelsTitle'), t('channelsSub'),
      `<label class="field" data-tip="${esc(t('revenueTip'))}">
         <input type="checkbox" id="ownRevenue"${d.mode === 'web' ? '' : ' checked'}> ${t('showRevenue')}</label>
       <button class="btn btn-sm" id="ownConnect" type="button">${t('connect')}</button>
       <button class="btn btn-ghost btn-sm" id="ownSync" type="button">${t('syncBtn')}</button>`)}
    ${d.channels.length ? table([
      { label: t('colChannel'), wrap: true, render: (c) => `<a href="#/channel/${esc(c.channelId)}">${esc(c.title || c.channelId)}</a>
        ${c.lastError ? `<div class="row-sub" style="color:var(--bad, #c55)">${esc(c.lastError)}</div>` : ''}` },
      { label: t('colViews'), num: true, render: (c) => compact(c.last28d.views) },
      { label: t('colRevenue'), num: true, render: (c) => money(c.last28d.revenue) },
      { label: 'RPM', num: true, render: (c) => money(c.last28d.rpm) },
      { label: t('colRetention'), num: true, render: (c) => pct(c.last28d.medianRetentionPct) },
      { label: t('colRpmEstimate'), render: (c) => {
        const k = byId[c.channelId];
        if (!k) return '—';
        const [label, cls] = positions[k.position] || [k.position, ''];
        return `<span class="chip ${cls}">${esc(label)}</span>
          <div class="row-sub">${money(k.estimate.low)}–${money(k.estimate.high)}</div>`;
      } },
      { label: t('colUpdated'), render: (c) => (c.lastSyncedAt ? ago(c.lastSyncedAt) : '—') },
      { label: '', render: (c) => `${c.monetaryScope ? '' : `<button class="btn btn-ghost btn-sm js-own-revenue" type="button"
          data-tip="${esc(t('addRevenueTip'))}">${t('addRevenue')}</button> `}<button class="btn btn-ghost btn-sm js-own-off" data-id="${esc(c.channelId)}" type="button">${t('disconnect')}</button>` },
    ], d.channels) : empty(t('channelsEmpty'))}
    <div class="section-sub" style="margin-top:10px">${t('channelsNote', { note: esc(d.note) })}</div>
  </div>
  ${d.channels.length ? `<div class="card">
    ${sectionHead(t('formatsTitle'), t('formatsSub'),
      `<select id="ownFmtChannel">${d.channels.map((c) => `<option value="${esc(c.channelId)}">${esc(c.title || c.channelId)}</option>`).join('')}</select>`)}
    <div id="ownFmtOut">${empty(t('computing'))}</div>
  </div>` : ''}
  ${d.channels.length ? `<div class="card" id="ownVsCard">
    ${sectionHead(t('vsTitle'), t('vsSub'),
      `<select id="ownVsChannel">${d.channels.map((c) => `<option value="${esc(c.channelId)}">${esc(c.title || c.channelId)}</option>`).join('')}</select>
       <select id="ownVsNiche"><option value="">${t('nichePlaceholder')}</option></select>`)}
    <div id="ownVsOut">${empty(t('pickNiche'))}</div>
  </div>` : ''}
  ${d.channels.length ? `<div class="card">
    ${sectionHead(t('collabsTitle'), t('collabsSub'),
      `<select id="ownCollabChannel">${d.channels.map((c) => `<option value="${esc(c.channelId)}">${esc(c.title || c.channelId)}</option>`).join('')}</select>`)}
    <div id="ownCollabOut"></div>
  </div>` : ''}`;
}

function vsHtml(v) {
  return `<div class="tiles">
      ${tile(t('myMedian'), compact(v.ownMedianViews), t('videosCount', { count: num(v.ownVideos) }))}
      ${tile(t('nicheMedian'), compact(v.nicheMedianViews), t('videosInDb', { count: num(v.nicheVideos) }))}
      ${tile(t('ratio'), v.ratio == null ? '—' : `×${v.ratio}`, t('ratioNote'))}
      ${tile(t('aboveNicheMedian'), v.shareAboveNicheMedian == null ? '—' : `${Math.round(v.shareAboveNicheMedian * 100)}%`, t('ofMyVideos'))}
      ${tile(t('retention'), pct(v.ownMedianRetentionPct), t('myMedianNote'))}
    </div>
    ${v.topVideos.length ? table([
      { label: t('colVideo'), render: (r) => `<a href="https://www.youtube.com/watch?v=${esc(r.videoId)}" target="_blank" rel="noopener">${esc(r.videoId)}</a>` },
      { label: t('colViews'), num: true, render: (r) => compact(r.views) },
      { label: t('colRetention'), num: true, render: (r) => pct(r.averageViewPercentage) },
      { label: 'RPM', num: true, render: (r) => money(r.rpm) },
    ], v.topVideos) : ''}
    <div class="section-sub">${esc(v.note)}</div>`;
}

function wire() {
  const connect = $('#ownConnect');
  if (connect) connect.addEventListener('click', async () => {
    connect.disabled = true;
    try {
      const r = await api('/api/own/connect', { method: 'POST', body: { includeRevenue: !!$('#ownRevenue')?.checked } });
      window.location.href = r.authUrl;
    } catch (e) { connect.disabled = false; toast(e.message, 'err'); }
  });
  // план 25: доход -- отдельным согласием, для уже подключённого канала
  document.querySelectorAll('.js-own-revenue').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true;
    try {
      const r = await api('/api/own/connect', { method: 'POST', body: { includeRevenue: true } });
      window.location.href = r.authUrl;
    } catch (e) { b.disabled = false; toast(e.message, 'err'); }
  }));
  const sync = $('#ownSync');
  if (sync) sync.addEventListener('click', async () => {
    sync.disabled = true; sync.textContent = t('syncing');
    try {
      const r = await api('/api/own/sync', { method: 'POST' });
      const bad = r.channels.filter((c) => c.error);
      toast(bad.length ? t('syncFailed', { errors: bad.map((c) => c.error).join('; ') }) : t('synced'), bad.length ? 'err' : 'ok');
      viewOwn();
    } catch (e) { sync.disabled = false; sync.textContent = t('syncBtn'); toast(e.message, 'err'); }
  });
  view.querySelectorAll('.js-own-off').forEach((btn) => btn.addEventListener('click', async () => {
    // Двойное нажатие вместо confirm(): диалоги браузера блокируют страницу.
    if (btn.dataset.armed !== '1') { btn.dataset.armed = '1'; btn.textContent = t('confirmDisconnect'); return; }
    btn.disabled = true;
    try {
      const r = await api(`/api/own/channels/${encodeURIComponent(btn.dataset.id)}`, { method: 'DELETE' });
      toast(r.revoked ? t('disconnectedRevoked') : t('disconnectedManual'), 'ok');
      viewOwn();
    } catch (e) { btn.disabled = false; toast(e.message, 'err'); }
  }));
  const nicheSel = $('#ownVsNiche');
  if (nicheSel) {
    api('/api/niches').then((d) => {
      nicheSel.innerHTML += (d.niches || []).map((n) => `<option value="${esc(n.slug)}">${esc(n.slug)}</option>`).join('');
    }).catch(() => {});
    const load = async () => {
      const out = $('#ownVsOut');
      if (!nicheSel.value) { out.innerHTML = empty(t('pickNiche')); return; }
      out.innerHTML = empty(t('computing'));
      try {
        const v = await api(`/api/own/channels/${encodeURIComponent($('#ownVsChannel').value)}/vs-niche?niche=${encodeURIComponent(nicheSel.value)}`);
        out.innerHTML = vsHtml(v);
      } catch (e) { out.innerHTML = notice(esc(e.message)); }
    };
    nicheSel.addEventListener('change', load);
    $('#ownVsChannel').addEventListener('change', load);
  }
}

/* План 24: форматы своего канала (creatorContentType) и часы просмотра за год.
   Функции, а не константы: подписи переводятся при каждом рендере. */
const formatLabels = () => ({
  SHORTS: t('fmtShorts'), VIDEO_ON_DEMAND: t('fmtLong'), LIVE_STREAM: t('fmtLive'),
  STORY: t('fmtStory'), UNSPECIFIED: t('fmtOther'),
});
const readings = () => ({
  'move-together': t('readingTogether'),
  'move-apart': t('readingApart'),
  'no-clear-link': t('readingNoLink'),
});

function formatsHtml(f) {
  if (!f.available) return empty(f.hint || t('formatsEmpty'));
  const types = Object.entries(f.last90).sort((a, b) => b[1].views - a[1].views);
  const h = f.watchHours;
  const d = (iso) => (iso ? fmtDate(iso) : null);
  const bar = (need, reached, eta) => (reached ? t('hoursReached', { need: num(need) })
    : eta ? t('hoursEta', { need: num(need), date: d(eta), pace: num(h.paceHoursPerDay) })
      : t('hoursNoEta', { need: num(need) }));
  const sv = f.shortsVsLong;
  const FORMAT = formatLabels();
  const READING = readings();
  return `<div class="tiles">${types.map(([k, ft]) => tile(FORMAT[k] || k, compact(ft.views),
      t('formatTileNote', { share: Math.round((ft.viewShare || 0) * 100), subs: ft.subscribersPer1000Views ?? '—' }))).join('')}</div>
    <div class="section-sub" style="margin-top:8px">${t('formatsPeriod', { date: esc(d(f.through)) })}</div>
    <div class="section-sub" style="margin-top:8px"><b>${t('shortsVsLong')}</b> ${sv.r == null
      ? (sv.reason === 'few-weeks' ? t('fewWeeks') : t('flatViews'))
      : t('correlation', { reading: esc(READING[sv.reading] || sv.reading), r: sv.r, weeks: sv.weeks })}</div>
    <div class="section-sub" style="margin-top:8px">${t('watchHours', {
      hours: num(Math.round(h.hours365)), shorts: compact(h.shortsViews90d),
      bar4000: bar(4000, h.reached4000, h.eta4000), bar8000: bar(8000, h.reached8000, h.eta8000),
    })}</div>`;
}

async function loadFormats() {
  const sel = $('#ownFmtChannel');
  const out = $('#ownFmtOut');
  if (!sel || !out) return;
  try { out.innerHTML = formatsHtml(await api(`/api/own/channels/${encodeURIComponent(sel.value)}/formats`)); }
  catch (e) { out.innerHTML = notice(esc(e.message)); }
}

async function viewOwn() {
  const params = new URLSearchParams((location.hash.split('?')[1]) || '');
  const flash = params.get('connected')
    ? notice(t('connected', { title: esc(params.get('connected')) }), 'ok')
    : params.get('error') ? notice(t('connectFailed', { error: esc(params.get('error')) }), 'error') : '';
  const st = await api('/api/own/status');
  if (!st.configured) { view.innerHTML = flash + setupHtml(st); return; }
  const [d, cal] = await Promise.all([api('/api/own/channels'), api('/api/own/rpm-calibration').catch(() => ({}))]);
  view.innerHTML = flash + channelsHtml({ ...d, mode: st.mode }, cal);
  wire();
  $('#ownFmtChannel')?.addEventListener('change', loadFormats);
  loadFormats();
  // план 31: партнёры для выбранного своего канала
  const collabSel = $('#ownCollabChannel');
  if (collabSel) {
    const loadCollabs = () => mountCollabs($('#ownCollabOut'), collabSel.value, { bare: true });
    collabSel.addEventListener('change', loadCollabs);
    loadCollabs();
  }
}

export { viewOwn };
