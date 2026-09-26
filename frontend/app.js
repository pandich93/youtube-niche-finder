/* Экраны и роутинг. Компоненты и форматтеры — в ui.js. */
import {
  $, api, q, num, compact, mult, ago, esc, delta, toast, tile, sectionHead,
  notice, empty, barList, channelRow, videoCard, table, commentList, lineChart, funnelBlock, pl,
  aiLabelsBadge, scatterChart, state,
} from './ui.js';

const view = $('#view');

const PERIOD_LABEL = {
  '1h': 'за час', '6h': 'за 6 часов', '24h': 'за 24 часа', '48h': 'за 48 часов',
  '7d': 'за 7 дней', '14d': 'за 14 дней', '30d': 'за 30 дней', '60d': 'за 60 дней',
  '90d': 'за 90 дней', '180d': 'за 180 дней', '365d': 'за год', all: 'за всё время',
};
const plabel = (p) => PERIOD_LABEL[p] || `за ${p}`;

const SATURATION = {
  'dominated by big channels -- hard to break in': 'доминируют крупные каналы — войти трудно',
  'plenty of small channels performing -- room to enter': 'много выстреливающих мелких каналов — есть куда войти',
  'mixed field': 'смешанное поле',
};

function base() { return { period: state.period, niche: state.niche }; }

async function guard(fn) {
  view.innerHTML = '<div class="skeleton-page"></div>';
  try {
    await fn();
  } catch (e) {
    view.innerHTML = notice(
      `<div><b>Не удалось загрузить данные.</b><br>${esc(e.message)}<br>
       <span style="color:var(--muted)">Проверьте, что сервис поднят:
       <code>docker compose up -d web</code></span></div>`, 'error');
  }
}

/* Форма сбора переиспользуется на нескольких экранах. */
function collectForm() {
  return `<div class="card">
    ${sectionHead('Добавить данные', 'сбор тратит квоту YouTube; канал ≈ 1 unit на 50 видео')}
    <div class="form-row">
      <label class="field"><span class="field-label">Канал: @handle, UC-id или URL</span>
        <input type="text" id="collectChannel" placeholder="@Inkexplainer96"></label>
      <label class="field"><span class="field-label">Ниша (необязательно)</span>
        <input type="text" id="collectNiche" placeholder="explainer"></label>
      <button class="btn" id="collectBtn" type="button">Собрать канал</button>
      <button class="btn btn-ghost" id="collectTrackBtn" type="button">Собрать и отслеживать</button>
    </div>
    <div class="form-row" style="margin-top:12px">
      <label class="field"><span class="field-label">Поисковый запрос (1 из 100 поисков в сутки)</span>
        <input type="text" id="collectQuery" placeholder="ai automation"></label>
      <label class="field"><span class="field-label">Окно публикации</span>
        <select id="collectPeriod"><option value="">любое</option><option value="24h">24 часа</option>
        <option value="7d">7 дней</option><option value="30d">30 дней</option></select></label>
      <button class="btn btn-ghost" id="searchBtn" type="button">Собрать поиском</button>
    </div>
  </div>`;
}

function wireCollect(refresh) {
  for (const id of ['collectBtn', 'collectTrackBtn', 'searchBtn']) {
    const b = $(`#${id}`);
    if (b) b.dataset.label = b.textContent;
  }
  const busy = (btn, on) => { btn.disabled = on; btn.textContent = on ? 'Собираю…' : btn.dataset.label; };
  const run = async (btn, path, body, ok) => {
    busy(btn, true);
    try {
      const r = await api(path, { method: 'POST', body });
      toast(ok(r), 'ok');
      await Promise.all([refresh(), loadFootStat()]);
    } catch (e) { toast(e.message, 'err'); } finally { busy(btn, false); }
  };
  $('#collectBtn')?.addEventListener('click', () => {
    const ch = $('#collectChannel').value.trim();
    if (!ch) return toast('Укажите канал', 'err');
    run($('#collectBtn'), '/api/collect/channel',
      { channel: ch, niche: $('#collectNiche').value.trim() || null },
      (r) => `${r.channelTitle}: сохранено ${r.videos_stored} видео, ${r.quota.units_from_shared_pool} units`);
  });
  $('#collectTrackBtn')?.addEventListener('click', () => {
    const ch = $('#collectChannel').value.trim();
    if (!ch) return toast('Укажите канал', 'err');
    run($('#collectTrackBtn'), '/api/collect/channel',
      { channel: ch, niche: $('#collectNiche').value.trim() || null, track: true },
      (r) => `${r.channelTitle} добавлен в трекер, ${r.videos_stored} видео`);
  });
  $('#searchBtn')?.addEventListener('click', () => {
    const query = $('#collectQuery').value.trim();
    if (!query) return toast('Укажите запрос', 'err');
    run($('#searchBtn'), '/api/collect/niche',
      { query, period: $('#collectPeriod').value || null },
      (r) => `«${r.query}»: ${r.videos_stored ?? 0} видео, потрачен ${r.quota.search_calls} поиск`);
  });
}

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

/* --------------------------------------------------------------- Обзор */

async function viewOverview() {
  const d = await api(`/api/overview${q(base())}`);
  const cov = d.coverage;
  const thin = cov.videosPublishedInPeriod < 5;

  view.innerHTML = `
    ${thin ? notice(`За окно ${plabel(state.period)} в базе всего
      <b>${cov.videosPublishedInPeriod}</b> видео из ${num(cov.videosTotal)}.
      Секции ниже считаются по окну «когда мы впервые увидели», поэтому что-то показывают,
      но для честной картины нужно собрать больше каналов — это дёшево, ≈1 unit на 50 видео.`) : ''}

    <div class="tiles">
      ${tile('Каналов', num(d.stats.channels))}
      ${tile('Видео', num(d.stats.videos))}
      ${tile('Снимков статистики', num(d.stats.video_stat_snapshots),
        d.stats.history_since ? `история с ${new Date(d.stats.history_since).toLocaleDateString('ru-RU')}`
                              : 'истории пока нет')}
      ${tile('В трекере', num(d.stats.tracked_channels))}
      ${tile('Видео в окне', num(cov.videosPublishedInPeriod), plabel(state.period))}
      ${tile('Без эмбеддинга', num(d.stats.videos_without_embedding), 'досчитает воркер')}
    </div>

    <div class="grid-2">
      <section class="card">
        ${sectionHead('Недавно добавленные outlier-каналы', plabel(state.period),
          '<a class="btn btn-ghost btn-sm" href="#/channels">Все</a>')}
        ${d.outlierChannels.channels.length
          ? `<div class="rows">${d.outlierChannels.channels.map(channelRow).join('')}</div>`
          : empty(d.outlierChannels.hint || 'нет каналов, прошедших порог множителя')}
      </section>

      <section class="card">
        ${sectionHead('Будущая конкуренция', `${plabel(d.widePeriod)} · молодые быстрорастущие каналы`)}
        ${d.competition.channels.length
          ? `<div class="rows">${d.competition.channels.slice(0, 6).map((c) => `
              <div class="row">
                <div class="avatar">${esc((c.channelTitle || '?').slice(0, 1).toUpperCase())}</div>
                <div class="row-main">
                  <div class="row-title"><a href="#/channel/${esc(c.channelId)}">${esc(c.channelTitle)}</a></div>
                  <div class="row-sub">${compact(c.subscribers)} подп. · ${
                    pl(c.uploadsInWindow, 'загрузка', 'загрузки', 'загрузок')} · ${
                    c.channelAgeDays ? `${num(c.channelAgeDays)} дн` : 'возраст неизвестен'}</div>
                </div>
                <div class="row-metrics">
                  <div class="metric" data-tip="medianMultiplier × log2(1+загрузок) × коэффициент молодости">
                    <div class="metric-value">${c.competitionScore}</div>
                    <div class="metric-label">score</div>
                  </div>
                </div>
              </div>`).join('')}</div>`
          : empty('нет каналов — нужно больше данных за это окно')}
      </section>
    </div>

    <section class="card">
      ${sectionHead('Популярные категории', `${plabel(state.period)} · по числу каналов`,
        '<a class="btn btn-ghost btn-sm" href="#/categories">Подробнее</a>')}
      ${d.categories.categories.length
        ? `<div class="scroller">${d.categories.categories.map((c) => `
            <div class="cat-card" data-tip="${esc(c.category)}: ${num(c.videos)} видео, ${compact(c.totalViews)} просмотров">
              <div class="cat-name">${esc(c.category)}</div>
              <div class="cat-count">${pl(c.channels, 'канал', 'канала', 'каналов')} · ${num(c.videos)} видео</div>
              <div class="cat-delta">${c.shareChange != null ? delta(c.shareChange, ' п.п. доли')
                                                             : '<span class="chip">нет сравнения</span>'}</div>
            </div>`).join('')}</div>`
        : empty(d.categories.hint || 'нет данных')}
    </section>

    <section class="card">
      ${sectionHead('Трендовые ключевые слова', `${plabel(state.period)} · по trendScore`,
        '<a class="btn btn-ghost btn-sm" href="#/keywords">Все</a>')}
      ${d.keywords.keywords.length
        ? barList(d.keywords.keywords.slice(0, 10).map((k) => ({
            name: k.keyword, value: k.trendScore, display: k.trendScore,
            tip: `${k.keyword}<br>видео: <b>${k.videos}</b><br>momentum: <b>${k.momentum ?? '—'}</b><br>lift: <b>${k.outlierLift ?? '—'}</b>`,
          })))
        : empty(d.keywords.hint || 'нет фраз, проходящих порог')}
    </section>

    <section class="card">
      ${sectionHead('Вирусные видео у маленьких каналов', `${plabel(state.period)} · по попаданию в базу`,
        '<a class="btn btn-ghost btn-sm" href="#/viral">Все</a>')}
      ${d.viral.results.length
        ? `<div class="cards">${d.viral.results.map(videoCard).join('')}</div>`
        : empty(d.viral.hint || 'нет видео под фильтры')}
    </section>`;
}

/* -------------------------------------------------------- Вирусные видео */

async function viewViral() {
  const p = { period_by: 'discovered', max_subscribers: 100000, min_views: 1000,
              min_views_per_subscriber: 0.5, sort_by: 'viral', limit: 48, preset: '' };
  Object.assign(p, JSON.parse(localStorage.getItem('nf.viral') || '{}'), base());
  if (p.preset === 'niche_all' && !state.niche) p.preset = '';  // пресет требует нишу
  const d = await api(`/api/viral${q(p)}`);

  view.innerHTML = `
    <div class="card">
      ${sectionHead('Вирусные видео у маленьких каналов', plabel(state.period))}
      <div class="form-row">
        <label class="field"><span class="field-label">Окно считается по</span>
          <select id="fPeriodBy">
            <option value="published"${p.period_by === 'published' ? ' selected' : ''}>дате публикации</option>
            <option value="discovered"${p.period_by === 'discovered' ? ' selected' : ''}>попаданию в базу</option>
          </select></label>
        <label class="field"><span class="field-label">Подписчиков не больше</span>
          <input type="number" id="fSubs" value="${p.max_subscribers}" step="1000" ${p.preset === 'niche_all' ? 'disabled' : ''}></label>
        <label class="field"><span class="field-label">Просмотров не меньше</span>
          <input type="number" id="fViews" value="${p.min_views}" step="1000" ${p.preset === 'niche_all' ? 'disabled' : ''}></label>
        <label class="field"><span class="field-label">VSR не меньше</span>
          <input type="number" id="fVsr" value="${p.min_views_per_subscriber}" step="0.5" ${p.preset === 'niche_all' ? 'disabled' : ''}></label>
        <label class="field"><span class="field-label">Сортировка</span>
          <select id="fSort">
            ${[['viral', 'вирусность'], ['vsr', 'просмотров на подписчика'], ['views', 'просмотры'],
               ['outlier', 'множитель'], ['vph', 'VPH за 24ч'], ['acceleration', 'ускорение'],
               ['published', 'дата публикации']]
              .map(([v, l]) => `<option value="${v}"${p.sort_by === v ? ' selected' : ''}>${l}</option>`).join('')}
          </select></label>
        <button class="btn" id="applyViral" type="button">Применить</button>
      </div>
      ${state.niche ? `
      <div class="form-row" style="margin-top:8px">
        <label class="field" style="flex:0 0 auto">
          <span class="field-label">&nbsp;</span>
          <span><input type="checkbox" id="fNicheAll" style="width:auto" ${p.preset === 'niche_all' ? 'checked' : ''}>
          все каналы ниши «${esc(state.niche)}», без ограничений по размеру</span></label>
      </div>` : ''}
      <div class="section-sub" style="margin-top:10px">Найдено: <b>${num(d.matched)}</b> · квота не потрачена</div>
    </div>
    ${d.error ? notice(esc(d.error), 'error') : ''}
    ${d.results.length ? `<div class="cards">${d.results.map(videoCard).join('')}</div>`
                       : empty('под эти фильтры ничего не попало')}
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

/* --------------------------------------------------------- Outlier-каналы */

async function viewChannels() {
  const p = Object.assign(
    { min_multiplier: 1.5, min_subscribers: '', max_subscribers: '' },
    JSON.parse(localStorage.getItem('nf.channels') || '{}'));
  const d = await api(`/api/outlier-channels${q({
    ...base(), min_multiplier: p.min_multiplier,
    min_subscribers: p.min_subscribers || null, max_subscribers: p.max_subscribers || null,
    limit: 50,
  })}`);
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Outlier-каналы', `${plabel(state.period)} · по попаданию в базу — свежие возможности`)}
      <div class="form-row">
        <label class="field"><span class="field-label">Множитель не меньше</span>
          <input type="number" id="fcMult" value="${p.min_multiplier}" step="0.5"></label>
        <label class="field"><span class="field-label">Подписчиков не меньше</span>
          <input type="number" id="fcMinSubs" value="${p.min_subscribers}" step="1000"></label>
        <label class="field"><span class="field-label">Подписчиков не больше</span>
          <input type="number" id="fcMaxSubs" value="${p.max_subscribers}" step="1000"></label>
        <button class="btn" id="applyChannels" type="button">Применить</button>
      </div>
      <div class="section-sub" style="margin-top:10px">Множитель — лучший возрастно-нормированный outlier среди
        видео канала в окне, против медианы предыдущих загрузок этого же канала.
        Полоса: &lt;2x, 2–3x, 3–5x, 5–10x, &gt;10x. Отсортировано по силе множителя;
        сузьте окно периода вверху, чтобы увидеть только самые свежие открытия.</div>
    </div>
    <div class="card">
      ${d.channels.length ? `<div class="rows">${d.channels.map(channelRow).join('')}</div>`
                          : empty(d.hint || 'нет каналов над порогом')}
    </div>`;

  $('#applyChannels').addEventListener('click', () => {
    localStorage.setItem('nf.channels', JSON.stringify({
      min_multiplier: +$('#fcMult').value || 0,
      min_subscribers: $('#fcMinSubs').value ? +$('#fcMinSubs').value : '',
      max_subscribers: $('#fcMaxSubs').value ? +$('#fcMaxSubs').value : '',
    }));
    render();
  });
}

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

