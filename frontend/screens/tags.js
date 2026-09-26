/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { api, q, num, compact, esc, sectionHead, empty, table, state } from '../ui.js';
import { view, plabel, base } from '../shared.js';

/* ---------------------------------------------------- Топ теги по категориям */

async function viewTopTags() {
  const d = await api(`/api/tags/top-by-category${q({ ...base(), min_videos: 3, top_n: 10 })}`);
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Топ теги по категориям', `${plabel(state.period)} · целый тег, как его указал автор — не разбивается на слова`)}
    </div>
    ${d.categories.length ? d.categories.map((c) => `
      <div class="card">
        ${sectionHead(c.category || `Категория ${c.categoryId}`, `${num(c.videosAnalysed)} видео в анализе`)}
        ${table([
            { label: 'Тег', wrap: true, render: (r) => esc(r.tag) },
            { label: 'Видео', num: true, render: (r) => num(r.videos) },
            { label: 'Доля', num: true, render: (r) => `${r.share}%` },
            { label: 'Lift', num: true, render: (r) => r.outlierLift ?? '—' },
            { label: 'Медиана просмотров', num: true, render: (r) => compact(r.medianViews) },
            { label: 'Пример', wrap: true, render: (r) => esc(r.examples?.[0]?.title || '') },
          ], c.tags)}
      </div>`).join('') : `<div class="card">${empty(d.hint || 'нет тегов над порогом')}</div>`}
    <div class="card">
      <div class="section-sub">
        <b>Lift</b> — во сколько раз тег повышает шансы видео пробить (outlier);
        больше 1.5 значит тег реально коррелирует с пробитиями, не просто популярен.
      </div>
    </div>`;
}

export { viewTopTags };
