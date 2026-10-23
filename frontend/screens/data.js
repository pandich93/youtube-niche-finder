/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, num, esc, toast, tile, sectionHead, notice, state } from '../ui.js';
import { view, plabel, collectForm, wireCollect, render } from '../shared.js';
import { dict, fmtDate } from '../i18n.js';
import ru from '../i18n/ru/data.js';
import en from '../i18n/en/data.js';

const t = dict({ ru, en });

/* ----------------------------------------------------------------- Данные */

async function viewData() {
  const [h, cov, fresh] = await Promise.all([
    api('/api/health'), api(`/api/coverage${q({ period: state.period })}`),
    api('/api/freshness').catch(() => null)]);
  view.innerHTML = `
    ${h.hasApiKey ? notice(t('apiKeyOk'), 'ok')
                  : notice(t('apiKeyMissing'), 'error')}
    ${h.historyAvailable ? '' : notice(t('noHistory'))}
    <div class="card">
      ${sectionHead(t('dbTitle'), h.db.db_path)}
      <div class="tiles">
        ${tile(t('tileChannels'), num(h.db.channels))}
        ${tile(t('tileVideos'), num(h.db.videos))}
        ${tile(t('tileNiches'), num(h.db.niches))}
        ${tile(t('tileVideoSnapshots'), num(h.db.video_stat_snapshots))}
        ${tile(t('tileChannelSnapshots'), num(h.db.channel_stat_snapshots))}
        ${tile(t('tilePackagingChanges'), num(h.db.title_thumbnail_changes))}
        ${tile(t('tileInWindow'), num(cov.videosPublishedInPeriod), plabel(state.period))}
      </div>
    </div>
    ${freshnessCard(fresh)}
    <div id="costProfiles"></div>
    ${collectForm()}
    <div class="card">
      ${sectionHead(t('refreshTitle'), t('refreshSub'))}
      <button class="btn btn-ghost" id="refreshBtn" type="button">${t('refreshNow')}</button>
    </div>`;
  wireCollect(render);
  loadNotifySettings();
  loadCostProfiles();
  $('#refreshBtn')?.addEventListener('click', async (e) => {
    e.target.disabled = true;
    try {
      const r = await api('/api/refresh', { method: 'POST', body: { period: '30d' } });
      toast(t('refreshed', { videos: r.videos.refreshed, channels: r.channels.refreshed }), 'ok');
      render();
    } catch (err) { toast(err.message, 'err'); } finally { e.target.disabled = false; }
  });
}

/* План 30: профили расходов -- сколько стоит сделать одно видео (озвучка, монтаж,
   ИИ-генерация, стоки) и постоянные расходы в месяц. Личные: у каждого свои. */
async function loadCostProfiles() {
  const box = $('#costProfiles');
  if (!box) return;
  let profiles = [];
  try { profiles = (await api('/api/cost-profiles')).profiles; } catch { return; }
  const field = (id, label, value = '') => `<label class="field-label">${label}<br>
    <input type="number" min="0" step="0.01" id="${id}" value="${esc(value)}" style="width:110px"></label>`;
  box.innerHTML = `<div class="card">
    ${sectionHead(t('costTitle'), t('costSub'))}
    ${profiles.length ? `<div class="table-wrap"><table><thead><tr><th>${t('colProfile')}</th><th class="num">${t('colPerVideo')}</th>
      <th class="num">${t('colPerMinute')}</th><th class="num">${t('colMonthly')}</th><th></th></tr></thead><tbody>${profiles.map((p) => `<tr>
        <td>${esc(p.name)}</td><td class="num">$${p.per_video_usd}</td><td class="num">$${p.per_minute_usd}</td>
        <td class="num">$${p.monthly_usd}</td>
        <td class="num"><button class="btn btn-ghost btn-sm js-cp-edit" data-id="${p.id}">${t('edit')}</button>
          <button class="btn btn-ghost btn-sm js-cp-del" data-id="${p.id}">${t('del')}</button></td></tr>`).join('')}</tbody></table></div>`
      : `<div class="section-sub">${t('costEmpty')}</div>`}
    <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;margin-top:12px">
      <label class="field-label">${t('fieldName')}<br><input type="text" id="cpName" placeholder="${esc(t('namePlaceholder'))}" style="width:200px"></label>
      ${field('cpVideo', t('fieldPerVideo'))}${field('cpMinute', t('fieldPerMinute'))}${field('cpMonth', t('fieldMonthly'))}
      <button class="btn" id="cpSave">${t('save')}</button>
    </div>
    <div class="section-sub" style="margin-top:6px">${t('costExample')}</div></div>`;
  let editing = null;
  box.querySelectorAll('.js-cp-edit').forEach((b) => b.addEventListener('click', () => {
    const p = profiles.find((x) => String(x.id) === b.dataset.id);
    editing = p.id;
    $('#cpName').value = p.name; $('#cpVideo').value = p.per_video_usd;
    $('#cpMinute').value = p.per_minute_usd; $('#cpMonth').value = p.monthly_usd;
  }));
  box.querySelectorAll('.js-cp-del').forEach((b) => b.addEventListener('click', async () => {
    try { await api(`/api/cost-profiles/${b.dataset.id}`, { method: 'DELETE' }); loadCostProfiles(); }
    catch (e) { toast(e.message, 'err'); }
  }));
  $('#cpSave').addEventListener('click', async () => {
    try {
      await api('/api/cost-profiles', { method: 'POST', body: { id: editing, name: $('#cpName').value,
        perVideoUsd: $('#cpVideo').value, perMinuteUsd: $('#cpMinute').value, monthlyUsd: $('#cpMonth').value } });
      toast(t('profileSaved'), 'ok');
      loadCostProfiles();
    } catch (e) { toast(e.message, 'err'); }
  });
}

