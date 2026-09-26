/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, num, esc, sectionHead, empty, videoCard } from '../ui.js';
import { view, base, collectForm, wireCollect, render } from '../shared.js';

/* ------------------------------------------------- Найти нишу (бесплатно) */

const FIND_SORTS = [
  ['outlier', 'множитель'], ['outlier_adjusted', 'множитель с поправкой на возраст'],
  ['views', 'просмотры'], ['vph', 'VPH за 24ч'], ['velocity', 'прирост за сутки'],
  ['engagement', 'вовлечённость'], ['acceleration', 'ускорение'], ['published', 'дата публикации'],
];

/* Читаемое перечисление активных нестандартных фильтров -- нужно, чтобы
   "ничего не нашлось" объясняло причину, а не просто разводило руками
   (застрявший в localStorage фильтр вроде "подписчиков не больше 10"
   иначе выглядит как будто весь поиск сломан). */
function activeFindFilters(p) {
  const active = [];
  if (p.query) active.push(`тема «${p.query}»`);
  if (p.min_outlier_score) active.push(`множитель ≥ ${p.min_outlier_score}`);
  if (p.max_subscribers) active.push(`подписчиков ≤ ${num(p.max_subscribers)}`);
  if (p.min_rpm) active.push(`RPM ≥ $${p.min_rpm}`);
  if (p.max_rpm) active.push(`RPM ≤ $${p.max_rpm}`);
  if (p.min_length_min) active.push(`длина видео ≥ ${p.min_length_min} мин`);
  if (p.max_length_min) active.push(`длина видео ≤ ${p.max_length_min} мин`);
  if (p.shorts !== 'any') active.push(p.shorts === 'exclude' ? 'без Shorts' : 'только Shorts');
  return active;
}

async function viewFind() {
  const p = Object.assign(
    { query: '', min_outlier_score: 0, max_subscribers: '', sort_by: 'outlier',
      min_rpm: '', max_rpm: '', min_length_min: '', max_length_min: '', shorts: 'any' },
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
    sort_by: p.sort_by, limit: 30,
  })}`);

  view.innerHTML = `
    <div class="card">
      ${sectionHead('Найти нишу', 'семантический поиск по уже собранной базе — бесплатно, квота YouTube не тратится')}
      <div class="form-row">
        <label class="field" style="flex:2"><span class="field-label">Тема на естественном языке (необязательно)</span>
          <input type="text" id="fQuery" placeholder="расслабляющие видео о природе" value="${esc(p.query)}"></label>
        <label class="field"><span class="field-label">Множитель не меньше</span>
          <input type="number" id="fMinOutlier" value="${p.min_outlier_score}" step="0.5"></label>
        <label class="field"><span class="field-label">Подписчиков не больше</span>
          <input type="number" id="fMaxSubs" value="${p.max_subscribers}" step="1000" placeholder="например, 100000"></label>
        <label class="field"><span class="field-label">Сортировка</span>
          <select id="fFindSort">
            ${FIND_SORTS.map(([v, l]) => `<option value="${v}"${p.sort_by === v ? ' selected' : ''}>${l}</option>`).join('')}
          </select></label>
      </div>
      <div class="form-row" style="margin-top:8px">
        <label class="field"><span class="field-label">RPM от, $</span>
          <input type="number" id="fMinRpm" value="${p.min_rpm}" step="1" min="0" placeholder="например, 5"></label>
        <label class="field"><span class="field-label">RPM до, $</span>
          <input type="number" id="fMaxRpm" value="${p.max_rpm}" step="1" min="0" placeholder="например, 8"></label>
        <label class="field"><span class="field-label">Длина видео от, мин</span>
          <input type="number" id="fMinLen" value="${p.min_length_min}" step="1" min="0" placeholder="например, 3"></label>
        <label class="field"><span class="field-label">Длина видео до, мин</span>
          <input type="number" id="fMaxLen" value="${p.max_length_min}" step="1" min="0" placeholder="например, 20"></label>
        <label class="field"><span class="field-label">Shorts</span>
          <select id="fShorts">
            <option value="any"${p.shorts === 'any' ? ' selected' : ''}>любые</option>
            <option value="exclude"${p.shorts === 'exclude' ? ' selected' : ''}>без Shorts</option>
            <option value="only"${p.shorts === 'only' ? ' selected' : ''}>только Shorts</option>
          </select></label>
        <button class="btn" id="applyFind" type="button">Искать</button>
        <button class="btn btn-ghost" id="resetFind" type="button">Сбросить фильтры</button>
      </div>
      <div class="section-sub" style="margin-top:10px">Ищет по уже собранным видео через эмбеддинги title+description
        (если тема не задана — просто просмотр по выбранной сортировке). RPM — оценка по официальной категории
        YouTube видео (та же модель, что и в оценке дохода канала), не измеренная выплата; категория YouTube не
        различает высокодоходные ниши вроде finance/business, поэтому оценка на практике не превышает ~$8 —
        «RPM от $10» и выше всегда даст пустой список. Фильтры запоминаются в этом браузере между визитами —
        если поиск вдруг перестал что-либо находить, нажмите «Сбросить фильтры». Ничего не находит и без
        фильтров? Ниже квотированный сбор новых данных с YouTube, или загляните в раздел «Данные», чтобы
        проверить покрытие корпуса.</div>
    </div>
    ${d.results.length ? `<div class="cards">${d.results.map(videoCard).join('')}</div>`
                       : empty(activeFindFilters(p).length
                           ? `Ничего не нашлось с фильтрами: ${esc(activeFindFilters(p).join(', '))}. `
                             + 'Если вы их не выставляли сами -- нажмите «Сбросить фильтры» выше.'
                           : 'под эти фильтры в собранной базе ничего не нашлось')}
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
