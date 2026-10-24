/* Вход и всё, что можно сделать без входа (план 15, многопользовательский
   P1-06): вход, регистрация по ссылке из письма, «Забыли пароль?» и ссылки из
   писем #/signup/<токен>, #/reset/<токен>, #/email/<токен>. Только при
   NF_MULTI_USER=1. Сессия -- HttpOnly cookie, скрипт её не видит и не хранит.
   P2-04: у аккаунта с двухфакторной защитой после пароля (и после сброса по
   ссылке) -- второй шаг, код из приложения или резервный код. */
import { $, api, esc, notice } from './ui.js';
import { dict } from './i18n.js';
import ru from './i18n/ru/account.js';
import en from './i18n/en/account.js';

const t = dict({ ru, en });

// что предлагает сервер: регистрация и сброс пароля есть только с почтой (SMTP)
let offers = { signup: false, passwordReset: false };

function setOffers(next) { if (next) offers = next; }

function screen(title, sub, body, message = '', kind = 'error') {
  $('#crumbSection').textContent = t('crumb');
  $('#view').innerHTML = `${message ? notice(esc(message), kind) : ''}
    <div class="card" style="max-width:420px">
      <div class="section-head"><div><div class="section-title">${esc(title)}</div>
        ${sub ? `<div class="section-sub">${esc(sub)}</div>` : ''}</div></div>
      ${body}
    </div>`;
}

function linkRow(items) {
  const shown = items.filter((x) => x.on);
  if (!shown.length) return '';
  return `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">${shown.map((x) =>
    `<button class="btn btn-ghost btn-sm" type="button" data-go="${x.go}">${esc(x.label)}</button>`).join('')}</div>`;
}

function wireLinks() {
  const go = { signin: () => showSignIn(), signup: () => showSignUp(), forgot: () => showForgot() };
  document.querySelectorAll('#view [data-go]').forEach((b) =>
    b.addEventListener('click', () => go[b.dataset.go]()));
}

function passwordFields() {
  return `<input type="password" id="newPassword" placeholder="${esc(t('newPassword'))}" autocomplete="new-password" required>
    <input type="password" id="newPassword2" placeholder="${esc(t('repeatPassword'))}" autocomplete="new-password" required>`;
}

function chosenPassword() {
  const p = $('#newPassword').value;
  if (p !== $('#newPassword2').value) throw new Error(t('passwordsDiffer'));
  return p;
}

// токен из ссылки не остаётся в адресной строке и в истории браузера
function forgetToken(kind) { history.replaceState(null, '', `#/${kind}`); }

function showSignIn(message = '', kind = 'error') {
  const more = linkRow([
    { on: offers.signup, go: 'signup', label: t('createAccount') },
    { on: offers.passwordReset, go: 'forgot', label: t('forgotPassword') },
  ]);
  screen(t('signInTitle'), offers.signup ? t('signInSubOpen') : t('signInSub'), `
    <form id="signInForm" style="display:grid;gap:10px">
      <input type="email" id="signInEmail" placeholder="email" autocomplete="username" required>
      <input type="password" id="signInPassword" placeholder="${esc(t('passwordPlaceholder'))}" autocomplete="current-password" required>
      <button class="btn" type="submit">${esc(t('signIn'))}</button>
    </form>${more}`, message, kind);
  wireLinks();
  $('#signInForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      const r = await api('/api/auth/login', { method: 'POST',
        body: { email: $('#signInEmail').value, password: $('#signInPassword').value } });
      if (r?.mfaRequired) { showSecondStep(r.challenge); return; }
      location.reload();
    } catch (err) { showSignIn(err.message); }
  });
}

/* P2-04: второй шаг. Промежуточный токен живёт только в памяти этой страницы,
   несколько минут и до нескольких ошибок -- потом вход начинается с пароля. */
function showSecondStep(challenge, message = '', kind = 'error', done = () => location.reload()) {
  screen(t('codeTitle'), t('codeSub'), `
    <form id="codeForm" style="display:grid;gap:10px">
      <input type="text" id="signInCode" inputmode="numeric" autocomplete="one-time-code" placeholder="${esc(t('codePlaceholder'))}" required>
      <button class="btn" type="submit">${esc(t('confirmCode'))}</button>
    </form>${linkRow([{ on: true, go: 'signin', label: t('backToSignIn') }])}`, message, kind);
  wireLinks();
  $('#signInCode').focus();
  $('#codeForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      await api('/api/auth/login/2fa', { method: 'POST', body: { challenge, code: $('#signInCode').value } });
      done();
    } catch (err) { showSecondStep(challenge, err.message, 'error', done); }
  });
}