/* --------------------------------------------------------- Ключевые слова */

async function viewKeywords() {
  const semantic = localStorage.getItem('nf.keywordsMode') === 'semantic';
  const d = await api(`/api/keywords${q({ ...base(), sort_by: 'trend', top_n: 60, min_videos: 2,
    keywords_mode: semantic ? 'semantic' : 'ngram' })}`);
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Трендовые ключевые слова', plabel(state.period), `
        <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--muted)">
          <input type="checkbox" id="semanticModeToggle" ${semantic ? 'checked' : ''}>
          смысловой режим (слияние синонимов через эмбеддинги)
        </label>`)}
      <div class="tiles">
        ${tile('Видео в анализе', num(d.videosAnalysed))}
        ${tile('В предыдущем окне', num(d.previousWindowVideos))}
        ${tile('Базовая доля outlier', `${d.outlierBaseRate}%`, 'сколько видео вообще пробивают')}
        ${tile('Покрытие тегами', `${d.tagCoveragePercent}%`, 'теги проставляют не все авторы')}
      </div>
    </div>
    <div class="card">
      ${d.keywords.length ? table([
        { label: 'Фраза', wrap: true, render: (r) => esc(r.keyword) },
        { label: 'Opportunity', num: true, render: (r) => r.opportunityScore != null
            ? `<span class="chip ${r.opportunityScore >= 70 ? 'chip-good' : r.opportunityScore < 30 ? 'chip-bad' : ''}">${r.opportunityScore}</span>`
            : '—' },
        { label: 'Видео', num: true, render: (r) => num(r.videos) },
        { label: 'Momentum', num: true, render: (r) => r.momentum ?? '—' },
        { label: 'Lift', num: true, render: (r) => r.outlierLift ?? '—' },
        { label: 'Доля', num: true, render: (r) => `${r.share}%` },
        { label: 'Медиана просмотров', num: true, render: (r) => compact(r.medianViews) },
        { label: 'Новая', render: (r) => (r.isNew ? 'да' : '') },
        { label: 'Пример', wrap: true, render: (r) => esc(r.examples?.[0]?.title || '') },
      ], d.keywords) : empty(d.hint || 'нет фраз над порогом')}
      <div class="section-sub" style="margin-top:14px">
        <b>Opportunity</b> — 0-100, для удобства чтения: trendScore, растянутый по
        мин/макс среди фраз именно этого экрана. Это НЕ настоящий keyword score вроде
        vidIQ (для него нужен бы объём поиска YouTube, а такого API не существует) —
        сравнивать это число между разными запросами или окнами нельзя, только внутри
        одной текущей выдачи.<br>
        <b>momentum</b> — доля фразы в этом окне против доли в предыдущем таком же
        (со сглаживанием); больше 2 значит быстрый рост.<br>
        <b>outlierLift</b> — во сколько раз фраза повышает шансы видео пробить;
        больше 1.5 значит, что фраза реально коррелирует с пробитиями.
      </div>
    </div>`;

  $('#semanticModeToggle').addEventListener('change', (e) => {
    localStorage.setItem('nf.keywordsMode', e.target.checked ? 'semantic' : 'ngram');
    render();
  });
}

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

/* ------------------------------------------------------- Проверка идей (17) */

let ideasText = '';
let ideasFilters = { recentDays: 90, provenOutlier: 2.0, flopOutlier: 0.5 };
let ideasResult = null;

const IDEA_VERDICT_LABEL = {
  free: 'свободна', recent: 'недавно снимали',
  proven: 'доказан спрос', flopped: 'слабо/провал',
};
const IDEA_VERDICT_CLASS = { proven: 'chip-good', recent: 'chip-bad', flopped: 'chip-bad' };

function ideaVerdictChip(v) {
  return `<span class="chip ${IDEA_VERDICT_CLASS[v] || ''}">${esc(IDEA_VERDICT_LABEL[v] || v)}</span>`;
}

function exportIdeasCsv(result) {
  if (!result) return;
  const header = ['idea', 'verdict', 'daysSinceLastCoverage', 'bestOutlierScore', 'matchCount'];
  const rows = [header, ...result.ideas.map((i) => [
    i.idea, i.verdict, i.daysSinceLastCoverage ?? '', i.bestOutlierScore ?? '', i.matchCount,
  ])];
  const csv = rows.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'idea-check.csv';
  a.click();
  URL.revokeObjectURL(a.href);
}

function ideasResultsSection(result) {
  if (!result) return '';
  return `
    <div class="card">
      ${sectionHead('Результаты', result.hint || '',
        `<button class="btn btn-ghost btn-sm" id="ideasExportCsv">Экспорт CSV</button>`)}
      <div class="rows">${result.ideas.map((i) => `
        <details style="border:1px solid var(--border);border-radius:10px;padding:10px 12px">
          <summary style="cursor:pointer;display:flex;justify-content:space-between;align-items:center;gap:12px">
            <span>${esc(i.idea)}</span>
            <span style="display:flex;gap:8px;align-items:center;flex:none">
              ${ideaVerdictChip(i.verdict)}
              <span class="chip">${i.matchCount} найдено</span>
            </span>
          </summary>
          <div style="margin-top:10px">
            ${i.matches.length ? table([
              { label: 'Видео', wrap: true, render: (m) =>
                `<a href="https://www.youtube.com/watch?v=${esc(m.videoId)}" target="_blank" rel="noopener">${esc(m.title)}</a>` },
              { label: 'Канал', render: (m) => esc(m.channelTitle || m.channelId) },
              { label: 'Просмотры', num: true, render: (m) => compact(m.views) },
              { label: 'Outlier', num: true, render: (m) => mult(m.outlierScore) },
              { label: 'Опубликовано', render: (m) => ago(m.publishedAt) },
            ], i.matches) : empty('видео не найдены')}
          </div>
        </details>`).join('')}</div>
    </div>`;
}

async function viewIdeas() {
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Проверка идей', 'по одной идее на строку — свободна / недавно снимали / доказан спрос / провал')}
      <textarea id="ideasInput" rows="8" placeholder="car wash&#10;funeral home&#10;pet grooming"
        style="width:100%;font-family:inherit;resize:vertical">${esc(ideasText)}</textarea>
      <div style="display:flex;gap:16px;margin-top:10px;flex-wrap:wrap;align-items:center">
        <label style="font-size:12px;color:var(--muted)">recent, дней
          <input type="number" id="ideasRecentDays" value="${ideasFilters.recentDays}" style="width:70px"></label>
        <label style="font-size:12px;color:var(--muted)">proven outlier ≥
          <input type="number" step="0.1" id="ideasProvenOutlier" value="${ideasFilters.provenOutlier}" style="width:70px"></label>
        <label style="font-size:12px;color:var(--muted)">flop outlier ≤
          <input type="number" step="0.1" id="ideasFlopOutlier" value="${ideasFilters.flopOutlier}" style="width:70px"></label>
        <button class="btn" id="ideasSubmit">Проверить</button>
      </div>
    </div>
    ${ideasResultsSection(ideasResult)}`;

  $('#ideasSubmit').addEventListener('click', async () => {
    ideasText = $('#ideasInput').value;
    const ideas = ideasText.split('\n').map((s) => s.trim()).filter(Boolean);
    if (!ideas.length) { toast('Введите хотя бы одну идею', 'err'); return; }
    ideasFilters = {
      recentDays: Number($('#ideasRecentDays').value) || 90,
      provenOutlier: Number($('#ideasProvenOutlier').value) || 2.0,
      flopOutlier: Number($('#ideasFlopOutlier').value) || 0.5,
    };
    const btn = $('#ideasSubmit');
    btn.disabled = true; btn.textContent = 'Проверяю…';
    try {
      ideasResult = await api('/api/ideas/check', { method: 'POST', body: {
        ideas, niche: state.niche || undefined,
        recentDays: ideasFilters.recentDays, provenOutlier: ideasFilters.provenOutlier,
        flopOutlier: ideasFilters.flopOutlier,
      } });
      viewIdeas();
    } catch (e) {
      toast(e.message, 'err');
      btn.disabled = false; btn.textContent = 'Проверить';
    }
  });

  const csvBtn = $('#ideasExportCsv');
  if (csvBtn) csvBtn.addEventListener('click', () => exportIdeasCsv(ideasResult));
}

/* ------------------------------------------------------- Транскрипты (19) */

let transcriptTab = 'pending';
let transcriptSearchQuery = '';
let transcriptSearchResult = null;
let transcriptPasteOpenFor = null;

function transcriptQueueCard(item, tab) {
  const link = `https://www.youtube.com/watch?v=${esc(item.videoId)}`;
  return `
    <div class="row" style="align-items:flex-start;flex-direction:column;gap:8px">
      <div style="display:flex;width:100%;justify-content:space-between;gap:12px">
        <div class="row-main">
          <div class="row-title"><a href="${link}" target="_blank" rel="noopener">${esc(item.title || item.videoId)}</a></div>
          <div class="row-sub">${esc(item.channelTitle || '')}${item.views != null ? ` · ${compact(item.views)} просмотров` : ''}
            ${item.reason ? ` · причина: ${esc(item.reason)}` : ''}${item.compareGroup ? ` · группа: ${esc(item.compareGroup)}` : ''}</div>
          ${item.error ? `<div class="row-sub" style="color:var(--critical)">${esc(item.error)}</div>` : ''}
        </div>
        ${tab === 'pending' || tab === 'error'
          ? `<button class="btn btn-ghost btn-sm transcript-paste-btn" data-video-id="${esc(item.videoId)}">
              ${tab === 'error' ? 'Повторить' : 'Вставить транскрипт'}</button>`
          : `<button class="btn btn-ghost btn-sm transcript-reindex-btn" data-video-id="${esc(item.videoId)}">Переиндексировать</button>`}
      </div>
      <div class="transcript-paste-form" data-video-id="${esc(item.videoId)}" ${transcriptPasteOpenFor === item.videoId ? '' : 'hidden'}
        style="width:100%">
        <textarea class="transcript-paste-input" rows="8" placeholder="Вставьте текст транскрипта с YouTube (со таймкодами или без)"
          style="width:100%;font-family:inherit;resize:vertical"></textarea>
        <div style="display:flex;gap:10px;margin-top:8px;align-items:center">
          <input type="text" class="transcript-lang-input" placeholder="язык (необязательно)" style="width:140px">
          <button class="btn btn-sm transcript-save-btn" data-video-id="${esc(item.videoId)}">Сохранить</button>
          <span class="transcript-save-status row-sub"></span>
        </div>
      </div>
    </div>`;
}

function transcriptSearchResultsHtml(result) {
  if (!result) return '';
  if (result.hint) return notice(esc(result.hint));
  if (!result.results.length) return empty('ничего не найдено');
  return `<div class="rows">${result.results.map((r) => `
    <div class="row" style="align-items:flex-start">
      <div class="row-main">
        <div class="row-title"><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title || r.videoId)}</a></div>
        <div class="row-sub">${esc(r.channelTitle || '')}${r.views != null ? ` · ${compact(r.views)} просмотров` : ''}
          ${r.startSec != null ? ` · ${Math.floor(r.startSec / 60)}:${String(r.startSec % 60).padStart(2, '0')}` : ''}</div>
        <div style="margin-top:4px">${esc(r.text || '')}</div>
      </div>
    </div>`).join('')}</div>`;
}

