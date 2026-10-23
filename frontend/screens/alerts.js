/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, num, compact, ago, esc, toast, sectionHead, empty } from '../ui.js';
import { view, render } from '../shared.js';
import { dict, fmtDate, fmtDateTime } from '../i18n.js';
import ru from '../i18n/ru/alerts.js';
import en from '../i18n/en/alerts.js';

const t = dict({ ru, en });

/* ------------------------------------------------------------- Алерты */

/* Те же события, что уходят в Telegram/webhook; видны только по каналам из трекера.
   Функция, а не константа: подписи переводятся при каждом рендере. */
const kinds = () => ({
  outlier: ['Outlier', (p) => t('lineOutlier', { score: p.outlierScore, views: compact(p.views) })],
  acceleration: [t('kindAcceleration'), (p) => t('lineAcceleration', { acceleration: p.acceleration, vph: num(p.vph24h) })],
  title_change: [t('kindTitleChange'), (p) => t('lineTitleChange', { old: p.oldTitle || '', new: p.newTitle || '' })],
  silence_break: [t('kindSilenceBreak'), (p) => t('lineSilenceBreak', { days: p.gapDays })],
  channel_gone: [t('kindChannelGone'), (p) => t('lineChannelGone', {
    subs: compact(p.subscribers), views: compact(p.views), videos: num(p.videoCount) })],
  video_gone: [t('kindVideoGone'), (p) => t('lineVideoGone', { score: p.outlierScore, views: compact(p.views) })],
  milestone: [t('kindMilestone'), (p) => t('lineMilestone', { milestone: num(p.milestone), subs: num(p.subscribers) })],
  topic_match: [t('kindTopic'), (p) => `${t('lineTopic', { topic: p.topic, similarity: p.similarity })}${p.channelTitle ? ` · ${p.channelTitle}` : ''}`],
});
const GONE = new Set(['channel_gone', 'video_gone']);

function eventRow(e) {
  const p = e.payload || {};
  const [label, line] = kinds()[e.kind] || [e.kind, () => ''];
  const title = p.videoId
    ? `<a href="https://www.youtube.com/watch?v=${esc(p.videoId)}" target="_blank" rel="noopener">${esc(p.title || p.videoId)}</a>`
    : esc(p.title || e.refId);
  const channel = p.channelId ? ` · <a href="#/channel/${esc(p.channelId)}">${t('channelLink')}</a>` : '';
  return `<div class="row">
    <div class="row-main">
      <div class="row-title">${e.seenAt ? '' : `<span class="chip chip-accent">${t('newChip')}</span> `}
        <span class="chip ${GONE.has(e.kind) ? 'chip-bad' : ''}">${esc(label)}</span> ${title}</div>
      <div class="row-sub">${esc(line(p))}${channel} · ${ago(e.createdAt)}</div>
    </div>
  </div>`;
}

/* План 19: свои темы. Новое видео, близкое к теме по эмбеддингу, даёт личный
   алерт «Тема» — только автору темы. Квота не тратится. */
function topicsCard(tp) {
  const row = (x) => `<div class="row">
    <div class="row-main">
      <div class="row-title">${x.paused ? `<span class="chip">${t('pausedChip')}</span> ` : ''}${esc(x.text)}</div>
      <div class="row-sub">${t('topicMeta', { threshold: x.threshold, date: fmtDate(x.createdAt) })}${
        x.searchYoutube ? ` · ${t('searchedOnYoutube')}${x.lastSearchedAt ? `, ${t('lastSearched', { date: fmtDateTime(x.lastSearchedAt) })}` : ''}` : ''}</div>
    </div>
    <button class="btn btn-ghost btn-sm" data-search="${x.id}" data-on="${x.searchYoutube ? 1 : 0}" type="button"
      data-tip="${esc(t('searchTip'))}">${
        x.searchYoutube ? t('stopSearch') : t('startSearch')}</button>
    <button class="btn btn-ghost btn-sm" data-pause="${x.id}" data-paused="${x.paused ? 1 : 0}" type="button">${x.paused ? t('resume') : t('pause')}</button>
    <button class="btn btn-ghost btn-sm" data-del="${x.id}" type="button">${t('remove')}</button>
  </div>`;
  return `<div class="card">
    ${sectionHead(t('topicsTitle'), t('topicsSub'))}
    <div class="form-row">
      <label class="field" style="flex:1"><span class="field-label">${t('topicText')}</span>
        <input id="tpText" placeholder="${esc(t('topicPlaceholder'))}" maxlength="300"></label>
      <label class="field"><span class="field-label">${t('threshold')}</span>
        <input id="tpThr" type="number" min="0.3" max="0.95" step="0.05" value="0.6" style="width:90px"></label>
      <label class="field" data-tip="${esc(t('onYoutubeTip'))}">
        <span class="field-label">${t('onYoutube')}</span><input type="checkbox" id="tpSearch"></label>
      <button class="btn" id="tpAdd" type="button">${t('follow')}</button>
    </div>
    ${tp.topics.length ? `<div class="rows" style="margin-top:10px">${tp.topics.map(row).join('')}</div>`
                       : `<div class="section-sub" style="margin-top:10px">${t('noTopics')}</div>`}
  </div>`;
}

