/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, esc, toast, sectionHead, notice, state } from '../ui.js';
import { view } from '../shared.js';

/* ---------------------------------------------------- Проверить заголовки (09) */

let titleScoreResult = null;
let titleSuggestResult = null;

function titleResultRow(t) {
  const cls = t.score >= 70 ? 'chip-good' : t.score < 40 ? 'chip-bad' : '';
  return `
    <div class="row" style="align-items:flex-start">
      <div class="row-main">
        <div class="row-title">${esc(t.title)} <span class="chip ${cls}">${t.score}</span></div>
        <div class="row-sub">длина ${t.signals.length}${t.signals.hasNumber ? ' · есть число' : ''}
          ${t.signals.matchedPatterns.length ? ` · паттерны: ${t.signals.matchedPatterns.map(esc).join(', ')}` : ''}
          ${t.signals.isLikelyDuplicate ? ' · похоже на уже вышедшее видео' : ''}</div>
        ${t.strengths?.length ? `<div class="row-sub">+ ${t.strengths.map(esc).join(' · ')}</div>` : ''}
        ${t.risks?.length ? `<div class="row-sub">− ${t.risks.map(esc).join(' · ')}</div>` : ''}
        ${t.improved ? `<div class="row-sub"><b>Улучшенный вариант:</b> ${esc(t.improved)}</div>` : ''}
      </div>
    </div>`;
}

async function viewTitleScoring() {
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Проверить заголовки', 'оценка по шаблонам ниши + LLM (если включён); ниша берётся из глобального фильтра')}
      <textarea id="titleCandidatesInput" rows="6" placeholder="по одному заголовку на строку"
        style="width:100%;font-family:inherit;resize:vertical"></textarea>
      <div style="margin-top:10px">
        <button class="btn" id="titleScoreBtn">Оценить</button>
      </div>
      ${titleScoreResult ? `<div style="margin-top:12px">
        ${titleScoreResult.hint ? notice(esc(titleScoreResult.hint)) : ''}
        <div class="rows">${titleScoreResult.titles.map(titleResultRow).join('')}</div>
      </div>` : ''}
    </div>

    <div class="card">
      ${sectionHead('Сгенерировать варианты', 'нужен настроенный LLM_PROVIDER')}
      <div style="display:flex;gap:10px">
        <input type="text" id="titleTopicInput" placeholder="тема видео" style="flex:1">
        <button class="btn" id="titleSuggestBtn">Сгенерировать</button>
      </div>
      ${titleSuggestResult ? `<div style="margin-top:12px">
        ${titleSuggestResult.hint ? notice(esc(titleSuggestResult.hint)) : ''}
        <div class="rows">${(titleSuggestResult.titles || []).map(titleResultRow).join('')}</div>
      </div>` : ''}
    </div>`;

  $('#titleScoreBtn').addEventListener('click', async () => {
    const candidates = $('#titleCandidatesInput').value.split('\n').map((s) => s.trim()).filter(Boolean);
    if (!candidates.length) { toast('Введите хотя бы один заголовок', 'err'); return; }
    if (!state.niche) { toast('Выберите нишу в фильтре сверху', 'err'); return; }
    try {
      titleScoreResult = await api('/api/titles/score', { method: 'POST',
        body: { candidates, niche: state.niche } });
      viewTitleScoring();
    } catch (e) { toast(e.message, 'err'); }
  });
  $('#titleSuggestBtn').addEventListener('click', async () => {
    const topic = $('#titleTopicInput').value.trim();
    if (!topic) { toast('Введите тему', 'err'); return; }
    if (!state.niche) { toast('Выберите нишу в фильтре сверху', 'err'); return; }
    try {
      titleSuggestResult = await api('/api/titles/suggest', { method: 'POST',
        body: { topic, niche: state.niche, n: 8 } });
      viewTitleScoring();
    } catch (e) { toast(e.message, 'err'); }
  });
}

export { viewTitleScoring };
