/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, compact, esc, toast, sectionHead, notice, empty } from '../ui.js';
import { view } from '../shared.js';

/* ------------------------------------------------------- Транскрипты (19) */

let transcriptTab = 'pending';
let transcriptSearchQuery = '';
let transcriptSearchResult = null;
let transcriptPasteOpenFor = null;

function transcriptQueueCard(item, tab) {
  const link = `https://www.youtube.com/watch?v=${esc(item.videoId)}`;
  return `
    <div class="row" style="align-items:flex-start;flex-direction:column;gap:8px">
      <div style="display:flex;width:100%;justify-content:space-between;gap:12px">
        <div class="row-main">
          <div class="row-title"><a href="${link}" target="_blank" rel="noopener">${esc(item.title || item.videoId)}</a></div>
          <div class="row-sub">${esc(item.channelTitle || '')}${item.views != null ? ` · ${compact(item.views)} просмотров` : ''}
            ${item.reason ? ` · причина: ${esc(item.reason)}` : ''}${item.compareGroup ? ` · группа: ${esc(item.compareGroup)}` : ''}</div>
          ${item.error ? `<div class="row-sub" style="color:var(--critical)">${esc(item.error)}</div>` : ''}
        </div>
        ${tab === 'pending' || tab === 'error'
          ? `<button class="btn btn-ghost btn-sm transcript-paste-btn" data-video-id="${esc(item.videoId)}">
              ${tab === 'error' ? 'Повторить' : 'Вставить транскрипт'}</button>`
          : `<button class="btn btn-ghost btn-sm transcript-reindex-btn" data-video-id="${esc(item.videoId)}">Переиндексировать</button>`}
      </div>
      <div class="transcript-paste-form" data-video-id="${esc(item.videoId)}" ${transcriptPasteOpenFor === item.videoId ? '' : 'hidden'}
        style="width:100%">
        <textarea class="transcript-paste-input" rows="8" placeholder="Вставьте текст транскрипта с YouTube (со таймкодами или без)"
          style="width:100%;font-family:inherit;resize:vertical"></textarea>
        <div style="display:flex;gap:10px;margin-top:8px;align-items:center">
          <input type="text" class="transcript-lang-input" placeholder="язык (необязательно)" style="width:140px">
          <button class="btn btn-sm transcript-save-btn" data-video-id="${esc(item.videoId)}">Сохранить</button>
          <span class="transcript-save-status row-sub"></span>
        </div>
      </div>
    </div>`;
}

function transcriptSearchResultsHtml(result) {
  if (!result) return '';
  if (result.hint) return notice(esc(result.hint));
  if (!result.results.length) return empty('ничего не найдено');
  return `<div class="rows">${result.results.map((r) => `
    <div class="row" style="align-items:flex-start">
      <div class="row-main">
        <div class="row-title"><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title || r.videoId)}</a></div>
        <div class="row-sub">${esc(r.channelTitle || '')}${r.views != null ? ` · ${compact(r.views)} просмотров` : ''}
          ${r.startSec != null ? ` · ${Math.floor(r.startSec / 60)}:${String(r.startSec % 60).padStart(2, '0')}` : ''}</div>
        <div style="margin-top:4px">${esc(r.text || '')}</div>
      </div>
    </div>`).join('')}</div>`;
}

