/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, num, esc, sectionHead, notice, empty, videoCard, funnelBlock, state } from '../ui.js';
import { view, plabel, base, render } from '../shared.js';
import { dict } from '../i18n.js';
import ru from '../i18n/ru/viral.js';
import en from '../i18n/en/viral.js';

const t = dict({ ru, en });

/* -------------------------------------------------------- Вирусные видео */

async function viewViral() {
  const p = { period_by: 'discovered', max_subscribers: 100000, min_views: 1000,
              min_views_per_subscriber: 0.5, sort_by: 'viral', limit: 48, preset: '' };
  Object.assign(p, JSON.parse(localStorage.getItem('nf.viral') || '{}'), base());
  if (p.preset === 'niche_all' && !state.niche) p.preset = '';  // пресет требует нишу
  const d = await api(`/api/viral${q(p)}`);

  view.innerHTML = `
    <div class="card">
      ${sectionHead(t('title'), plabel(state.period))}
      <div class="form-row">
        <label class="field"><span class="field-label">${t('periodBy')}</span>
          <select id="fPeriodBy">
            <option value="published"${p.period_by === 'published' ? ' selected' : ''}>${t('periodPublished')}</option>
            <option value="discovered"${p.period_by === 'discovered' ? ' selected' : ''}>${t('periodDiscovered')}</option>
          </select></label>
        <label class="field"><span class="field-label">${t('maxSubs')}</span>
          <input type="number" id="fSubs" value="${p.max_subscribers}" step="1000" ${p.preset === 'niche_all' ? 'disabled' : ''}></label>
        <label class="field"><span class="field-label">${t('minViews')}</span>
          <input type="number" id="fViews" value="${p.min_views}" step="1000" ${p.preset === 'niche_all' ? 'disabled' : ''}></label>
        <label class="field"><span class="field-label">${t('minVsr')}</span>
          <input type="number" id="fVsr" value="${p.min_views_per_subscriber}" step="0.5" ${p.preset === 'niche_all' ? 'disabled' : ''}></label>
        <label class="field"><span class="field-label">${t('sort')}</span>
          <select id="fSort">
            ${[['viral', t('sortViral')], ['vsr', t('sortVsr')], ['views', t('sortViews')],
               ['outlier', t('sortOutlier')], ['vph', t('sortVph')], ['acceleration', t('sortAcceleration')],
               ['published', t('sortPublished')]]
              .map(([v, l]) => `<option value="${v}"${p.sort_by === v ? ' selected' : ''}>${l}</option>`).join('')}
          </select></label>
        <button class="btn" id="applyViral" type="button">${t('apply')}</button>
      </div>
      ${state.niche ? `
      <div class="form-row" style="margin-top:8px">
        <label class="field" style="flex:0 0 auto">
          <span class="field-label">&nbsp;</span>
          <span><input type="checkbox" id="fNicheAll" style="width:auto" ${p.preset === 'niche_all' ? 'checked' : ''}>
          ${t('nicheAll', { niche: esc(state.niche) })}</span></label>
      </div>` : ''}
      <div class="section-sub" style="margin-top:10px">${t('found', { matched: num(d.matched) })}</div>
    </div>
    ${d.error ? notice(esc(d.error), 'error') : ''}
    ${d.results.length ? `<div class="cards">${d.results.map(videoCard).join('')}</div>`
                       : empty(t('empty'))}
    ${funnelBlock(d)}`;

  $('#applyViral').addEventListener('click', () => {
    localStorage.setItem('nf.viral', JSON.stringify({
      period_by: $('#fPeriodBy').value,
      max_subscribers: +$('#fSubs').value,
      min_views: +$('#fViews').value,
      preset: $('#fNicheAll') && $('#fNicheAll').checked ? 'niche_all' : '',
      min_views_per_subscriber: +$('#fVsr').value,
      sort_by: $('#fSort').value,
    }));
    render();
  });
}

export { viewViral };
