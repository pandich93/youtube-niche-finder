/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { api, q, num, compact, esc, delta, tile, sectionHead, notice, empty, barList, channelRow, videoCard, state } from '../ui.js';
import { view, plabel, base } from '../shared.js';
import { dict, fmtDate } from '../i18n.js';
import ru from '../i18n/ru/overview.js';
import en from '../i18n/en/overview.js';

const t = dict({ ru, en });

/* --------------------------------------------------------------- Обзор */

/* «За сутки» -- то же, что уходит в Telegram утренним дайджестом (plan 07).
   Функция, а не константа: заголовки переводятся при каждом рендере. */
const digestSections = () => [
  ['outliers', t('digestOutliers'), (e) => `×${e.payload.outlierScore} · ${e.payload.title || e.payload.videoId}`],
  ['acceleration', t('digestAcceleration'), (e) => `×${e.payload.acceleration} · ${e.payload.title || e.payload.videoId}`],
  ['risingChannels', t('digestRising'), (c) => `${c.channelTitle || c.channelId} · ×${c.multiplier}`],
  ['repackaging', t('digestRepackaging'), (s) => (s.field === 'title'
    ? t('digestTitleChanged', { title: s.new }) : t('digestThumbChanged', { title: s.title || s.videoId }))],
  ['gone', t('digestGone'), (e) => t(e.kind === 'channel_gone' ? 'goneChannel' : 'goneVideo',
    { title: e.payload.title || '' })],
];

function digestBlock(g) {
  if (!g) return '';
  const secs = digestSections().filter(([key]) => g[key] && g[key].total);
  return `<section class="card">
    ${sectionHead(t('digestTitle'), t('digestSub'))}
    ${g.empty ? empty(t('digestEmpty')) : `<div class="grid-2">${secs.map(([key, title, line]) => `
      <div>
        <div class="section-sub"><b>${esc(title)}</b> · ${num(g[key].total)}</div>
        <ul class="digest-list">${g[key].items.map((x) => `<li>${esc(line(x))}</li>`).join('')}</ul>
      </div>`).join('')}</div>`}
  </section>`;
}

async function viewOverview() {
  const [d, g] = await Promise.all([
    api(`/api/overview${q(base())}`),
    api('/api/digest').catch(() => null),
  ]);
  const cov = d.coverage;
  const thin = cov.videosPublishedInPeriod < 5;

  view.innerHTML = `
    ${thin ? notice(t('thinWindow', {
      period: plabel(state.period), inWindow: cov.videosPublishedInPeriod, total: num(cov.videosTotal),
    })) : ''}

    <div class="tiles">
      ${tile(t('tileChannels'), num(d.stats.channels))}
      ${tile(t('tileVideos'), num(d.stats.videos))}
      ${tile(t('tileSnapshots'), num(d.stats.video_stat_snapshots),
        d.stats.history_since ? t('historySince', { date: fmtDate(d.stats.history_since) })
                              : t('noHistory'))}
      ${tile(t('tileTracked'), num(d.stats.tracked_channels))}
      ${tile(t('tileInWindow'), num(cov.videosPublishedInPeriod), plabel(state.period))}
      ${tile(t('tileNoEmbedding'), num(d.stats.videos_without_embedding), t('workerWillFill'))}
    </div>

    ${digestBlock(g)}

    <div class="grid-2">
      <section class="card">
        ${sectionHead(t('outlierChannelsTitle'), plabel(state.period),
          `<a class="btn btn-ghost btn-sm" href="#/channels">${t('all')}</a>`)}
        ${d.outlierChannels.channels.length
          ? `<div class="rows">${d.outlierChannels.channels.map(channelRow).join('')}</div>`
          : empty(d.outlierChannels.hint || t('noOutlierChannels'))}
      </section>

      <section class="card">
        ${sectionHead(t('competitionTitle'), t('competitionSub', { period: plabel(d.widePeriod) }))}
        ${d.competition.channels.length
          ? `<div class="rows">${d.competition.channels.slice(0, 6).map((c) => `
              <div class="row">
                <div class="avatar">${esc((c.channelTitle || '?').slice(0, 1).toUpperCase())}</div>
                <div class="row-main">
                  <div class="row-title"><a href="#/channel/${esc(c.channelId)}">${esc(c.channelTitle)}</a></div>
                  <div class="row-sub">${t('subsShort', { subs: compact(c.subscribers) })} · ${
                    t('uploads', { n: c.uploadsInWindow })} · ${
                    c.channelAgeDays ? t('ageDays', { n: c.channelAgeDays }) : t('ageUnknown')}</div>
                </div>
                <div class="row-metrics">
                  <div class="metric" data-tip="${esc(t('competitionTip'))}">
                    <div class="metric-value">${c.competitionScore}</div>
                    <div class="metric-label">score</div>
                  </div>
                </div>
              </div>`).join('')}</div>`
          : empty(t('noCompetition'))}
      </section>
    </div>

    <section class="card">
      ${sectionHead(t('categoriesTitle'), t('categoriesSub', { period: plabel(state.period) }),
        `<a class="btn btn-ghost btn-sm" href="#/categories">${t('more')}</a>`)}
      ${d.categories.categories.length
        ? `<div class="scroller">${d.categories.categories.map((c) => `
            <div class="cat-card" data-tip="${esc(t('categoryTip', {
              category: c.category, videos: num(c.videos), views: compact(c.totalViews) }))}">
              <div class="cat-name">${esc(c.category)}</div>
              <div class="cat-count">${t('channels', { n: c.channels })} · ${t('videosCount', { n: c.videos })}</div>
              <div class="cat-delta">${c.shareChange != null ? delta(c.shareChange, t('shareDelta'))
                                                             : `<span class="chip">${t('noComparison')}</span>`}</div>
            </div>`).join('')}</div>`
        : empty(d.categories.hint || t('noData'))}
    </section>

    <section class="card">
      ${sectionHead(t('keywordsTitle'), t('keywordsSub', { period: plabel(state.period) }),
        `<a class="btn btn-ghost btn-sm" href="#/keywords">${t('all')}</a>`)}
      ${d.keywords.keywords.length
        ? barList(d.keywords.keywords.slice(0, 10).map((k) => ({
            name: k.keyword, value: k.trendScore, display: k.trendScore,
            tip: t('keywordTip', { keyword: k.keyword, videos: k.videos,
              momentum: k.momentum ?? '—', lift: k.outlierLift ?? '—' }),
          })))
        : empty(d.keywords.hint || t('noKeywords'))}
    </section>

    <section class="card">
      ${sectionHead(t('viralTitle'), t('viralSub', { period: plabel(state.period) }),
        `<a class="btn btn-ghost btn-sm" href="#/viral">${t('all')}</a>`)}
      ${d.viral.results.length
        ? `<div class="cards">${d.viral.results.map(videoCard).join('')}</div>`
        : empty(d.viral.hint || t('noViral'))}
    </section>`;
}

export { viewOverview };