async function viewTranscripts() {
  const [queueRes, searchRes] = await Promise.all([
    api('/api/transcripts/queue'),
    transcriptSearchQuery ? api(`/api/transcripts/search${q({ query: transcriptSearchQuery })}`) : null,
  ]);
  transcriptSearchResult = searchRes;
  const queue = queueRes.queue || [];
  const byTab = {
    pending: queue.filter((i) => i.status === 'pending'),
    ready: queue.filter((i) => i.status === 'ready'),
    error: queue.filter((i) => i.status === 'error'),
  };
  const tabs = [['pending', 'Ожидают'], ['ready', 'Готовы'], ['error', 'Ошибки']];

  view.innerHTML = `
    <div class="card">
      ${sectionHead('Поиск по транскриптам', 'гибридный поиск: вектор + полнотекстовый')}
      <div style="display:flex;gap:10px">
        <input type="text" id="transcriptSearchInput" placeholder="что ищем..." value="${esc(transcriptSearchQuery)}" style="flex:1">
        <button class="btn" id="transcriptSearchBtn">Искать</button>
      </div>
      ${transcriptSearchQuery ? `<div style="margin-top:12px">${transcriptSearchResultsHtml(transcriptSearchResult)}</div>` : ''}
    </div>

    <div class="card">
      ${sectionHead('Очередь транскриптов', 'субтитры не скачиваются автоматически — только вручную')}
      <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">
        <input type="text" id="transcriptAddVideoId" placeholder="ID видео" style="width:140px">
        <input type="text" id="transcriptAddReason" placeholder="причина (необязательно)" style="width:200px">
        <button class="btn btn-ghost btn-sm" id="transcriptAddBtn">Запросить транскрипт</button>
      </div>
      <div style="display:flex;gap:8px;margin-bottom:12px">
        ${tabs.map(([key, label]) => `<button class="btn btn-sm ${transcriptTab === key ? '' : 'btn-ghost'} transcript-tab-btn" data-tab="${key}">
          ${label} (${byTab[key].length})</button>`).join('')}
      </div>
      <div class="rows">${byTab[transcriptTab].length
        ? byTab[transcriptTab].map((item) => transcriptQueueCard(item, transcriptTab)).join('')
        : empty('пусто')}</div>
    </div>`;

  $('#transcriptAddBtn').addEventListener('click', async () => {
    const videoId = $('#transcriptAddVideoId').value.trim();
    if (!videoId) { toast('Введите ID видео', 'err'); return; }
    try {
      await api('/api/transcripts/request', { method: 'POST', body: {
        videoId, reason: $('#transcriptAddReason').value.trim() || undefined,
      } });
      toast('Добавлено в очередь', 'ok');
      viewTranscripts();
    } catch (e) { toast(e.message, 'err'); }
  });
  $('#transcriptSearchBtn').addEventListener('click', () => {
    transcriptSearchQuery = $('#transcriptSearchInput').value.trim();
    viewTranscripts();
  });
  view.querySelectorAll('.transcript-tab-btn').forEach((b) => b.addEventListener('click', () => {
    transcriptTab = b.dataset.tab;
    viewTranscripts();
  }));
  view.querySelectorAll('.transcript-paste-btn').forEach((b) => b.addEventListener('click', () => {
    transcriptPasteOpenFor = transcriptPasteOpenFor === b.dataset.videoId ? null : b.dataset.videoId;
    viewTranscripts();
  }));
  view.querySelectorAll('.transcript-reindex-btn').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true;
    try {
      await api(`/api/transcripts/${encodeURIComponent(b.dataset.videoId)}/reindex`, { method: 'POST' });
      toast('Переиндексировано', 'ok');
    } catch (e) { toast(e.message, 'err'); }
    finally { b.disabled = false; }
  }));
  view.querySelectorAll('.transcript-save-btn').forEach((b) => b.addEventListener('click', async () => {
    const form = b.closest('.transcript-paste-form');
    const text = form.querySelector('.transcript-paste-input').value;
    const language = form.querySelector('.transcript-lang-input').value.trim() || undefined;
    const status = form.querySelector('.transcript-save-status');
    if (!text.trim()) { status.textContent = 'вставьте текст'; return; }
    b.disabled = true;
    status.textContent = 'сохраняю…';
    try {
      const r = await api(`/api/transcripts/${encodeURIComponent(b.dataset.videoId)}/save`,
        { method: 'POST', body: { text, language } });
      if (r.status === 'ready') {
        toast(`Сохранено: ${r.chunks} кусков, ${r.wordCount} слов`, 'ok');
        transcriptPasteOpenFor = null;
        viewTranscripts();
      } else {
        status.textContent = r.error || 'ошибка';
      }
    } catch (e) {
      status.textContent = e.message;
    } finally {
      b.disabled = false;
    }
  }));
}

/* ------------------------------------------------------- Карта ниш (08) */

async function viewNicheClusters() {
  const d = await api('/api/niche-clusters');
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Карта ниш', 'кластеры каналов по эмбеддингам видео — без ручного определения ниши',
        `<button class="btn btn-ghost btn-sm" id="clustersRecomputeBtn">Пересчитать</button>`)}
      ${d.clusters.length ? table([
        { label: 'Название', wrap: true, render: (c) => `<b>${esc(c.name)}</b>
          ${c.description ? `<div class="row-sub">${esc(c.description)}</div>` : ''}` },
        { label: 'Каналов', num: true, render: (c) => num(c.channelCount) },
        { label: 'Медиана outlier', num: true, render: (c) => mult(c.medianOutlierScore) },
        { label: 'Скорость (VPH)', num: true, render: (c) => num(c.totalVelocity) },
        { label: 'Faceless', num: true, render: (c) => c.facelessShare != null ? `${Math.round(c.facelessShare * 100)}%` : '—' },
        { label: 'Конкуренция (>100k)', num: true, render: (c) => num(c.competitionCount) },
      ], d.clusters) : empty(d.hint || 'кластеров пока нет')}
    </div>`;

  $('#clustersRecomputeBtn').addEventListener('click', async () => {
    const btn = $('#clustersRecomputeBtn');
    btn.disabled = true;
    btn.textContent = 'Считаю…';
    try {
      await api('/api/niche-clusters/recompute', { method: 'POST' });
      toast('Пересчитано', 'ok');
      viewNicheClusters();
    } catch (e) {
      toast(e.message, 'err');
      btn.disabled = false;
      btn.textContent = 'Пересчитать';
    }
  });
}

/* ---------------------------------------------------- Проверить заголовки (09) */

let titleScoreResult = null;
let titleSuggestResult = null;

function titleResultRow(t) {
  const cls = t.score >= 70 ? 'chip-good' : t.score < 40 ? 'chip-bad' : '';
  return `
    <div class="row" style="align-items:flex-start">
      <div class="row-main">
        <div class="row-title">${esc(t.title)} <span class="chip ${cls}">${t.score}</span></div>
        <div class="row-sub">длина ${t.signals.length}${t.signals.hasNumber ? ' · есть число' : ''}
          ${t.signals.matchedPatterns.length ? ` · паттерны: ${t.signals.matchedPatterns.map(esc).join(', ')}` : ''}
          ${t.signals.isLikelyDuplicate ? ' · похоже на уже вышедшее видео' : ''}</div>
        ${t.strengths?.length ? `<div class="row-sub">+ ${t.strengths.map(esc).join(' · ')}</div>` : ''}
        ${t.risks?.length ? `<div class="row-sub">− ${t.risks.map(esc).join(' · ')}</div>` : ''}
        ${t.improved ? `<div class="row-sub"><b>Улучшенный вариант:</b> ${esc(t.improved)}</div>` : ''}
      </div>
    </div>`;
}

async function viewTitleScoring() {
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Проверить заголовки', 'оценка по шаблонам ниши + LLM (если включён); ниша берётся из глобального фильтра')}
      <textarea id="titleCandidatesInput" rows="6" placeholder="по одному заголовку на строку"
        style="width:100%;font-family:inherit;resize:vertical"></textarea>
      <div style="margin-top:10px">
        <button class="btn" id="titleScoreBtn">Оценить</button>
      </div>
      ${titleScoreResult ? `<div style="margin-top:12px">
        ${titleScoreResult.hint ? notice(esc(titleScoreResult.hint)) : ''}
        <div class="rows">${titleScoreResult.titles.map(titleResultRow).join('')}</div>
      </div>` : ''}
    </div>

    <div class="card">
      ${sectionHead('Сгенерировать варианты', 'нужен настроенный LLM_PROVIDER')}
      <div style="display:flex;gap:10px">
        <input type="text" id="titleTopicInput" placeholder="тема видео" style="flex:1">
        <button class="btn" id="titleSuggestBtn">Сгенерировать</button>
      </div>
      ${titleSuggestResult ? `<div style="margin-top:12px">
        ${titleSuggestResult.hint ? notice(esc(titleSuggestResult.hint)) : ''}
        <div class="rows">${(titleSuggestResult.titles || []).map(titleResultRow).join('')}</div>
      </div>` : ''}
    </div>`;

  $('#titleScoreBtn').addEventListener('click', async () => {
    const candidates = $('#titleCandidatesInput').value.split('\n').map((s) => s.trim()).filter(Boolean);
    if (!candidates.length) { toast('Введите хотя бы один заголовок', 'err'); return; }
    if (!state.niche) { toast('Выберите нишу в фильтре сверху', 'err'); return; }
    try {
      titleScoreResult = await api('/api/titles/score', { method: 'POST',
        body: { candidates, niche: state.niche } });
      viewTitleScoring();
    } catch (e) { toast(e.message, 'err'); }
  });
  $('#titleSuggestBtn').addEventListener('click', async () => {
    const topic = $('#titleTopicInput').value.trim();
    if (!topic) { toast('Введите тему', 'err'); return; }
    if (!state.niche) { toast('Выберите нишу в фильтре сверху', 'err'); return; }
    try {
      titleSuggestResult = await api('/api/titles/suggest', { method: 'POST',
        body: { topic, niche: state.niche, n: 8 } });
      viewTitleScoring();
    } catch (e) { toast(e.message, 'err'); }
  });
}

/* ----------------------------------------------------------------- Трекер */

/* faceless/format/topic (этап 03) живут только в памяти вкладки -- фильтр
   по AI-разметке не настолько важен, чтобы переживать перезагрузку, в
   отличие от state.period/niche. */
let channelFilters = { faceless: '', format: '', topic: '' };

async function viewTracker() {
  const f = channelFilters;
  const d = await api(`/api/channels/tracked${q({
    faceless: f.faceless || undefined, content_format: f.format || undefined, topic: f.topic || undefined,
  })}`);
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Трекер каналов', 'воркер снимает статистику по этим каналам и копит историю', `
        <select id="filterFaceless">
          <option value="" ${f.faceless === '' ? 'selected' : ''}>faceless: любой</option>
          <option value="true" ${f.faceless === 'true' ? 'selected' : ''}>faceless: да</option>
          <option value="false" ${f.faceless === 'false' ? 'selected' : ''}>faceless: нет</option>
        </select>
        <input type="text" id="filterFormat" placeholder="формат" value="${esc(f.format)}" style="width:120px">
        <input type="text" id="filterTopic" placeholder="тема" value="${esc(f.topic)}" style="width:120px">`)}
      ${d.channels.length ? table([
        { label: 'Канал', render: (r) => `<a href="#/channel/${esc(r.channel_id)}">${esc(r.title || r.channel_id)}</a>` },
        { label: 'AI', render: (r) => aiLabelsBadge(r.aiLabels) || '—' },
        { label: 'Подписчиков', num: true, render: (r) => compact(r.subscriber_count) },
        { label: 'Видео', num: true, render: (r) => num(r.video_count) },
        { label: 'Просмотров', num: true, render: (r) => compact(r.view_count) },
        { label: 'Снимков', num: true, render: (r) => num(r.snapshots) },
        { label: 'Обновлён', render: (r) => ago(r.last_refreshed_at) },
        { label: '', render: (r) => `<button class="btn btn-ghost btn-sm untrack" data-id="${esc(r.channel_id)}">убрать</button>` },
      ], d.channels) : empty('пока никого — добавьте канал ниже, или фильтр по AI-разметке ничего не нашёл')}
    </div>
    ${collectForm()}`;
  wireCollect(render);
  const applyFilters = () => {
    channelFilters = {
      faceless: $('#filterFaceless').value,
      format: $('#filterFormat').value.trim(),
      topic: $('#filterTopic').value.trim(),
    };
    render();
  };
  $('#filterFaceless').addEventListener('change', applyFilters);
  $('#filterFormat').addEventListener('change', applyFilters);
  $('#filterTopic').addEventListener('change', applyFilters);
  view.querySelectorAll('.untrack').forEach((b) => b.addEventListener('click', async () => {
    try {
      await api(`/api/channels/tracked/${b.dataset.id}`, { method: 'DELETE' });
      toast('Убрал из трекера', 'ok'); render();
    } catch (e) { toast(e.message, 'err'); }
  }));
}

/* --------------------------------------------------------------- Избранное */

