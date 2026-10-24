/* Просмотр под пользователем (многопользовательский план P2-05): баннер над
   всеми экранами, пока админ смотрит глазами пользователя, -- только чтение,
   кнопка «Выйти» и автоматический возврат, когда время вышло. Начинают со
   страницы «Админка»; что можно и что нет, решает сервер (/api/auth/me
   отдаёт impersonation). */
import { $, api, esc, toast } from './ui.js';
import { dict, fmtTime } from './i18n.js';
import ru from './i18n/ru/impersonation.js';
import en from './i18n/en/impersonation.js';

const t = dict({ ru, en });

let view = null;
let timer = null;

function renderBanner() {
  const box = $('#impersonationBanner');
  if (!box) return;
  if (!view) { box.hidden = true; box.innerHTML = ''; return; }
  box.hidden = false;
  box.innerHTML = `<span>${t('banner', { email: `<b>${esc(view.email)}</b>` })}</span>
    <span class="imp-until">${esc(t('until', { time: fmtTime(view.expiresAt, { hour: '2-digit', minute: '2-digit' }) }))}</span>
    <button class="btn btn-sm" type="button" id="impersonationExit">${esc(t('exit'))}</button>`;
  box.querySelector('#impersonationExit').addEventListener('click', exitView);
}

async function exitView() {
  try {
    await api('/api/auth/impersonation/end', { method: 'POST' });
  } catch (e) { toast(e.message, 'err'); return; }
  location.hash = '#/admin';
  location.reload();
}

/* me из /api/auth/me. Время кончилось -- сервер уже вернул админа на следующем
   запросе; перезагрузка показывает это сразу, а не по первому клику. */
function setView(me) {
  view = me?.impersonation || null;
  document.body.classList.toggle('impersonating', !!view);
  renderBanner();
  clearTimeout(timer);
  if (view) {
    const left = new Date(view.expiresAt).getTime() - Date.now();
    timer = setTimeout(() => location.reload(), Math.max(1000, left + 1000));
  }
}

function inView() { return !!view; }

/* Админка: начать просмотр. Причина обязательна -- она уходит в журнал. */
async function startView(userId, email) {
  const reason = prompt(t('reasonPrompt', { email }));
  if (reason === null) return;
  if (!reason.trim()) { toast(t('reasonRequired'), 'err'); return; }
  try {
    await api(`/api/admin/users/${userId}/impersonate`, { method: 'POST', body: { reason } });
  } catch (e) { toast(e.message, 'err'); return; }
  location.hash = '#/overview';
  location.reload();
}

export { setView, renderBanner, inView, startView };
