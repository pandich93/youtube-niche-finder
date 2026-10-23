/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, num, compact, mult, ago, esc, toast, tile, sectionHead, notice, empty, table, commentList, lineChart, VIEW_COUNT_CHANGE, aiLabelsBadge, sponsorBlock, state, rpmRange, templateRiskBlock, yppLine, milestonesLine, qmark, policyBlock, mountProfit, mountCollabs } from '../ui.js';
import { view, nicheOverviewBlock } from '../shared.js';
import { dict, fmtDate } from '../i18n.js';
import ru from '../i18n/ru/channel.js';
import en from '../i18n/en/channel.js';

const t = dict({ ru, en });

/* ----------------------------------------------------------------- Канал */

async function viewChannel(id) {
  const [a, vel, hist, sim, nicheOv, risk, sponsors] = await Promise.all([
    api(`/api/channels/${encodeURIComponent(id)}${q({ period: state.period })}`),
    api(`/api/channels/${encodeURIComponent(id)}/velocity${q({ period: state.period })}`),
    api(`/api/channels/${encodeURIComponent(id)}/history`),
    api(`/api/channels/${encodeURIComponent(id)}/similar`),
    api(`/api/channels/${encodeURIComponent(id)}/niche-overview`),
    api(`/api/channels/${encodeURIComponent(id)}/template-risk`).catch(() => null),
    // Не критично для экрана: без спонсоров канал всё равно открывается.
    api(`/api/channels/${encodeURIComponent(id)}/sponsors${q({ period: state.period })}`)
      .catch(() => null),
  ]);
  view.innerHTML = `
    <div class="card">
      ${sectionHead(a.profile.title || id,
        t('profileSub', {
          handle: a.profile.handle || '', country: a.profile.country || '—',
          date: a.profile.createdAt ? fmtDate(a.profile.createdAt) : '—',
        }),
        `${aiLabelsBadge(a.profile.aiLabels)}
         <button class="btn btn-ghost btn-sm" id="trackThis">${t('trackBtn')}</button>
         <a class="btn btn-ghost btn-sm" href="https://www.youtube.com/channel/${esc(id)}" target="_blank" rel="noopener">YouTube</a>`)}
      <div class="tiles">
        ${tile(t('tileSubs'), compact(a.profile.subscribers))}
        ${tile(t('tileViews'), compact(a.profile.totalViews))}
        ${tile(t('tileVideos'), num(a.profile.videoCount))}
        ${tile(t('tileUploadsPerWeek'), a.cadence.uploadsPerWeekLifetime ?? '—')}
        ${tile(t('tileMedianPerVideo'), compact(a.performance.medianViewsPerVideo))}
        ${tile('Viral skew', a.performance.viralSkew ?? '—', t('viralSkewNote'))}
        ${tile(t('tileGrade'), `${esc(a.grade ?? '—')} ${qmark('momentumGrade')}`, a.momentum ? `momentum ${a.momentum}` : t('needHistory'))}
      </div>
      ${a.gone ? notice(`<div>${t('goneNotice', { date: fmtDate(a.gone.missingSince) })}</div>`, 'error') : ''}
      ${!a.growthAvailable ? notice(t('noGrowth', { snapshots: a.snapshots })) : ''}
    </div>

    <div class="card">
      ${sectionHead(t('viewsOverTimeTitle'), t('viewsOverTimeSub'))}
      ${hist.count > 1
        ? lineChart(hist.points.map((p) => ({ t: p.t, v: p.views })), { valueLabel: t('viewsValueLabel'), marks: [VIEW_COUNT_CHANGE] })
        : empty(hist.hint)}
    </div>

    <div class="grid-2">
      <div class="card">
        ${sectionHead(t('growthTitle'))}
        ${table([
          { label: t('colWindow'), render: (r) => r.w },
          { label: t('colSubscribers'), num: true, render: (r) => (r.subs ? `${r.subs.delta > 0 ? '+' : ''}${num(r.subs.delta)}` : '—') },
          { label: t('colPerDay'), num: true, render: (r) => (r.subs ? num(r.subs.per_day) : '—') },
          { label: t('colViews'), num: true, render: (r) => (r.views ? `${r.views.delta > 0 ? '+' : ''}${compact(r.views.delta)}` : '—') },
          { label: t('colPerDay'), num: true, render: (r) => (r.views ? compact(r.views.per_day) : '—') },
        ], ['24h', '7d', '30d', '90d'].map((w) => ({ w, subs: a.growth[w].subscribers, views: a.growth[w].views })))}
        <div class="section-sub" style="margin-top:10px">${t('subsRoundingNote')}</div>
      </div>

      <div class="card">
        ${sectionHead(t('revenueTitle'), t('revenueSub'))}
        <div class="tiles">
          ${tile(t('socialBladeRange'),
            `$${num(a.revenue.socialBladeRange.low_usd)}–${num(a.revenue.socialBladeRange.high_usd)}`, t('perMonth'))}
          ${a.revenue.nicheModel.monthly_usd_low != null
            ? tile(t('nicheRpmModel'),
                `$${num(a.revenue.nicheModel.monthly_usd_low)}–${num(a.revenue.nicheModel.monthly_usd_high)}`,
                t('perMonthRpm', { range: rpmRange(a.revenue.nicheModel.rpm_range) }))
            : tile(t('nicheRpmModel'), `$${num(a.revenue.nicheModel.monthly_usd)}`,
                `RPM $${a.revenue.nicheModel.rpm_effective}`)}
        </div>
        <div class="section-sub" style="margin-top:10px">${t('revenueNote')}</div>
        ${yppLine(a.yppEligibility)}
        ${milestonesLine(a.milestones)}
      </div>
    </div>

    <div class="card">
      ${sectionHead(t('topOutliersTitle'), t('topOutliersSub'))}
      ${table([
        { label: t('colVideo'), wrap: true, render: (r) => `<a href="https://www.youtube.com/watch?v=${esc(r.videoId)}" target="_blank" rel="noopener">${esc(r.title)}</a>` },
        { label: t('colViews'), num: true, render: (r) => compact(r.views) },
        { label: t('colMultiplier'), num: true, render: (r) => mult(r.outlierScore) },
        { label: t('colAgeAdjusted'), num: true, render: (r) => mult(r.outlierScoreAgeAdjusted) },
        { label: t('colBand'), render: (r) => esc(r.band) },
        { label: t('colPublished'), render: (r) => ago(r.publishedAt) },
        { label: '', render: (r) => `<button class="btn btn-ghost btn-sm js-comments" data-video-id="${esc(r.videoId)}" data-video-title="${esc(r.title)}">${t('commentsBtn')}</button>
          <button class="btn btn-ghost btn-sm js-why" data-video-id="${esc(r.videoId)}" data-video-title="${esc(r.title)}">${t('whyBtn')}</button>` },
      ], a.topOutliers)}
    </div>

    ${templateRiskBlock(risk)}
    <div id="chPolicy"></div>
    <div id="chProfit"></div>
    <div id="chCollabs"></div>

    ${sponsorBlock(sponsors, t('channelSponsors'), t('channelSponsorsSub'))}

    <div class="card" id="commentsPanel" hidden></div>
    <div class="card" id="whyPanel" hidden></div>

    <div class="card">
      ${sectionHead(t('similarTitle'), t('similarSub', {
        detail: sim.videosEmbedded ? t('similarCentroid', { count: sim.videosEmbedded }) : t('similarNeedEmbeddings'),
      }))}
      ${sim.similar.length
        ? table([
            { label: t('colChannel'), wrap: true, render: (r) => `<a href="#/channel/${esc(r.channelId)}">${esc(r.title || r.channelId)}</a>` },
            { label: t('colSubs'), num: true, render: (r) => compact(r.subscriberCount) },
            { label: t('colSimilarity'), num: true, render: (r) => r.similarity.toFixed(2) },
            { label: t('colVideosEmbedded'), num: true, render: (r) => num(r.videosEmbedded) },
          ], sim.similar)
        : empty(sim.hint || t('similarEmpty'))}
    </div>

    ${nicheOv.found
      ? nicheOverviewBlock(nicheOv, sectionHead(t('nicheOverviewTitle'),
          t('nicheOverviewSub', { count: nicheOv.peer_channel_count })))
      : `<div class="card">
          ${sectionHead(t('nicheOverviewTitle'))}
          ${empty(nicheOv.hint || t('nicheOverviewEmpty'))}
        </div>`}

    <div class="card">
      ${sectionHead(t('velocityTitle'), t('velocitySub'))}
      ${table([
        { label: t('colVideo'), wrap: true, render: (r) => esc(r.title) },
        { label: t('colViews'), num: true, render: (r) => compact(r.views) },
        { label: t('colVphLifetime'), num: true, render: (r) => num(r.vphLifetime) },
        { label: t('colVph24h'), num: true, render: (r) => (r.vph24h != null ? num(r.vph24h) : '—') },
        { label: t('colGained24h'), num: true, render: (r) => (r.viewsGained24h != null ? compact(r.viewsGained24h) : '—') },
        { label: t('colTrend'), render: (r) => (r.trend ? esc(r.trend) : '—') },
      ], vel.videos)}
    </div>`;

  $('#trackThis')?.addEventListener('click', async () => {
    try {
      await api('/api/channels/track', { method: 'POST', body: { channel_id: id } });
      toast(t('addedToTracker'), 'ok');
    } catch (e) { toast(e.message, 'err'); }
  });

  view.querySelectorAll('.js-comments').forEach((b) => b.addEventListener('click', async () => {
    const panel = $('#commentsPanel');
    const title = t('commentsTitle', { title: esc(b.dataset.videoTitle) });
    panel.hidden = false;
    panel.innerHTML = sectionHead(title, t('commentsLive')) + empty(t('loading'));
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    try {
      const res = await api(`/api/videos/${encodeURIComponent(b.dataset.videoId)}/comments`,
        { method: 'POST', body: {} });
      panel.innerHTML = sectionHead(title, t('commentsLiveDone')) + commentList(res.comments);
    } catch (e) {
      panel.innerHTML = sectionHead(title, '') + empty(e.message);
    }
  }));

  view.querySelectorAll('.js-why').forEach((b) => b.addEventListener('click', async () => {
    const panel = $('#whyPanel');
    const title = t('whyTitle', { title: esc(b.dataset.videoTitle) });
    panel.hidden = false;
    panel.innerHTML = sectionHead(title, '') + empty(t('loading'));
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    try {
      const res = await api(`/api/video/${encodeURIComponent(b.dataset.videoId)}/why`);
      if (!res) {
        panel.innerHTML = sectionHead(title, '') + notice(t('llmOff'));
        return;
      }
      if (res.hint) {
        panel.innerHTML = sectionHead(title, '') + notice(esc(res.hint));
        return;
      }
      panel.innerHTML = sectionHead(title,
        `${t('confidence', { pct: Math.round((res.confidence || 0) * 100) })}${res.cached ? t('fromCacheSuffix') : ''}`) + `
        <div style="border:1px solid var(--border);border-radius:10px;padding:12px">
          ${res.hooks?.length ? `<div style="margin-bottom:8px"><b>${t('hooks')}</b>
            <ul style="margin:4px 0 0 18px">${res.hooks.map((h) => `<li>${esc(h)}</li>`).join('')}</ul></div>` : ''}
          ${res.title_pattern ? `<div class="row-sub" style="margin-bottom:6px"><b>${t('titlePattern')}</b> ${esc(res.title_pattern)}</div>` : ''}
          ${res.timing_factor ? `<div class="row-sub" style="margin-bottom:6px"><b>${t('timingFactor')}</b> ${esc(res.timing_factor)}</div>` : ''}
          ${res.replicable_formula ? `<div><b>${t('replicableFormula')}</b> ${esc(res.replicable_formula)}</div>` : ''}
        </div>`;
    } catch (e) {
      panel.innerHTML = sectionHead(title, '') + empty(e.message);
    }
  }));
  // план 30: чистая прибыль по выбранному профилю расходов
  mountProfit($('#chProfit'), { channel_id: id });
  // план 31: с кем делать совместные видео
  mountCollabs($('#chCollabs'), id);
  // план 22: сигналы по правилам монетизации -- отдельным запросом, экран не ждёт
  api(`/api/channels/${encodeURIComponent(id)}/policy-signals`)
    .then((r) => { const box = $('#chPolicy'); if (box) box.innerHTML = policyBlock(r); })
    .catch(() => {});
}

export { viewChannel };
