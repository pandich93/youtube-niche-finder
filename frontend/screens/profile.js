/* Экран «Профиль» (многопользовательский план P1-06): свой email, смена пароля
   (нужен текущий; остальные входы и API-токены завершаются) и смена email
   (ссылка подтверждения уходит на новый адрес, без почты на сервере -- нельзя),
   список своих входов с выходом на других устройствах (P1-04), двухфакторная
   защита отдельной карточкой (P2-04), часовой пояс дайджеста и напоминаний
   (P2-03; он есть и без NF_MULTI_USER). */
import { $, api, esc, toast, sectionHead, notice, ago } from '../ui.js';
import { view } from '../shared.js';
import { getMe } from '../plan-state.js';
import { dict, fmtDate, browserTimeZone } from '../i18n.js';
import ru from '../i18n/ru/profile.js';
import en from '../i18n/en/profile.js';

const t = dict({ ru, en });

async function viewProfile() {
  const me = getMe();
  if (!me.multiUser) {
    view.innerHTML = `${notice(t('singleUserOnly'))}${timezoneCard(me)}`;
    bindTimezone(me);
    return;
  }
  const mail = !!me.signup?.passwordReset;      // сервер умеет слать письма
  view.innerHTML = `
    <div class="card" style="max-width:520px">
      ${sectionHead(t('title'), t('signedInAs', { email: me.user.email }))}
    </div>
    <div class="card" id="twoFactorCard" style="max-width:620px;margin-top:12px"></div>
    <div class="card" style="max-width:520px;margin-top:12px">
      ${sectionHead(t('passwordTitle'), t('passwordSub'))}
      <form id="passwordForm" style="display:grid;gap:10px">
        <input type="password" name="current" required autocomplete="current-password" placeholder="${esc(t('currentPassword'))}">
        <input type="password" name="password" required autocomplete="new-password" placeholder="${esc(t('newPassword'))}">
        <input type="password" name="password2" required autocomplete="new-password" placeholder="${esc(t('repeatPassword'))}">
        <button class="btn btn-sm" type="submit">${esc(t('changePassword'))}</button>
      </form>
    </div>
    <div class="card" style="max-width:520px;margin-top:12px">
      ${sectionHead(t('emailTitle'), t('emailSub'))}
      ${mail ? '' : notice(esc(t('noMail')))}
      <div id="emailSent"></div>
      <form id="emailForm" style="display:grid;gap:10px">
        <input type="email" name="email" required autocomplete="email" placeholder="${esc(t('newEmail'))}" ${mail ? '' : 'disabled'}>
        <input type="password" name="password" required autocomplete="current-password" placeholder="${esc(t('currentPassword'))}" ${mail ? '' : 'disabled'}>
        <button class="btn btn-sm" type="submit" ${mail ? '' : 'disabled'}>${esc(t('sendConfirm'))}</button>
      </form>
    </div>
    ${timezoneCard(me)}
    <div class="card" id="sessionsCard" style="max-width:720px;margin-top:12px"></div>`;
  bindTimezone(me);
  loadSessions();
  loadTwoFactor();
  view.querySelector('#passwordForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    if (f.get('password') !== f.get('password2')) { toast(t('passwordsDiffer'), 'err'); return; }
    try {
      await api('/api/auth/password', { method: 'POST',
        body: { currentPassword: f.get('current'), password: f.get('password') } });
      e.target.reset();
      toast(t('passwordChanged'), 'ok');
    } catch (err) { toast(err.message, 'err'); }
  });
  view.querySelector('#emailForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      const r = await api('/api/auth/email', { method: 'POST',
        body: { email: f.get('email'), password: f.get('password') } });
      e.target.reset();
      view.querySelector('#emailSent').innerHTML = notice(esc(t('confirmSent', { email: r.sentTo })));
    } catch (err) { toast(err.message, 'err'); }
  });
}

/* P2-03: часовой пояс -- список IANA из браузера (старые браузеры без
   Intl.supportedValuesOf: свой, браузерный и UTC), «как в браузере» одной
   кнопкой, пустое значение -- пояс по умолчанию сервера. */