async function viewSaved() {
  const d = await api('/api/saved');
  const items = d.items || [];
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Избранное', 'сохранённые видео и каналы со снимком метрик на момент сохранения')}
      ${items.length ? table([
        { label: 'Тип', render: (r) => r.kind === 'video' ? 'видео' : 'канал' },
        { label: '', wrap: true, render: (r) => r.kind === 'video'
            ? `<a href="https://www.youtube.com/watch?v=${esc(r.refId)}" target="_blank" rel="noopener">${esc(r.payload?.video?.title || r.refId)}</a>`
            : `<a href="#/channel/${esc(r.refId)}">${esc(r.payload?.profile?.title || r.refId)}</a>` },
        { label: 'Папка', render: (r) => esc(r.folder || 'default') },
        { label: 'Заметка', wrap: true, render: (r) => esc(r.note || '') },
        { label: 'Сохранено', render: (r) => ago(r.createdAt) },
        { label: '', render: (r) => `<button class="btn btn-ghost btn-sm unsave" data-id="${r.id}">убрать</button>` },
      ], items) : empty('пока пусто — кнопка «В избранное» есть в панели расширения на странице ролика или канала')}
    </div>`;
  view.querySelectorAll('.unsave').forEach((b) => b.addEventListener('click', async () => {
    try {
      await api(`/api/saved/${b.dataset.id}`, { method: 'DELETE' });
      toast('Убрал из избранного', 'ok'); render();
    } catch (e) { toast(e.message, 'err'); }
  }));
}

/* ------------------------------------------------------- Разбор метаданных */

const MD_VERDICT_LABEL = { ok: 'ок', warn: 'стоит поправить', unreliable: 'выборка мала' };

function metadataSignalRow(s) {
  const cls = s.verdict === 'ok' ? 'chip-good' : s.verdict === 'warn' ? 'chip-bad' : '';
  return `<div class="row" style="align-items:flex-start">
    <div class="row-main">
      <div class="row-title"><span class="chip ${cls}">${esc(MD_VERDICT_LABEL[s.verdict] || s.verdict)}</span> ${esc(s.id || '')}</div>
      <div class="row-sub">${esc(s.explanation || '')}</div>
    </div>
  </div>`;
}

let mdLastReview = null;

async function viewMetadata() {
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Разбор метаданных', 'проверка черновика заголовка/описания/тегов по собственной базе -- без единого «SEO score», только сигналы со своей выборкой')}
      <div class="form-row">
        <label class="field" style="flex:2 1 320px"><span class="field-label">Заголовок</span>
          <input type="text" id="mdTitle" placeholder="5 секретов нейросетей, о которых молчат"></label>
        <label class="field"><span class="field-label">Ниша</span>
          <input type="text" id="mdNiche" value="${esc(state.niche || '')}" placeholder="ai-explainers"></label>
        <label class="field"><span class="field-label">ID канала (необязательно)</span>
          <input type="text" id="mdChannel" placeholder="UC..."></label>
      </div>
      <div class="form-row" style="margin-top:12px">
        <label class="field" style="flex:2 1 320px"><span class="field-label">Описание</span>
          <textarea id="mdDesc" rows="3" placeholder="00:00 вступление..."></textarea></label>
        <label class="field"><span class="field-label">Теги (через запятую)</span>
          <input type="text" id="mdTags" placeholder="нейросети, ai"></label>
        <label class="field" style="flex:0 0 auto">
          <span class="field-label">Shorts</span>
          <input type="checkbox" id="mdShort" style="width:auto;height:38px"></label>
      </div>
      <div class="form-row" style="margin-top:12px">
        <button class="btn" id="mdReviewBtn" type="button">Проверить</button>
        <button class="btn btn-ghost" id="mdSaveBtn" type="button">Сохранить как черновик</button>
      </div>
    </div>
    <div id="mdResult"></div>
    <div class="card" id="mdDraftsCard">
      ${sectionHead('Сохранённые черновики', 'привяжите к video ID после публикации -- пригодится для проверки, сбылся ли прогноз')}
      <div id="mdDraftsBody">${empty('загрузка…')}</div>
    </div>`;

  function renderMdResult(res) {
    mdLastReview = res;
    const box = $('#mdResult');
    if (res.hint) {
      box.innerHTML = `<div class="card">${notice(esc(res.hint), 'warn')}</div>`;
      return;
    }
    box.innerHTML = `
      <div class="card">
        ${sectionHead('Сводка', `${res.sample.videosAnalysed} видео в выборке, из них ${res.sample.outliersInSample} выбросов · ${plabel(res.sample.period)}`)}
        <div class="tiles">
          ${tile('В норме', num(res.summary.ok))}
          ${tile('Стоит поправить', num(res.summary.warn))}
          ${tile('Ненадёжно', num(res.summary.unreliable))}
        </div>
      </div>
      <div class="grid-2">
        <div class="card">
          ${sectionHead('Сигналы')}
          ${res.signals.length ? res.signals.map(metadataSignalRow).join('') : empty('нет сигналов')}
        </div>
        <div class="card">
          ${sectionHead('Структурные паттерны ниши', res.keyPhrase ? `ключевая фраза: «${esc(res.keyPhrase)}»` : 'нет устойчивой ключевой фразы')}
          ${res.structuralPatterns.length ? table([
            { label: 'Признак', render: (r) => esc(r.label) },
            { label: 'Lift', num: true, render: (r) => r.lift != null ? mult(r.lift) : '—' },
            { label: 'Выборка', num: true, render: (r) => num(r.sample) },
            { label: 'В черновике', render: (r) => r.presentInDraft ? 'есть' : 'нет' },
            { label: '', render: (r) => r.verdict === 'unreliable' ? '<span class="chip">выборка мала</span>' : '' },
          ], res.structuralPatterns) : empty('нет данных')}
        </div>
      </div>
      <div class="card">
        ${sectionHead('Похожие темы в базе', 'косинус эмбеддинга черновика против собственной базы -- тема уже забита?')}
        ${res.nearDuplicates.hint ? notice(esc(res.nearDuplicates.hint), '')
          : res.nearDuplicates.near.length ? table([
              { label: 'Видео', wrap: true, render: (r) => `<a href="https://www.youtube.com/watch?v=${esc(r.videoId)}" target="_blank" rel="noopener">${esc(r.title || r.videoId)}</a>` },
              { label: 'Просмотры', num: true, render: (r) => compact(r.views) },
              { label: 'Похожесть', num: true, render: (r) => r.similarity },
            ], res.nearDuplicates.near)
          : empty(`проверено ${res.nearDuplicates.checked} видео -- близких тем не найдено`)}
      </div>`;
  }

  async function loadMdDrafts() {
    const chId = $('#mdChannel')?.value.trim() || null;
    const d = await api(`/api/drafts${q({ channel_id: chId })}`);
    $('#mdDraftsBody').innerHTML = d.drafts.length ? table([
      { label: 'Заголовок', wrap: true, render: (r) => esc(r.title) },
      { label: 'Ниша', render: (r) => esc(r.niche || '—') },
      { label: 'Сохранён', render: (r) => ago(r.createdAt) },
      { label: 'Видео', render: (r) => r.videoId
          ? `<a href="https://www.youtube.com/watch?v=${esc(r.videoId)}" target="_blank" rel="noopener">${esc(r.videoId)}</a>`
          : `<div class="form-row"><input type="text" class="mdLinkVid" data-id="${r.id}" placeholder="video ID" style="width:140px">
             <button class="btn btn-ghost btn-sm mdDoLink" data-id="${r.id}" type="button">привязать</button></div>` },
    ], d.drafts) : empty('пока пусто -- нажмите «Сохранить как черновик» выше');
    $('#mdDraftsBody').querySelectorAll('.mdDoLink').forEach((b) => b.addEventListener('click', async () => {
      const input = $(`.mdLinkVid[data-id="${b.dataset.id}"]`);
      const vid = input.value.trim();
      if (!vid) return toast('Укажите video ID', 'err');
      try {
        await api(`/api/drafts/${b.dataset.id}/link`, { method: 'POST', body: { videoId: vid } });
        toast('Привязано', 'ok'); loadMdDrafts();
      } catch (e) { toast(e.message, 'err'); }
    }));
  }

  function mdReadForm() {
    return {
      title: $('#mdTitle').value.trim(),
      description: $('#mdDesc').value,
      tags: $('#mdTags').value.split(',').map((t) => t.trim()).filter(Boolean),
      niche: $('#mdNiche').value.trim() || null,
      channelId: $('#mdChannel').value.trim() || null,
      isShort: $('#mdShort').checked,
    };
  }

  $('#mdReviewBtn').addEventListener('click', async () => {
    const body = mdReadForm();
    if (!body.title) return toast('Введите заголовок', 'err');
    try {
      renderMdResult(await api('/api/metadata/review', { method: 'POST', body }));
    } catch (e) { toast(e.message, 'err'); }
  });

  $('#mdSaveBtn').addEventListener('click', async () => {
    const body = mdReadForm();
    if (!body.title) return toast('Введите заголовок', 'err');
    body.review = mdLastReview;
    try {
      await api('/api/drafts', { method: 'POST', body });
      toast('Черновик сохранён', 'ok');
      await loadMdDrafts();
    } catch (e) { toast(e.message, 'err'); }
  });

  await loadMdDrafts();
}

/* ------------------------------------------------------------------ Ниши */

