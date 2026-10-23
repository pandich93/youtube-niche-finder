/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, num, compact, ago, esc, sectionHead, empty, state } from '../ui.js';
import { view, plabel, base, render } from '../shared.js';
import { dict } from '../i18n.js';
import ru from '../i18n/ru/packaging.js';
import en from '../i18n/en/packaging.js';

const t = dict({ ru, en });

/* ------------------------------------------------------------ Перепаковки */

/* Функция, а не константа: подписи переводятся при каждом рендере. */
const fieldLabel = (f) => ({ title: t('fieldTitle'), thumbnail_image: t('fieldThumbnail') })[f];

function effectChip(e) {
  if (!e || !e.enoughData) {
    return `<span class="chip" data-tip="${t('effectNoDataTip')}">${t('effectNoData')}</span>`;
  }
  const r = e.ratio;
  const cls = r == null ? '' : r > 1.2 ? 'chip-good' : r < 0.8 ? 'chip-bad' : '';
  const arrow = r == null ? '' : r > 1.2 ? '▲ ' : r < 0.8 ? '▼ ' : '= ';
  const tip = t('effectTip');
  return `<span class="chip ${cls}" data-tip="${tip}">${arrow}${num(e.vphBefore)} → ${num(e.vphAfter)} VPH${r != null ? ` (×${r})` : ''}</span>`;
}

function img(src, label) {
  return `<figure class="pk-shot">
    ${src ? `<img src="${esc(src)}" alt="${esc(label)}" loading="lazy">`
          : `<div class="thumb-fallback">${t('notArchived')}</div>`}
    <figcaption>${esc(label)}</figcaption></figure>`;
}

function changeCard(c) {
  const body = c.field === 'thumbnail_image'
    ? `<div class="pk-pair">${img(c.beforeImage, t('before'))}${img(c.afterImage, t('after'))}</div>`
    : `<div class="pk-titles"><div class="pk-old">${esc(c.old)}</div>
         <div class="pk-arrow">↓</div><div class="pk-new">${esc(c.new)}</div></div>`;
  return `<article class="card pk-card">
    <div class="vcard-chips">
      <span class="chip chip-accent">${esc(fieldLabel(c.field) || c.field)}</span>
      ${effectChip(c.effect)}
    </div>
    ${body}
    <div class="vcard-title"><a href="https://www.youtube.com/watch?v=${esc(c.videoId)}" target="_blank" rel="noopener">${esc(c.title || c.videoId)}</a></div>
    <div class="vcard-meta"><a href="#/channel/${esc(c.channelId)}">${esc(c.channelTitle || c.channelId)}</a>
      · ${t('cardMeta', { views: compact(c.views), ago: ago(c.changedAt) })}</div>
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
      ${sectionHead(t('title'), t('sub', { period: plabel(state.period) }))}
      <div class="form-row">
        <label class="field"><span class="field-label">${t('whatChanged')}</span>
          <select id="pkField">${opt('', t('optAll'), p.field)}${opt('title', t('optTitles'), p.field)}${opt('thumbnail_image', t('optThumbnails'), p.field)}</select></label>
        <label class="field"><span class="field-label">${t('channel')}</span>
          <select id="pkChannel">${opt('', t('allChannels'), p.channel_id)}${(tracked.channels || [])
            .map((c) => opt(c.channel_id, c.title || c.channel_id, p.channel_id)).join('')}</select></label>
        <button class="btn" id="pkApply" type="button">${t('apply')}</button>
      </div>
      <div class="section-sub" style="margin-top:10px">${t('found', { count: num(d.count) })}</div>
    </div>
    ${d.changes.length ? `<div class="cards pk-cards">${d.changes.map(changeCard).join('')}</div>`
                       : empty(t('empty'))}`;

  $('#pkApply').addEventListener('click', () => {
    localStorage.setItem('nf.packaging', JSON.stringify({
      field: $('#pkField').value, channel_id: $('#pkChannel').value,
    }));
    render();
  });
}

export { viewPackaging };
