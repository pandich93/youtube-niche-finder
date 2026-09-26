/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, num, compact, mult, ago, esc, toast, tile, sectionHead, notice, empty, table, commentList, lineChart, aiLabelsBadge, state } from '../ui.js';
import { view, nicheOverviewBlock } from '../shared.js';

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

export { viewChannel };
