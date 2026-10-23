/* Общее для экранов: контейнер #view, подписи периодов, форма сбора, подвал
   с квотой, нишевый обзор и перерисовка текущего экрана. */
import { $, api, num, compact, mult, esc, toast, tile, sectionHead, barList, table, state, saturationBlock } from './ui.js';
import { dict } from './i18n.js';
import ru from './i18n/ru/shared.js';
import en from './i18n/en/shared.js';

const t = dict({ ru, en });

/* render() живёт в router.js, но экраны перерисовывают себя через него после
   действий. Прямой импорт дал бы цикл router -> screens -> router, поэтому
   router регистрирует свою функцию здесь при загрузке (setRender). */
let renderImpl = null;
function setRender(fn) { renderImpl = fn; }
function render() { return renderImpl(); }

const view = $('#view');

// период -> ключ подписи; текст берётся при каждом вызове plabel()
const PERIOD_LABEL = {
  '1h': 'period1h', '6h': 'period6h', '24h': 'period24h', '48h': 'period48h',
  '7d': 'period7d', '14d': 'period14d', '30d': 'period30d', '60d': 'period60d',
  '90d': 'period90d', '180d': 'period180d', '365d': 'period365d', all: 'periodAll',
};
const plabel = (p) => (PERIOD_LABEL[p] ? t(PERIOD_LABEL[p]) : t('periodOther', { p }));

// ключи -- строки с сервера, не меняются
const SATURATION = {
  'dominated by big channels -- hard to break in': 'satDominated',
  'plenty of small channels performing -- room to enter': 'satRoom',
  'mixed field': 'satMixed',
};

function base() { return { period: state.period, niche: state.niche }; }

/* Форма сбора переиспользуется на нескольких экранах. */
function collectForm() {
  return `<div class="card">
    ${sectionHead(t('collectTitle'), t('collectSub'))}
    <div class="form-row">
      <label class="field"><span class="field-label">${t('collectChannelLabel')}</span>
        <input type="text" id="collectChannel" placeholder="@Inkexplainer96"></label>
      <label class="field"><span class="field-label">${t('collectNicheLabel')}</span>
        <input type="text" id="collectNiche" placeholder="explainer"></label>
      <button class="btn" id="collectBtn" type="button">${t('collectChannelBtn')}</button>
      <button class="btn btn-ghost" id="collectTrackBtn" type="button">${t('collectTrackBtn')}</button>
    </div>
    <div class="form-row" style="margin-top:12px">
      <label class="field"><span class="field-label">${t('collectQueryLabel')}</span>
        <input type="text" id="collectQuery" placeholder="ai automation"></label>
      <label class="field"><span class="field-label">${t('collectPeriodLabel')}</span>
        <select id="collectPeriod"><option value="">${t('periodAny')}</option><option value="24h">${t('opt24h')}</option>
        <option value="7d">${t('opt7d')}</option><option value="30d">${t('opt30d')}</option></select></label>
      <button class="btn btn-ghost" id="searchBtn" type="button">${t('collectSearchBtn')}</button>
    </div>
  </div>`;
}

function wireCollect(refresh) {
  for (const id of ['collectBtn', 'collectTrackBtn', 'searchBtn']) {
    const b = $(`#${id}`);
    if (b) b.dataset.label = b.textContent;
  }
  const busy = (btn, on) => { btn.disabled = on; btn.textContent = on ? t('collecting') : btn.dataset.label; };
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
    if (!ch) return toast(t('needChannel'), 'err');
    run($('#collectBtn'), '/api/collect/channel',
      { channel: ch, niche: $('#collectNiche').value.trim() || null },
      (r) => t('collectedChannel', { title: r.channelTitle, videos: r.videos_stored, units: r.quota.units_from_shared_pool }));
  });
  $('#collectTrackBtn')?.addEventListener('click', () => {
    const ch = $('#collectChannel').value.trim();
    if (!ch) return toast(t('needChannel'), 'err');
    run($('#collectTrackBtn'), '/api/collect/channel',
      { channel: ch, niche: $('#collectNiche').value.trim() || null, track: true },
      (r) => t('collectedTracked', { title: r.channelTitle, videos: r.videos_stored }));
  });
  $('#searchBtn')?.addEventListener('click', () => {
    const query = $('#collectQuery').value.trim();
    if (!query) return toast(t('needQuery'), 'err');
    run($('#searchBtn'), '/api/collect/niche',
      { query, period: $('#collectPeriod').value || null },
      (r) => t('collectedSearch', { query: r.query, videos: r.videos_stored ?? 0, calls: r.quota.search_calls }));
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
        ${tile(t('tileVideos'), num(d.video_count))}
        ${tile(t('tileChannels'), num(d.channel_count))}
        ${tile(t('tileMedianMult'), mult(d.median_outlier_score))}
        ${tile(t('tileMedianViews'), compact(d.median_views_per_video))}
        ${tile('Viral skew', d.viral_skew ?? '—', t('viralSkewNote'))}
        ${tile('Shorts', `${d.shorts_share_percent}%`)}
        ${tile(t('tileSmallBreakouts'), num(d.small_channel_breakouts), t('smallBreakoutsNote'))}
      </div>
    </div>
    ${saturationBlock(d.saturation_v2)}
    <div class="grid-2">
      <div class="card">
        ${sectionHead(t('channelsBySize'), SATURATION[d.saturation_hint] ? t(SATURATION[d.saturation_hint]) : d.saturation_hint)}
        ${barList(Object.entries(d.channel_size_distribution).map(([k, v]) => ({ name: k, value: v, display: num(v) })))}
      </div>
      <div class="card">
        ${sectionHead(t('topByMult'))}
        ${table([
          { label: t('colVideo'), wrap: true, render: (r) => esc(r.title) },
          { label: t('colViews'), num: true, render: (r) => compact(r.views) },
          { label: t('colMult'), num: true, render: (r) => mult(r.outlierScore) },
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
      `${t('footCounts', { channels: num(h.db.channels), videos: num(h.db.videos) })}<br>` +
      (h.historyAvailable ? t('historyOn') : t('historyOff')) +
      (sq ? `<br>${t('footSearch', { left: sq.callsLeft, limit: sq.dailyLimit })}` : '') +
      (uq ? `<br>${t('footUnits', { left: num(uq.unitsLeft), limit: num(uq.dailyLimit) })}` : '');
    $('#brandTag').textContent = h.hasApiKey ? 'local' : t('noApiKey');
  } catch { $('#footStat').textContent = t('serviceDown'); }
}

export { view, plabel, base, collectForm, wireCollect, loadFootStat, nicheOverviewBlock, render, setRender };
