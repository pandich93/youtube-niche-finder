/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, num, mult, esc, toast, sectionHead, empty, table } from '../ui.js';
import { view } from '../shared.js';

/* ------------------------------------------------------- Карта ниш (08) */

async function viewNicheClusters() {
  const d = await api('/api/niche-clusters');
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Карта ниш', 'кластеры каналов по эмбеддингам видео — без ручного определения ниши',
        `<button class="btn btn-ghost btn-sm" id="clustersRecomputeBtn">Пересчитать</button>`)}
      ${d.clusters.length ? table([
        { label: 'Название', wrap: true, render: (c) => `<b>${esc(c.name)}</b>
          ${c.description ? `<div class="row-sub">${esc(c.description)}</div>` : ''}` },
        { label: 'Каналов', num: true, render: (c) => num(c.channelCount) },
        { label: 'Медиана outlier', num: true, render: (c) => mult(c.medianOutlierScore) },
        { label: 'Скорость (VPH)', num: true, render: (c) => num(c.totalVelocity) },
        { label: 'Faceless', num: true, render: (c) => c.facelessShare != null ? `${Math.round(c.facelessShare * 100)}%` : '—' },
        { label: 'Конкуренция (>100k)', num: true, render: (c) => num(c.competitionCount) },
      ], d.clusters) : empty(d.hint || 'кластеров пока нет')}
    </div>`;

  $('#clustersRecomputeBtn').addEventListener('click', async () => {
    const btn = $('#clustersRecomputeBtn');
    btn.disabled = true;
    btn.textContent = 'Считаю…';
    try {
      await api('/api/niche-clusters/recompute', { method: 'POST' });
      toast('Пересчитано', 'ok');
      viewNicheClusters();
    } catch (e) {
      toast(e.message, 'err');
      btn.disabled = false;
      btn.textContent = 'Пересчитать';
    }
  });
}

export { viewNicheClusters };
