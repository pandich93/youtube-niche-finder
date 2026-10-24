/* Тариф текущего пользователя (SaaS, подпроект 1): что включено, что скрыть
   в меню, строка «Тариф · квота» внизу. Только при NF_MULTI_USER=1; без него
   всё включено, как раньше. */
import { $, api } from './ui.js';
import { dict } from './i18n.js';
import ru from './i18n/ru/planState.js';
import en from './i18n/en/planState.js';

const t = dict({ ru, en });

let me = { multiUser: false, user: null, plan: null };

// экран -> функция тарифа, без которой он не нужен
const ROUTE_FEATURE = { own: 'own_channels' };

function setMe(next) { if (next) me = next; }
function getMe() { return me; }

function featureOn(key) {
  return !me.multiUser || !me.plan || me.plan.features?.[key] !== false;
}

// многопользовательский план P0-02: ручной запуск работы сервера (пересчёт,
// обновление, эмбеддинги) и разбор предложений LLM -- только администратору;
// без NF_MULTI_USER оператор и есть администратор
function isAdmin() {
  return !me.multiUser || !!me.user?.isAdmin;
}

function routeAllowed(name) {
  if (name === 'plan') return me.multiUser;
  // P2-03: в однопользовательском режиме профиль -- только карточка часового пояса
  if (name === 'profile') return true;
  if (name === 'admin') return me.multiUser && !!me.user?.isAdmin;
  const f = ROUTE_FEATURE[name];
  return !f || featureOn(f);
}

function applyNav() {
  document.querySelectorAll('.nav-item').forEach((a) => {
    const name = (a.getAttribute('href') || '').replace('#/', '');
    a.hidden = !routeAllowed(name);
  });
}

function renderPlanLine() {
  const box = $('#myQuota');
  if (!box) return;
  if (!me.multiUser || !me.plan) { box.hidden = true; return; }
  const used = me.plan.usage?.daily?.yt_units ?? 0;
  const limit = me.plan.limits?.yt_units;
  box.hidden = false;
  box.textContent = t('planLine', { name: me.plan.plan.name, used, limit: limit == null ? '∞' : limit });
}

let timer = null;
async function refreshMe() {
  if (!me.multiUser) return;
  try { setMe(await api('/api/auth/me')); } catch { return; }
  applyNav();
  renderPlanLine();
}

// после изменяющих запросов строка квоты обновляется не чаще раза в секунду
addEventListener('nf:api-write', () => {
  clearTimeout(timer);
  timer = setTimeout(refreshMe, 1000);
});

export { setMe, getMe, featureOn, isAdmin, routeAllowed, applyNav, renderPlanLine, refreshMe };