function zoneList(me) {
  let zones = [];
  try { zones = Intl.supportedValuesOf('timeZone'); } catch { /* старый браузер */ }
  return [...new Set([...zones, me.timezone, browserTimeZone(), 'UTC'].filter(Boolean))].sort();
}

function timezoneCard(me) {
  const current = me.timezone || '';
  const browser = browserTimeZone();
  const fallback = me.defaultTimezone || t('tzServerTime');
  return `<div class="card" id="timezoneCard" style="max-width:520px;margin-top:12px">
      ${sectionHead(t('tzTitle'), t('tzSub'))}
      <form id="timezoneForm" style="display:grid;gap:10px">
        <select name="timezone" aria-label="${esc(t('tzTitle'))}">
          <option value="">${esc(t('tzDefault', { zone: fallback }))}</option>
          ${zoneList(me).map((z) => `<option value="${esc(z)}" ${z === current ? 'selected' : ''}>${esc(z)}</option>`).join('')}
        </select>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn btn-sm" type="submit">${esc(t('tzSave'))}</button>
          ${browser && browser !== current ? `<button class="btn btn-ghost btn-sm" id="tzBrowser" type="button">${esc(t('tzUseBrowser', { zone: browser }))}</button>` : ''}
        </div>
      </form>
    </div>`;
}

function bindTimezone(me) {
  const form = view.querySelector('#timezoneForm');
  if (!form) return;
  const save = async (zone) => {
    try {
      const r = await api('/api/me/timezone', { method: 'PUT', body: { timezone: zone } });
      me.timezone = r.timezone;
      me.defaultTimezone = r.defaultTimezone;
      view.querySelector('#timezoneCard').outerHTML = timezoneCard(me);
      bindTimezone(me);
      toast(r.timezone ? t('tzSaved', { zone: r.timezone }) : t('tzReset'), 'ok');
    } catch (err) { toast(err.message, 'err'); }
  };
  form.addEventListener('submit', (e) => { e.preventDefault(); save(new FormData(form).get('timezone')); });
  view.querySelector('#tzBrowser')?.addEventListener('click', () => save(browserTimeZone()));
}

/* Первый вход (NF_MULTI_USER=1): пояс не выбран -- молча берём браузерный.
   Иначе дайджест придёт по времени сервера, которое у большинства не своё;
   onlyIfEmpty не перебьёт пояс, выбранный на другом устройстве, а сменить его
   можно здесь же, в профиле. Без входа -- не трогаем: там пояс задаёт сам
   оператор (TZ / NF_DEFAULT_TIMEZONE в .env). */
async function adoptBrowserTimezone(me) {
  const zone = browserTimeZone();
  if (!me.multiUser || !me.user || me.timezone || !zone) return;
  try {
    const r = await api('/api/me/timezone', { method: 'PUT', body: { timezone: zone, onlyIfEmpty: true } });
    me.timezone = r.timezone;
  } catch { /* неизвестный серверу пояс -- останется время по умолчанию */ }
}

/* P1-04: где выполнен вход -- текущая сессия помечена, остальные можно
   завершить по одной или все сразу. Токенов сервер не отдаёт. */