async function viewNiches() {
  const d = await api('/api/niches');
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Ниши', 'всё, что собрано под ярлыками')}
      ${d.niches.length ? table([
        { label: 'Слаг', render: (r) => `<a href="#/niche/${esc(r.slug)}">${esc(r.slug)}</a>` },
        { label: 'Запрос', wrap: true, render: (r) => esc(r.query || '') },
        { label: 'Видео', num: true, render: (r) => num(r.video_count) },
        { label: 'Последний сбор', render: (r) => ago(r.last_collected_at) },
      ], d.niches) : empty('ниш пока нет')}
    </div>
    ${collectForm()}`;
  wireCollect(render);
}

/* Общие тайлы + графики для нишевого обзора -- используется и по слагу
   (viewNiche), и от канала (niche_overview_from_channel в viewChannel).
   `head` -- готовый sectionHead(...) для верхней карточки. */
function nicheOverviewBlock(d, head) {
  return `
    <div class="card">
      ${head}
      <div class="tiles">
        ${tile('Видео', num(d.video_count))}
        ${tile('Каналов', num(d.channel_count))}
        ${tile('Медиана множителя', mult(d.median_outlier_score))}
        ${tile('Медиана просмотров', compact(d.median_views_per_video))}
        ${tile('Viral skew', d.viral_skew ?? '—', 'среднее / медиана')}
        ${tile('Shorts', `${d.shorts_share_percent}%`)}
        ${tile('Пробитий у мелких', num(d.small_channel_breakouts), '≤10k подп., VSR ≥ 5')}
      </div>
    </div>
    <div class="grid-2">
      <div class="card">
        ${sectionHead('Каналы по размеру', SATURATION[d.saturation_hint] || d.saturation_hint)}
        ${barList(Object.entries(d.channel_size_distribution).map(([k, v]) => ({ name: k, value: v, display: num(v) })))}
      </div>
      <div class="card">
        ${sectionHead('Топ по множителю')}
        ${table([
          { label: 'Видео', wrap: true, render: (r) => esc(r.title) },
          { label: 'Просмотры', num: true, render: (r) => compact(r.views) },
          { label: 'Множитель', num: true, render: (r) => mult(r.outlierScore) },
          { label: 'VSR', num: true, render: (r) => (+r.viewsPerSubscriber).toFixed(1) },
        ], d.top_videos_by_outlier_score)}
      </div>
    </div>`;
}

/* Доля хитов по тегам одной группы -- barList из tag_stats. Группа вводится
   свободным текстом (теги произвольные: theme/trigger/format/...), поэтому
   запоминаем последнюю выбранную в localStorage. */
function tagStatsSection(stats, tagGroup) {
  const body = stats.found === false
    ? empty(stats.hint || 'тегов в этой группе пока нет')
    : barList((stats.tags || []).map((t) => ({
        name: t.tag, value: t.hitRate, display: `${t.hitRate}%`,
        tip: `${t.tag}: ${t.videos} видео, hitRate ${t.hitRate}%, lift ${t.lift ?? '—'}`,
      })));
  return `
    <div class="card">
      ${sectionHead('Доля хитов по тегам', stats.found === false ? '' :
          `группа «${esc(tagGroup)}» · база по нише ${stats.baseRate}%`,
        `<input type="text" id="tagGroupInput" value="${esc(tagGroup)}"
           placeholder="группа тегов" style="width:140px">`)}
      ${body}
    </div>`;
}

/* Ручная правка тегов у видео -- каждое изменение шлёт replace:true с полным
   новым набором тегов этой группы для видео (см. application/tags.py:
   replace сохраняет защищённые manual/claude-mcp теги даже когда сам вызов
   идёт с другим source, здесь source всегда 'manual'). */
/* Панель Инсайты из комментариев (этап 04) -- строго по клику, никогда не
   автоматически: тратит квоту YouTube + может тратить деньги на LLM. */
function insightsPanelHtml(d) {
  if (d.hint) return notice(esc(d.hint));
  const pains = d.pains || [];
  const requests = d.requests || [];
  const ideas = d.video_ideas || [];
  const s = d.sentiment;
  const meta = d.cached ? 'из кеша' : `квота потрачена: ${d.quotaSpent ?? 0}`;
  if (!pains.length && !requests.length && !ideas.length) {
    return empty(`LLM не нашёл значимых сигналов в комментариях (${meta})`);
  }
  return `
    <div style="border:1px solid var(--border);border-radius:10px;padding:12px">
      ${s ? `<div class="row-sub" style="margin-bottom:8px">
        Тональность: ${Math.round(s.positive * 100)}% позитив ·
        ${Math.round(s.neutral * 100)}% нейтрально ·
        ${Math.round(s.negative * 100)}% негатив · ${meta}</div>` : ''}
      ${pains.length ? `<div style="margin-bottom:8px"><b>Боли</b>
        <ul style="margin:4px 0 0 18px">${pains.map((p) => `<li>${esc(p.text)}
          (~${p.count_estimate})${p.quotes?.[0] ? ` <span class="row-sub">«${esc(p.quotes[0])}»</span>` : ''}</li>`).join('')}</ul></div>` : ''}
      ${requests.length ? `<div style="margin-bottom:8px"><b>Запросы</b>
        <ul style="margin:4px 0 0 18px">${requests.map((r) => `<li>${esc(r.topic)}
          <span class="row-sub">«${esc(r.evidence)}»</span></li>`).join('')}</ul></div>` : ''}
      ${ideas.length ? `<div><b>Идеи видео</b>
        <ul style="margin:4px 0 0 18px">${ideas.map((i) => `<li>${esc(i.title)}
          <span class="row-sub">— ${esc(i.why)}</span></li>`).join('')}</ul></div>` : ''}
    </div>`;
}

function wireCommentInsights() {
  view.querySelectorAll('.insights-btn').forEach((btn) => btn.addEventListener('click', async () => {
    const videoId = btn.dataset.insightsVideoId;
    const panel = view.querySelector(`.insights-panel[data-insights-video-id="${CSS.escape(videoId)}"]`);
    if (!panel) return;
    if (!panel.hidden) { panel.hidden = true; return; }
    panel.hidden = false;
    panel.innerHTML = empty('Загружаю… (тратит квоту YouTube и, если включён LLM, деньги)');
    btn.disabled = true;
    try {
      const d = await api(`/api/videos/${encodeURIComponent(videoId)}/insights`,
        { method: 'POST', body: {} });
      panel.innerHTML = insightsPanelHtml(d);
    } catch (e) {
      panel.innerHTML = notice(esc(e.message));
    } finally {
      btn.disabled = false;
    }
  }));
  const nicheBtn = $('#nicheInsightsBtn');
  if (nicheBtn) nicheBtn.addEventListener('click', async () => {
    const panel = $('#nicheInsightsPanel');
    if (!panel) return;
    if (!panel.hidden) { panel.hidden = true; return; }
    panel.hidden = false;
    panel.innerHTML = empty('Загружаю…');
    nicheBtn.disabled = true;
    try {
      const d = await api(`/api/niches/${encodeURIComponent(nicheBtn.dataset.slug)}/insights`);
      panel.innerHTML = d.found === false ? notice(esc(d.hint)) : insightsPanelHtml(d);
    } catch (e) {
      panel.innerHTML = notice(esc(e.message));
    } finally {
      nicheBtn.disabled = false;
    }
  });
}

function nicheVideoTagsSection(videos, tagsByVideo, tagGroup, nicheSlug) {
  if (!videos.length) return '';
  return `
    <div class="card">
      ${sectionHead('Видео ниши', `теги группы «${esc(tagGroup)}» — правится вручную`,
        `<button class="btn btn-ghost btn-sm" id="nicheInsightsBtn" data-slug="${esc(nicheSlug)}">
           Инсайты по нише из кеша</button>`)}
      <div id="nicheInsightsPanel" hidden style="margin-bottom:12px"></div>
      <div class="rows">${videos.map((v) => `
        <div class="row" data-video-id="${esc(v.videoId)}" style="align-items:flex-start;flex-direction:column;gap:8px">
          <div style="display:flex;width:100%;align-items:flex-start;gap:12px">
            <div class="row-main">
              <div class="row-title">
                <a href="https://www.youtube.com/watch?v=${esc(v.videoId)}" target="_blank" rel="noopener">${esc(v.title)}</a>
              </div>
              <div class="row-sub">${compact(v.views)} просмотров · ${mult(v.outlierScore)}</div>
              <div class="tag-editor" style="margin-top:6px;display:flex;flex-wrap:wrap;gap:6px;align-items:center">
                ${(tagsByVideo[v.videoId] || []).map((t) => `
                  <span class="chip tag-chip" data-tag="${esc(t)}">${esc(t)}
                    <a href="#" class="tag-remove" data-tag="${esc(t)}" title="убрать тег">×</a>
                  </span>`).join('')}
                <input type="text" class="tag-add-input" placeholder="+ тег" style="width:100px">
              </div>
            </div>
            <button class="btn btn-ghost btn-sm insights-btn" data-insights-video-id="${esc(v.videoId)}">Инсайты из комментариев</button>
          </div>
          <div class="insights-panel" data-insights-video-id="${esc(v.videoId)}" hidden style="width:100%"></div>
        </div>`).join('')}</div>
    </div>`;
}

function wireNicheTagEditor(slug, tagGroup) {
  const groupInput = $('#tagGroupInput');
  if (groupInput) {
    groupInput.addEventListener('change', () => {
      const g = groupInput.value.trim() || 'theme';
      localStorage.setItem('nf.tagGroup', g);
      render();
    });
  }
  const writeTags = async (videoId, tags) => {
    await api('/api/tags', { method: 'POST', body: {
      items: [{ video_id: videoId, tag_group: tagGroup, tags }],
      source: 'manual', replace: true,
    } });
    render();
  };
  view.querySelectorAll('[data-video-id]').forEach((rowEl) => {
    const videoId = rowEl.dataset.videoId;
    const current = () => [...rowEl.querySelectorAll('.tag-chip')].map((c) => c.dataset.tag);

    rowEl.querySelectorAll('.tag-remove').forEach((a) => a.addEventListener('click', async (e) => {
      e.preventDefault();
      try { await writeTags(videoId, current().filter((t) => t !== a.dataset.tag)); }
      catch (err) { toast(err.message, 'err'); }
    }));

    const input = rowEl.querySelector('.tag-add-input');
    input.addEventListener('keydown', async (e) => {
      if (e.key !== 'Enter') return;
      const tag = input.value.trim();
      if (!tag) return;
      try { await writeTags(videoId, [...current(), tag]); }
      catch (err) { toast(err.message, 'err'); }
    });
  });
}

/* Предложенные LLM-теги вне таксономии ниши (этап 03) -- принять/отклонить
   через POST /api/tags/proposed/resolve. Не привязана к tagGroup выбранной
   в tagStatsSection: proposed-теги могут быть в любой группе. */
function proposedTagsSection(proposed) {
  if (!proposed.length) return '';
  return `
    <div class="card">
      ${sectionHead('Предложенные теги', 'LLM предложил теги вне таксономии ниши — подтвердите или отклоните')}
      <div class="rows">${proposed.map((p) => `
        <div class="row" data-proposed-video-id="${esc(p.videoId)}" data-tag-group="${esc(p.tagGroup)}" data-tag="${esc(p.tag)}">
          <div class="row-main">
            <div class="row-title">${esc(p.tag)} <span class="chip">${esc(p.tagGroup)}</span></div>
            <div class="row-sub">видео <a href="https://www.youtube.com/watch?v=${esc(p.videoId)}"
              target="_blank" rel="noopener">${esc(p.videoId)}</a> · ${ago(p.createdAt)}</div>
          </div>
          <div class="row-metrics">
            <button class="btn btn-ghost btn-sm proposed-accept">принять</button>
            <button class="btn btn-ghost btn-sm proposed-reject">отклонить</button>
          </div>
        </div>`).join('')}</div>
    </div>`;
}

function wireProposedTags() {
  view.querySelectorAll('[data-proposed-video-id]').forEach((rowEl) => {
    const { proposedVideoId: videoId, tagGroup, tag } = rowEl.dataset;
    const resolve = async (accept) => {
      try {
        await api('/api/tags/proposed/resolve', { method: 'POST',
          body: { videoId, tagGroup, tag, accept } });
        toast(accept ? 'Тег принят' : 'Тег отклонён', 'ok');
        render();
      } catch (e) { toast(e.message, 'err'); }
    };
    rowEl.querySelector('.proposed-accept')?.addEventListener('click', () => resolve(true));
    rowEl.querySelector('.proposed-reject')?.addEventListener('click', () => resolve(false));
  });
}

/* этап 15: фильтры точечного графика живут в памяти вкладки, как
   channelFilters у трекера -- не настолько важны, чтобы переживать
   перезагрузку. */
let scatterFilters = { channels: '', includeShorts: true };

function scatterSection(videos, filters) {
  return `
    <div class="card">
      ${sectionHead('Просмотры по датам публикации', 'цвет — канал; полый контур — моложе 30 дней; крупный контур — аномалия', `
        <input type="text" id="scatterChannels" placeholder="ID каналов через запятую"
          value="${esc(filters.channels)}" style="width:220px">
        <label style="display:flex;align-items:center;gap:4px;font-size:12px;color:var(--muted)">
          <input type="checkbox" id="scatterHideShorts" ${filters.includeShorts ? '' : 'checked'}> скрыть Shorts
        </label>`)}
      ${scatterChart(videos)}
    </div>`;
}

function wireScatterFilters(rerender) {
  const apply = () => {
    scatterFilters = {
      channels: $('#scatterChannels').value.trim(),
      includeShorts: !$('#scatterHideShorts').checked,
    };
    rerender();
  };
  $('#scatterChannels').addEventListener('change', apply);
  $('#scatterHideShorts').addEventListener('change', apply);
}

async function viewNiche(slug) {
  const tagGroup = localStorage.getItem('nf.tagGroup') || 'theme';
  const [d, stats, videoTags, proposed, scatter] = await Promise.all([
    api(`/api/niches/${encodeURIComponent(slug)}${q({ period: state.period, top_n: 30 })}`),
    api(`/api/tags/stats${q({ niche: slug, tag_group: tagGroup })}`),
    api(`/api/tags${q({ niche: slug })}`),
    api(`/api/tags/proposed${q({ niche: slug })}`),
    api(`/api/niches/${encodeURIComponent(slug)}/videos${q({
      period: state.period, channels: scatterFilters.channels || undefined,
      include_shorts: scatterFilters.includeShorts,
    })}`),
  ]);
  if (!d.found) { view.innerHTML = notice(esc(d.hint || 'ниша не найдена')); return; }

  const tagsByVideo = {};
  for (const t of (videoTags.tags || [])) {
    if (t.tagGroup !== tagGroup) continue;
    (tagsByVideo[t.videoId] ||= []).push(t.tag);
  }

  view.innerHTML = nicheOverviewBlock(d,
    sectionHead(`Ниша: ${slug}`, `${esc(d.query || '')} · ${plabel(state.period)}`, `
      <a class="btn btn-ghost btn-sm" href="/api/niche/${encodeURIComponent(slug)}/export.tsv">Экспорт TSV</a>
      <a class="btn btn-ghost btn-sm" href="/api/niche/${encodeURIComponent(slug)}/export.csv">Экспорт CSV</a>`))
    + scatterSection(scatter.videos || [], scatterFilters)
    + tagStatsSection(stats, tagGroup)
    + nicheVideoTagsSection(d.top_videos_by_outlier_score || [], tagsByVideo, tagGroup, slug)
    + proposedTagsSection(proposed.proposed || []);

  wireNicheTagEditor(slug, tagGroup);
  wireProposedTags();
  wireScatterFilters(() => viewNiche(slug));
  wireCommentInsights();
}

/* ----------------------------------------------------------------- Канал */

async function viewChannel(id) {
  const [a, vel, hist, sim, nicheOv] = await Promise.all([
    api(`/api/channels/${encodeURIComponent(id)}${q({ period: state.period })}`),
    api(`/api/channels/${encodeURIComponent(id)}/velocity${q({ period: state.period })}`),
    api(`/api/channels/${encodeURIComponent(id)}/history`),
    api(`/api/channels/${encodeURIComponent(id)}/similar`),
    api(`/api/channels/${encodeURIComponent(id)}/niche-overview`),
  ]);
  view.innerHTML = `
    <div class="card">
      ${sectionHead(a.profile.title || id,
        `${a.profile.handle || ''} · ${a.profile.country || '—'} · создан ${
          a.profile.createdAt ? new Date(a.profile.createdAt).toLocaleDateString('ru-RU') : '—'}`,
        `${aiLabelsBadge(a.profile.aiLabels)}
         <button class="btn btn-ghost btn-sm" id="trackThis">В трекер</button>
         <a class="btn btn-ghost btn-sm" href="https://www.youtube.com/channel/${esc(id)}" target="_blank" rel="noopener">YouTube</a>`)}
      <div class="tiles">
        ${tile('Подписчиков', compact(a.profile.subscribers))}
        ${tile('Просмотров', compact(a.profile.totalViews))}
        ${tile('Видео', num(a.profile.videoCount))}
        ${tile('Загрузок в неделю', a.cadence.uploadsPerWeekLifetime ?? '—')}
        ${tile('Медиана на видео', compact(a.performance.medianViewsPerVideo))}
        ${tile('Viral skew', a.performance.viralSkew ?? '—', 'среднее / медиана')}
        ${tile('Грейд', a.grade ?? '—', a.momentum ? `momentum ${a.momentum}` : 'нужна история')}
      </div>
      ${!a.growthAvailable ? notice(`Роста пока нет: снимков ${a.snapshots}.
        Он появится, когда воркер поработает несколько дней — <code>docker compose up -d worker</code>.`) : ''}
    </div>

    <div class="card">
      ${sectionHead('Просмотры во времени', 'по снимкам, которые пишет воркер')}
      ${hist.count > 1
        ? lineChart(hist.points.map((p) => ({ t: p.t, v: p.views })), { valueLabel: 'просмотров' })
        : empty(hist.hint)}
    </div>

    <div class="grid-2">
      <div class="card">
        ${sectionHead('Рост')}
        ${table([
          { label: 'Окно', render: (r) => r.w },
          { label: 'Подписчики', num: true, render: (r) => (r.subs ? `${r.subs.delta > 0 ? '+' : ''}${num(r.subs.delta)}` : '—') },
          { label: 'в день', num: true, render: (r) => (r.subs ? num(r.subs.per_day) : '—') },
          { label: 'Просмотры', num: true, render: (r) => (r.views ? `${r.views.delta > 0 ? '+' : ''}${compact(r.views.delta)}` : '—') },
          { label: 'в день', num: true, render: (r) => (r.views ? compact(r.views.per_day) : '—') },
        ], ['24h', '7d', '30d', '90d'].map((w) => ({ w, subs: a.growth[w].subscribers, views: a.growth[w].views })))}
        <div class="section-sub" style="margin-top:10px">API округляет число подписчиков
          до трёх значащих цифр, поэтому дельты подписчиков осмысленны примерно до 100 тысяч.
          Просмотры считаются точно.</div>
      </div>

      <div class="card">
        ${sectionHead('Оценка дохода', 'только AdSense, без интеграций')}
        <div class="tiles">
          ${tile('Диапазон Social Blade',
            `$${num(a.revenue.socialBladeRange.low_usd)}–${num(a.revenue.socialBladeRange.high_usd)}`, 'в месяц')}
          ${tile('Модель по RPM ниши', `$${num(a.revenue.nicheModel.monthly_usd)}`,
            `RPM $${a.revenue.nicheModel.rpm_effective}`)}
        </div>
        <div class="section-sub" style="margin-top:10px">Только AdSense, без спонсорских
          интеграций. Публичные оценки дохода регулярно ошибаются в 2–4 раза — читайте это
          как порядок величины, а не как сумму.</div>
      </div>
    </div>

    <div class="card">
      ${sectionHead('Топ outlier-видео', 'против медианы предыдущих загрузок этого канала')}
      ${table([
        { label: 'Видео', wrap: true, render: (r) => `<a href="https://www.youtube.com/watch?v=${esc(r.videoId)}" target="_blank" rel="noopener">${esc(r.title)}</a>` },
        { label: 'Просмотры', num: true, render: (r) => compact(r.views) },
        { label: 'Множитель', num: true, render: (r) => mult(r.outlierScore) },
        { label: 'С поправкой на возраст', num: true, render: (r) => mult(r.outlierScoreAgeAdjusted) },
        { label: 'Полоса', render: (r) => esc(r.band) },
        { label: 'Опубликовано', render: (r) => ago(r.publishedAt) },
        { label: '', render: (r) => `<button class="btn btn-ghost btn-sm js-comments" data-video-id="${esc(r.videoId)}" data-video-title="${esc(r.title)}">комментарии</button>
          <button class="btn btn-ghost btn-sm js-why" data-video-id="${esc(r.videoId)}" data-video-title="${esc(r.title)}">почему выстрелило</button>` },
      ], a.topOutliers)}
    </div>

    <div class="card" id="commentsPanel" hidden></div>
    <div class="card" id="whyPanel" hidden></div>

    <div class="card">
      ${sectionHead('Похожие каналы', 'по эмбеддингам собранных видео -- ' +
        (sim.videosEmbedded ? `centroid по ${sim.videosEmbedded} видео этого канала` : 'нужны эмбеддинги'))}
      ${sim.similar.length
        ? table([
            { label: 'Канал', wrap: true, render: (r) => `<a href="#/channel/${esc(r.channelId)}">${esc(r.title || r.channelId)}</a>` },
            { label: 'Подписчиков', num: true, render: (r) => compact(r.subscriberCount) },
            { label: 'Похожесть', num: true, render: (r) => r.similarity.toFixed(2) },
            { label: 'Видео с эмбеддингом', num: true, render: (r) => num(r.videosEmbedded) },
          ], sim.similar)
        : empty(sim.hint || 'ничего похожего не нашлось в собранном корпусе')}
    </div>

    ${nicheOv.found
      ? nicheOverviewBlock(nicheOv, sectionHead('Нишевый обзор',
          `по ${nicheOv.peer_channel_count} похожим каналам -- насыщенность и точки входа без сбора отдельной ниши`))
      : `<div class="card">
          ${sectionHead('Нишевый обзор')}
          ${empty(nicheOv.hint || 'недостаточно данных для нишевого обзора')}
        </div>`}

    <div class="card">
      ${sectionHead('Скорость по видео',
        'VPH за 24 часа и ускорение появляются, когда есть хотя бы два снимка с интервалом около суток')}
      ${table([
        { label: 'Видео', wrap: true, render: (r) => esc(r.title) },
        { label: 'Просмотры', num: true, render: (r) => compact(r.views) },
        { label: 'VPH за всё время', num: true, render: (r) => num(r.vphLifetime) },
        { label: 'VPH за 24ч', num: true, render: (r) => (r.vph24h != null ? num(r.vph24h) : '—') },
        { label: 'Прирост за сутки', num: true, render: (r) => (r.viewsGained24h != null ? compact(r.viewsGained24h) : '—') },
        { label: 'Тренд', render: (r) => (r.trend ? esc(r.trend) : '—') },
      ], vel.videos)}
    </div>`;

  $('#trackThis')?.addEventListener('click', async () => {
    try {
      await api('/api/channels/track', { method: 'POST', body: { channel_id: id } });
      toast('Добавил в трекер', 'ok');
    } catch (e) { toast(e.message, 'err'); }
  });

  view.querySelectorAll('.js-comments').forEach((b) => b.addEventListener('click', async () => {
    const panel = $('#commentsPanel');
    panel.hidden = false;
    panel.innerHTML = sectionHead(`Комментарии: ${esc(b.dataset.videoTitle)}`,
      'живой запрос к YouTube, тратит 1 unit квоты') + empty('загружаю…');
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    try {
      const res = await api(`/api/videos/${encodeURIComponent(b.dataset.videoId)}/comments`,
        { method: 'POST', body: {} });
      panel.innerHTML = sectionHead(`Комментарии: ${esc(b.dataset.videoTitle)}`,
        'живой запрос к YouTube, потратил 1 unit квоты') + commentList(res.comments);
    } catch (e) {
      panel.innerHTML = sectionHead(`Комментарии: ${esc(b.dataset.videoTitle)}`, '') + empty(e.message);
    }
  }));

  view.querySelectorAll('.js-why').forEach((b) => b.addEventListener('click', async () => {
    const panel = $('#whyPanel');
    panel.hidden = false;
    panel.innerHTML = sectionHead(`Почему выстрелило: ${esc(b.dataset.videoTitle)}`, '') + empty('загружаю…');
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    try {
      const res = await api(`/api/video/${encodeURIComponent(b.dataset.videoId)}/why`);
      if (!res) {
        panel.innerHTML = sectionHead(`Почему выстрелило: ${esc(b.dataset.videoTitle)}`, '')
          + notice('LLM выключен -- задайте LLM_PROVIDER=openrouter и OPENROUTER_API_KEY, чтобы включить');
        return;
      }
      if (res.hint) {
        panel.innerHTML = sectionHead(`Почему выстрелило: ${esc(b.dataset.videoTitle)}`, '') + notice(esc(res.hint));
        return;
      }
      panel.innerHTML = sectionHead(`Почему выстрелило: ${esc(b.dataset.videoTitle)}`,
        `уверенность: ${Math.round((res.confidence || 0) * 100)}%${res.cached ? ' · из кеша' : ''}`) + `
        <div style="border:1px solid var(--border);border-radius:10px;padding:12px">
          ${res.hooks?.length ? `<div style="margin-bottom:8px"><b>Зацепки</b>
            <ul style="margin:4px 0 0 18px">${res.hooks.map((h) => `<li>${esc(h)}</li>`).join('')}</ul></div>` : ''}
          ${res.title_pattern ? `<div class="row-sub" style="margin-bottom:6px"><b>Паттерн заголовка:</b> ${esc(res.title_pattern)}</div>` : ''}
          ${res.timing_factor ? `<div class="row-sub" style="margin-bottom:6px"><b>Фактор времени:</b> ${esc(res.timing_factor)}</div>` : ''}
          ${res.replicable_formula ? `<div><b>Формула для повтора:</b> ${esc(res.replicable_formula)}</div>` : ''}
        </div>`;
    } catch (e) {
      panel.innerHTML = sectionHead(`Почему выстрелило: ${esc(b.dataset.videoTitle)}`, '') + empty(e.message);
    }
  }));
}


/* ----------------------------------------------------------------- Данные */

async function viewData() {
  const [h, cov] = await Promise.all([
    api('/api/health'), api(`/api/coverage${q({ period: state.period })}`)]);
  view.innerHTML = `
    ${h.hasApiKey ? notice('Ключ YouTube API задан — сбор доступен.', 'ok')
                  : notice('Ключ YouTube API не задан. Впишите <code>YOUTUBE_API_KEY</code> в <code>.env</code> и перезапустите <code>docker compose up -d web</code>.', 'error')}
    ${h.historyAvailable ? '' : notice('Истории статистики нет — VPH за 24 часа, ускорение и рост каналов будут пустыми, пока воркер не поработает. Запустите <code>docker compose up -d worker</code>.')}
    <div class="card">
      ${sectionHead('База', h.db.db_path)}
      <div class="tiles">
        ${tile('Каналов', num(h.db.channels))}
        ${tile('Видео', num(h.db.videos))}
        ${tile('Ниш', num(h.db.niches))}
        ${tile('Снимков видео', num(h.db.video_stat_snapshots))}
        ${tile('Снимков каналов', num(h.db.channel_stat_snapshots))}
        ${tile('Смен заголовка/обложки', num(h.db.title_thumbnail_changes))}
        ${tile('Видео в окне', num(cov.videosPublishedInPeriod), plabel(state.period))}
      </div>
    </div>
    ${collectForm()}
    <div class="card">
      ${sectionHead('Обновить статистику', 'перечитывает счётчики и дописывает снимок — из этого берутся скорости')}
      <button class="btn btn-ghost" id="refreshBtn" type="button">Обновить сейчас</button>
    </div>`;
  wireCollect(render);
  $('#refreshBtn')?.addEventListener('click', async (e) => {
    e.target.disabled = true;
    try {
      const r = await api('/api/refresh', { method: 'POST', body: { period: '30d' } });
      toast(`Обновлено ${r.videos.refreshed} видео и ${r.channels.refreshed} каналов`, 'ok');
      render();
    } catch (err) { toast(err.message, 'err'); } finally { e.target.disabled = false; }
  });
}

/* ------------------------------------------------------------- Справка */

async function viewHelp() {
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Что это такое', 'бесплатный локальный аналог NexLev Niche Finder')}
      <div class="prose">
        <p class="lead"><strong>niche-finder</strong> — свой инструмент поиска ниш и вирусных видео на
          YouTube: те же разделы, что у NexLev / vidIQ / ViewStats (вирусные видео у маленьких
          каналов, топ категорий, растущие ключевые слова, разбор и трекинг каналов), но поверх
          бесплатного YouTube Data API v3 и локальной базы Postgres (с pgvector для смыслового
          поиска) — без подписки и без чужого сервера.</p>
        <p>Работает в двух формах одновременно: как <strong>MCP-сервер</strong> для Claude Desktop
          (инструкция — на отдельной странице «MCP-подключение» в меню слева) и как этот
          <strong>веб-дашборд</strong>, который вы сейчас открыли. Оба читают одну и ту же базу и
          одни и те же формулы — расхождений в цифрах между ними быть не может.</p>
        <p>Все базовые разделы работают без какого-либо ключа к LLM: сервер отдаёт сырые цифры,
          заголовки и обложки, а смысловые решения вида «faceless / подходит по теме» может принять
          модель, которая обращается к MCP-инструментам в диалоге. Опционально можно подключить
          LLM (<code>LLM_PROVIDER</code>: OpenRouter, в том числе бесплатные модели, или локальный
          Ollama) — тогда включаются фоновые AI-метки каналов, «почему выстрелило», выжимка
          комментариев, генерация заголовков, названия кластеров на карте ниш и авто-теги.</p>
      </div>
    </div>

    <div class="card">
      ${sectionHead('Экраны', 'что показывает каждый пункт меню слева')}
      <div class="prose">
        <h4>Обзор</h4>
        <p>Всё сразу одним запросом: топ outlier-каналов, кто скоро станет конкурентом, категории,
          ключевые слова и вирусные видео за выбранный период. Стартовая точка каждой сессии.</p>
        <h4>Найти нишу</h4>
        <p>Смысловой поиск по уже собранной базе (по эмбеддингам заголовков и описаний) — бесплатно,
          без квоты. Фильтры: минимальный множитель, максимум подписчиков, оценка RPM, длина видео,
          Shorts. Внизу — форма сбора, которая уже тратит квоту.</p>
        <h4>Вирусные видео</h4>
        <p>Видео маленьких каналов, которые выстрелили сильнее ожидаемого. Есть фильтры (подписчики,
          просмотры, период) и «воронка фильтров» внизу — видно, какой именно порог отсёк результаты,
          если список пуст.</p>
        <h4>Outlier-каналы</h4>
        <p>То же самое на уровне канала: лучший множитель среди его видео за окно и полоса силы
          0–4. Значок ≈ у множителя означает, что он посчитан по более грубой формуле — см. вопрос
          об этом в FAQ ниже.</p>
        <h4>Категории</h4>
        <p>Рейтинг категорий YouTube за период со сдвигом доли против предыдущего окна такой же
          длины — можно ранжировать по просмотрам или по числу каналов.</p>
        <h4>Ключевые слова</h4>
        <p>Растущие фразы (n-граммы) из заголовков с momentum и outlier-lift — насколько видео с этой
          фразой в среднем выстреливают сильнее прочих.</p>
        <h4>Топ теги по категориям</h4>
        <p>Авторские теги YouTube целиком, по категориям: сколько видео, доля и outlier-lift —
          lift выше 1,5 значит, что тег связан с выстрелами, а не просто популярен.</p>
        <h4>Трекер каналов</h4>
        <p>Вотчлист: каналы отсюда регулярно обновляет фоновый воркер, поэтому только для них со
          временем появляются рост подписчиков, грейд и вкладка «просмотры во времени». Если
          подключён LLM, список можно фильтровать по AI-меткам (faceless, формат, тема).</p>
        <h4>Проверка идей</h4>
        <p>Идея на строку → вердикт по вашей базе: свободно, недавно снимали, спрос доказан или
          провалилось. Результат можно выгрузить в CSV.</p>
        <h4>Транскрипты</h4>
        <p>Ручная очередь: транскрипты никогда не скачиваются автоматически — текст вставляется
          вручную, после чего по нему работает гибридный поиск (векторный + полнотекстовый).</p>
        <h4>Карта ниш</h4>
        <p>Каналы, сгруппированные k-means по эмбеддингам их видео: ниши, найденные без ручной
          разметки, с медианным множителем, скоростью, долей faceless и числом крупных конкурентов.</p>
        <h4>Проверить заголовки</h4>
        <p>Оценка заголовков 0–100 по паттернам выбранной ниши и проверка на дубли уже вышедших
          видео; генерация новых заголовков требует LLM.</p>
        <h4>Избранное</h4>
        <p>Swipe file: видео и каналы, сохранённые из расширения, со снимком метрик на момент
          сохранения, папкой и заметкой.</p>
        <h4>Разбор метаданных</h4>
        <p>Проверка черновика заголовка/описания/тегов по вашей базе — отдельные сигналы с размером
          выборки, а не один выдуманный «SEO-балл». Черновик можно сохранить и потом привязать к
          вышедшему видео, чтобы проверить прогноз.</p>
        <h4>Ниши</h4>
        <p>Список тем, под которыми вы собирали данные (поле <code>niche</code> при сборе). Внутри
          ниши — сводка, scatter просмотров по дате публикации, hit rate по группам тегов, видео с
          редактором тегов, выжимка комментариев и выгрузка в TSV/CSV.</p>
        <h4>Данные</h4>
        <p>Состояние ключа API, базы и истории, плюс формы сбора и ручное обновление статистики.
          Первая остановка, если какой-то раздел выглядит пустым или подозрительным.</p>
      </div>
    </div>

    <div class="card">
      ${sectionHead('Как читать метрики', 'глоссарий')}
      <dl class="glossary">
        <div><dt>Множитель / outlier score</dt>
          <dd>Просмотры видео, делённые на медиану просмотров предыдущих 10 обычных (не Shorts)
            загрузок того же канала. Возрастно-нормированная версия дополнительно поправляет на то,
            сколько дней видео уже живёт — иначе свежий ролик нечестно проигрывает старому.</dd></div>
        <div><dt>Значок ≈ рядом с множителем</dt>
          <dd>В базе меньше 4 видео этого канала — медианную базу посчитать не из чего, и число
            откатывается на формулу NexLev: просмотры делённые на среднее за всю жизнь канала. Один
            вирусный ролик раздувает такое среднее в разы, поэтому цифра приблизительная. Лечится
            одним сбором — <code>collect_channel</code> на весь канал.</dd></div>
        <div><dt>VSR (views per subscriber)</dt>
          <dd>Просмотры на одного подписчика — насколько видео вышло за пределы своей подписной
            базы, в чужие рекомендации.</dd></div>
        <div><dt>VPH (views per hour)</dt>
          <dd>«За всё время» — просто просмотры делённые на часы с публикации, это и есть VPH-бейдж
            у NexLev. «За 24 часа» — сколько просмотров ролик набрал именно за последние сутки;
            появляется только когда по нему накопилась история снимков.</dd></div>
        <div><dt>Ускорение</dt>
          <dd>VPH за сегодня, делённое на VPH за вчера. Больше 1 — разгоняется, меньше 1 — затухает.</dd></div>
        <div><dt>Momentum / грейд канала</dt>
          <dd>Просмотров в день за последние 30 дней, делённое на просмотров в день за всю жизнь
            канала, переведённое в шкалу Social Blade A++…D.</dd></div>
        <div><dt>period / period_by</dt>
          <dd><code>period_by="published"</code> (по умолчанию) — что вышло в окне по дате публикации.
            <code>period_by="discovered"</code> — что мы сами впервые увидели в окне, независимо от
            даты публикации. У NexLev в «Last 24 hours» встречаются ролики годовалой давности —
            это ровно discovered-режим; чтобы воспроизвести его поведение, переключайтесь на discovered.</dd></div>
        <div><dt>Воронка фильтров и hint</dt>
          <dd>Если раздел вернул пусто, внизу страницы — сколько видео осталось после каждого
            фильтра и пояснение, какой именно порог всё отсёк. Пустой результат почти всегда
            значит «в это окно ничего не собрано», а не «ничего не трендит».</dd></div>
      </dl>
    </div>

    <div class="card">
      ${sectionHead('С чего начать', 'день первый')}
      <div class="prose">
        <p>1. На странице «Данные» впишите канал (@handle, UC-id или ссылку) и нажмите
          <strong>«Собрать канал»</strong>. Это уходит через плейлист загрузок — около 1 unit
          квоты на 50 видео, поиск не тратится. Повторите для 20–50 каналов вашей темы.</p>
        <p>2. Если темы в базе ещё нет вообще — используйте поле поискового запроса
          (<strong>«Собрать поиском»</strong>). Он единственный тратит поиск: 1 из 100 вызовов в сутки.</p>
        <p>3. Смотрите разделы — они бесплатны, обновляйте сколько угодно раз кнопкой «Обновить»
          в шапке.</p>
        <p>4. Держите воркер включённым (<code>docker compose up -d worker</code>): через сутки
          появится VPH за 24 часа и прирост, через неделю — рост каналов и momentum, через месяц —
          можно пересчитать кривую зрелости под свои ниши.</p>
      </div>
    </div>

    <div class="card">
      ${sectionHead('FAQ', 'частые вопросы')}
      <div class="faq">
        <details>
          <summary>Раздел вернул пусто — что не так?</summary>
          <div class="a">Почти никогда не «ничего не трендит» — почти всегда «в это окно ничего не
            собрано» или порог фильтра отсёк все строки. Прокрутите вниз до блока «Воронка фильтров»:
            там видно, на каком шаге список опустел, и подсказку, что с этим делать. Проверьте
            заодно окно на странице «Данные» — там показано, сколько видео реально попало в
            выбранный период.</div>
        </details>
        <details>
          <summary>Что означает «за последние 24 часа»?</summary>
          <div class="a">По умолчанию — по дате публикации (<code>period_by=published</code>). Есть
            второй режим, <code>discovered</code> — по дате, когда система сама впервые увидела
            видео. Подробнее — в глоссарии выше, пункт «period / period_by».</div>
        </details>
        <details>
          <summary>Почему у множителя канала стоит значок ≈?</summary>
          <div class="a">Значит в базе меньше 4 видео этого канала, и множитель посчитан по более
            грубой формуле NexLev (просмотры / среднее за всю жизнь), а не по медиане прошлых
            загрузок. Соберите канал целиком через <code>collect_channel</code>, и значок исчезнет
            сам собой, как только видео станет 4 и больше.</div>
        </details>
        <details>
          <summary>Сколько это стоит по квоте YouTube?</summary>
          <div class="a">Поиск (<code>collect_niche</code>) — отдельная корзина, всего 100 вызовов
            в сутки. Всё остальное — сбор канала, обновление статистики, чтение разделов — идёт из
            общего пула на 10 000 units в сутки и стоит копейки: около 1 unit на 50 видео. Разделы
            дашборда (вирусные видео, категории, ключевые слова, аналитика канала) вообще бесплатны —
            они читают уже собранную базу, а не ходят в YouTube заново.</div>
        </details>
        <details>
          <summary>Я поправил код, но изменений не видно</summary>
          <div class="a">Если правили <code>frontend/*</code> (HTML/CSS/JS) — просто перезагрузите
            страницу, папка примонтирована в контейнер только для чтения. Если правили
            <code>backend/*.py</code> — веб-сервер (uvicorn) не перечитывает код на лету даже с
            примонтированной папкой, нужен рестарт процесса:
            <code>docker compose restart web</code>. Пересборка образа (<code>docker compose build</code>)
            нужна вообще только когда меняется <code>requirements.txt</code>.</div>
        </details>
        <details>
          <summary>Docker собирается вечно и падает с ошибкой Docker Hub</summary>
          <div class="a">Это сеть, не код: <code>lookup auth.docker.io: i/o timeout</code> значит
            Docker не достучался до реестра образов. Чаще всего виноват включённый VPN — отключите
            и повторите; если не помогло, перезапустите Docker Desktop. Проверить:
            <code>curl -sI https://auth.docker.io/token</code> и <code>docker pull hello-world</code>.</div>
        </details>
        <details>
          <summary>Откуда берутся VPH за 24 часа, ускорение и рост?</summary>
          <div class="a">YouTube API отдаёт только «сколько просмотров прямо сейчас» — снимок без
            истории. Все скоростные метрики существуют только потому, что фоновый воркер регулярно
            записывает эти снимки. Без запущенного <code>docker compose up -d worker</code> такие
            поля останутся пустыми независимо от того, сколько каналов вы собрали.</div>
        </details>
        <details>
          <summary>Это то же самое, что NexLev?</summary>
          <div class="a">Те же разделы и по возможности те же формулы (сверка — в
            <code>outlierScoreNexlev</code>), но локально, бесплатно и с одним улучшением: базовая
            линия для множителя — медиана прошлых загрузок канала, а не среднее за всю жизнь,
            которое один вирусный ролик может исказить в 20+ раз.</div>
        </details>
        <details>
          <summary>Нужен платный ключ к ИИ для классификации ниш?</summary>
          <div class="a">Нет. Без LLM сервер отдаёт сырые данные (заголовки, описания, обложки,
            цифры), а смысловые решения вроде «это faceless-канал» или «подходит под эту нишу»
            принимает модель, которая вызывает MCP-инструменты в диалоге — она у вас уже есть в
            Claude Desktop. Если хочется, чтобы метки ставились сами в фоне, задайте
            <code>LLM_PROVIDER</code> в <code>.env</code>: OpenRouter (есть бесплатные модели,
            суточный бюджет ограничивается) или локальный Ollama — тогда ничего не уходит с
            машины.</div>
        </details>
        <details>
          <summary>Ключ не работает или квота внезапно кончилась</summary>
          <div class="a">Запустите диагностику: <code>make doctor</code> (или
            <code>docker compose run --rm mcp python cli.py doctor</code>). Она по порядку
            проверяет формат ключа, что API реально отвечает, включён ли YouTube Data API v3 в
            Google Cloud Console, ограничения по IP/referrer у ключа и состояние базы — и печатает
            конкретный список того, что чинить.</div>
        </details>
      </div>
    </div>`;
}

