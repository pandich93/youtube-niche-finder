/* Точка входа дашборда: тема, язык, глобальные фильтры, подвал, первый render().
   Экраны -- screens/*.js, роутинг -- router.js, общее -- shared.js. */
import { $, api, esc, notice, state, loadScores } from './ui.js';
import { loadFootStat } from './shared.js';
import { render, routeTitle } from './router.js';
import { setMe, applyNav, renderPlanLine } from './plan-state.js';
import { dict, getLocale, hasStoredLocale, setLocale, onLocaleChange, translateStatic } from './i18n.js';
import ru from './i18n/ru/app.js';
import en from './i18n/en/app.js';

const t = dict({ ru, en });

// названия языков на самих этих языках -- кнопка показывает, на какой переключит
const LANG_NAMES = { ru: 'Русский', en: 'English' };

let me = { multiUser: false, user: null };

/* --------------------------------------------------------------- запуск */

async function loadNiches() {
  try {
    const d = await api('/api/niches');
    const sel = $('#globalNiche');
    sel.innerHTML = `<option value="">${esc(t('allNiches'))}</option>` +
      (d.niches || []).map((n) => `<option value="${esc(n.slug)}">${esc(n.slug)} (${n.video_count})</option>`).join('');
    sel.value = state.niche;
  } catch { /* фронт работает и без списка ниш */ }
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem('nf.theme', theme);
  $('#themeToggle').textContent = theme === 'dark' ? t('themeLight') : t('themeDark');
}

/* Всё, что нарисовано вне экрана: меню, шапка, подвал. Вызывается при старте
   и при каждой смене языка. */
function translateChrome() {
  translateStatic(t);
  document.querySelectorAll('#nav .nav-item').forEach((a) => {
    const label = a.querySelector('.nav-label');
    if (label) label.textContent = routeTitle(a.getAttribute('href').slice(2));
  });
  applyTheme(document.documentElement.dataset.theme || 'dark');
  $('#langToggle').textContent = LANG_NAMES[getLocale() === 'ru' ? 'en' : 'ru'];
  if (me.multiUser && me.user) $('#signOut').textContent = t('signOutAs', { email: me.user.email });
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

  applyTheme(localStorage.getItem('nf.theme') || 'dark');
  $('#themeToggle').addEventListener('click', () =>
    applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));

  $('#langToggle').addEventListener('click', () => setLocale(getLocale() === 'ru' ? 'en' : 'ru'));

  translateChrome();
  addEventListener('hashchange', render);
}

/* Смена языка кнопкой: каркас, подвал и экран перерисовываются без перезагрузки;
   каталог оценок приходит с сервера уже переведённым -- перечитываем. */
function listenLocale() {
  onLocaleChange(async (loc) => {
    translateChrome();
    loadNiches();
    loadFootStat();
    if (me.multiUser && me.user) renderPlanLine();
    // язык уведомлений и дайджеста хранится на сервере; без сервера -- не беда
    api('/api/me/locale', { method: 'PUT', body: { locale: loc } }).catch(() => {});
    if (me.multiUser && !me.user) { showSignIn(); return; }
    await loadScores();
    render();
  });
}

/* ------------------------------------------------ вход (план 15)
   Только при NF_MULTI_USER=1 на сервере. Аккаунты создаёт администратор
   (cli.py create-user), регистрации нет. Сессия -- HttpOnly cookie, скрипт её
   не видит и не хранит. */
function showSignIn(message = '') {
  $('#crumbSection').textContent = t('signInCrumb');
  $('#view').innerHTML = `${message ? notice(esc(message), 'error') : ''}
    <div class="card" style="max-width:420px">
      <div class="section-head"><div><div class="section-title">${t('signInTitle')}</div>
        <div class="section-sub">${t('signInSub')}</div></div></div>
      <form id="signInForm" style="display:grid;gap:10px">
        <input type="email" id="signInEmail" placeholder="email" autocomplete="username" required>
        <input type="password" id="signInPassword" placeholder="${esc(t('passwordPlaceholder'))}" autocomplete="current-password" required>
        <button class="btn" type="submit">${t('signIn')}</button>
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
  try { me = await api('/api/auth/me'); } catch { /* старый сервер без входа */ }
  // язык, выбранный на другом устройстве, -- если в этом браузере ещё не выбирали;
  // слушатель смены ещё не подписан, поэтому каркас переводим сами
  if (!hasStoredLocale() && me.locale && setLocale(me.locale, { persist: false })) translateChrome();
  listenLocale();
  if (me.multiUser && !me.user) { showSignIn(); return; }
  setMe(me);
  if (me.multiUser) {
    const out = $('#signOut');
    out.hidden = false;
    out.textContent = t('signOutAs', { email: me.user.email });
    out.addEventListener('click', async () => {
      await api('/api/auth/logout', { method: 'POST' }).catch(() => {});
      location.reload();
    });
    renderPlanLine();
  }
  applyNav();
  addEventListener('nf:signin-required', () => showSignIn(t('sessionExpired')));
  loadNiches();
  loadFootStat();
  // план 23: каталог оценок до первого экрана -- подсказки «?» берут формулы из него
  await loadScores();
  render();
}

start();
