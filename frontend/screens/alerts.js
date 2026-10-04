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
  topic_match: ['Тема', (p) => `«${p.topic}» · похожесть ${p.similarity}${p.channelTitle ? ` · ${p.channelTitle}` : ''}`],
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

/* План 19: свои темы. Новое видео, близкое к теме по эмбеддингу, даёт личный
   алерт «Тема» — только автору темы. Квота не тратится. */
function topicsCard(t) {
  const row = (x) => `<div class="row">
    <div class="row-main">
      <div class="row-title">${x.paused ? '<span class="chip">пауза</span> ' : ''}${esc(x.text)}</div>
      <div class="row-sub">порог похожести ${x.threshold} · с ${new Date(x.createdAt).toLocaleDateString('ru-RU')}</div>
    </div>
    <button class="btn btn-ghost btn-sm" data-pause="${x.id}" data-paused="${x.paused ? 1 : 0}" type="button">${x.paused ? 'Возобновить' : 'Пауза'}</button>
    <button class="btn btn-ghost btn-sm" data-del="${x.id}" type="button">Убрать</button>
  </div>`;
  return `<div class="card">
    ${sectionHead('Мои темы', 'алерт, когда в базу попадает видео на вашу тему — из RSS трекера, собранных ниш и трендов, не со всего YouTube')}
    <div class="form-row">
      <label class="field" style="flex:1"><span class="field-label">Тема своими словами</span>
        <input id="tpText" placeholder="например, ИИ-агенты для малого бизнеса" maxlength="300"></label>
      <label class="field"><span class="field-label">Порог</span>
        <input id="tpThr" type="number" min="0.3" max="0.95" step="0.05" value="0.6" style="width:90px"></label>
      <button class="btn" id="tpAdd" type="button">Следить</button>
    </div>
    ${t.topics.length ? `<div class="rows" style="margin-top:10px">${t.topics.map(row).join('')}</div>`
                      : '<div class="section-sub" style="margin-top:10px">тем пока нет</div>'}
  </div>`;
}

function wireTopics() {
  $('#tpAdd')?.addEventListener('click', async () => {
    try {
      await api('/api/topics', { method: 'POST', body: { text: $('#tpText').value, threshold: Number($('#tpThr').value) } });
      toast('Тема добавлена — совпадения появятся здесь', 'ok');
      render();
    } catch (err) { toast(err.message, 'err'); }
  });
  document.querySelectorAll('[data-pause]').forEach((b) => b.addEventListener('click', async () => {
    await api(`/api/topics/${b.dataset.pause}/pause`, { method: 'POST', body: { paused: b.dataset.paused !== '1' } });
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
      ${sectionHead('Алерты', 'события по каналам из трекера и по вашим темам — то же, что уходит в Telegram или webhook',
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
                        : empty('событий нет — добавьте каналы в трекер или тему ниже, воркер проверяет их по расписанию')}
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
    toast('Все алерты отмечены прочитанными');
    render();
  });
}

export { viewAlerts };
