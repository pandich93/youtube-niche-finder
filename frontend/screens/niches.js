/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { api, num, ago, esc, sectionHead, empty, table, saturationChip } from '../ui.js';
import { view, collectForm, wireCollect, render } from '../shared.js';

/* ------------------------------------------------------------------ Ниши */

async function viewNiches() {
  const d = await api('/api/niches');
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Ниши', 'всё, что собрано под ярлыками')}
      ${d.niches.length ? table([
        { label: 'Слаг', render: (r) => `<a href="#/niche/${esc(r.slug)}">${esc(r.slug)}</a>` },
        { label: 'Запрос', wrap: true, render: (r) => esc(r.query || '') },
        { label: 'Видео', num: true, render: (r) => num(r.video_count) },
        { label: 'Тренд', render: (r) => `<span class="niche-trend" data-slug="${esc(r.slug)}">…</span>` },
        { label: 'Последний сбор', render: (r) => ago(r.last_collected_at) },
      ], d.niches) : empty('ниш пока нет')}
    </div>
    ${collectForm()}`;
  wireCollect(render);
  // Тренд (план 08) считается по всем нишам за 120 дней и на большой базе
  // занимает секунды -- список показываем сразу, колонку заполняем потом.
  api('/api/niches/saturation').then((sat) => {
    const trend = Object.fromEntries((sat.niches || []).map((n) => [n.niche, n]));
    view.querySelectorAll('.niche-trend').forEach((el) => {
      el.innerHTML = saturationChip(trend[el.dataset.slug]) || '—';
    });
  }).catch(() => view.querySelectorAll('.niche-trend').forEach((el) => { el.textContent = '—'; }));
}

export { viewNiches };
