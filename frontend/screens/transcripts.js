/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, compact, esc, toast, sectionHead, notice, empty, state } from '../ui.js';
import { view } from '../shared.js';
import { hookResultHtml, HOOK_FEATURE_LABEL } from '../hook_view.js';
import { dict } from '../i18n.js';
import ru from '../i18n/ru/transcripts.js';
import en from '../i18n/en/transcripts.js';

const t = dict({ ru, en });

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
          <div class="row-sub">${esc(item.channelTitle || '')}${item.views != null ? ` · ${t('viewsCount', { views: compact(item.views) })}` : ''}
            ${item.reason ? ` · ${t('reason', { reason: esc(item.reason) })}` : ''}${item.compareGroup ? ` · ${t('group', { group: esc(item.compareGroup) })}` : ''}</div>
          ${item.error ? `<div class="row-sub" style="color:var(--critical)">${esc(item.error)}</div>` : ''}
        </div>
        ${tab === 'pending' || tab === 'error'
          ? `<button class="btn btn-ghost btn-sm transcript-paste-btn" data-video-id="${esc(item.videoId)}">
              ${tab === 'error' ? t('retry') : t('paste')}</button>`
          : `<div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end">
              <button class="btn btn-ghost btn-sm transcript-hook-btn" data-video-id="${esc(item.videoId)}">${t('hook')}</button>
              <button class="btn btn-ghost btn-sm transcript-reindex-btn" data-video-id="${esc(item.videoId)}">${t('reindex')}</button>
            </div>`}
      </div>
      ${tab === 'ready' ? `
      <div class="transcript-hook-box" data-video-id="${esc(item.videoId)}" hidden style="width:100%">
        <label class="row-sub" style="display:flex;gap:6px;align-items:center;margin-bottom:8px">
          <input type="checkbox" class="transcript-hook-llm">
          ${t('llmLabel')}
        </label>
        <div class="transcript-hook-result"></div>
      </div>` : ''}
      <div class="transcript-paste-form" data-video-id="${esc(item.videoId)}" ${transcriptPasteOpenFor === item.videoId ? '' : 'hidden'}
        style="width:100%">
        <textarea class="transcript-paste-input" rows="8" placeholder="${esc(t('pastePlaceholder'))}"
          style="width:100%;font-family:inherit;resize:vertical"></textarea>
        <div style="display:flex;gap:10px;margin-top:8px;align-items:center">
          <input type="text" class="transcript-lang-input" placeholder="${esc(t('langPlaceholder'))}" style="width:140px">
          <button class="btn btn-sm transcript-save-btn" data-video-id="${esc(item.videoId)}">${t('save')}</button>
          <span class="transcript-save-status row-sub"></span>
        </div>
      </div>
    </div>`;
}

function hookBenchmarkHtml(b) {
  const head = sectionHead(t('benchTitle'), t('benchSub'));
  if (!b) return `<div class="card">${head}<div class="section-sub">${t('pickNiche')}</div></div>`;
  if (!b.found) return `<div class="card">${head}<div class="section-sub">${t('benchNone')}</div></div>`;
  if (!b.reliable) {
    return `<div class="card">${head}<div class="section-sub">${t('benchThin', {
      outliers: b.have.outliers, need: b.need, regular: b.have.regular,
      grey: b.greyZone, noBase: b.withoutOutlierScore })}</div></div>`;
  }
  const more = b.moreCommonInOutliers.length
    ? b.moreCommonInOutliers.map((n) => t('featureDiff', { label: esc(HOOK_FEATURE_LABEL[n] || n), pp: b.features[n].diffPp })).join(', ')
    : t('noDiff');
  return `<div class="card">${head}
    <div class="section-sub">${t('benchResult', {
      outScore: b.outliers.meanScore, regScore: b.regular.meanScore,
      outN: b.outliers.n, regN: b.regular.n, more })}</div></div>`;
}

function transcriptSearchResultsHtml(result) {
  if (!result) return '';
  if (result.hint) return notice(esc(result.hint));
  if (!result.results.length) return empty(t('nothingFound'));
  return `<div class="rows">${result.results.map((r) => `
    <div class="row" style="align-items:flex-start">
      <div class="row-main">
        <div class="row-title"><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title || r.videoId)}</a></div>
        <div class="row-sub">${esc(r.channelTitle || '')}${r.views != null ? ` · ${t('viewsCount', { views: compact(r.views) })}` : ''}
          ${r.startSec != null ? ` · ${Math.floor(r.startSec / 60)}:${String(r.startSec % 60).padStart(2, '0')}` : ''}</div>
        <div style="margin-top:4px">${esc(r.text || '')}</div>
      </div>
    </div>`).join('')}</div>`;
}

async function viewTranscripts() {
  const [queueRes, searchRes, hookBench] = await Promise.all([
    api('/api/transcripts/queue'),
    transcriptSearchQuery ? api(`/api/transcripts/search${q({ query: transcriptSearchQuery })}`) : null,
    state.niche ? api(`/api/niches/${encodeURIComponent(state.niche)}/hook-benchmark`) : null,
  ]);
  transcriptSearchResult = searchRes;
  const queue = queueRes.queue || [];
  const byTab = {
    pending: queue.filter((i) => i.status === 'pending'),
    ready: queue.filter((i) => i.status === 'ready'),
    error: queue.filter((i) => i.status === 'error'),
  };
  const tabs = [['pending', t('tabPending')], ['ready', t('tabReady')], ['error', t('tabError')]];

  view.innerHTML = `
    <div class="card">
      ${sectionHead(t('searchTitle'), t('searchSub'))}
      <div style="display:flex;gap:10px">
        <input type="text" id="transcriptSearchInput" placeholder="${esc(t('searchPlaceholder'))}" value="${esc(transcriptSearchQuery)}" style="flex:1">
        <button class="btn" id="transcriptSearchBtn">${t('searchBtn')}</button>
      </div>
      ${transcriptSearchQuery ? `<div style="margin-top:12px">${transcriptSearchResultsHtml(transcriptSearchResult)}</div>` : ''}
    </div>

    ${hookBenchmarkHtml(hookBench)}

    <div class="card">
      ${sectionHead(t('queueTitle'), t('queueSub'))}
      <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">
        <input type="text" id="transcriptAddVideoId" placeholder="${esc(t('videoIdPlaceholder'))}" style="width:140px">
        <input type="text" id="transcriptAddReason" placeholder="${esc(t('reasonPlaceholder'))}" style="width:200px">
        <button class="btn btn-ghost btn-sm" id="transcriptAddBtn">${t('requestBtn')}</button>
      </div>
      <div style="display:flex;gap:8px;margin-bottom:12px">
        ${tabs.map(([key, label]) => `<button class="btn btn-sm ${transcriptTab === key ? '' : 'btn-ghost'} transcript-tab-btn" data-tab="${key}">
          ${label} (${byTab[key].length})</button>`).join('')}
      </div>
      <div class="rows">${byTab[transcriptTab].length
        ? byTab[transcriptTab].map((item) => transcriptQueueCard(item, transcriptTab)).join('')
        : empty(t('emptyTab'))}</div>
    </div>`;

  $('#transcriptAddBtn').addEventListener('click', async () => {
    const videoId = $('#transcriptAddVideoId').value.trim();
    if (!videoId) { toast(t('enterVideoId'), 'err'); return; }
    try {
      await api('/api/transcripts/request', { method: 'POST', body: {
        videoId, reason: $('#transcriptAddReason').value.trim() || undefined,
      } });
      toast(t('queued'), 'ok');
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
      toast(t('reindexed'), 'ok');
    } catch (e) { toast(e.message, 'err'); }
    finally { b.disabled = false; }
  }));
  view.querySelectorAll('.transcript-hook-btn').forEach((b) => b.addEventListener('click', async () => {
    const box = view.querySelector(`.transcript-hook-box[data-video-id="${CSS.escape(b.dataset.videoId)}"]`);
    const out = box.querySelector('.transcript-hook-result');
    box.hidden = false;
    b.disabled = true;
    out.innerHTML = `<div class="row-sub">${t('computing')}</div>`;
    try {
      const llm = box.querySelector('.transcript-hook-llm').checked;
      const r = await api(`/api/videos/${encodeURIComponent(b.dataset.videoId)}/hook${q({
        niche: state.niche || undefined, llm: llm ? 'true' : undefined })}`);
      out.innerHTML = hookResultHtml(r);
    } catch (e) { out.innerHTML = `<div class="row-sub" style="color:var(--critical)">${esc(e.message)}</div>`; }
    finally { b.disabled = false; }
  }));
  view.querySelectorAll('.transcript-save-btn').forEach((b) => b.addEventListener('click', async () => {
    const form = b.closest('.transcript-paste-form');
    const text = form.querySelector('.transcript-paste-input').value;
    const language = form.querySelector('.transcript-lang-input').value.trim() || undefined;
    const status = form.querySelector('.transcript-save-status');
    if (!text.trim()) { status.textContent = t('pasteText'); return; }
    b.disabled = true;
    status.textContent = t('saving');
    try {
      const r = await api(`/api/transcripts/${encodeURIComponent(b.dataset.videoId)}/save`,
        { method: 'POST', body: { text, language } });
      if (r.status === 'ready') {
        toast(t('savedChunks', { chunks: r.chunks, words: r.wordCount }), 'ok');
        transcriptPasteOpenFor = null;
        viewTranscripts();
      } else {
        status.textContent = r.error || t('error');
      }
    } catch (e) {
      status.textContent = e.message;
    } finally {
      b.disabled = false;
    }
  }));
}

export { viewTranscripts };