async function loadSessions() {
  const box = view.querySelector('#sessionsCard');
  if (!box) return;
  let d;
  try { d = await api('/api/auth/sessions'); } catch (e) { box.innerHTML = notice(esc(e.message), 'error'); return; }
  const others = d.sessions.filter((s) => !s.current).length;
  box.innerHTML = `
    ${sectionHead(t('sessionsTitle'), t('sessionsSub', { idle: d.idleDays, max: d.maxDays }),
      others ? `<button class="btn btn-ghost btn-sm" id="sessionsEndOthers" type="button">${esc(t('endOthers'))}</button>` : '')}
    <ul class="digest-list">${d.sessions.map((s) => `<li style="white-space:normal">
      <b title="${esc(s.userAgent || '')}">${esc(deviceLabel(s.userAgent))}</b>
      · ${s.ip ? esc(s.ip) : esc(t('unknownAddress'))}
      · ${esc(t('lastActive', { ago: ago(s.lastSeenAt) }))}
      · ${esc(t('signedInAt', { date: fmtDate(s.createdAt) }))}
      ${s.current ? `<span class="chip chip-accent">${esc(t('thisDevice'))}</span>`
        : `<button class="btn btn-ghost btn-sm js-session-end" data-id="${s.id}" type="button">${esc(t('endSession'))}</button>`}
    </li>`).join('')}</ul>`;
  box.querySelector('#sessionsEndOthers')?.addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    if (btn.dataset.armed !== '1') { btn.dataset.armed = '1'; btn.textContent = t('endOthersConfirm'); return; }
    try {
      const r = await api('/api/auth/sessions/end-others', { method: 'POST' });
      toast(t('othersEnded', { n: r.ended }), 'ok');
      loadSessions();
    } catch (err) { toast(err.message, 'err'); }
  });
  box.querySelectorAll('.js-session-end').forEach((btn) => btn.addEventListener('click', async () => {
    if (btn.dataset.armed !== '1') { btn.dataset.armed = '1'; btn.textContent = t('endSessionConfirm'); return; }
    try { await api(`/api/auth/sessions/${btn.dataset.id}`, { method: 'DELETE' }); loadSessions(); }
    catch (err) { toast(err.message, 'err'); }
  }));
}

/* ------------------------------------------------ двухфакторная защита (P2-04)
   QR-код не рисуем намеренно: внешние сервисы и CDN нельзя, а свой генератор
   QR -- несколько сотен строк кода без проверки в CI ради одного экрана.
   Вместо него -- секрет группами по 4 знака для ручного ввода и ссылка
   otpauth:// (на телефоне открывает приложение-аутентификатор сразу). */

async function loadTwoFactor(onEnabled) {
  const box = view.querySelector('#twoFactorCard');
  if (!box) return;
  let st;
  try { st = await api('/api/auth/2fa'); } catch (e) { box.innerHTML = notice(esc(e.message), 'error'); return; }
  if (st.enabled) { drawTwoFactorOn(box, st); return; }
  box.innerHTML = `
    ${sectionHead(t('tfTitle'), t('tfSubOff'))}
    ${st.required ? notice(esc(t('tfRequired'))) : ''}
    ${st.available ? `<button class="btn btn-sm" id="tfStart" type="button">${esc(t('tfStart'))}</button>`
      : notice(esc(t('tfUnavailable')))}
    <div id="tfSetup"></div>`;
  box.querySelector('#tfStart')?.addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    let s;
    try { s = await api('/api/auth/2fa/setup', { method: 'POST' }); } catch (err) { toast(err.message, 'err'); return; }
    btn.hidden = true;
    drawSetup(box, s, onEnabled);
  });
}

function inGroups(secret) { return (secret.match(/.{1,4}/g) || []).join(' '); }

function drawSetup(box, s, onEnabled) {
  box.querySelector('#tfSetup').innerHTML = `
    <ol style="display:grid;gap:10px;padding-left:18px">
      <li>${esc(t('tfStep1'))}</li>
      <li>${esc(t('tfStep2'))}<br>
        <code style="font-size:15px;letter-spacing:1px;user-select:all">${esc(inGroups(s.secret))}</code><br>
        <span style="color:var(--muted)">${esc(t('tfManual', { issuer: s.issuer, account: s.account }))}</span><br>
        <a href="${esc(s.uri)}">${esc(t('tfOpenApp'))}</a>
        <details><summary>${esc(t('tfShowUri'))}</summary>
          <code style="word-break:break-all;user-select:all">${esc(s.uri)}</code></details>
      </li>
      <li>${esc(t('tfStep3'))}</li>
    </ol>
    <form id="tfEnableForm" style="display:grid;gap:10px;max-width:260px">
      <input type="text" name="code" inputmode="numeric" autocomplete="one-time-code" required placeholder="123456">
      <button class="btn btn-sm" type="submit">${esc(t('tfConfirm'))}</button>
    </form>`;
  box.querySelector('#tfEnableForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      const r = await api('/api/auth/2fa/enable', { method: 'POST',
        body: { code: new FormData(e.target).get('code') } });
      showBackupCodes(box, r.backupCodes, t('tfEnabled', { n: r.sessionsEnded }), onEnabled);
    } catch (err) { toast(err.message, 'err'); }
  });
}

