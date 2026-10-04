/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, num, esc, toast, tile, sectionHead, notice, state } from '../ui.js';
import { view, plabel, collectForm, wireCollect, render } from '../shared.js';

/* ----------------------------------------------------------------- Данные */

async function viewData() {
  const [h, cov, fresh] = await Promise.all([
    api('/api/health'), api(`/api/coverage${q({ period: state.period })}`),
    api('/api/freshness').catch(() => null)]);
  view.innerHTML = `
    ${h.hasApiKey ? notice('Ключ YouTube API задан — сбор доступен.', 'ok')
                  : notice('Ключ YouTube API не задан. Впишите <code>YOUTUBE_API_KEY</code> в <code>.env</code> и перезапустите <code>docker compose up -d web</code>.', 'error')}
    ${h.historyAvailable ? '' : notice('Истории статистики нет — VPH за 24 часа, ускорение и рост каналов будут пустыми, пока воркер не поработает. Запустите <code>docker compose up -d worker</code>.')}
    <div class="card">
      ${sectionHead('База', h.db.db_path)}
      <div class="tiles">
        ${tile('Каналов', num(h.db.channels))}
        ${tile('Видео', num(h.db.videos))}
        ${tile('Ниш', num(h.db.niches))}
        ${tile('Снимков видео', num(h.db.video_stat_snapshots))}
        ${tile('Снимков каналов', num(h.db.channel_stat_snapshots))}
        ${tile('Смен заголовка/обложки', num(h.db.title_thumbnail_changes))}
        ${tile('Видео в окне', num(cov.videosPublishedInPeriod), plabel(state.period))}
      </div>
    </div>
    ${freshnessCard(fresh)}
    <div id="costProfiles"></div>
    ${collectForm()}
    <div class="card">
      ${sectionHead('Обновить статистику', 'перечитывает счётчики и дописывает снимок — из этого берутся скорости')}
      <button class="btn btn-ghost" id="refreshBtn" type="button">Обновить сейчас</button>
    </div>`;
  wireCollect(render);
  loadNotifySettings();
  loadCostProfiles();
  $('#refreshBtn')?.addEventListener('click', async (e) => {
    e.target.disabled = true;
    try {
      const r = await api('/api/refresh', { method: 'POST', body: { period: '30d' } });
      toast(`Обновлено ${r.videos.refreshed} видео и ${r.channels.refreshed} каналов`, 'ok');
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
    ${sectionHead('Профили расходов', 'для калькулятора чистой прибыли на экранах канала и ниши, в долларах')}
    ${profiles.length ? `<div class="table-wrap"><table><thead><tr><th>Профиль</th><th class="num">За видео</th>
      <th class="num">За минуту</th><th class="num">В месяц</th><th></th></tr></thead><tbody>${profiles.map((p) => `<tr>
        <td>${esc(p.name)}</td><td class="num">$${p.per_video_usd}</td><td class="num">$${p.per_minute_usd}</td>
        <td class="num">$${p.monthly_usd}</td>
        <td class="num"><button class="btn btn-ghost btn-sm js-cp-edit" data-id="${p.id}">изменить</button>
          <button class="btn btn-ghost btn-sm js-cp-del" data-id="${p.id}">удалить</button></td></tr>`).join('')}</tbody></table></div>`
      : '<div class="section-sub">Профилей пока нет: прибыль считается без расходов.</div>'}
    <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;margin-top:12px">
      <label class="field-label">Название<br><input type="text" id="cpName" placeholder="озвучка + монтаж" style="width:200px"></label>
      ${field('cpVideo', 'За видео, $')}${field('cpMinute', 'За минуту видео, $')}${field('cpMonth', 'В месяц, $')}
      <button class="btn" id="cpSave">Сохранить</button>
    </div>
    <div class="section-sub" style="margin-top:6px">Пример: озвучка $0,3 за минуту, монтаж $15 за видео, подписки $40 в месяц.
      Профиль с тем же названием перезаписывается.</div></div>`;
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
      toast('Профиль сохранён', 'ok');
      loadCostProfiles();
    } catch (e) { toast(e.message, 'err'); }
  });
}

/* План 16: правило YouTube API (III.E.4) — данные, полученные по ключу, хранить
   не дольше 30 дней без обновления. Воркер раз в сутки перечитывает самые старые
   строки; ничего не удаляется (решение владельца) — история остаётся на его риске. */
function freshnessCard(f) {
  if (!f) return '';
  const day = (iso) => (iso ? new Date(iso).toLocaleDateString('ru-RU') : '—');
  return `<div class="card">
      ${sectionHead('Хранение данных', `правило YouTube API: данные, полученные по ключу, — не дольше 30 дней без обновления`)}
      <div class="tiles">
        ${tile('Видео не обновлялись', num(f.videos.stale), `больше ${f.staleDays} дн. из ${num(f.videos.total)}`)}
        ${tile('Каналы не обновлялись', num(f.channels.stale), `больше ${f.staleDays} дн. из ${num(f.channels.total)}`)}
        ${tile('История снимков с', day(f.historySince))}
      </div>
      <div class="section-sub" style="margin-top:10px">Воркер раз в сутки перечитывает из API до
        ${num(f.dailyCap.videos)} видео и ${num(f.dailyCap.channels)} каналов, начиная с самых старых:
        названия, описания и счётчики. <b>Ничего не удаляется</b> — история снимков и перепаковок хранится
        без срока, а это правилами YouTube не разрешено без отдельного одобрения. Для себя это ваш риск;
        перед тем как давать доступ другим людям, прочитайте SECURITY.md и PRIVACY.md.</div>
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
  const src = { env: 'из .env', settings: 'свои настройки', none: 'не настроены' }[st.source] || st.source;
  box.innerHTML = `${sectionHead('Уведомления', `сейчас: ${esc(src)} · Telegram ${st.telegram ? 'да' : 'нет'}
      · webhook ${st.webhook ? 'да' : 'нет'} · режим ${esc(st.mode)}`)}
    <div style="display:grid;gap:8px;max-width:520px">
      <input type="password" id="nfBot" placeholder="${st.telegram ? 'токен бота сохранён — введите новый, чтобы заменить' : 'токен Telegram-бота'}" autocomplete="off">
      <input type="text" id="nfChat" placeholder="chat id" value="${esc(st.telegramChatId || '')}">
      <input type="password" id="nfHook" placeholder="${st.webhook ? 'webhook сохранён — введите новый, чтобы заменить' : 'или https-адрес webhook'}" autocomplete="off">
      <select id="nfMode">${['instant', 'digest', 'both', 'off'].map((m) =>
        `<option value="${m}"${m === st.mode ? ' selected' : ''}>${{ instant: 'сразу', digest: 'утренний дайджест', both: 'и то и другое', off: 'выключены' }[m]}</option>`).join('')}</select>
      <div><button class="btn btn-sm" id="nfSave" type="button">Сохранить</button>
        <button class="btn btn-ghost btn-sm" id="nfTest" type="button">Тестовое сообщение</button>
        ${st.source === 'settings' ? '<button class="btn btn-ghost btn-sm" id="nfClear" type="button">Сбросить</button>' : ''}</div>
    </div>
    <div class="section-sub">Алерты приходят только по вашим отслеживаемым каналам. Токен и адрес хранятся зашифрованными
      (нужен <code>OWN_TOKENS_KEY</code> в <code>.env</code>); webhook — только https на публичный адрес.</div>`;
  view.appendChild(box);
  const body = () => {
    const b = { telegramChatId: $('#nfChat').value, mode: $('#nfMode').value };
    if ($('#nfBot').value.trim()) b.telegramBotToken = $('#nfBot').value.trim();
    if ($('#nfHook').value.trim()) b.webhookUrl = $('#nfHook').value.trim();
    return b;
  };
  $('#nfSave').addEventListener('click', async () => {
    try { await api('/api/settings/notifications', { method: 'PUT', body: body() }); toast('Сохранено', 'ok'); box.remove(); loadNotifySettings(); }
    catch (e) { toast(e.message, 'err'); }
  });
  $('#nfTest').addEventListener('click', async () => {
    try { const r = await api('/api/settings/notifications/test', { method: 'POST' }); toast(r.sent ? 'Отправлено' : 'Не отправилось', r.sent ? 'ok' : 'err'); }
    catch (e) { toast(e.message, 'err'); }
  });
  const clear = $('#nfClear');
  if (clear) clear.addEventListener('click', async () => {
    try { await api('/api/settings/notifications', { method: 'PUT', body: { clear: true } }); box.remove(); loadNotifySettings(); }
    catch (e) { toast(e.message, 'err'); }
  });
}

export { viewData };
