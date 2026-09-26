/* Точка входа дашборда: тема, глобальные фильтры, подвал, первый render().
   Экраны -- screens/*.js, роутинг -- router.js, общее -- shared.js. */
import { $, api, esc, state } from './ui.js';
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

initChrome();
loadNiches();
loadFootStat();
render();
