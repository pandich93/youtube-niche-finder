/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, num, compact, ago, esc, sectionHead, empty, state } from '../ui.js';
import { view, plabel, base, render } from '../shared.js';

/* ------------------------------------------------------------ Перепаковки */

const FIELD_LABEL = { title: 'заголовок', thumbnail_image: 'обложка' };

function effectChip(e) {
  if (!e || !e.enoughData) {
    return '<span class="chip" data-tip="Нужно по два снимка статистики до и после смены в пределах 48 часов">эффект: мало данных</span>';
  }
  const r = e.ratio;
  const cls = r == null ? '' : r > 1.2 ? 'chip-good' : r < 0.8 ? 'chip-bad' : '';
  const arrow = r == null ? '' : r > 1.2 ? '▲ ' : r < 0.8 ? '▼ ' : '= ';
  const tip = 'Просмотров в час за 48 ч до и после смены. Это наблюдение, не причина: просмотры падают и сами с возрастом видео';
  return `<span class="chip ${cls}" data-tip="${tip}">${arrow}${num(e.vphBefore)} → ${num(e.vphAfter)} VPH${r != null ? ` (×${r})` : ''}</span>`;
}

function img(src, label) {
  return `<figure class="pk-shot">
    ${src ? `<img src="${esc(src)}" alt="${esc(label)}" loading="lazy">`
          : '<div class="thumb-fallback">нет в архиве</div>'}
    <figcaption>${esc(label)}</figcaption></figure>`;
}

function changeCard(c) {
  const body = c.field === 'thumbnail_image'
    ? `<div class="pk-pair">${img(c.beforeImage, 'было')}${img(c.afterImage, 'стало')}</div>`
    : `<div class="pk-titles"><div class="pk-old">${esc(c.old)}</div>
         <div class="pk-arrow">↓</div><div class="pk-new">${esc(c.new)}</div></div>`;
  return `<article class="card pk-card">
    <div class="vcard-chips">
      <span class="chip chip-accent">${esc(FIELD_LABEL[c.field] || c.field)}</span>
      ${effectChip(c.effect)}
    </div>
    ${body}
    <div class="vcard-title"><a href="https://www.youtube.com/watch?v=${esc(c.videoId)}" target="_blank" rel="noopener">${esc(c.title || c.videoId)}</a></div>
    <div class="vcard-meta"><a href="#/channel/${esc(c.channelId)}">${esc(c.channelTitle || c.channelId)}</a>
      · ${compact(c.views)} просмотров · сменили ${ago(c.changedAt)}</div>
  </article>`;
}

async function viewPackaging() {
  const p = { field: '', channel_id: '' };
  try { Object.assign(p, JSON.parse(localStorage.getItem('nf.packaging') || '{}')); } catch (e) { /* пусто */ }
  const [d, tracked] = await Promise.all([
    api(`/api/packaging${q({ period: base().period, field: p.field, channel_id: p.channel_id, limit: 60 })}`),
    api('/api/channels/tracked').catch(() => ({ channels: [] })),
  ]);
  const opt = (v, l, cur) => `<option value="${esc(v)}"${cur === v ? ' selected' : ''}>${esc(l)}</option>`;

  view.innerHTML = `
    <div class="card">
      ${sectionHead('Перепаковки', `смены заголовков и обложек после публикации — «было / стало» и что стало с просмотрами · ${plabel(state.period)}`)}
      <div class="form-row">
        <label class="field"><span class="field-label">Что меняли</span>
          <select id="pkField">${opt('', 'всё', p.field)}${opt('title', 'заголовки', p.field)}${opt('thumbnail_image', 'обложки', p.field)}</select></label>
        <label class="field"><span class="field-label">Канал</span>
          <select id="pkChannel">${opt('', 'все каналы', p.channel_id)}${(tracked.channels || [])
            .map((c) => opt(c.channel_id, c.title || c.channel_id, p.channel_id)).join('')}</select></label>
        <button class="btn" id="pkApply" type="button">Применить</button>
      </div>
      <div class="section-sub" style="margin-top:10px">Найдено: <b>${num(d.count)}</b> · квота не потрачена ·
        обложки снимаются только у отслеживаемых каналов, история начинается с момента первого снимка</div>
    </div>
    ${d.changes.length ? `<div class="cards pk-cards">${d.changes.map(changeCard).join('')}</div>`
                       : empty('за этот период смен не найдено — добавьте каналы в трекер, воркер начнёт снимать обложки')}`;

  $('#pkApply').addEventListener('click', () => {
    localStorage.setItem('nf.packaging', JSON.stringify({
      field: $('#pkField').value, channel_id: $('#pkChannel').value,
    }));
    render();
  });
}

export { viewPackaging };
