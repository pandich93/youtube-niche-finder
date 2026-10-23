/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { api, num, esc, sectionHead, notice, empty, trajectoryChart } from '../ui.js';
import { view } from '../shared.js';
import { dict } from '../i18n.js';
import ru from '../i18n/ru/compare.js';
import en from '../i18n/en/compare.js';

const t = dict({ ru, en });

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
    view.innerHTML = empty(t('emptyBasket'));
    return;
  }
  saveBasket(ids);
  const d = await api(`/api/videos/trajectory?ids=${ids.map(encodeURIComponent).join(',')}`);
  const without = (id) => `#/compare/${ids.filter((x) => x !== id).map(encodeURIComponent).join(',')}`;
  view.innerHTML = `
    ${d.missing.length ? notice(t('missing', { ids: d.missing.map(esc).join(', ') }), 'warn') : ''}
    <div class="card">
      ${sectionHead(t('title'), t('sub'))}
      ${trajectoryChart(d.videos)}
      <div class="section-sub" style="margin-top:10px">${t('chartNote')}</div>
    </div>
    <div class="card">
      ${sectionHead(t('basketTitle'), t('basketSub', { n: d.videos.length }))}
      <div class="rows">${d.videos.map((v) => `<div class="row">
        <div class="row-main">
          <div class="row-title"><a href="https://www.youtube.com/watch?v=${esc(v.videoId)}" target="_blank" rel="noopener">${esc(v.title || v.videoId)}</a></div>
          <div class="row-sub">${v.channelId ? `<a href="#/channel/${esc(v.channelId)}">${esc(v.channelTitle || v.channelId)}</a> · ` : ''}${t('views', { views: num(v.views) })}
            · ${t('snapshots', { snaps: num(v.points.length) })}${v.baselineMedianViews ? ` · ${t('channelMedian', { median: num(v.baselineMedianViews) })}` : ''}</div>
        </div>
        <a class="btn btn-ghost btn-sm" href="${without(v.videoId)}">${t('remove')}</a>
      </div>`).join('')}</div>
    </div>`;
}

export { viewCompare };
