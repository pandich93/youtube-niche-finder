/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { api, q, num, compact, mult, esc, delta, sectionHead, empty, barList, table, state, rpmRange, RPM_TIP } from '../ui.js';
import { view, plabel, base } from '../shared.js';
import { dict } from '../i18n.js';
import ru from '../i18n/ru/categories.js';
import en from '../i18n/en/categories.js';

const t = dict({ ru, en });

/* ------------------------------------------------------------- Категории */

async function viewCategories() {
  const d = await api(`/api/categories${q({ ...base(), rank_by: 'views', limit: 30 })}`);
  const cats = d.categories;
  view.innerHTML = `
    <div class="card">
      ${sectionHead(t('title'), t('sub', { period: plabel(state.period) }))}
      ${cats.length ? barList(cats.map((c) => ({
          name: c.category, value: c.totalViews, display: `${compact(c.totalViews)} (${c.viewShare}%)`,
          tip: t('barTip', { category: c.category, videos: num(c.videos), channels: num(c.channels), median: num(c.medianViews) }),
        }))) : empty(d.hint || t('noData'))}
      <div class="section-sub" style="margin-top:14px">${t('localNote')}</div>
    </div>
    <div class="card">
      ${sectionHead(t('allMetricsTitle'))}
      ${table([
        { label: t('colCategory'), render: (r) => esc(r.category) },
        { label: t('colVideos'), num: true, render: (r) => num(r.videos) },
        { label: t('colChannels'), num: true, render: (r) => num(r.channels) },
        { label: t('colViews'), num: true, render: (r) => compact(r.totalViews) },
        { label: t('colMedian'), num: true, render: (r) => compact(r.medianViews) },
        { label: t('colShare'), num: true, render: (r) => `${r.viewShare}%` },
        { label: t('colShareShift'), num: true, render: (r) => delta(r.shareChange, t('ppUnit')) },
        { label: t('colGrowth'), num: true, render: (r) => delta(r.viewsGrowth) },
        { label: t('colMedianMultiplier'), num: true, render: (r) => mult(r.medianOutlier) },
        { label: 'Shorts', num: true, render: (r) => `${r.shortsShare}%` },
        { label: t('colNicheRpm'), num: true, render: (r) => r.estimatedRpmRange
            ? `<span data-tip="${RPM_TIP}">${rpmRange(r.estimatedRpmRange)}</span>` : `$${r.estimatedRpmNiche}` },
      ], cats)}
    </div>`;
}

export { viewCategories };