function wireTopics() {
  $('#tpAdd')?.addEventListener('click', async () => {
    try {
      await api('/api/topics', { method: 'POST', body: {
        text: $('#tpText').value, threshold: Number($('#tpThr').value), searchYoutube: $('#tpSearch').checked } });
      toast(t('topicAdded'), 'ok');
      render();
    } catch (err) { toast(err.message, 'err'); }
  });
  document.querySelectorAll('[data-pause]').forEach((b) => b.addEventListener('click', async () => {
    await api(`/api/topics/${b.dataset.pause}/pause`, { method: 'POST', body: { paused: b.dataset.paused !== '1' } });
    render();
  }));
  document.querySelectorAll('[data-search]').forEach((b) => b.addEventListener('click', async () => {
    await api(`/api/topics/${b.dataset.search}/search`, { method: 'POST', body: { on: b.dataset.on !== '1' } });
    render();
  }));
  document.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', async () => {
    await api(`/api/topics/${b.dataset.del}`, { method: 'DELETE' });
    render();
  }));
}

async function viewAlerts() {
  const p = { kind: '', unseen_only: false };
  try { Object.assign(p, JSON.parse(localStorage.getItem('nf.alerts') || '{}')); } catch (e) { /* пусто */ }
  const [d, topics] = await Promise.all([
    api(`/api/events${q({ kind: p.kind, unseen_only: p.unseen_only || '', limit: 200 })}`),
    api('/api/topics')]);
  const opt = (v, l) => `<option value="${esc(v)}"${p.kind === v ? ' selected' : ''}>${esc(l)}</option>`;

  view.innerHTML = `
    <div class="card">
      ${sectionHead(t('title'), t('sub'),
        `<button class="btn btn-ghost btn-sm" id="alSeen" type="button">${t('markAllSeen')}</button>`)}
      <div class="form-row">
        <label class="field"><span class="field-label">${t('type')}</span>
          <select id="alKind">${opt('', t('all'))}${Object.entries(kinds()).map(([k, [l]]) => opt(k, l)).join('')}</select></label>
        <label class="field"><span class="field-label">${t('unseenOnly')}</span>
          <input type="checkbox" id="alUnseen"${p.unseen_only ? ' checked' : ''}></label>
        <button class="btn" id="alApply" type="button">${t('apply')}</button>
      </div>
      <div class="section-sub" style="margin-top:10px">${t('summary', { shown: num(d.events.length), unseen: num(d.unseenCount) })}</div>
    </div>
    <div class="card">
      ${d.events.length ? `<div class="rows">${d.events.map(eventRow).join('')}</div>`
                        : empty(t('empty'))}
    </div>
    ${topicsCard(topics)}`;
  wireTopics();

  $('#alApply').addEventListener('click', () => {
    localStorage.setItem('nf.alerts', JSON.stringify({
      kind: $('#alKind').value, unseen_only: $('#alUnseen').checked,
    }));
    render();
  });
  $('#alSeen').addEventListener('click', async () => {
    await api('/api/events/seen', { method: 'POST', body: { all: true } });
    toast(t('allSeen'));
    render();
  });
}

export { viewAlerts };
