/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, esc, toast, sectionHead, notice, state } from '../ui.js';
import { view } from '../shared.js';
import { hookResultHtml } from '../hook_view.js';
import { dict } from '../i18n.js';
import ru from '../i18n/ru/titles.js';
import en from '../i18n/en/titles.js';

const t = dict({ ru, en });

/* ---------------------------------------------------- Проверить заголовки (09) */

let titleScoreResult = null;
let titleSuggestResult = null;
let hookDraftResult = null;   // the draft text itself is never kept, only its score

function titleResultRow(x) {
  const cls = x.score >= 70 ? 'chip-good' : x.score < 40 ? 'chip-bad' : '';
  return `
    <div class="row" style="align-items:flex-start">
      <div class="row-main">
        <div class="row-title">${esc(x.title)} <span class="chip ${cls}">${x.score}</span></div>
        <div class="row-sub">${t('length', { len: x.signals.length })}${x.signals.hasNumber ? ` · ${t('hasNumber')}` : ''}
          ${x.signals.matchedPatterns.length ? ` · ${t('patterns', { list: x.signals.matchedPatterns.map(esc).join(', ') })}` : ''}
          ${x.signals.isLikelyDuplicate ? ` · ${t('likelyDuplicate')}` : ''}</div>
        ${x.strengths?.length ? `<div class="row-sub">+ ${x.strengths.map(esc).join(' · ')}</div>` : ''}
        ${x.risks?.length ? `<div class="row-sub">− ${x.risks.map(esc).join(' · ')}</div>` : ''}
        ${x.improved ? `<div class="row-sub">${t('improved', { title: esc(x.improved) })}</div>` : ''}
      </div>
    </div>`;
}

async function viewTitleScoring() {
  view.innerHTML = `
    <div class="card">
      ${sectionHead(t('scoreTitle'), t('scoreSub'))}
      <textarea id="titleCandidatesInput" rows="6" placeholder="${esc(t('candidatesPlaceholder'))}"
        style="width:100%;font-family:inherit;resize:vertical"></textarea>
      <div style="margin-top:10px">
        <button class="btn" id="titleScoreBtn">${t('score')}</button>
      </div>
      ${titleScoreResult ? `<div style="margin-top:12px">
        ${titleScoreResult.hint ? notice(esc(titleScoreResult.hint)) : ''}
        <div class="rows">${titleScoreResult.titles.map(titleResultRow).join('')}</div>
      </div>` : ''}
    </div>

    <div class="card">
      ${sectionHead(t('suggestTitle'), t('suggestSub'))}
      <div style="display:flex;gap:10px">
        <input type="text" id="titleTopicInput" placeholder="${esc(t('topicPlaceholder'))}" style="flex:1">
        <button class="btn" id="titleSuggestBtn">${t('generate')}</button>
      </div>
      ${titleSuggestResult ? `<div style="margin-top:12px">
        ${titleSuggestResult.hint ? notice(esc(titleSuggestResult.hint)) : ''}
        <div class="rows">${(titleSuggestResult.titles || []).map(titleResultRow).join('')}</div>
      </div>` : ''}
    </div>

    <div class="card">
      ${sectionHead(t('hookTitle'), t('hookSub'))}
      <textarea id="hookDraftInput" rows="5" placeholder="${esc(t('hookPlaceholder'))}"
        style="width:100%;font-family:inherit;resize:vertical"></textarea>
      <div style="margin-top:10px">
        <button class="btn" id="hookScoreBtn">${t('score')}</button>
      </div>
      ${hookDraftResult ? `<div style="margin-top:12px">${hookResultHtml(hookDraftResult)}</div>` : ''}
    </div>`;

  $('#titleScoreBtn').addEventListener('click', async () => {
    const candidates = $('#titleCandidatesInput').value.split('\n').map((s) => s.trim()).filter(Boolean);
    if (!candidates.length) { toast(t('needTitle'), 'err'); return; }
    if (!state.niche) { toast(t('needNiche'), 'err'); return; }
    try {
      titleScoreResult = await api('/api/titles/score', { method: 'POST',
        body: { candidates, niche: state.niche } });
      viewTitleScoring();
    } catch (e) { toast(e.message, 'err'); }
  });
  $('#hookScoreBtn').addEventListener('click', async () => {
    const text = $('#hookDraftInput').value.trim();
    if (!text) { toast(t('needHook'), 'err'); return; }
    try {
      hookDraftResult = await api('/api/hooks/score', { method: 'POST',
        body: { text, niche: state.niche || undefined } });
      viewTitleScoring();
    } catch (e) { toast(e.message, 'err'); }
  });
  $('#titleSuggestBtn').addEventListener('click', async () => {
    const topic = $('#titleTopicInput').value.trim();
    if (!topic) { toast(t('needTopic'), 'err'); return; }
    if (!state.niche) { toast(t('needNiche'), 'err'); return; }
    try {
      titleSuggestResult = await api('/api/titles/suggest', { method: 'POST',
        body: { topic, niche: state.niche, n: 8 } });
      viewTitleScoring();
    } catch (e) { toast(e.message, 'err'); }
  });
}

export { viewTitleScoring };
