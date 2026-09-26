/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { api, num, ago, esc, sectionHead, empty, table } from '../ui.js';
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
        { label: 'Последний сбор', render: (r) => ago(r.last_collected_at) },
      ], d.niches) : empty('ниш пока нет')}
    </div>
    ${collectForm()}`;
  wireCollect(render);
}

export { viewNiches };
