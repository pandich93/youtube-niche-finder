/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, sectionHead, empty, channelRow, state } from '../ui.js';
import { view, plabel, base, render } from '../shared.js';

/* --------------------------------------------------------- Outlier-каналы */

async function viewChannels() {
  const p = Object.assign(
    { min_multiplier: 1.5, min_subscribers: '', max_subscribers: '' },
    JSON.parse(localStorage.getItem('nf.channels') || '{}'));
  const d = await api(`/api/outlier-channels${q({
    ...base(), min_multiplier: p.min_multiplier,
    min_subscribers: p.min_subscribers || null, max_subscribers: p.max_subscribers || null,
    limit: 50,
  })}`);
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Outlier-каналы', `${plabel(state.period)} · по попаданию в базу — свежие возможности`)}
      <div class="form-row">
        <label class="field"><span class="field-label">Множитель не меньше</span>
          <input type="number" id="fcMult" value="${p.min_multiplier}" step="0.5"></label>
        <label class="field"><span class="field-label">Подписчиков не меньше</span>
          <input type="number" id="fcMinSubs" value="${p.min_subscribers}" step="1000"></label>
        <label class="field"><span class="field-label">Подписчиков не больше</span>
          <input type="number" id="fcMaxSubs" value="${p.max_subscribers}" step="1000"></label>
        <button class="btn" id="applyChannels" type="button">Применить</button>
      </div>
      <div class="section-sub" style="margin-top:10px">Множитель — лучший возрастно-нормированный outlier среди
        видео канала в окне, против медианы предыдущих загрузок этого же канала.
        Полоса: &lt;2x, 2–3x, 3–5x, 5–10x, &gt;10x. Отсортировано по силе множителя;
        сузьте окно периода вверху, чтобы увидеть только самые свежие открытия.</div>
    </div>
    <div class="card">
      ${d.channels.length ? `<div class="rows">${d.channels.map(channelRow).join('')}</div>`
                          : empty(d.hint || 'нет каналов над порогом')}
    </div>`;

  $('#applyChannels').addEventListener('click', () => {
    localStorage.setItem('nf.channels', JSON.stringify({
      min_multiplier: +$('#fcMult').value || 0,
      min_subscribers: $('#fcMinSubs').value ? +$('#fcMinSubs').value : '',
      max_subscribers: $('#fcMaxSubs').value ? +$('#fcMaxSubs').value : '',
    }));
    render();
  });
}

export { viewChannels };
