/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, num, toast, tile, sectionHead, notice, state } from '../ui.js';
import { view, plabel, collectForm, wireCollect, render } from '../shared.js';

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

export { viewData };
