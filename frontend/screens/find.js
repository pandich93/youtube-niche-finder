/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, num, esc, sectionHead, empty, videoCard, yppSelect } from '../ui.js';
import { view, base, collectForm, wireCollect, render } from '../shared.js';
import { dict } from '../i18n.js';
import ru from '../i18n/ru/find.js';
import en from '../i18n/en/find.js';

const t = dict({ ru, en });

/* ------------------------------------------------- Найти нишу (бесплатно) */

/* Функция, а не константа: подписи переводятся при каждом рендере. */
const findSorts = () => [
  ['outlier', t('sortOutlier')], ['outlier_adjusted', t('sortOutlierAdjusted')],
  ['views', t('sortViews')], ['vph', t('sortVph')], ['velocity', t('sortVelocity')],
  ['engagement', t('sortEngagement')], ['acceleration', t('sortAcceleration')], ['published', t('sortPublished')],
];

/* Читаемое перечисление активных нестандартных фильтров -- нужно, чтобы
   "ничего не нашлось" объясняло причину, а не просто разводило руками
   (застрявший в localStorage фильтр вроде "подписчиков не больше 10"
   иначе выглядит как будто весь поиск сломан). */
function activeFindFilters(p) {
  const active = [];
  if (p.query) active.push(t('filterTopic', { query: p.query }));
  if (p.min_outlier_score) active.push(t('filterMinOutlier', { value: p.min_outlier_score }));
  if (p.max_subscribers) active.push(t('filterMaxSubs', { value: num(p.max_subscribers) }));
  if (p.min_rpm) active.push(`RPM ≥ $${p.min_rpm}`);
  if (p.max_rpm) active.push(`RPM ≤ $${p.max_rpm}`);
  if (p.min_length_min) active.push(t('filterMinLength', { value: p.min_length_min }));
  if (p.max_length_min) active.push(t('filterMaxLength', { value: p.max_length_min }));
  if (p.shorts !== 'any') active.push(p.shorts === 'exclude' ? t('filterNoShorts') : t('filterOnlyShorts'));
  if (p.min_ypp_status) active.push(t('filterYpp'));
  return active;
}

async function viewFind() {
  const p = Object.assign(
    { query: '', min_outlier_score: 0, max_subscribers: '', sort_by: 'outlier',
      min_rpm: '', max_rpm: '', min_length_min: '', max_length_min: '', shorts: 'any',
      min_ypp_status: '' },
    JSON.parse(localStorage.getItem('nf.find') || '{}'));
  const d = await api(`/api/search${q({
    ...base(), query: p.query || null,
    min_outlier_score: p.min_outlier_score || null,
    max_subscribers: p.max_subscribers || null,
    min_rpm: p.min_rpm || null, max_rpm: p.max_rpm || null,
    min_video_length: p.min_length_min ? p.min_length_min * 60 : null,
    max_video_length: p.max_length_min ? p.max_length_min * 60 : null,
    exclude_shorts: p.shorts === 'exclude' || null,
    only_shorts: p.shorts === 'only' || null,
    min_ypp_status: p.min_ypp_status || null,
    sort_by: p.sort_by, limit: 30,
  })}`);
  const ex = (value) => esc(t('example', { value }));

  view.innerHTML = `
    <div class="card">
      ${sectionHead(t('title'), t('sub'))}
      <div class="form-row">
        <label class="field" style="flex:2"><span class="field-label">${t('topic')}</span>
          <input type="text" id="fQuery" placeholder="${esc(t('topicPlaceholder'))}" value="${esc(p.query)}"></label>
        <label class="field"><span class="field-label">${t('minMultiplier')}</span>
          <input type="number" id="fMinOutlier" value="${p.min_outlier_score}" step="0.5"></label>
        <label class="field"><span class="field-label">${t('maxSubs')}</span>
          <input type="number" id="fMaxSubs" value="${p.max_subscribers}" step="1000" placeholder="${ex(100000)}"></label>
        <label class="field"><span class="field-label">${t('sort')}</span>
          <select id="fFindSort">
            ${findSorts().map(([v, l]) => `<option value="${v}"${p.sort_by === v ? ' selected' : ''}>${l}</option>`).join('')}
          </select></label>
      </div>
      <div class="form-row" style="margin-top:8px">
        <label class="field"><span class="field-label">${t('minRpm')}</span>
          <input type="number" id="fMinRpm" value="${p.min_rpm}" step="1" min="0" placeholder="${ex(5)}"></label>
        <label class="field"><span class="field-label">${t('maxRpm')}</span>
          <input type="number" id="fMaxRpm" value="${p.max_rpm}" step="1" min="0" placeholder="${ex(8)}"></label>
        <label class="field"><span class="field-label">${t('minLength')}</span>
          <input type="number" id="fMinLen" value="${p.min_length_min}" step="1" min="0" placeholder="${ex(3)}"></label>
        <label class="field"><span class="field-label">${t('maxLength')}</span>
          <input type="number" id="fMaxLen" value="${p.max_length_min}" step="1" min="0" placeholder="${ex(20)}"></label>
        <label class="field"><span class="field-label">Shorts</span>
          <select id="fShorts">
            <option value="any"${p.shorts === 'any' ? ' selected' : ''}>${t('shortsAny')}</option>
            <option value="exclude"${p.shorts === 'exclude' ? ' selected' : ''}>${t('shortsExclude')}</option>
            <option value="only"${p.shorts === 'only' ? ' selected' : ''}>${t('shortsOnly')}</option>
          </select></label>
        ${yppSelect('fYpp', p.min_ypp_status)}
        <button class="btn" id="applyFind" type="button">${t('search')}</button>
        <button class="btn btn-ghost" id="resetFind" type="button">${t('reset')}</button>
      </div>
      <div class="section-sub" style="margin-top:10px">${t('note')}</div>
    </div>
    ${d.results.length ? `<div class="cards">${d.results.map(videoCard).join('')}</div>`
                       : empty(activeFindFilters(p).length
                           ? t('emptyFiltered', { filters: esc(activeFindFilters(p).join(', ')) })
                           : t('empty'))}
    ${collectForm()}`;

  $('#applyFind').addEventListener('click', () => {
    localStorage.setItem('nf.find', JSON.stringify({
      query: $('#fQuery').value.trim(),
      min_outlier_score: +$('#fMinOutlier').value || 0,
      max_subscribers: $('#fMaxSubs').value ? +$('#fMaxSubs').value : '',
      min_rpm: $('#fMinRpm').value ? +$('#fMinRpm').value : '',
      max_rpm: $('#fMaxRpm').value ? +$('#fMaxRpm').value : '',
      min_length_min: $('#fMinLen').value ? +$('#fMinLen').value : '',
      max_length_min: $('#fMaxLen').value ? +$('#fMaxLen').value : '',
      shorts: $('#fShorts').value,
      min_ypp_status: $('#fYpp').value,
      sort_by: $('#fFindSort').value,
    }));
    render();
  });
  $('#resetFind').addEventListener('click', () => {
    localStorage.removeItem('nf.find');
    render();
  });
  wireCollect(render);
}

export { viewFind };
