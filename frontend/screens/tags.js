/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { api, q, num, compact, esc, sectionHead, empty, table, state } from '../ui.js';
import { view, plabel, base } from '../shared.js';
import { dict } from '../i18n.js';
import ru from '../i18n/ru/tags.js';
import en from '../i18n/en/tags.js';

const t = dict({ ru, en });

/* ---------------------------------------------------- Топ теги по категориям */

async function viewTopTags() {
  const d = await api(`/api/tags/top-by-category${q({ ...base(), min_videos: 3, top_n: 10 })}`);
  view.innerHTML = `
    <div class="card">
      ${sectionHead(t('title'), t('sub', { period: plabel(state.period) }))}
    </div>
    ${d.categories.length ? d.categories.map((c) => `
      <div class="card">
        ${sectionHead(c.category || t('categoryFallback', { id: c.categoryId }), t('videosAnalysed', { n: c.videosAnalysed }))}
        ${table([
            { label: t('colTag'), wrap: true, render: (r) => esc(r.tag) },
            { label: t('colVideos'), num: true, render: (r) => num(r.videos) },
            { label: t('colShare'), num: true, render: (r) => `${r.share}%` },
            { label: 'Lift', num: true, render: (r) => r.outlierLift ?? '—' },
            { label: t('colMedianViews'), num: true, render: (r) => compact(r.medianViews) },
            { label: t('colExample'), wrap: true, render: (r) => esc(r.examples?.[0]?.title || '') },
          ], c.tags)}
      </div>`).join('') : `<div class="card">${empty(d.hint || t('empty'))}</div>`}
    <div class="card">
      <div class="section-sub">
        ${t('liftNote')}
      </div>
    </div>`;
}

export { viewTopTags };
