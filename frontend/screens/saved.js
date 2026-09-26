/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { api, ago, esc, toast, sectionHead, empty, table } from '../ui.js';
import { view, render } from '../shared.js';

/* --------------------------------------------------------------- Избранное */

async function viewSaved() {
  const d = await api('/api/saved');
  const items = d.items || [];
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Избранное', 'сохранённые видео и каналы со снимком метрик на момент сохранения')}
      ${items.length ? table([
        { label: 'Тип', render: (r) => r.kind === 'video' ? 'видео' : 'канал' },
        { label: '', wrap: true, render: (r) => r.kind === 'video'
            ? `<a href="https://www.youtube.com/watch?v=${esc(r.refId)}" target="_blank" rel="noopener">${esc(r.payload?.video?.title || r.refId)}</a>`
            : `<a href="#/channel/${esc(r.refId)}">${esc(r.payload?.profile?.title || r.refId)}</a>` },
        { label: 'Папка', render: (r) => esc(r.folder || 'default') },
        { label: 'Заметка', wrap: true, render: (r) => esc(r.note || '') },
        { label: 'Сохранено', render: (r) => ago(r.createdAt) },
        { label: '', render: (r) => `<button class="btn btn-ghost btn-sm unsave" data-id="${r.id}">убрать</button>` },
      ], items) : empty('пока пусто — кнопка «В избранное» есть в панели расширения на странице ролика или канала')}
    </div>`;
  view.querySelectorAll('.unsave').forEach((b) => b.addEventListener('click', async () => {
    try {
      await api(`/api/saved/${b.dataset.id}`, { method: 'DELETE' });
      toast('Убрал из избранного', 'ok'); render();
    } catch (e) { toast(e.message, 'err'); }
  }));
}

export { viewSaved };
