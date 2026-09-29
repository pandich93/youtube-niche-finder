/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, num, compact, ago, esc, toast, tile, sectionHead, notice, empty, table } from '../ui.js';
import { view } from '../shared.js';

/* ------------------------------------------------ Мои каналы (план 14)
   Реальные цифры своих каналов из YouTube Analytics через OAuth. Токен
   хранится зашифрованным на сервере и сюда никогда не приходит. */

const POSITION = {
  below: ['ниже нашей оценки', 'chip-warn'],
  inside: ['в пределах оценки', 'chip-good'],
  above: ['выше нашей оценки', 'chip-good'],
  unknown: ['неизвестно', ''],
};

function money(x) { return x == null ? '—' : `$${num(Math.round(x * 100) / 100)}`; }
function pct(x) { return x == null ? '—' : `${num(x)}%`; }

function setupHtml(st) {
  return `<div class="card">
    ${sectionHead('Подключение не настроено', 'один раз: свой OAuth-клиент Google и ключ шифрования')}
    <div class="prose">
      <p>Не хватает переменных в <code>.env</code>: ${st.missing.map((m) => `<code>${esc(m)}</code>`).join(', ')}.</p>
      <ol>
        <li>В <a href="https://console.cloud.google.com/" target="_blank" rel="noopener">Google Cloud Console</a> создайте проект,
          включите <b>YouTube Data API v3</b> и <b>YouTube Analytics API</b>.</li>
        <li>OAuth consent screen: тип External, режим Testing, добавьте свой Google-аккаунт в тестовые пользователи.</li>
        <li>Credentials → Create credentials → OAuth client ID → тип <b>Desktop app</b>. Скопируйте Client ID и Client secret
          в <code>OWN_OAUTH_CLIENT_ID</code> и <code>OWN_OAUTH_CLIENT_SECRET</code>.</li>
        <li>Сгенерируйте ключ шифрования токенов и впишите в <code>OWN_TOKENS_KEY</code>:
          <pre><code>docker compose run --rm web python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"</code></pre>
          Храните ключ только в <code>.env</code>: без него сохранённые токены не прочитать, придётся подключить канал заново.</li>
        <li>Перезапустите: <code>docker compose up -d web worker</code>.</li>
      </ol>
      <p>После входа Google вернёт вас на этот же адрес дашборда (<code>/api/own/oauth/callback</code>) — для клиента
        типа Desktop app его отдельно прописывать не нужно. Завершайте вход в том же браузере, где начали. Подробно — в <code>backend/README.md</code>, раздел «Your own channels».</p>
    </div></div>`;
}

function channelsHtml(d, cal) {
  const byId = Object.fromEntries((cal.channels || []).map((c) => [c.channelId, c]));
  return `<div class="card">
    ${sectionHead('Мои каналы', 'реальные цифры за 28 дней, заканчивая 3 днями назад (задержка Analytics)',
      `<button class="btn btn-sm" id="ownConnect" type="button">Подключить канал</button>
       <button class="btn btn-ghost btn-sm" id="ownSync" type="button">Обновить цифры</button>`)}
    ${d.channels.length ? table([
      { label: 'Канал', wrap: true, render: (c) => `<a href="#/channel/${esc(c.channelId)}">${esc(c.title || c.channelId)}</a>
        ${c.lastError ? `<div class="row-sub" style="color:var(--bad, #c55)">${esc(c.lastError)}</div>` : ''}` },
      { label: 'Просмотры', num: true, render: (c) => compact(c.last28d.views) },
      { label: 'Доход', num: true, render: (c) => money(c.last28d.revenue) },
      { label: 'RPM', num: true, render: (c) => money(c.last28d.rpm) },
      { label: 'Удержание', num: true, render: (c) => pct(c.last28d.medianRetentionPct) },
      { label: 'Наша оценка RPM', render: (c) => {
        const k = byId[c.channelId];
        if (!k) return '—';
        const [label, cls] = POSITION[k.position] || [k.position, ''];
        return `<span class="chip ${cls}">${esc(label)}</span>
          <div class="row-sub">${money(k.estimate.low)}–${money(k.estimate.high)}</div>`;
      } },
      { label: 'Обновлено', render: (c) => (c.lastSyncedAt ? ago(c.lastSyncedAt) : '—') },
      { label: '', render: (c) => `<button class="btn btn-ghost btn-sm js-own-off" data-id="${esc(c.channelId)}" type="button">Отключить</button>` },
    ], d.channels) : empty('каналов пока нет — нажмите «Подключить канал»')}
    <div class="section-sub" style="margin-top:10px">${esc(d.note)} RPM = доход на 1000 всех просмотров, как в YouTube Studio;
      без монетизации доход пустой. Удержание — медиана среднего процента просмотра по видео.</div>
  </div>
  ${d.channels.length ? `<div class="card" id="ownVsCard">
    ${sectionHead('Мои видео против ниши', 'пожизненные просмотры и удержание своих видео против собранных видео ниши',
      `<select id="ownVsChannel">${d.channels.map((c) => `<option value="${esc(c.channelId)}">${esc(c.title || c.channelId)}</option>`).join('')}</select>
       <select id="ownVsNiche"><option value="">ниша…</option></select>`)}
    <div id="ownVsOut">${empty('выберите нишу')}</div>
  </div>` : ''}`;
}

