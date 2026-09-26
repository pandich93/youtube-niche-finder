/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, num, compact, esc, tile, sectionHead, empty, table, state } from '../ui.js';
import { view, plabel, base, render } from '../shared.js';

/* --------------------------------------------------------- Ключевые слова */

async function viewKeywords() {
  const semantic = localStorage.getItem('nf.keywordsMode') === 'semantic';
  const d = await api(`/api/keywords${q({ ...base(), sort_by: 'trend', top_n: 60, min_videos: 2,
    keywords_mode: semantic ? 'semantic' : 'ngram' })}`);
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Трендовые ключевые слова', plabel(state.period), `
        <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--muted)">
          <input type="checkbox" id="semanticModeToggle" ${semantic ? 'checked' : ''}>
          смысловой режим (слияние синонимов через эмбеддинги)
        </label>`)}
      <div class="tiles">
        ${tile('Видео в анализе', num(d.videosAnalysed))}
        ${tile('В предыдущем окне', num(d.previousWindowVideos))}
        ${tile('Базовая доля outlier', `${d.outlierBaseRate}%`, 'сколько видео вообще пробивают')}
        ${tile('Покрытие тегами', `${d.tagCoveragePercent}%`, 'теги проставляют не все авторы')}
      </div>
    </div>
    <div class="card">
      ${d.keywords.length ? table([
        { label: 'Фраза', wrap: true, render: (r) => esc(r.keyword) },
        { label: 'Opportunity', num: true, render: (r) => r.opportunityScore != null
            ? `<span class="chip ${r.opportunityScore >= 70 ? 'chip-good' : r.opportunityScore < 30 ? 'chip-bad' : ''}">${r.opportunityScore}</span>`
            : '—' },
        { label: 'Видео', num: true, render: (r) => num(r.videos) },
        { label: 'Momentum', num: true, render: (r) => r.momentum ?? '—' },
        { label: 'Lift', num: true, render: (r) => r.outlierLift ?? '—' },
        { label: 'Доля', num: true, render: (r) => `${r.share}%` },
        { label: 'Медиана просмотров', num: true, render: (r) => compact(r.medianViews) },
        { label: 'Новая', render: (r) => (r.isNew ? 'да' : '') },
        { label: 'Пример', wrap: true, render: (r) => esc(r.examples?.[0]?.title || '') },
      ], d.keywords) : empty(d.hint || 'нет фраз над порогом')}
      <div class="section-sub" style="margin-top:14px">
        <b>Opportunity</b> — 0-100, для удобства чтения: trendScore, растянутый по
        мин/макс среди фраз именно этого экрана. Это НЕ настоящий keyword score вроде
        vidIQ (для него нужен бы объём поиска YouTube, а такого API не существует) —
        сравнивать это число между разными запросами или окнами нельзя, только внутри
        одной текущей выдачи.<br>
        <b>momentum</b> — доля фразы в этом окне против доли в предыдущем таком же
        (со сглаживанием); больше 2 значит быстрый рост.<br>
        <b>outlierLift</b> — во сколько раз фраза повышает шансы видео пробить;
        больше 1.5 значит, что фраза реально коррелирует с пробитиями.
      </div>
    </div>`;

  $('#semanticModeToggle').addEventListener('change', (e) => {
    localStorage.setItem('nf.keywordsMode', e.target.checked ? 'semantic' : 'ngram');
    render();
  });
}

export { viewKeywords };
