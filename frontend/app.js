/* Точка входа дашборда: тема, глобальные фильтры, подвал, первый render().
   Экраны -- screens/*.js, роутинг -- router.js, общее -- shared.js. */
import { $, api, esc, notice, state } from './ui.js';
import { loadFootStat } from './shared.js';
import { render } from './router.js';

/* --------------------------------------------------------------- запуск */

async function loadNiches() {
  try {
    const d = await api('/api/niches');
    const sel = $('#globalNiche');
    sel.innerHTML = '<option value="">все ниши</option>' +
      (d.niches || []).map((n) => `<option value="${esc(n.slug)}">${esc(n.slug)} (${n.video_count})</option>`).join('');
    sel.value = state.niche;
  } catch { /* фронт работает и без списка ниш */ }
}

function initChrome() {
  const per = $('#globalPeriod');
  per.value = state.period;
  per.addEventListener('change', () => {
    state.period = per.value; localStorage.setItem('nf.period', state.period); render();
  });
  $('#globalNiche').addEventListener('change', (e) => {
    state.niche = e.target.value; localStorage.setItem('nf.niche', state.niche); render();
  });
  $('#reloadBtn').addEventListener('click', () => { loadFootStat(); loadNiches(); render(); });

  const themeBtn = $('#themeToggle');
  const applyTheme = (t) => {
    document.documentElement.dataset.theme = t;
    localStorage.setItem('nf.theme', t);
    themeBtn.textContent = t === 'dark' ? 'Светлая тема' : 'Тёмная тема';
  };
  applyTheme(localStorage.getItem('nf.theme') || 'dark');
  themeBtn.addEventListener('click', () =>
    applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));

  addEventListener('hashchange', render);
}

/* ------------------------------------------------ вход (план 15)
   Только при NF_MULTI_USER=1 на сервере. Аккаунты создаёт администратор
   (cli.py create-user), регистрации нет. Сессия -- HttpOnly cookie, скрипт её
   не видит и не хранит. */
function showSignIn(message = '') {
  $('#crumbSection').textContent = 'Вход';
  $('#view').innerHTML = `${message ? notice(esc(message), 'error') : ''}
    <div class="card" style="max-width:420px">
      <div class="section-head"><div><div class="section-title">Вход в niche-finder</div>
        <div class="section-sub">аккаунт выдаёт администратор этого сервера</div></div></div>
      <form id="signInForm" style="display:grid;gap:10px">
        <input type="email" id="signInEmail" placeholder="email" autocomplete="username" required>
        <input type="password" id="signInPassword" placeholder="пароль" autocomplete="current-password" required>
        <button class="btn" type="submit">Войти</button>
      </form>
    </div>`;
  $('#signInForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      await api('/api/auth/login', { method: 'POST',
        body: { email: $('#signInEmail').value, password: $('#signInPassword').value } });
      location.reload();
    } catch (err) { showSignIn(err.message); }
  });
}

async function start() {
  initChrome();
  let me = { multiUser: false, user: null };
  try { me = await api('/api/auth/me'); } catch { /* старый сервер без входа */ }
  if (me.multiUser && !me.user) { showSignIn(); return; }
  if (me.multiUser) {
    const out = $('#signOut');
    out.hidden = false;
    out.textContent = `Выйти (${me.user.email})`;
    out.addEventListener('click', async () => {
      await api('/api/auth/logout', { method: 'POST' }).catch(() => {});
      location.reload();
    });
    // План 15 (5.5): личная доля общей квоты YouTube на сегодня.
    const qd = me.quota;
    if (qd) {
      const part = (used, limit) => (limit ? `${used}/${limit}` : `${used}, без лимита`);
      const box = $('#myQuota');
      box.hidden = false;
      box.textContent = `Ваша квота сегодня: ${part(qd.units, qd.limits.units)} units, `
        + `поисков ${part(qd.searchCalls, qd.limits.searchCalls)}`;
    }
  }
  addEventListener('nf:signin-required', () => showSignIn('Сессия закончилась — войдите снова.'));
  loadNiches();
  loadFootStat();
  render();
}

start();
