/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, num, mult, esc, toast, sectionHead, empty, table, saturationChip } from '../ui.js';
import { view } from '../shared.js';
import { dict } from '../i18n.js';
import ru from '../i18n/ru/clusters.js';
import en from '../i18n/en/clusters.js';

const t = dict({ ru, en });

/* ------------------------------------------------------- Карта ниш (08) */

async function viewNicheClusters() {
  const d = await api('/api/niche-clusters');
  view.innerHTML = `
    <div class="card">
      ${sectionHead(t('title'), t('sub'),
        `<button class="btn btn-ghost btn-sm" id="clustersRecomputeBtn">${t('recompute')}</button>`)}
      ${d.clusters.length ? table([
        { label: t('colName'), wrap: true, render: (c) => `<b>${esc(c.name)}</b>
          ${c.description ? `<div class="row-sub">${esc(c.description)}</div>` : ''}` },
        { label: t('colChannels'), num: true, render: (c) => num(c.channelCount) },
        { label: t('colMedianOutlier'), num: true, render: (c) => mult(c.medianOutlierScore) },
        { label: t('colVelocity'), num: true, render: (c) => num(c.totalVelocity) },
        { label: 'Faceless', num: true, render: (c) => c.facelessShare != null ? `${Math.round(c.facelessShare * 100)}%` : '—' },
        { label: t('colCompetition'), num: true, render: (c) => num(c.competitionCount) },
        { label: t('colTrend'), render: (c) => saturationChip(c.saturation) || '—' },
      ], d.clusters) : empty(d.hint || t('empty'))}
    </div>`;

  $('#clustersRecomputeBtn').addEventListener('click', async () => {
    const btn = $('#clustersRecomputeBtn');
    btn.disabled = true;
    btn.textContent = t('computing');
    try {
      await api('/api/niche-clusters/recompute', { method: 'POST' });
      toast(t('recomputed'), 'ok');
      viewNicheClusters();
    } catch (e) {
      toast(e.message, 'err');
      btn.disabled = false;
      btn.textContent = t('recompute');
    }
  });
}

export { viewNicheClusters };
