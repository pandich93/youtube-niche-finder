/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { api, num, esc, sectionHead, notice, empty, trajectoryChart } from '../ui.js';
import { view } from '../shared.js';

/* ------------------------------------------- Сравнение траекторий (план 20) */

/* До 5 видео на одном графике: просмотры по возрасту из снимков воркера и
   ожидаемая кривая каждого канала. Корзина живёт в localStorage (nf.compare),
   адрес #/compare/<id,id> — то, что видно сейчас, им можно поделиться. */
function saveBasket(ids) {
  try { localStorage.setItem('nf.compare', JSON.stringify(ids)); } catch { /* приватный режим */ }
}

async function viewCompare(arg) {
  const ids = (arg || '').split(',').map((x) => decodeURIComponent(x).trim()).filter(Boolean).slice(0, 5);
  if (!ids.length) {
    view.innerHTML = empty('добавьте видео ссылкой «сравнить» на любой карточке видео');
    return;
  }
  saveBasket(ids);
  const d = await api(`/api/videos/trajectory?ids=${ids.map(encodeURIComponent).join(',')}`);
  const without = (id) => `#/compare/${ids.filter((x) => x !== id).map(encodeURIComponent).join(',')}`;
  view.innerHTML = `
    ${d.missing.length ? notice(`Нет в базе: ${d.missing.map(esc).join(', ')}`, 'warn') : ''}
    <div class="card">
      ${sectionHead('Сравнение траекторий', 'просмотры по возрасту видео — в одном возрасте, а не в одну дату')}
      ${trajectoryChart(d.videos)}
      <div class="section-sub" style="margin-top:10px">Точки — снимки воркера: каждые 3 часа первую неделю, потом раз
        в сутки до 30 дней. Пунктир — сколько набрало бы обычное видео этого канала к тому же возрасту (медиана канала ×
        кривая взросления), это оценка niche-finder. Квота не тратится.</div>
    </div>
    <div class="card">
      ${sectionHead('Видео в сравнении', `${d.videos.length} из 5 — ещё добавляются ссылкой «сравнить» на карточках`)}
      <div class="rows">${d.videos.map((v) => `<div class="row">
        <div class="row-main">
          <div class="row-title"><a href="https://www.youtube.com/watch?v=${esc(v.videoId)}" target="_blank" rel="noopener">${esc(v.title || v.videoId)}</a></div>
          <div class="row-sub">${v.channelId ? `<a href="#/channel/${esc(v.channelId)}">${esc(v.channelTitle || v.channelId)}</a> · ` : ''}${num(v.views)} просмотров
            · снимков ${num(v.points.length)}${v.baselineMedianViews ? ` · медиана канала ${num(v.baselineMedianViews)}` : ''}</div>
        </div>
        <a class="btn btn-ghost btn-sm" href="${without(v.videoId)}">Убрать</a>
      </div>`).join('')}</div>
    </div>`;
}

export { viewCompare };