/* Шаг 1 из 2: только адрес. Пароль задаётся по ссылке из письма -- его выбирает
   тот, кто читает этот ящик. */
function showSignUp(message = '') {
  screen(t('signUpTitle'), t('signUpSub'), `
    <form id="signUpForm" style="display:grid;gap:10px">
      <input type="email" id="signUpEmail" placeholder="email" autocomplete="email" required>
      <button class="btn" type="submit">${esc(t('sendLink'))}</button>
    </form>${linkRow([{ on: true, go: 'signin', label: t('haveAccount') }])}`, message);
  wireLinks();
  $('#signUpForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      const r = await api('/api/auth/register', { method: 'POST', body: { email: $('#signUpEmail').value } });
      showSignIn(r.detail, '');
    } catch (err) { showSignUp(err.message); }
  });
}

function showForgot(message = '') {
  screen(t('forgotTitle'), t('forgotSub'), `
    <form id="forgotForm" style="display:grid;gap:10px">
      <input type="email" id="forgotEmail" placeholder="email" autocomplete="username" required>
      <button class="btn" type="submit">${esc(t('sendLink'))}</button>
    </form>${linkRow([{ on: true, go: 'signin', label: t('backToSignIn') }])}`, message);
  wireLinks();
  $('#forgotForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      const r = await api('/api/auth/password/forgot', { method: 'POST', body: { email: $('#forgotEmail').value } });
      showSignIn(r.detail, '');
    } catch (err) { showForgot(err.message); }
  });
}

function signedIn() {
  location.hash = '#/overview';
  location.reload();
}

async function completeSignUp(token) {
  let email;
  try {
    email = (await api('/api/auth/register/link', { method: 'POST', body: { token } })).email;
  } catch (err) { showSignIn(err.message); return; }
  const draw = (message = '') => {
    screen(t('choosePasswordTitle'), t('choosePasswordSub', { email }), `
      <form id="completeForm" style="display:grid;gap:10px">${passwordFields()}
        <button class="btn" type="submit">${esc(t('createAccountBtn'))}</button>
      </form>`, message);
    $('#completeForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      try {
        await api('/api/auth/register/complete', { method: 'POST', body: { token, password: chosenPassword() } });
        signedIn();
      } catch (err) { draw(err.message); }
    });
  };
  draw();
}

function resetPassword(token) {
  const draw = (message = '') => {
    screen(t('resetTitle'), t('resetSub'), `
      <form id="resetForm" style="display:grid;gap:10px">${passwordFields()}
        <button class="btn" type="submit">${esc(t('savePassword'))}</button>
      </form>${linkRow([{ on: true, go: 'signin', label: t('backToSignIn') }])}`, message);
    wireLinks();
    $('#resetForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      try {
        const r = await api('/api/auth/password/reset', { method: 'POST', body: { token, password: chosenPassword() } });
        // пароль сменён, но двухфакторная защита осталась -- нужен код
        if (r?.mfaRequired) { showSecondStep(r.challenge, t('resetNeedsCode'), '', signedIn); return; }
        signedIn();
      } catch (err) { draw(err.message); }
    });
  };
  draw();
}

async function confirmEmail(token, signedInNow) {
  try {
    const r = await api('/api/auth/email/confirm', { method: 'POST', body: { token } });
    screen(t('emailChangedTitle'), '', `<p>${esc(t('emailChanged', { email: r.email }))}</p>
      <button class="btn" type="button" id="afterEmail">${esc(t('continue'))}</button>`);
    $('#afterEmail').addEventListener('click', () => {
      location.hash = signedInNow ? '#/profile' : '#/overview';
      location.reload();
    });
  } catch (err) {
    if (signedInNow) screen(t('emailChangedTitle'), '', '', err.message);
    else showSignIn(err.message);
  }
}

/* Ссылка из письма? Тогда рисуем её экран и отвечаем true -- app.js не
   запускает обычный экран. */
function openAccountLink(signedInNow) {
  const m = location.hash.match(/^#\/(signup|reset|email)\/([\w-]+)$/);
  if (!m) return false;
  const [, kind, token] = m;
  forgetToken(kind);
  if (kind === 'signup') completeSignUp(token);
  else if (kind === 'reset') resetPassword(token);
  else confirmEmail(token, signedInNow);
  return true;
}

export { setOffers, showSignIn, openAccountLink };
