/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { api, q, num, compact, esc, delta, tile, sectionHead, notice, empty, barList, channelRow, videoCard, pl, state } from '../ui.js';
import { view, plabel, base } from '../shared.js';

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

export { viewOverview };
