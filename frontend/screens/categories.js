/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { api, q, num, compact, mult, esc, delta, sectionHead, empty, barList, table, state } from '../ui.js';
import { view, plabel, base } from '../shared.js';

/* ------------------------------------------------------------- Категории */

async function viewCategories() {
  const d = await api(`/api/categories${q({ ...base(), rank_by: 'views', limit: 30 })}`);
  const cats = d.categories;
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Популярные категории', `${plabel(state.period)} · сравнение с предыдущим таким же окном`)}
      ${cats.length ? barList(cats.map((c) => ({
          name: c.category, value: c.totalViews, display: `${compact(c.totalViews)} (${c.viewShare}%)`,
          tip: `${c.category}<br>видео: <b>${num(c.videos)}</b><br>каналов: <b>${num(c.channels)}</b><br>медиана просмотров: <b>${num(c.medianViews)}</b>`,
        }))) : empty(d.hint || 'нет данных')}
      <div class="section-sub" style="margin-top:14px">Считается по локальному корпусу:
        собственный чарт YouTube с июля 2025 покрывает только Музыку, Фильмы и Игры и
        категории вроде Education ранжировать не может.</div>
    </div>
    <div class="card">
      ${sectionHead('Все метрики по категориям')}
      ${table([
        { label: 'Категория', render: (r) => esc(r.category) },
        { label: 'Видео', num: true, render: (r) => num(r.videos) },
        { label: 'Каналов', num: true, render: (r) => num(r.channels) },
        { label: 'Просмотры', num: true, render: (r) => compact(r.totalViews) },
        { label: 'Медиана', num: true, render: (r) => compact(r.medianViews) },
        { label: 'Доля', num: true, render: (r) => `${r.viewShare}%` },
        { label: 'Сдвиг доли', num: true, render: (r) => delta(r.shareChange, ' п.п.') },
        { label: 'Рост', num: true, render: (r) => delta(r.viewsGrowth) },
        { label: 'Медиана множителя', num: true, render: (r) => mult(r.medianOutlier) },
        { label: 'Shorts', num: true, render: (r) => `${r.shortsShare}%` },
        { label: 'RPM ниши', num: true, render: (r) => `$${r.estimatedRpmNiche}` },
      ], cats)}
    </div>`;
}

export { viewCategories };