function vsHtml(v) {
  return `<div class="tiles">
      ${tile('Медиана моих видео', compact(v.ownMedianViews), `${num(v.ownVideos)} видео`)}
      ${tile('Медиана ниши', compact(v.nicheMedianViews), `${num(v.nicheVideos)} видео в базе`)}
      ${tile('Во сколько раз', v.ratio == null ? '—' : `×${v.ratio}`, 'мои к нише')}
      ${tile('Выше медианы ниши', v.shareAboveNicheMedian == null ? '—' : `${Math.round(v.shareAboveNicheMedian * 100)}%`, 'моих видео')}
      ${tile('Удержание', pct(v.ownMedianRetentionPct), 'медиана моих видео')}
    </div>
    ${v.topVideos.length ? table([
      { label: 'Видео', render: (r) => `<a href="https://www.youtube.com/watch?v=${esc(r.videoId)}" target="_blank" rel="noopener">${esc(r.videoId)}</a>` },
      { label: 'Просмотры', num: true, render: (r) => compact(r.views) },
      { label: 'Удержание', num: true, render: (r) => pct(r.averageViewPercentage) },
      { label: 'RPM', num: true, render: (r) => money(r.rpm) },
    ], v.topVideos) : ''}
    <div class="section-sub">${esc(v.note)}</div>`;
}

function wire() {
  const connect = $('#ownConnect');
  if (connect) connect.addEventListener('click', async () => {
    connect.disabled = true;
    try {
      const r = await api('/api/own/connect', { method: 'POST' });
      window.location.href = r.authUrl;
    } catch (e) { connect.disabled = false; toast(e.message, 'err'); }
  });
  const sync = $('#ownSync');
  if (sync) sync.addEventListener('click', async () => {
    sync.disabled = true; sync.textContent = 'Обновляю…';
    try {
      const r = await api('/api/own/sync', { method: 'POST' });
      const bad = r.channels.filter((c) => c.error);
      toast(bad.length ? `Не обновлено: ${bad.map((c) => c.error).join('; ')}` : 'Цифры обновлены', bad.length ? 'err' : 'ok');
      viewOwn();
    } catch (e) { sync.disabled = false; sync.textContent = 'Обновить цифры'; toast(e.message, 'err'); }
  });
  view.querySelectorAll('.js-own-off').forEach((btn) => btn.addEventListener('click', async () => {
    // Двойное нажатие вместо confirm(): диалоги браузера блокируют страницу.
    if (btn.dataset.armed !== '1') { btn.dataset.armed = '1'; btn.textContent = 'Точно отключить?'; return; }
    btn.disabled = true;
    try {
      const r = await api(`/api/own/channels/${encodeURIComponent(btn.dataset.id)}`, { method: 'DELETE' });
      toast(r.revoked ? 'Канал отключён, доступ у Google отозван' : 'Канал отключён; доступ можно убрать и на myaccount.google.com/permissions', 'ok');
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
      if (!nicheSel.value) { out.innerHTML = empty('выберите нишу'); return; }
      out.innerHTML = empty('Считаю…');
      try {
        const v = await api(`/api/own/channels/${encodeURIComponent($('#ownVsChannel').value)}/vs-niche?niche=${encodeURIComponent(nicheSel.value)}`);
        out.innerHTML = vsHtml(v);
      } catch (e) { out.innerHTML = notice(esc(e.message)); }
    };
    nicheSel.addEventListener('change', load);
    $('#ownVsChannel').addEventListener('change', load);
  }
}

async function viewOwn() {
  const params = new URLSearchParams((location.hash.split('?')[1]) || '');
  const flash = params.get('connected')
    ? notice(`Канал «${esc(params.get('connected'))}» подключён.`, 'ok')
    : params.get('error') ? notice(`<b>Подключить не удалось.</b> ${esc(params.get('error'))}`, 'error') : '';
  const st = await api('/api/own/status');
  if (!st.configured) { view.innerHTML = flash + setupHtml(st); return; }
  const [d, cal] = await Promise.all([api('/api/own/channels'), api('/api/own/rpm-calibration').catch(() => ({}))]);
  view.innerHTML = flash + channelsHtml(d, cal);
  wire();
}

export { viewOwn };