/* ---------------------------------------------------------------- MCP */

async function viewMcp() {
  view.innerHTML = `
    <div class="card">
      ${sectionHead('MCP-подключение', 'как дать Claude Desktop доступ к вашей базе')}
      <div class="prose">
        <p class="lead">MCP (Model Context Protocol) — это то, что превращает niche-finder из
          дашборда в набор инструментов, которыми Claude пользуется прямо в диалоге: собирает
          данные, ищет вирусные видео, разбирает каналы и сам решает, что из найденного релевантно
          вашей теме. Дашборд (страница «Справка и FAQ» рядом) и MCP-сервер читают одну и ту же
          базу Postgres — можно собирать данные откуда угодно, а смотреть результат в другом месте.</p>
        <p>Всего 61 инструмент. Тратят квоту YouTube только инструменты <strong>сбора</strong>;
          всё остальное — <strong>разделы</strong>, трекинг и анализ каналов, теги, идеи, заголовки,
          алерты, избранное, транскрипты — читает уже собранную базу бесплатно. Отдельная группа
          <strong>LLM-функций</strong> работает, только если задан <code>LLM_PROVIDER</code>.
          Краткий обзор групп — ниже.</p>
      </div>
    </div>

    <div class="card">
      ${sectionHead('Подключение через Docker', 'рекомендуемый способ — тот же образ, что и у дашборда')}
      <div class="prose">
        <p>Откройте
          <code>~/Library/Application Support/Claude/claude_desktop_config.json</code>
          и добавьте:</p>
        <pre><code>{
  "mcpServers": {
    "niche-finder": {
      "command": "/path/to/youtube-niche-finder/scripts/mcp-docker.sh"
    }
  }
}</code></pre>
        <p>Скрипт запускает <code>server.py</code> в образе <code>niche-finder:latest</code>,
          подключает контейнер к сети <code>niche-finder_default</code> (там живёт Postgres из
          compose), монтирует <code>backend/</code> только для чтения и общий том
          <code>niche-finder-models</code>. Переменные из <code>.env</code> он разбирает сам: снимает
          кавычки вокруг значений (у <code>docker run --env-file</code> они уехали бы в ключ
          вместе со значением) и пропускает <code>NICHE_DATABASE_URL</code> — это адрес базы с хоста,
          внутри контейнера он не работает. Поэтому вместо ручной команды <code>docker run</code>
          используйте именно скрипт.</p>
        <p>Сеть появляется только после первого <code>docker compose up</code> — сначала поднимите
          хотя бы базу: <code>docker compose up -d postgres</code>.</p>
        <p>После правки конфига полностью перезапустите Claude Desktop (не просто закрыть окно —
          выйти из приложения), иначе он не перечитает список серверов.</p>
      </div>
    </div>

    <div class="card">
      ${sectionHead('Подключение по HTTPS', 'для клиентов, которым удобнее URL, а не процесс')}
      <div class="prose">
        <pre><code>docker compose --profile http up -d mcp-http mcp-https</code></pre>
        <p>Сервер слушает <code>https://localhost:8765/mcp</code> (только 127.0.0.1). Конфиг клиента:</p>
        <pre><code>{
  "mcpServers": {
    "niche-finder": { "url": "https://localhost:8765/mcp", "type": "http" }
  }
}</code></pre>
        <p>TLS терминирует Caddy сертификатом своего локального CA (<code>tls internal</code>),
          поэтому корневой сертификат этого CA нужно один раз добавить в доверенные в системе.</p>
      </div>
    </div>

    <div class="card">
      ${sectionHead('Подключение без Docker', 'если запускаете backend напрямую, через venv')}
      <div class="prose">
        <pre><code>cd /path/to/youtube-niche-finder/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # впишите YOUTUBE_API_KEY</code></pre>
        <p>Базе Postgres всё равно нужно где-то работать — проще всего поднять её из compose
          (<code>docker compose up -d postgres</code>, на хосте она на порту 5433) и прописать в
          <code>.env</code> <code>NICHE_DATABASE_URL=postgresql://niches:niches@localhost:5433/niches</code>.</p>
        <p>Конфиг:</p>
        <pre><code>{
  "mcpServers": {
    "niche-finder": {
      "command": "/path/to/youtube-niche-finder/backend/.venv/bin/python3",
      "args": ["/path/to/youtube-niche-finder/backend/server.py"]
    }
  }
}</code></pre>
        <p>В этом режиме история не собирается сама — фоновый воркер не запущен, запускайте
          <code>python3 worker.py</code> отдельно (или по cron), иначе поля скорости (VPH за 24ч,
          ускорение, рост) останутся пустыми.</p>
      </div>
    </div>

    <div class="card">
      ${sectionHead('Инструменты', 'сбор тратит квоту, разделы и трекинг — бесплатны')}
      <div class="prose">
        <h4>Сбор</h4>
        <ul>
          <li><code>collect_niche</code> — поиск по теме → видео, каналы, эмбеддинги. 1 поисковый
            вызов из 100 в сутки.</li>
          <li><code>collect_channel</code> — загрузки канала через uploads-плейлист. ~1 unit / 50 видео,
            поиск не тратит. Основной способ набрать корпус.</li>
          <li><code>collect_trending</code> — снапшот чарта mostPopular (Музыка/Фильмы/Игры).</li>
          <li><code>refresh_stats</code> — перечитать счётчики видео, дописать снимок в историю.</li>
          <li><code>refresh_channels</code> — снапшот подписчиков/просмотров каналов.</li>
          <li><code>refresh_categories</code> — актуальная карта id → название категории.</li>
          <li><code>video_comments</code> — комментарии одного видео вживую, без сохранения (1 unit).</li>
          <li><code>backfill_embeddings</code> — эмбеддинги для видео, собранных без них (квоту не
            тратит, считается локально).</li>
        </ul>
        <h4>Разделы</h4>
        <ul>
          <li><code>viral_videos_small_channels</code>, <code>recently_added_outlier_channels</code>,
            <code>high_future_competition</code> — вирусные видео/каналы за период.</li>
          <li><code>most_popular_categories</code>, <code>trending_keywords</code>,
            <code>top_tags_by_category</code> — топ категорий, растущие фразы и теги.</li>
          <li><code>search_outliers</code>, <code>similar_channels</code>, <code>similar_videos</code> —
            смысловой поиск по базе (pgvector).</li>
          <li><code>niche_overview</code>, <code>niche_overview_from_channel</code>,
            <code>niche_videos</code>, <code>niche_map</code> — насыщенность ниши, её видео и карта
            ниш по кластерам каналов.</li>
          <li><code>check_ideas</code> — пакетная проверка идей по базе.</li>
          <li><code>list_niches</code>, <code>db_stats</code>, <code>data_coverage</code> — что собрано и
            хватает ли данных на окно.</li>
        </ul>
        <h4>Теги</h4>
        <ul>
          <li><code>tag_videos</code>, <code>list_video_tags</code>, <code>tag_stats</code>,
            <code>list_proposed_tags</code> / <code>resolve_proposed_tag</code> — своя разметка видео
            и какой угол реально выстреливает.</li>
        </ul>
        <h4>Трекинг и анализ каналов</h4>
        <ul>
          <li><code>track_channel</code> / <code>untrack_channel</code> / <code>list_tracked_channels</code> —
            вотчлист.</li>
          <li><code>channel_analytics</code>, <code>compare_channels</code>, <code>channel_velocity</code> —
            профиль, каденс, рост, momentum, грейд, проекции, доход.</li>
          <li><code>title_changes</code>, <code>title_patterns</code>, <code>best_time_to_publish</code>,
            <code>calibrate_maturity_curve</code> — более тонкие разборы.</li>
        </ul>
        <h4>Алерты, заголовки, избранное, транскрипты</h4>
        <ul>
          <li><code>scan_for_alerts</code>, <code>list_events</code>, <code>mark_events_seen</code> —
            новые outlier'ы, ускорение, смена заголовка, возвращение канала после паузы.</li>
          <li><code>score_titles</code>, <code>review_metadata</code>, <code>save_draft</code> /
            <code>list_drafts</code> / <code>link_draft</code>, <code>draft_outcomes</code> — проверка
            заголовков и метаданных, черновики и сверка прогноза с итогом.</li>
          <li><code>save_item</code>, <code>list_saved_items</code>, <code>delete_saved_item</code> —
            swipe file.</li>
          <li><code>request_transcript</code>, <code>list_transcript_queue</code>,
            <code>search_transcripts</code> — ручная очередь транскриптов и гибридный поиск по ним.</li>
        </ul>
        <h4>LLM-функции (нужен <code>LLM_PROVIDER</code>)</h4>
        <ul>
          <li><code>explain_outlier</code>, <code>comment_insights</code>,
            <code>niche_comment_insights</code> — почему видео выстрелило и что просят в комментариях.</li>
          <li><code>suggest_titles</code>, <code>enrich_channels</code>, <code>tag_new_videos</code> —
            генерация заголовков, AI-метки каналов и авто-теги.</li>
        </ul>
        <p>Полные описания, стоимость по квоте и формулы — в <code>backend/README.md</code> в папке
          проекта.</p>
      </div>
    </div>

    <div class="card">
      ${sectionHead('Проверка подключения', 'диагностика и первые команды')}
      <div class="prose">
        <p>Если Claude Desktop не видит сервер или инструменты падают с ошибкой — сначала
          диагностика, не гадание:</p>
        <pre><code>make doctor
# или: docker compose run --rm mcp python cli.py doctor</code></pre>
        <p>Она по порядку проверяет формат ключа, что API реально отвечает, включён ли YouTube
          Data API v3, ограничения по IP/referrer у ключа, состояние базы и покрытие окна 24 часа —
          и печатает список того, что чинить, а не просто «ошибка». С флагом <code>--llm</code>
          (<code>python cli.py doctor --llm</code>) она дополнительно пингует настроенный LLM.</p>
        <p>В самом Claude Desktop, если сервер подключился, можно просто попросить обычным языком —
          например: <em>«Собери канал @Inkexplainer96 и покажи его вирусные видео за 30 дней»</em>
          или <em>«Какие категории сейчас растут быстрее всего за последнюю неделю?»</em> — модель
          сама выберет и вызовет нужные инструменты.</p>
      </div>
    </div>

    <div class="card">
      ${notice('MCP-сервер — не то же самое, что веб-дашборд: Claude Desktop запускает свежий процесс на каждый диалог, поэтому правки в <code>backend/*.py</code> подхватываются в MCP сами собой при следующем запуске Claude Desktop. А вот у постоянно работающего дашборда (<code>docker compose up -d web</code>) после правок Python-файлов нужен <code>docker compose restart web</code> — подробнее в разделе «Справка и FAQ».')}
    </div>`;
}

