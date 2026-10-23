/* Экран «Профиль» (многопользовательский план P1-06): свой email, смена пароля
   (нужен текущий; остальные входы и API-токены завершаются) и смена email
   (ссылка подтверждения уходит на новый адрес, без почты на сервере -- нельзя). */
import { api, esc, toast, sectionHead, notice } from '../ui.js';
import { view } from '../shared.js';
import { getMe } from '../plan-state.js';
import { dict } from '../i18n.js';
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
    </div>`;
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

export { viewProfile };
