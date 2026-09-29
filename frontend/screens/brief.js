/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, num, compact, mult, ago, esc, toast, tile, sectionHead, notice, empty } from '../ui.js';
import { view } from '../shared.js';

/* ------------------------------------------------------- Бриф из outlier */

const VERDICT = {
  free: ['тема свободна', 'chip-good'],
  recent: ['недавно снимали', 'chip-warn'],
  proven: ['спрос доказан', 'chip-good'],
  flopped: ['уже проваливалась', 'chip-bad'],
};
const PART = {
  source: 'Исходное видео', hook: 'Крючок', why_viral: 'Почему выстрелило', titles: 'Заголовки', angle: 'Паттерны ниши',
  overlap: 'Занята ли тема', thumbnailReferences: 'Референсы превью',
};

function skippedBlock(list) {
  if (!list.length) return '';
  return `<div class="card">${sectionHead('Что не удалось собрать', 'каждая пропущенная часть названа честно, шаблоны вместо неё не подставляются')}
    <ul class="digest-list">${list.map((s) =>
      `<li style="white-space:normal"><b>${esc(PART[s.part] || s.part)}</b> — ${esc(s.reason)}</li>`).join('')}</ul></div>`;
}

function briefHtml(b, llm) {
  const s = b.source;
  const ov = b.overlap;
  const v = ov && (VERDICT[ov.verdict] || [ov.verdict, '']);
  return `
    <div class="card">
      ${sectionHead(b.gapTopic ? 'Бриф под пробел' : 'Бриф из outlier', 'исследование, не сценарий: выберите СВОЙ угол, а не копию')}
      ${b.gapTopic ? `<div class="section-sub">Вопрос зрителей: <b>${esc(b.gapTopic)}</b>. Проверка темы и заголовки — про него,
        видео ниже — источник, под которым спрашивали.</div>` : ''}
      <div class="tiles">
        ${tile('Множитель', mult(s.outlierScore), esc(s.outlierBand || ''))}
        ${tile('Просмотры', compact(s.views))}
        ${tile('Формат', s.isShort ? 'Short' : 'длинное', s.lengthSeconds ? `${num(s.lengthSeconds)} с` : '')}
        ${ov ? tile('Тема', `<span class="chip ${v[1]}">${esc(v[0])}</span>`,
            `${num(ov.matchCount)} похожих в базе`) : ''}
      </div>
      <div class="vcard-title" style="margin-top:10px"><a href="https://www.youtube.com/watch?v=${esc(s.videoId)}" target="_blank" rel="noopener">${esc(s.title)}</a></div>
      <div class="vcard-meta"><a href="#/channel/${esc(s.channelId)}">${esc(s.channelTitle || s.channelId)}</a>
        ${b.niche ? ` · ниша ${esc(b.niche)}` : ''}${s.publishedAt ? ` · ${ago(s.publishedAt)}` : ''}</div>
    </div>

    <div class="card">
      ${sectionHead('Крючок', 'первые ~75 слов вставленного транскрипта (около 30 секунд)')}
      ${b.hook.available ? `<blockquote class="brief-quote">${esc(b.hook.text)}</blockquote>`
        : `<div class="section-sub">${esc(b.hook.action)}</div>`}
    </div>

    ${b.why ? `<div class="card">${sectionHead('Почему выстрелило', 'разбор LLM по данным канала')}
      <ul class="digest-list">${(b.why.hooks || []).map((h) => `<li style="white-space:normal">${esc(h)}</li>`).join('')}</ul>
      ${b.why.replicable_formula ? `<div class="section-sub">Формула: ${esc(b.why.replicable_formula)}</div>` : ''}
      ${b.why.timing_factor ? `<div class="section-sub">Время: ${esc(b.why.timing_factor)}</div>` : ''}</div>` : ''}

    <div class="card">
      ${sectionHead('Заголовки', b.titles.suggestions.length ? 'кандидаты LLM с оценкой' : 'новые заголовки без LLM не генерируются')}
      ${b.titles.suggestions.length ? `<ul class="digest-list">${b.titles.suggestions.map((t) =>
        `<li style="white-space:normal"><b>${esc(t.score)}</b> · ${esc(t.title)}</li>`).join('')}</ul>` : ''}
      ${b.titles.patternSkeletons.length ? `<div class="section-sub">Фразы, которые у outlier'ов ниши встречаются чаще:
        ${b.titles.patternSkeletons.map((k) => `<span class="chip">${esc(k)}</span>`).join(' ')}</div>` : ''}
      ${b.angle.matchedInTitle.length ? `<div class="section-sub">В этом заголовке уже есть: ${
        b.angle.matchedInTitle.map((k) => `<span class="chip chip-good">${esc(k)}</span>`).join(' ')}</div>` : ''}
      ${b.angle.bestTime.length ? `<div class="section-sub">Лучшее время публикации в нише: ${
        b.angle.bestTime.map((t) => `${esc(t.weekday)} ${String(t.hour).padStart(2, '0')}:00`).join(', ')} (UTC)</div>` : ''}
    </div>

    ${ov && ov.matches.length ? `<div class="card">${sectionHead(b.gapTopic ? 'Уже снято по этому вопросу' : 'Уже снято по этой теме', 'без самого исходного видео')}
      <ul class="digest-list">${ov.matches.slice(0, 6).map((m) =>
        `<li style="white-space:normal"><a href="https://www.youtube.com/watch?v=${esc(m.videoId)}" target="_blank" rel="noopener">${esc(m.title)}</a>
          · ${esc(m.channelTitle || '')} · ×${esc(m.outlierScore ?? '—')}</li>`).join('')}</ul></div>` : ''}

    ${b.thumbnailReferences.length ? `<div class="card">${sectionHead('Референсы превью', 'похожие видео из вашей базы')}
      <div class="cards">${b.thumbnailReferences.map((r) => `<article class="vcard">
        <a class="thumb" href="https://www.youtube.com/watch?v=${esc(r.videoId)}" target="_blank" rel="noopener">
          ${r.thumbnail ? `<img src="${esc(r.thumbnail)}" alt="" loading="lazy">` : '<div class="thumb-fallback">без обложки</div>'}</a>
        <div class="vcard-title">${esc(r.title)}</div>
        <div class="vcard-meta">${compact(r.views)} просмотров · сходство ${Math.round((r.similarity || 0) * 100)}%</div>
      </article>`).join('')}</div></div>` : ''}

    ${skippedBlock(b.skipped)}

    <div class="card" id="briefActions">
      ${b.draftId ? `<div class="section-sub">Сохранено в черновики (№${b.draftId}). Дальше — <a href="#/metadata">разбор метаданных</a>
        и <a href="#/titles">проверка заголовков</a>.</div>`
        : `<button class="btn" id="saveBrief" type="button">Сохранить в черновики</button>
           ${llm ? '' : '<button class="btn btn-ghost" id="withLlm" type="button">Добавить LLM-разбор (может стоить денег)</button>'}
           <div class="section-sub" style="margin-top:8px">Сохранение создаёт черновик со ссылкой на это видео и ставит в очередь
             недостающий транскрипт. Квота YouTube не тратится.</div>`}
    </div>`;
}

