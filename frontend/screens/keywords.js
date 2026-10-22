/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, num, compact, esc, tile, sectionHead, empty, table, state } from '../ui.js';
import { view, plabel, base, render } from '../shared.js';
import { dict } from '../i18n.js';
import ru from '../i18n/ru/keywords.js';
import en from '../i18n/en/keywords.js';

const t = dict({ ru, en });

/* --------------------------------------------------------- Ключевые слова */

async function viewKeywords() {
  const semantic = localStorage.getItem('nf.keywordsMode') === 'semantic';
  const d = await api(`/api/keywords${q({ ...base(), sort_by: 'trend', top_n: 60, min_videos: 2,
    keywords_mode: semantic ? 'semantic' : 'ngram' })}`);
  view.innerHTML = `
    <div class="card">
      ${sectionHead(t('title'), plabel(state.period), `
        <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--muted)">
          <input type="checkbox" id="semanticModeToggle" ${semantic ? 'checked' : ''}>
          ${t('semanticMode')}
        </label>`)}
      <div class="tiles">
        ${tile(t('tileAnalysed'), num(d.videosAnalysed))}
        ${tile(t('tilePrevious'), num(d.previousWindowVideos))}
        ${tile(t('tileBaseRate'), `${d.outlierBaseRate}%`, t('tileBaseRateSub'))}
        ${tile(t('tileTagCoverage'), `${d.tagCoveragePercent}%`, t('tileTagCoverageSub'))}
      </div>
    </div>
    <div class="card">
      ${d.keywords.length ? table([
        { label: t('colPhrase'), wrap: true, render: (r) => esc(r.keyword) },
        { label: 'Opportunity', num: true, tip: 'trendScore', render: (r) => r.opportunityScore != null
            ? `<span class="chip ${r.opportunityScore >= 70 ? 'chip-good' : r.opportunityScore < 30 ? 'chip-bad' : ''}">${r.opportunityScore}</span>`
            : '—' },
        { label: t('colVideos'), num: true, render: (r) => num(r.videos) },
        { label: 'Momentum', num: true, tip: 'trendScore', render: (r) => r.momentum ?? '—' },
        { label: 'Lift', num: true, tip: 'trendScore', render: (r) => r.outlierLift ?? '—' },
        { label: t('colShare'), num: true, render: (r) => `${r.share}%` },
        { label: t('colMedianViews'), num: true, render: (r) => compact(r.medianViews) },
        { label: t('colNew'), render: (r) => (r.isNew ? t('yes') : '') },
        { label: t('colExample'), wrap: true, render: (r) => esc(r.examples?.[0]?.title || '') },
      ], d.keywords) : empty(d.hint || t('empty'))}
      <div class="section-sub" style="margin-top:14px">
        ${t('note')}
      </div>
    </div>`;

  $('#semanticModeToggle').addEventListener('change', (e) => {
    localStorage.setItem('nf.keywordsMode', e.target.checked ? 'semantic' : 'ngram');
    render();
  });
}

export { viewKeywords };
