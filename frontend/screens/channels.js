/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, sectionHead, empty, channelRow, state, yppSelect } from '../ui.js';
import { view, plabel, base, render } from '../shared.js';
import { dict } from '../i18n.js';
import ru from '../i18n/ru/channels.js';
import en from '../i18n/en/channels.js';

const t = dict({ ru, en });

/* --------------------------------------------------------- Outlier-каналы */

async function viewChannels() {
  const p = Object.assign(
    { min_multiplier: 1.5, min_subscribers: '', max_subscribers: '', min_ypp_status: '' },
    JSON.parse(localStorage.getItem('nf.channels') || '{}'));
  const d = await api(`/api/outlier-channels${q({
    ...base(), min_multiplier: p.min_multiplier,
    min_subscribers: p.min_subscribers || null, max_subscribers: p.max_subscribers || null,
    min_ypp_status: p.min_ypp_status || null, limit: 50,
  })}`);
  view.innerHTML = `
    <div class="card">
      ${sectionHead(t('title'), t('sub', { period: plabel(state.period) }))}
      <div class="form-row">
        <label class="field"><span class="field-label">${t('minMultiplier')}</span>
          <input type="number" id="fcMult" value="${p.min_multiplier}" step="0.5"></label>
        <label class="field"><span class="field-label">${t('minSubs')}</span>
          <input type="number" id="fcMinSubs" value="${p.min_subscribers}" step="1000"></label>
        <label class="field"><span class="field-label">${t('maxSubs')}</span>
          <input type="number" id="fcMaxSubs" value="${p.max_subscribers}" step="1000"></label>
        ${yppSelect('fcYpp', p.min_ypp_status)}
        <button class="btn" id="applyChannels" type="button">${t('apply')}</button>
      </div>
      <div class="section-sub" style="margin-top:10px">${t('note')}</div>
    </div>
    <div class="card">
      ${d.channels.length ? `<div class="rows">${d.channels.map(channelRow).join('')}</div>`
                          : empty(d.hint || t('empty'))}
    </div>`;

  $('#applyChannels').addEventListener('click', () => {
    localStorage.setItem('nf.channels', JSON.stringify({
      min_multiplier: +$('#fcMult').value || 0,
      min_subscribers: $('#fcMinSubs').value ? +$('#fcMinSubs').value : '',
      max_subscribers: $('#fcMaxSubs').value ? +$('#fcMaxSubs').value : '',
      min_ypp_status: $('#fcYpp').value,
    }));
    render();
  });
}

export { viewChannels };