/* --------------------------------------------------------------- роутинг */

const ROUTES = {
  overview: { title: 'Обзор', run: viewOverview },
  find: { title: 'Найти нишу', run: viewFind },
  viral: { title: 'Вирусные видео', run: viewViral },
  channels: { title: 'Outlier-каналы', run: viewChannels },
  categories: { title: 'Категории', run: viewCategories },
  keywords: { title: 'Ключевые слова', run: viewKeywords },
  tags: { title: 'Топ теги по категориям', run: viewTopTags },
  tracker: { title: 'Трекер каналов', run: viewTracker },
  ideas: { title: 'Проверка идей', run: viewIdeas },
  transcripts: { title: 'Транскрипты', run: viewTranscripts },
  clusters: { title: 'Карта ниш', run: viewNicheClusters },
  titles: { title: 'Проверить заголовки', run: viewTitleScoring },
  saved: { title: 'Избранное', run: viewSaved },
  metadata: { title: 'Разбор метаданных', run: viewMetadata },
  niches: { title: 'Ниши', run: viewNiches },
  data: { title: 'Данные', run: viewData },
  help: { title: 'Справка и FAQ', run: viewHelp },
  mcp: { title: 'MCP-подключение', run: viewMcp },
};

function setActive(href) {
  document.querySelectorAll('.nav-item').forEach((a) =>
    a.classList.toggle('active', href != null && a.getAttribute('href') === href));
}

