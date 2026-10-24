/* Ручные запуски работы сервера (многопользовательский план P2-11): «Обновить
   сейчас» и «Проверить сейчас». Администратор запускает всегда; обычный
   пользователь -- если тариф разрешает (manual_runs не 0), по своим каналам и
   не чаще лимита в час. Пока лимит исчерпан, кнопка выключена и говорит, когда
   можно снова; сервер всё равно проверяет сам (429 с Retry-After). */
import { api, toast } from './ui.js';
import { dict, fmtTime } from './i18n.js';
import ru from './i18n/ru/manualRuns.js';
import en from './i18n/en/manualRuns.js';

const t = dict({ ru, en });

async function manualRunStatus() {
  try { return await api('/api/manual-runs'); } catch { return null; }
}

// показывать ли кнопку вообще: 0 в тарифе -- только администратор, как раньше
function manualRunAvailable(st, op) {
  return !!st?.operations?.[op] && (st.admin || st.perHour !== 0);
}

function applyManualRun(btn, st, op, label) {
  const o = st?.operations?.[op];
  const until = o && !o.allowed && o.nextAt ? new Date(o.nextAt) : null;
  btn.disabled = !!until;
  btn.textContent = until ? t('againAt', { time: fmtTime(until, { hour: '2-digit', minute: '2-digit' }) }) : label;
  btn.title = !st?.admin && st?.perHour ? t('limitTip', { n: st.perHour }) : '';
  clearTimeout(btn.nfTimer);
  if (until) {
    // время пришло -- кнопка снова живая, без перезагрузки экрана
    btn.nfTimer = setTimeout(async () => {
      if (btn.isConnected) applyManualRun(btn, await manualRunStatus(), op, label);
    }, Math.max(1000, until - Date.now() + 1000));
  }
}

// run() делает запрос и сам сообщает об успехе; после любого ответа, в том
// числе 429, статус перечитывается
function wireManualRun(btn, st, op, label, run) {
  applyManualRun(btn, st, op, label);
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    try { await run(); } catch (err) { toast(err.message, 'err'); }
    if (btn.isConnected) applyManualRun(btn, await manualRunStatus(), op, label);
  });
}

export { manualRunStatus, manualRunAvailable, wireManualRun };
