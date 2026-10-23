/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, num, compact, mult, ago, esc, toast, tile, sectionHead, notice, empty, table, state } from '../ui.js';
import { view, plabel } from '../shared.js';
import { dict } from '../i18n.js';
import ru from '../i18n/ru/metadata.js';
import en from '../i18n/en/metadata.js';

const t = dict({ ru, en });

/* ------------------------------------------------------- Разбор метаданных */

// verdict с сервера -> ключ словаря; подпись переводится при рендере
const MD_VERDICT_KEY = { ok: 'verdictOk', warn: 'verdictWarn', unreliable: 'verdictUnreliable' };

function metadataSignalRow(s) {
  const cls = s.verdict === 'ok' ? 'chip-good' : s.verdict === 'warn' ? 'chip-bad' : '';
  const label = MD_VERDICT_KEY[s.verdict] ? t(MD_VERDICT_KEY[s.verdict]) : s.verdict;
  return `<div class="row" style="align-items:flex-start">
    <div class="row-main">
      <div class="row-title"><span class="chip ${cls}">${esc(label)}</span> ${esc(s.id || '')}</div>
      <div class="row-sub">${esc(s.explanation || '')}</div>
    </div>
  </div>`;
}

let mdLastReview = null;

async function viewMetadata() {
  view.innerHTML = `
    <div class="card">
      ${sectionHead(t('title'), t('sub'))}
      <div class="form-row">
        <label class="field" style="flex:2 1 320px"><span class="field-label">${t('fieldTitle')}</span>
          <input type="text" id="mdTitle" placeholder="${esc(t('titlePlaceholder'))}"></label>
        <label class="field"><span class="field-label">${t('fieldNiche')}</span>
          <input type="text" id="mdNiche" value="${esc(state.niche || '')}" placeholder="ai-explainers"></label>
        <label class="field"><span class="field-label">${t('fieldChannel')}</span>
          <input type="text" id="mdChannel" placeholder="UC..."></label>
      </div>
      <div class="form-row" style="margin-top:12px">
        <label class="field" style="flex:2 1 320px"><span class="field-label">${t('fieldDesc')}</span>
          <textarea id="mdDesc" rows="3" placeholder="${esc(t('descPlaceholder'))}"></textarea></label>
        <label class="field"><span class="field-label">${t('fieldTags')}</span>
          <input type="text" id="mdTags" placeholder="${esc(t('tagsPlaceholder'))}"></label>
        <label class="field" style="flex:0 0 auto">
          <span class="field-label">Shorts</span>
          <input type="checkbox" id="mdShort" style="width:auto;height:38px"></label>
      </div>
      <div class="form-row" style="margin-top:12px">
        <button class="btn" id="mdReviewBtn" type="button">${t('review')}</button>
        <button class="btn btn-ghost" id="mdSaveBtn" type="button">${t('saveDraft')}</button>
      </div>
    </div>
    <div id="mdResult"></div>
    <div class="card" id="mdDraftsCard">
      ${sectionHead(t('draftsTitle'), t('draftsSub'))}
      <div id="mdDraftsBody">${empty(t('loading'))}</div>
    </div>`;

  function renderMdResult(res) {
    mdLastReview = res;
    const box = $('#mdResult');
    if (res.hint) {
      box.innerHTML = `<div class="card">${notice(esc(res.hint), 'warn')}</div>`;
      return;
    }
    box.innerHTML = `
      <div class="card">
        ${sectionHead(t('summaryTitle'), t('summarySub', {
          videos: res.sample.videosAnalysed, outliers: res.sample.outliersInSample, period: plabel(res.sample.period) }))}
        <div class="tiles">
          ${tile(t('tileOk'), num(res.summary.ok))}
          ${tile(t('tileWarn'), num(res.summary.warn))}
          ${tile(t('tileUnreliable'), num(res.summary.unreliable))}
        </div>
      </div>
      <div class="grid-2">
        <div class="card">
          ${sectionHead(t('signals'))}
          ${res.signals.length ? res.signals.map(metadataSignalRow).join('') : empty(t('noSignals'))}
        </div>
        <div class="card">
          ${sectionHead(t('patternsTitle'), res.keyPhrase ? t('keyPhrase', { phrase: esc(res.keyPhrase) }) : t('noKeyPhrase'))}
          ${res.structuralPatterns.length ? table([
            { label: t('colFeature'), render: (r) => esc(r.label) },
            { label: 'Lift', num: true, render: (r) => r.lift != null ? mult(r.lift) : '—' },
            { label: t('colSample'), num: true, render: (r) => num(r.sample) },
            { label: t('colInDraft'), render: (r) => r.presentInDraft ? t('present') : t('absent') },
            { label: '', render: (r) => r.verdict === 'unreliable' ? `<span class="chip">${t('verdictUnreliable')}</span>` : '' },
          ], res.structuralPatterns) : empty(t('noData'))}
        </div>
      </div>
      <div class="card">
        ${sectionHead(t('similarTitle'), t('similarSub'))}
        ${res.nearDuplicates.hint ? notice(esc(res.nearDuplicates.hint), '')
          : res.nearDuplicates.near.length ? table([
              { label: t('colVideo'), wrap: true, render: (r) => `<a href="https://www.youtube.com/watch?v=${esc(r.videoId)}" target="_blank" rel="noopener">${esc(r.title || r.videoId)}</a>` },
              { label: t('colViews'), num: true, render: (r) => compact(r.views) },
              { label: t('colSimilarity'), num: true, render: (r) => r.similarity },
            ], res.nearDuplicates.near)
          : empty(t('noNear', { checked: res.nearDuplicates.checked }))}
      </div>`;
  }

  async function loadMdDrafts() {
    const chId = $('#mdChannel')?.value.trim() || null;
    const d = await api(`/api/drafts${q({ channel_id: chId })}`);
    $('#mdDraftsBody').innerHTML = d.drafts.length ? table([
      { label: t('colTitle'), wrap: true, render: (r) => esc(r.title) },
      { label: t('colNiche'), render: (r) => esc(r.niche || '—') },
      { label: t('colSaved'), render: (r) => ago(r.createdAt) },
      { label: t('colVideo'), render: (r) => r.videoId
          ? `<a href="https://www.youtube.com/watch?v=${esc(r.videoId)}" target="_blank" rel="noopener">${esc(r.videoId)}</a>`
          : `<div class="form-row"><input type="text" class="mdLinkVid" data-id="${r.id}" placeholder="video ID" style="width:140px">
             <button class="btn btn-ghost btn-sm mdDoLink" data-id="${r.id}" type="button">${t('link')}</button></div>` },
    ], d.drafts) : empty(t('draftsEmpty'));
    $('#mdDraftsBody').querySelectorAll('.mdDoLink').forEach((b) => b.addEventListener('click', async () => {
      const input = $(`.mdLinkVid[data-id="${b.dataset.id}"]`);
      const vid = input.value.trim();
      if (!vid) return toast(t('enterVideoId'), 'err');
      try {
        await api(`/api/drafts/${b.dataset.id}/link`, { method: 'POST', body: { videoId: vid } });
        toast(t('linked'), 'ok'); loadMdDrafts();
      } catch (e) { toast(e.message, 'err'); }
    }));
  }

  function mdReadForm() {
    return {
      title: $('#mdTitle').value.trim(),
      description: $('#mdDesc').value,
      tags: $('#mdTags').value.split(',').map((s) => s.trim()).filter(Boolean),
      niche: $('#mdNiche').value.trim() || null,
      channelId: $('#mdChannel').value.trim() || null,
      isShort: $('#mdShort').checked,
    };
  }

  $('#mdReviewBtn').addEventListener('click', async () => {
    const body = mdReadForm();
    if (!body.title) return toast(t('enterTitle'), 'err');
    try {
      renderMdResult(await api('/api/metadata/review', { method: 'POST', body }));
    } catch (e) { toast(e.message, 'err'); }
  });

  $('#mdSaveBtn').addEventListener('click', async () => {
    const body = mdReadForm();
    if (!body.title) return toast(t('enterTitle'), 'err');
    body.review = mdLastReview;
    try {
      await api('/api/drafts', { method: 'POST', body });
      toast(t('draftSaved'), 'ok');
      await loadMdDrafts();
    } catch (e) { toast(e.message, 'err'); }
  });

  await loadMdDrafts();
}

export { viewMetadata };
