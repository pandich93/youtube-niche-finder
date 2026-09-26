/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, num, compact, mult, ago, esc, toast, tile, sectionHead, notice, empty, table, state } from '../ui.js';
import { view, plabel } from '../shared.js';

/* ------------------------------------------------------- Разбор метаданных */

const MD_VERDICT_LABEL = { ok: 'ок', warn: 'стоит поправить', unreliable: 'выборка мала' };

function metadataSignalRow(s) {
  const cls = s.verdict === 'ok' ? 'chip-good' : s.verdict === 'warn' ? 'chip-bad' : '';
  return `<div class="row" style="align-items:flex-start">
    <div class="row-main">
      <div class="row-title"><span class="chip ${cls}">${esc(MD_VERDICT_LABEL[s.verdict] || s.verdict)}</span> ${esc(s.id || '')}</div>
      <div class="row-sub">${esc(s.explanation || '')}</div>
    </div>
  </div>`;
}

let mdLastReview = null;

async function viewMetadata() {
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Разбор метаданных', 'проверка черновика заголовка/описания/тегов по собственной базе -- без единого «SEO score», только сигналы со своей выборкой')}
      <div class="form-row">
        <label class="field" style="flex:2 1 320px"><span class="field-label">Заголовок</span>
          <input type="text" id="mdTitle" placeholder="5 секретов нейросетей, о которых молчат"></label>
        <label class="field"><span class="field-label">Ниша</span>
          <input type="text" id="mdNiche" value="${esc(state.niche || '')}" placeholder="ai-explainers"></label>
        <label class="field"><span class="field-label">ID канала (необязательно)</span>
          <input type="text" id="mdChannel" placeholder="UC..."></label>
      </div>
      <div class="form-row" style="margin-top:12px">
        <label class="field" style="flex:2 1 320px"><span class="field-label">Описание</span>
          <textarea id="mdDesc" rows="3" placeholder="00:00 вступление..."></textarea></label>
        <label class="field"><span class="field-label">Теги (через запятую)</span>
          <input type="text" id="mdTags" placeholder="нейросети, ai"></label>
        <label class="field" style="flex:0 0 auto">
          <span class="field-label">Shorts</span>
          <input type="checkbox" id="mdShort" style="width:auto;height:38px"></label>
      </div>
      <div class="form-row" style="margin-top:12px">
        <button class="btn" id="mdReviewBtn" type="button">Проверить</button>
        <button class="btn btn-ghost" id="mdSaveBtn" type="button">Сохранить как черновик</button>
      </div>
    </div>
    <div id="mdResult"></div>
    <div class="card" id="mdDraftsCard">
      ${sectionHead('Сохранённые черновики', 'привяжите к video ID после публикации -- пригодится для проверки, сбылся ли прогноз')}
      <div id="mdDraftsBody">${empty('загрузка…')}</div>
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
        ${sectionHead('Сводка', `${res.sample.videosAnalysed} видео в выборке, из них ${res.sample.outliersInSample} выбросов · ${plabel(res.sample.period)}`)}
        <div class="tiles">
          ${tile('В норме', num(res.summary.ok))}
          ${tile('Стоит поправить', num(res.summary.warn))}
          ${tile('Ненадёжно', num(res.summary.unreliable))}
        </div>
      </div>
      <div class="grid-2">
        <div class="card">
          ${sectionHead('Сигналы')}
          ${res.signals.length ? res.signals.map(metadataSignalRow).join('') : empty('нет сигналов')}
        </div>
        <div class="card">
          ${sectionHead('Структурные паттерны ниши', res.keyPhrase ? `ключевая фраза: «${esc(res.keyPhrase)}»` : 'нет устойчивой ключевой фразы')}
          ${res.structuralPatterns.length ? table([
            { label: 'Признак', render: (r) => esc(r.label) },
            { label: 'Lift', num: true, render: (r) => r.lift != null ? mult(r.lift) : '—' },
            { label: 'Выборка', num: true, render: (r) => num(r.sample) },
            { label: 'В черновике', render: (r) => r.presentInDraft ? 'есть' : 'нет' },
            { label: '', render: (r) => r.verdict === 'unreliable' ? '<span class="chip">выборка мала</span>' : '' },
          ], res.structuralPatterns) : empty('нет данных')}
        </div>
      </div>
      <div class="card">
        ${sectionHead('Похожие темы в базе', 'косинус эмбеддинга черновика против собственной базы -- тема уже забита?')}
        ${res.nearDuplicates.hint ? notice(esc(res.nearDuplicates.hint), '')
          : res.nearDuplicates.near.length ? table([
              { label: 'Видео', wrap: true, render: (r) => `<a href="https://www.youtube.com/watch?v=${esc(r.videoId)}" target="_blank" rel="noopener">${esc(r.title || r.videoId)}</a>` },
              { label: 'Просмотры', num: true, render: (r) => compact(r.views) },
              { label: 'Похожесть', num: true, render: (r) => r.similarity },
            ], res.nearDuplicates.near)
          : empty(`проверено ${res.nearDuplicates.checked} видео -- близких тем не найдено`)}
      </div>`;
  }

  async function loadMdDrafts() {
    const chId = $('#mdChannel')?.value.trim() || null;
    const d = await api(`/api/drafts${q({ channel_id: chId })}`);
    $('#mdDraftsBody').innerHTML = d.drafts.length ? table([
      { label: 'Заголовок', wrap: true, render: (r) => esc(r.title) },
      { label: 'Ниша', render: (r) => esc(r.niche || '—') },
      { label: 'Сохранён', render: (r) => ago(r.createdAt) },
      { label: 'Видео', render: (r) => r.videoId
          ? `<a href="https://www.youtube.com/watch?v=${esc(r.videoId)}" target="_blank" rel="noopener">${esc(r.videoId)}</a>`
          : `<div class="form-row"><input type="text" class="mdLinkVid" data-id="${r.id}" placeholder="video ID" style="width:140px">
             <button class="btn btn-ghost btn-sm mdDoLink" data-id="${r.id}" type="button">привязать</button></div>` },
    ], d.drafts) : empty('пока пусто -- нажмите «Сохранить как черновик» выше');
    $('#mdDraftsBody').querySelectorAll('.mdDoLink').forEach((b) => b.addEventListener('click', async () => {
      const input = $(`.mdLinkVid[data-id="${b.dataset.id}"]`);
      const vid = input.value.trim();
      if (!vid) return toast('Укажите video ID', 'err');
      try {
        await api(`/api/drafts/${b.dataset.id}/link`, { method: 'POST', body: { videoId: vid } });
        toast('Привязано', 'ok'); loadMdDrafts();
      } catch (e) { toast(e.message, 'err'); }
    }));
  }

  function mdReadForm() {
    return {
      title: $('#mdTitle').value.trim(),
      description: $('#mdDesc').value,
      tags: $('#mdTags').value.split(',').map((t) => t.trim()).filter(Boolean),
      niche: $('#mdNiche').value.trim() || null,
      channelId: $('#mdChannel').value.trim() || null,
      isShort: $('#mdShort').checked,
    };
  }

  $('#mdReviewBtn').addEventListener('click', async () => {
    const body = mdReadForm();
    if (!body.title) return toast('Введите заголовок', 'err');
    try {
      renderMdResult(await api('/api/metadata/review', { method: 'POST', body }));
    } catch (e) { toast(e.message, 'err'); }
  });

  $('#mdSaveBtn').addEventListener('click', async () => {
    const body = mdReadForm();
    if (!body.title) return toast('Введите заголовок', 'err');
    body.review = mdLastReview;
    try {
      await api('/api/drafts', { method: 'POST', body });
      toast('Черновик сохранён', 'ok');
      await loadMdDrafts();
    } catch (e) { toast(e.message, 'err'); }
  });

  await loadMdDrafts();
}

export { viewMetadata };