async function viewTranscripts() {
  const [queueRes, searchRes] = await Promise.all([
    api('/api/transcripts/queue'),
    transcriptSearchQuery ? api(`/api/transcripts/search${q({ query: transcriptSearchQuery })}`) : null,
  ]);
  transcriptSearchResult = searchRes;
  const queue = queueRes.queue || [];
  const byTab = {
    pending: queue.filter((i) => i.status === 'pending'),
    ready: queue.filter((i) => i.status === 'ready'),
    error: queue.filter((i) => i.status === 'error'),
  };
  const tabs = [['pending', 'Ожидают'], ['ready', 'Готовы'], ['error', 'Ошибки']];

  view.innerHTML = `
    <div class="card">
      ${sectionHead('Поиск по транскриптам', 'гибридный поиск: вектор + полнотекстовый')}
      <div style="display:flex;gap:10px">
        <input type="text" id="transcriptSearchInput" placeholder="что ищем..." value="${esc(transcriptSearchQuery)}" style="flex:1">
        <button class="btn" id="transcriptSearchBtn">Искать</button>
      </div>
      ${transcriptSearchQuery ? `<div style="margin-top:12px">${transcriptSearchResultsHtml(transcriptSearchResult)}</div>` : ''}
    </div>

    <div class="card">
      ${sectionHead('Очередь транскриптов', 'субтитры не скачиваются автоматически — только вручную')}
      <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">
        <input type="text" id="transcriptAddVideoId" placeholder="ID видео" style="width:140px">
        <input type="text" id="transcriptAddReason" placeholder="причина (необязательно)" style="width:200px">
        <button class="btn btn-ghost btn-sm" id="transcriptAddBtn">Запросить транскрипт</button>
      </div>
      <div style="display:flex;gap:8px;margin-bottom:12px">
        ${tabs.map(([key, label]) => `<button class="btn btn-sm ${transcriptTab === key ? '' : 'btn-ghost'} transcript-tab-btn" data-tab="${key}">
          ${label} (${byTab[key].length})</button>`).join('')}
      </div>
      <div class="rows">${byTab[transcriptTab].length
        ? byTab[transcriptTab].map((item) => transcriptQueueCard(item, transcriptTab)).join('')
        : empty('пусто')}</div>
    </div>`;

  $('#transcriptAddBtn').addEventListener('click', async () => {
    const videoId = $('#transcriptAddVideoId').value.trim();
    if (!videoId) { toast('Введите ID видео', 'err'); return; }
    try {
      await api('/api/transcripts/request', { method: 'POST', body: {
        videoId, reason: $('#transcriptAddReason').value.trim() || undefined,
      } });
      toast('Добавлено в очередь', 'ok');
      viewTranscripts();
    } catch (e) { toast(e.message, 'err'); }
  });
  $('#transcriptSearchBtn').addEventListener('click', () => {
    transcriptSearchQuery = $('#transcriptSearchInput').value.trim();
    viewTranscripts();
  });
  view.querySelectorAll('.transcript-tab-btn').forEach((b) => b.addEventListener('click', () => {
    transcriptTab = b.dataset.tab;
    viewTranscripts();
  }));
  view.querySelectorAll('.transcript-paste-btn').forEach((b) => b.addEventListener('click', () => {
    transcriptPasteOpenFor = transcriptPasteOpenFor === b.dataset.videoId ? null : b.dataset.videoId;
    viewTranscripts();
  }));
  view.querySelectorAll('.transcript-reindex-btn').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true;
    try {
      await api(`/api/transcripts/${encodeURIComponent(b.dataset.videoId)}/reindex`, { method: 'POST' });
      toast('Переиндексировано', 'ok');
    } catch (e) { toast(e.message, 'err'); }
    finally { b.disabled = false; }
  }));
  view.querySelectorAll('.transcript-save-btn').forEach((b) => b.addEventListener('click', async () => {
    const form = b.closest('.transcript-paste-form');
    const text = form.querySelector('.transcript-paste-input').value;
    const language = form.querySelector('.transcript-lang-input').value.trim() || undefined;
    const status = form.querySelector('.transcript-save-status');
    if (!text.trim()) { status.textContent = 'вставьте текст'; return; }
    b.disabled = true;
    status.textContent = 'сохраняю…';
    try {
      const r = await api(`/api/transcripts/${encodeURIComponent(b.dataset.videoId)}/save`,
        { method: 'POST', body: { text, language } });
      if (r.status === 'ready') {
        toast(`Сохранено: ${r.chunks} кусков, ${r.wordCount} слов`, 'ok');
        transcriptPasteOpenFor = null;
        viewTranscripts();
      } else {
        status.textContent = r.error || 'ошибка';
      }
    } catch (e) {
      status.textContent = e.message;
    } finally {
      b.disabled = false;
    }
  }));
}

export { viewTranscripts };
