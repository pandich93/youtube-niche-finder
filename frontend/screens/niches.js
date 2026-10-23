/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { api, num, ago, esc, sectionHead, empty, table, saturationChip } from '../ui.js';
import { view, collectForm, wireCollect, render } from '../shared.js';
import { dict } from '../i18n.js';
import ru from '../i18n/ru/niches.js';
import en from '../i18n/en/niches.js';

const t = dict({ ru, en });

/* ------------------------------------------------------------------ Ниши */

async function viewNiches() {
  const d = await api('/api/niches');
  view.innerHTML = `
    <div class="card">
      ${sectionHead(t('title'), t('sub'))}
      ${d.niches.length ? table([
        { label: t('colSlug'), render: (r) => `<a href="#/niche/${esc(r.slug)}">${esc(r.slug)}</a>` },
        { label: t('colQuery'), wrap: true, render: (r) => esc(r.query || '') },
        { label: t('colVideos'), num: true, render: (r) => num(r.video_count) },
        { label: t('colTrend'), render: (r) => `<span class="niche-trend" data-slug="${esc(r.slug)}">…</span>` },
        { label: t('colLastCollected'), render: (r) => ago(r.last_collected_at) },
      ], d.niches) : empty(t('empty'))}
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
