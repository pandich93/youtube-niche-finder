/* Экран «Профиль» (многопользовательский план P1-06): свой email, смена пароля
   (нужен текущий; остальные входы и API-токены завершаются) и смена email
   (ссылка подтверждения уходит на новый адрес, без почты на сервере -- нельзя),
   список своих входов с выходом на других устройствах (P1-04). */
import { api, esc, toast, sectionHead, notice, ago } from '../ui.js';
import { view } from '../shared.js';
import { getMe } from '../plan-state.js';
import { dict, fmtDate } from '../i18n.js';
import ru from '../i18n/ru/profile.js';
import en from '../i18n/en/profile.js';

const t = dict({ ru, en });

async function viewProfile() {
  const me = getMe();
  if (!me.multiUser) {
    view.innerHTML = notice(t('singleUserOnly'));
    return;
  }
  const mail = !!me.signup?.passwordReset;      // сервер умеет слать письма
  view.innerHTML = `
    <div class="card" style="max-width:520px">
      ${sectionHead(t('title'), t('signedInAs', { email: me.user.email }))}
    </div>
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
    <div class="card" id="sessionsCard" style="max-width:720px;margin-top:12px"></div>`;
  loadSessions();
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

/* Коротко «браузер · система» из User-Agent; целиком он в подсказке. */
function deviceLabel(ua) {
  if (!ua) return t('unknownDevice');
  const browser = [[/Edg\//, 'Edge'], [/OPR\//, 'Opera'], [/YaBrowser\//, 'Yandex'], [/Firefox\//, 'Firefox'],
    [/Chrome\//, 'Chrome'], [/Safari\//, 'Safari']].find(([rx]) => rx.test(ua))?.[1];
  const os = [[/Windows/, 'Windows'], [/Android/, 'Android'], [/iPhone|iPad/, 'iOS'], [/Mac OS X/, 'macOS'],
    [/Linux/, 'Linux']].find(([rx]) => rx.test(ua))?.[1];
  return [browser, os].filter(Boolean).join(' · ') || ua.slice(0, 60);
}

export { viewProfile };