async function viewBrief(videoId, gapTopic = null) {
  let useLlm = false;
  const load = async (save) => api('/api/briefs', { method: 'POST', body: { videoId, save, useLlm, gapTopic } });
  const draw = (b) => {
    view.innerHTML = briefHtml(b, useLlm);
    const saveBtn = $('#saveBrief');
    if (saveBtn) saveBtn.addEventListener('click', async () => {
      saveBtn.disabled = true;
      try { draw(await load(true)); toast('Бриф сохранён в черновики', 'ok'); }
      catch (e) { saveBtn.disabled = false; toast(e.message, 'err'); }
    });
    const llmBtn = $('#withLlm');
    if (llmBtn) llmBtn.addEventListener('click', async () => {
      llmBtn.disabled = true; llmBtn.textContent = 'Считаю…';
      useLlm = true;
      try { draw(await load(false)); }
      catch (e) { useLlm = false; llmBtn.disabled = false; llmBtn.textContent = 'Добавить LLM-разбор (может стоить денег)'; toast(e.message, 'err'); }
    });
  };
  try {
    draw(await load(false));
  } catch (e) {
    view.innerHTML = notice(`<div><b>Бриф не собрался.</b><br>${esc(e.message)}</div>`, 'error')
      + empty('видео нет в базе — соберите канал и откройте бриф снова');
  }
}

export { viewBrief };
