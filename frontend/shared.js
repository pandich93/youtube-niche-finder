/* Общее для экранов: контейнер #view, подписи периодов, форма сбора, подвал
   с квотой, нишевый обзор и перерисовка текущего экрана. */
import { $, api, num, compact, mult, esc, toast, tile, sectionHead, barList, table, state } from './ui.js';

/* render() живёт в router.js, но экраны перерисовывают себя через него после
   действий. Прямой импорт дал бы цикл router -> screens -> router, поэтому
   router регистрирует свою функцию здесь при загрузке (setRender). */
let renderImpl = null;
function setRender(fn) { renderImpl = fn; }
function render() { return renderImpl(); }

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

export { view, plabel, base, collectForm, wireCollect, loadFootStat, nicheOverviewBlock, render, setRender };