/* План 16: правило YouTube API (III.E.4) — данные, полученные по ключу, хранить
   не дольше 30 дней без обновления. Воркер раз в сутки перечитывает самые старые
   строки; ничего не удаляется (решение владельца) — история остаётся на его риске. */
function freshnessCard(f) {
  if (!f) return '';
  const day = (iso) => (iso ? fmtDate(iso) : '—');
  return `<div class="card">
      ${sectionHead(t('freshnessTitle'), t('freshnessSub'))}
      <div class="tiles">
        ${tile(t('tileStaleVideos'), num(f.videos.stale), t('staleNote', { days: f.staleDays, total: num(f.videos.total) }))}
        ${tile(t('tileStaleChannels'), num(f.channels.stale), t('staleNote', { days: f.staleDays, total: num(f.channels.total) }))}
        ${tile(t('tileHistorySince'), day(f.historySince))}
      </div>
      <div class="section-sub" style="margin-top:10px">${t('freshnessNote', {
        videos: num(f.dailyCap.videos), channels: num(f.dailyCap.channels) })}</div>
    </div>`;
}

/* План 15 (5.9): свои уведомления -- Telegram или webhook и режим. Секреты
   уходят на сервер один раз и обратно не показываются. Без сохранённых
   настроек у локального пользователя работают NOTIFY_* из .env. */
async function loadNotifySettings() {
  let st;
  try { st = await api('/api/settings/notifications'); } catch { return; }
  const box = document.createElement('div');
  box.className = 'card';
  box.id = 'notifyCard';
  const src = { env: t('srcEnv'), settings: t('srcSettings'), none: t('srcNone') }[st.source] || st.source;
  box.innerHTML = `${sectionHead(t('notifyTitle'), t('notifySub', {
      source: esc(src), telegram: st.telegram ? t('yes') : t('no'),
      webhook: st.webhook ? t('yes') : t('no'), mode: esc(st.mode) }))}
    <div style="display:grid;gap:8px;max-width:520px">
      <input type="password" id="nfBot" placeholder="${st.telegram ? t('botSaved') : t('botPlaceholder')}" autocomplete="off">
      <input type="text" id="nfChat" placeholder="chat id" value="${esc(st.telegramChatId || '')}">
      <input type="password" id="nfHook" placeholder="${st.webhook ? t('hookSaved') : t('hookPlaceholder')}" autocomplete="off">
      <select id="nfMode">${['instant', 'digest', 'both', 'off'].map((m) =>
        `<option value="${m}"${m === st.mode ? ' selected' : ''}>${{ instant: t('modeInstant'), digest: t('modeDigest'), both: t('modeBoth'), off: t('modeOff') }[m]}</option>`).join('')}</select>
      <div><button class="btn btn-sm" id="nfSave" type="button">${t('save')}</button>
        <button class="btn btn-ghost btn-sm" id="nfTest" type="button">${t('testMessage')}</button>
        ${st.source === 'settings' ? `<button class="btn btn-ghost btn-sm" id="nfClear" type="button">${t('reset')}</button>` : ''}</div>
    </div>
    <div class="section-sub">${t('notifyNote')}</div>`;
  view.appendChild(box);
  const body = () => {
    const b = { telegramChatId: $('#nfChat').value, mode: $('#nfMode').value };
    if ($('#nfBot').value.trim()) b.telegramBotToken = $('#nfBot').value.trim();
    if ($('#nfHook').value.trim()) b.webhookUrl = $('#nfHook').value.trim();
    return b;
  };
  $('#nfSave').addEventListener('click', async () => {
    try { await api('/api/settings/notifications', { method: 'PUT', body: body() }); toast(t('saved'), 'ok'); box.remove(); loadNotifySettings(); }
    catch (e) { toast(e.message, 'err'); }
  });
  $('#nfTest').addEventListener('click', async () => {
    try { const r = await api('/api/settings/notifications/test', { method: 'POST' }); toast(r.sent ? t('sent') : t('notSent'), r.sent ? 'ok' : 'err'); }
    catch (e) { toast(e.message, 'err'); }
  });
  const clear = $('#nfClear');
  if (clear) clear.addEventListener('click', async () => {
    try { await api('/api/settings/notifications', { method: 'PUT', body: { clear: true } }); box.remove(); loadNotifySettings(); }
    catch (e) { toast(e.message, 'err'); }
  });
}

export { viewData };
