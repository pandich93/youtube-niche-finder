/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, num, compact, ago, esc, toast, sectionHead, empty } from '../ui.js';
import { view, render } from '../shared.js';

/* ------------------------------------------------------------- Алерты */

/* Те же события, что уходят в Telegram/webhook; видны только по каналам из трекера. */
const KINDS = {
  outlier: ['Outlier', (p) => `×${p.outlierScore} · ${compact(p.views)} просмотров`],
  acceleration: ['Ускорение', (p) => `×${p.acceleration} · ${num(p.vph24h)} VPH за 24 ч`],
  title_change: ['Смена заголовка', (p) => `«${p.oldTitle || ''}» → «${p.newTitle || ''}»`],
  silence_break: ['Вернулись после паузы', (p) => `молчали ${p.gapDays} дн`],
  channel_gone: ['Канал исчез', (p) => `было ${compact(p.subscribers)} подп. · ${compact(p.views)} просмотров · ${num(p.videoCount)} видео`],
  video_gone: ['Видео исчезло', (p) => `было outlier ×${p.outlierScore} · ${compact(p.views)} просмотров`],
  milestone: ['Рубеж', (p) => `${num(p.milestone)} подписчиков · сейчас ${num(p.subscribers)}`],
};
const GONE = new Set(['channel_gone', 'video_gone']);

function eventRow(e) {
  const p = e.payload || {};
  const [label, line] = KINDS[e.kind] || [e.kind, () => ''];
  const title = p.videoId
    ? `<a href="https://www.youtube.com/watch?v=${esc(p.videoId)}" target="_blank" rel="noopener">${esc(p.title || p.videoId)}</a>`
    : esc(p.title || e.refId);
  const channel = p.channelId ? ` · <a href="#/channel/${esc(p.channelId)}">канал</a>` : '';
  return `<div class="row">
    <div class="row-main">
      <div class="row-title">${e.seenAt ? '' : '<span class="chip chip-accent">новое</span> '}
        <span class="chip ${GONE.has(e.kind) ? 'chip-bad' : ''}">${esc(label)}</span> ${title}</div>
      <div class="row-sub">${esc(line(p))}${channel} · ${ago(e.createdAt)}</div>
    </div>
  </div>`;
}

async function viewAlerts() {
  const p = { kind: '', unseen_only: false };
  try { Object.assign(p, JSON.parse(localStorage.getItem('nf.alerts') || '{}')); } catch (e) { /* пусто */ }
  const d = await api(`/api/events${q({ kind: p.kind, unseen_only: p.unseen_only || '', limit: 200 })}`);
  const opt = (v, l) => `<option value="${esc(v)}"${p.kind === v ? ' selected' : ''}>${esc(l)}</option>`;

  view.innerHTML = `
    <div class="card">
      ${sectionHead('Алерты', 'события по каналам из трекера — то же, что уходит в Telegram или webhook',
        '<button class="btn btn-ghost btn-sm" id="alSeen" type="button">Отметить всё прочитанным</button>')}
      <div class="form-row">
        <label class="field"><span class="field-label">Тип</span>
          <select id="alKind">${opt('', 'все')}${Object.entries(KINDS).map(([k, [l]]) => opt(k, l)).join('')}</select></label>
        <label class="field"><span class="field-label">Только новые</span>
          <input type="checkbox" id="alUnseen"${p.unseen_only ? ' checked' : ''}></label>
        <button class="btn" id="alApply" type="button">Применить</button>
      </div>
      <div class="section-sub" style="margin-top:10px">Показано: <b>${num(d.events.length)}</b> · новых всего:
        <b>${num(d.unseenCount)}</b> · квота не тратится. «Исчез» ставится после двух промахов API
        с разницей не меньше 6 часов: канал удалён, скрыт или заблокирован.</div>
    </div>
    <div class="card">
      ${d.events.length ? `<div class="rows">${d.events.map(eventRow).join('')}</div>`
                        : empty('событий нет — добавьте каналы в трекер, воркер проверяет их по расписанию')}
    </div>`;

  $('#alApply').addEventListener('click', () => {
    localStorage.setItem('nf.alerts', JSON.stringify({
      kind: $('#alKind').value, unseen_only: $('#alUnseen').checked,
    }));
    render();
  });
  $('#alSeen').addEventListener('click', async () => {
    await api('/api/events/seen', { method: 'POST', body: { all: true } });
    toast('Все алерты отмечены прочитанными');
    render();
  });
}

export { viewAlerts };