/* Резервные коды -- один раз: на сервере только их хэши. */
function showBackupCodes(box, codes, message, done) {
  box.innerHTML = `
    ${sectionHead(t('tfCodesTitle'), t('tfCodesSub'))}
    ${notice(esc(message), 'ok')}
    <pre style="user-select:all;font-size:15px;line-height:1.7">${codes.map(esc).join('\n')}</pre>
    <button class="btn btn-sm" id="tfCodesDone" type="button">${esc(t('tfCodesSaved'))}</button>`;
  box.querySelector('#tfCodesDone').addEventListener('click', () => (done ? done() : loadTwoFactor()));
}

function drawTwoFactorOn(box, st) {
  box.innerHTML = `
    ${sectionHead(t('tfTitle'), t('tfSubOn', { date: fmtDate(st.enabledAt), n: st.backupCodesLeft }))}
    ${st.backupCodesLeft <= 2 ? notice(esc(t('tfFewCodes'))) : ''}
    <form id="tfCodesForm" style="display:grid;gap:10px;max-width:320px">
      <input type="text" name="code" inputmode="numeric" autocomplete="one-time-code" required placeholder="${esc(t('tfCodePlaceholder'))}">
      <button class="btn btn-ghost btn-sm" type="submit">${esc(t('tfRegenerate'))}</button>
    </form>
    ${st.required ? notice(esc(t('tfRequiredOn'))) : `
    <form id="tfDisableForm" style="display:grid;gap:10px;max-width:320px;margin-top:14px">
      <input type="password" name="password" required autocomplete="current-password" placeholder="${esc(t('currentPassword'))}">
      <input type="text" name="code" required autocomplete="one-time-code" placeholder="${esc(t('tfCodePlaceholder'))}">
      <button class="btn btn-ghost btn-sm" type="submit">${esc(t('tfDisable'))}</button>
    </form>`}`;
  box.querySelector('#tfCodesForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      const r = await api('/api/auth/2fa/backup-codes', { method: 'POST',
        body: { code: new FormData(e.target).get('code') } });
      showBackupCodes(box, r.backupCodes, t('tfRegenerated'));
    } catch (err) { toast(err.message, 'err'); }
  });
  box.querySelector('#tfDisableForm')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      await api('/api/auth/2fa/disable', { method: 'POST',
        body: { password: f.get('password'), code: f.get('code') } });
      toast(t('tfDisabled'), 'ok');
      loadTwoFactor();
    } catch (err) { toast(err.message, 'err'); }
  });
}

/* Администратор без 2FA при NF_ADMIN_2FA=required: до включения -- только этот
   экран (app.js); после -- обычный дашборд. */
function viewTwoFactorSetup() {
  $('#crumbSection').textContent = t('tfTitle');
  view.innerHTML = `${notice(esc(t('tfAdminRequired')))}
    <div class="card" id="twoFactorCard" style="max-width:620px;margin-top:12px"></div>`;
  loadTwoFactor(() => { location.hash = '#/overview'; location.reload(); });
}

/* Коротко «браузер · система» из User-Agent; целиком он в подсказке. */
function deviceLabel(ua) {
  if (!ua) return t('unknownDevice');
  const browser = [[/Edg\//, 'Edge'], [/OPR\//, 'Opera'], [/YaBrowser\//, 'Yandex'], [/Firefox\//, 'Firefox'],
    [/Chrome\//, 'Chrome'], [/Safari\//, 'Safari']].find(([rx]) => rx.test(ua))?.[1];
  const os = [[/Windows/, 'Windows'], [/Android/, 'Android'], [/iPhone|iPad/, 'iOS'], [/Mac OS X/, 'macOS'],
    [/Linux/, 'Linux']].find(([rx]) => rx.test(ua))?.[1];
  return [browser, os].filter(Boolean).join(' · ') || ua.slice(0, 60);
}

export { viewProfile, viewTwoFactorSetup, adoptBrowserTimezone };
