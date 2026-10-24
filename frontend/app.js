/* Точка входа дашборда: тема, язык, глобальные фильтры, подвал, первый render().
   Экраны -- screens/*.js, роутинг -- router.js, общее -- shared.js. */
import { $, api, esc, state, loadScores } from './ui.js';
import { loadFootStat } from './shared.js';
import { render, routeTitle } from './router.js';
import { setMe, applyNav, renderPlanLine } from './plan-state.js';
import { setOffers, showSignIn, openAccountLink } from './account.js';
import { setView, renderBanner, inView } from './impersonation.js';
import { viewTwoFactorSetup, adoptBrowserTimezone } from './screens/profile.js';
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
  renderBanner();
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
    // язык уведомлений и дайджеста хранится на сервере; без сервера -- не беда.
    // P2-05: в просмотре под пользователем его настройки не трогаем (сервер и так даст 403)
    if (!inView()) api('/api/me/locale', { method: 'PUT', body: { locale: loc } }).catch(() => {});
    if (me.multiUser && !me.user) { showSignIn(); return; }
    await loadScores();
    render();
  });
}

/* Вход, регистрация и ссылки из писем -- account.js (план 15, P1-06). Только
   при NF_MULTI_USER=1 на сервере. */
async function start() {
  initChrome();
  try { me = await api('/api/auth/me'); } catch { /* старый сервер без входа */ }
  // язык, выбранный на другом устройстве, -- если в этом браузере ещё не выбирали;
  // слушатель смены ещё не подписан, поэтому каркас переводим сами
  if (!hasStoredLocale() && me.locale && setLocale(me.locale, { persist: false })) translateChrome();
  listenLocale();
  setOffers(me.signup);
  if (me.multiUser && !me.user) { if (!openAccountLink(false)) showSignIn(); return; }
  setMe(me);
  // P2-05: админ смотрит глазами пользователя -- баннер «только чтение · Выйти»;
  // «Выйти (email)» внизу назвало бы чужой адрес, поэтому его прячем
  setView(me);
  // P2-03: только если пояс ещё не выбран; из режима просмотра не сохраняем --
  // это был бы пояс браузера админа
  if (!inView()) adoptBrowserTimezone(me);
  if (me.multiUser) {
    const out = $('#signOut');
    out.hidden = !!me.impersonation;
    out.textContent = t('signOutAs', { email: me.user.email });
    out.addEventListener('click', async () => {
      await api('/api/auth/logout', { method: 'POST' }).catch(() => {});
      location.reload();
    });
    renderPlanLine();
  }
  if (me.twoFactor?.setupRequired) {
    // P2-04: администратор без 2FA при NF_ADMIN_2FA=required -- пока не включит, только её настройка
    document.querySelectorAll('#nav .nav-item').forEach((a) => { a.hidden = true; });
    viewTwoFactorSetup();
    return;
  }
  applyNav();
  addEventListener('nf:signin-required', () => showSignIn(t('sessionExpired')));
  loadNiches();
  loadFootStat();
  // ссылка из письма о смене email открыта уже со входом
  if (me.multiUser && openAccountLink(true)) return;
  // план 23: каталог оценок до первого экрана -- подсказки «?» берут формулы из него
  await loadScores();
  render();
}

start();
