/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, compact, mult, ago, esc, toast, sectionHead, empty, table, state } from '../ui.js';
import { view } from '../shared.js';

/* ------------------------------------------------------- Проверка идей (17) */

let ideasText = '';
let ideasFilters = { recentDays: 90, provenOutlier: 2.0, flopOutlier: 0.5 };
let ideasResult = null;

const IDEA_VERDICT_LABEL = {
  free: 'свободна', recent: 'недавно снимали',
  proven: 'доказан спрос', flopped: 'слабо/провал',
};
const IDEA_VERDICT_CLASS = { proven: 'chip-good', recent: 'chip-bad', flopped: 'chip-bad' };

function ideaVerdictChip(v) {
  return `<span class="chip ${IDEA_VERDICT_CLASS[v] || ''}">${esc(IDEA_VERDICT_LABEL[v] || v)}</span>`;
}

function exportIdeasCsv(result) {
  if (!result) return;
  const header = ['idea', 'verdict', 'daysSinceLastCoverage', 'bestOutlierScore', 'matchCount'];
  const rows = [header, ...result.ideas.map((i) => [
    i.idea, i.verdict, i.daysSinceLastCoverage ?? '', i.bestOutlierScore ?? '', i.matchCount,
  ])];
  const csv = rows.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'idea-check.csv';
  a.click();
  URL.revokeObjectURL(a.href);
}

function ideasResultsSection(result) {
  if (!result) return '';
  return `
    <div class="card">
      ${sectionHead('Результаты', result.hint || '',
        `<button class="btn btn-ghost btn-sm" id="ideasExportCsv">Экспорт CSV</button>`)}
      <div class="rows">${result.ideas.map((i) => `
        <details style="border:1px solid var(--border);border-radius:10px;padding:10px 12px">
          <summary style="cursor:pointer;display:flex;justify-content:space-between;align-items:center;gap:12px">
            <span>${esc(i.idea)}</span>
            <span style="display:flex;gap:8px;align-items:center;flex:none">
              ${ideaVerdictChip(i.verdict)}
              <span class="chip">${i.matchCount} найдено</span>
            </span>
          </summary>
          <div style="margin-top:10px">
            ${i.matches.length ? table([
              { label: 'Видео', wrap: true, render: (m) =>
                `<a href="https://www.youtube.com/watch?v=${esc(m.videoId)}" target="_blank" rel="noopener">${esc(m.title)}</a>` },
              { label: 'Канал', render: (m) => esc(m.channelTitle || m.channelId) },
              { label: 'Просмотры', num: true, render: (m) => compact(m.views) },
              { label: 'Outlier', num: true, render: (m) => mult(m.outlierScore) },
              { label: 'Опубликовано', render: (m) => ago(m.publishedAt) },
            ], i.matches) : empty('видео не найдены')}
          </div>
        </details>`).join('')}</div>
    </div>`;
}

async function viewIdeas() {
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Проверка идей', 'по одной идее на строку — свободна / недавно снимали / доказан спрос / провал')}
      <textarea id="ideasInput" rows="8" placeholder="car wash&#10;funeral home&#10;pet grooming"
        style="width:100%;font-family:inherit;resize:vertical">${esc(ideasText)}</textarea>
      <div style="display:flex;gap:16px;margin-top:10px;flex-wrap:wrap;align-items:center">
        <label style="font-size:12px;color:var(--muted)">recent, дней
          <input type="number" id="ideasRecentDays" value="${ideasFilters.recentDays}" style="width:70px"></label>
        <label style="font-size:12px;color:var(--muted)">proven outlier ≥
          <input type="number" step="0.1" id="ideasProvenOutlier" value="${ideasFilters.provenOutlier}" style="width:70px"></label>
        <label style="font-size:12px;color:var(--muted)">flop outlier ≤
          <input type="number" step="0.1" id="ideasFlopOutlier" value="${ideasFilters.flopOutlier}" style="width:70px"></label>
        <button class="btn" id="ideasSubmit">Проверить</button>
      </div>
    </div>
    ${ideasResultsSection(ideasResult)}`;

  $('#ideasSubmit').addEventListener('click', async () => {
    ideasText = $('#ideasInput').value;
    const ideas = ideasText.split('\n').map((s) => s.trim()).filter(Boolean);
    if (!ideas.length) { toast('Введите хотя бы одну идею', 'err'); return; }
    ideasFilters = {
      recentDays: Number($('#ideasRecentDays').value) || 90,
      provenOutlier: Number($('#ideasProvenOutlier').value) || 2.0,
      flopOutlier: Number($('#ideasFlopOutlier').value) || 0.5,
    };
    const btn = $('#ideasSubmit');
    btn.disabled = true; btn.textContent = 'Проверяю…';
    try {
      ideasResult = await api('/api/ideas/check', { method: 'POST', body: {
        ideas, niche: state.niche || undefined,
        recentDays: ideasFilters.recentDays, provenOutlier: ideasFilters.provenOutlier,
        flopOutlier: ideasFilters.flopOutlier,
      } });
      viewIdeas();
    } catch (e) {
      toast(e.message, 'err');
      btn.disabled = false; btn.textContent = 'Проверить';
    }
  });

  const csvBtn = $('#ideasExportCsv');
  if (csvBtn) csvBtn.addEventListener('click', () => exportIdeasCsv(ideasResult));
}

export { viewIdeas };