async function render() {
  const raw = (location.hash || '#/overview').slice(2);
  const [name, arg] = raw.split('/');
  if (name === 'channel' && arg) {
    $('#crumbSection').textContent = 'Канал'; setActive(null);
    return guard(() => viewChannel(decodeURIComponent(arg)));
  }
  if (name === 'niche' && arg) {
    $('#crumbSection').textContent = 'Ниша'; setActive('#/niches');
    return guard(() => viewNiche(decodeURIComponent(arg)));
  }
  const route = ROUTES[name || 'overview'] || ROUTES.overview;
  $('#crumbSection').textContent = route.title;
  setActive(`#/${name || 'overview'}`);
  return guard(route.run);
}

/* --------------------------------------------------------------- запуск */

async function loadNiches() {
  try {
    const d = await api('/api/niches');
    const sel = $('#globalNiche');
    sel.innerHTML = '<option value="">все ниши</option>' +
      (d.niches || []).map((n) => `<option value="${esc(n.slug)}">${esc(n.slug)} (${n.video_count})</option>`).join('');
    sel.value = state.niche;
  } catch { /* фронт работает и без списка ниш */ }
}

async function loadFootStat() {
  try {
    const h = await api('/api/health');
    const sq = h.searchQuota;
    const uq = h.unitQuota;
    $('#footStat').innerHTML =
      `${num(h.db.channels)} каналов · ${num(h.db.videos)} видео<br>` +
      (h.historyAvailable ? 'история пишется' : 'истории нет — запустите воркер') +
      (sq ? `<br>Поиск: ${sq.callsLeft}/${sq.dailyLimit} осталось сегодня` : '') +
      (uq ? `<br>Квота API: ${num(uq.unitsLeft)}/${num(uq.dailyLimit)} units осталось` : '');
    $('#brandTag').textContent = h.hasApiKey ? 'local' : 'без ключа';
  } catch { $('#footStat').textContent = 'сервис недоступен'; }
}

function initChrome() {
  const per = $('#globalPeriod');
  per.value = state.period;
  per.addEventListener('change', () => {
    state.period = per.value; localStorage.setItem('nf.period', state.period); render();
  });
  $('#globalNiche').addEventListener('change', (e) => {
    state.niche = e.target.value; localStorage.setItem('nf.niche', state.niche); render();
  });
  $('#reloadBtn').addEventListener('click', () => { loadFootStat(); loadNiches(); render(); });

  const themeBtn = $('#themeToggle');
  const applyTheme = (t) => {
    document.documentElement.dataset.theme = t;
    localStorage.setItem('nf.theme', t);
    themeBtn.textContent = t === 'dark' ? 'Светлая тема' : 'Тёмная тема';
  };
  applyTheme(localStorage.getItem('nf.theme') || 'dark');
  themeBtn.addEventListener('click', () =>
    applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));

  addEventListener('hashchange', render);
}

initChrome();
loadNiches();
loadFootStat();
render();
