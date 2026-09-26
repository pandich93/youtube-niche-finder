/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, num, compact, ago, esc, toast, sectionHead, empty, table, aiLabelsBadge } from '../ui.js';
import { view, collectForm, wireCollect, render } from '../shared.js';

/* ----------------------------------------------------------------- Трекер */

/* faceless/format/topic (этап 03) живут только в памяти вкладки -- фильтр
   по AI-разметке не настолько важен, чтобы переживать перезагрузку, в
   отличие от state.period/niche. */
let channelFilters = { faceless: '', format: '', topic: '' };

async function viewTracker() {
  const f = channelFilters;
  const d = await api(`/api/channels/tracked${q({
    faceless: f.faceless || undefined, content_format: f.format || undefined, topic: f.topic || undefined,
  })}`);
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Трекер каналов', 'воркер снимает статистику по этим каналам и копит историю', `
        <select id="filterFaceless">
          <option value="" ${f.faceless === '' ? 'selected' : ''}>faceless: любой</option>
          <option value="true" ${f.faceless === 'true' ? 'selected' : ''}>faceless: да</option>
          <option value="false" ${f.faceless === 'false' ? 'selected' : ''}>faceless: нет</option>
        </select>
        <input type="text" id="filterFormat" placeholder="формат" value="${esc(f.format)}" style="width:120px">
        <input type="text" id="filterTopic" placeholder="тема" value="${esc(f.topic)}" style="width:120px">`)}
      ${d.channels.length ? table([
        { label: 'Канал', render: (r) => `<a href="#/channel/${esc(r.channel_id)}">${esc(r.title || r.channel_id)}</a>` },
        { label: 'AI', render: (r) => aiLabelsBadge(r.aiLabels) || '—' },
        { label: 'Подписчиков', num: true, render: (r) => compact(r.subscriber_count) },
        { label: 'Видео', num: true, render: (r) => num(r.video_count) },
        { label: 'Просмотров', num: true, render: (r) => compact(r.view_count) },
        { label: 'Снимков', num: true, render: (r) => num(r.snapshots) },
        { label: 'Обновлён', render: (r) => ago(r.last_refreshed_at) },
        { label: '', render: (r) => `<button class="btn btn-ghost btn-sm untrack" data-id="${esc(r.channel_id)}">убрать</button>` },
      ], d.channels) : empty('пока никого — добавьте канал ниже, или фильтр по AI-разметке ничего не нашёл')}
    </div>
    ${collectForm()}`;
  wireCollect(render);
  const applyFilters = () => {
    channelFilters = {
      faceless: $('#filterFaceless').value,
      format: $('#filterFormat').value.trim(),
      topic: $('#filterTopic').value.trim(),
    };
    render();
  };
  $('#filterFaceless').addEventListener('change', applyFilters);
  $('#filterFormat').addEventListener('change', applyFilters);
  $('#filterTopic').addEventListener('change', applyFilters);
  view.querySelectorAll('.untrack').forEach((b) => b.addEventListener('click', async () => {
    try {
      await api(`/api/channels/tracked/${b.dataset.id}`, { method: 'DELETE' });
      toast('Убрал из трекера', 'ok'); render();
    } catch (e) { toast(e.message, 'err'); }
  }));
}

export { viewTracker };
