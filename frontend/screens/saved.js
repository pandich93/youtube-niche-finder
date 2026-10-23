/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { api, ago, esc, toast, sectionHead, empty, table } from '../ui.js';
import { view, render } from '../shared.js';
import { dict } from '../i18n.js';
import ru from '../i18n/ru/saved.js';
import en from '../i18n/en/saved.js';

const t = dict({ ru, en });

/* --------------------------------------------------------------- Избранное */

async function viewSaved() {
  const d = await api('/api/saved');
  const items = d.items || [];
  view.innerHTML = `
    <div class="card">
      ${sectionHead(t('title'), t('sub'))}
      ${items.length ? table([
        { label: t('colType'), render: (r) => r.kind === 'video' ? t('kindVideo') : t('kindChannel') },
        { label: '', wrap: true, render: (r) => r.kind === 'video'
            ? `<a href="https://www.youtube.com/watch?v=${esc(r.refId)}" target="_blank" rel="noopener">${esc(r.payload?.video?.title || r.refId)}</a>`
            : `<a href="#/channel/${esc(r.refId)}">${esc(r.payload?.profile?.title || r.refId)}</a>` },
        { label: t('colFolder'), render: (r) => esc(r.folder || 'default') },
        { label: t('colNote'), wrap: true, render: (r) => esc(r.note || '') },
        { label: t('colSaved'), render: (r) => ago(r.createdAt) },
        { label: '', render: (r) => `<button class="btn btn-ghost btn-sm unsave" data-id="${r.id}">${t('remove')}</button>` },
      ], items) : empty(t('empty'))}
    </div>`;
  view.querySelectorAll('.unsave').forEach((b) => b.addEventListener('click', async () => {
    try {
      await api(`/api/saved/${b.dataset.id}`, { method: 'DELETE' });
      toast(t('removed'), 'ok'); render();
    } catch (e) { toast(e.message, 'err'); }
  }));
}

export { viewSaved };
