/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, num, compact, mult, ago, esc, toast, tile, sectionHead, notice, empty, trajectoryChart, compareHref, qmark, outlierTraitsBlock } from '../ui.js';
import { view } from '../shared.js';
import { dict, fmtUtcSlot } from '../i18n.js';
import ru from '../i18n/ru/brief.js';
import en from '../i18n/en/brief.js';

const t = dict({ ru, en });

/* ------------------------------------------------------- Бриф из outlier */

/* Функции, а не константы: подписи переводятся при каждом рендере. */
const verdict = () => ({
  free: [t('verdictFree'), 'chip-good'],
  recent: [t('verdictRecent'), 'chip-warn'],
  proven: [t('verdictProven'), 'chip-good'],
  flopped: [t('verdictFlopped'), 'chip-bad'],
});
const part = () => ({
  source: t('partSource'), hook: t('partHook'), why_viral: t('partWhyViral'), titles: t('partTitles'),
  angle: t('partAngle'), overlap: t('partOverlap'), thumbnailReferences: t('partThumbnailReferences'),
});

function skippedBlock(list) {
  if (!list.length) return '';
  const names = part();
  return `<div class="card">${sectionHead(t('skippedTitle'), t('skippedSub'))}
    <ul class="digest-list">${list.map((s) =>
      `<li style="white-space:normal"><b>${esc(names[s.part] || s.part)}</b> — ${esc(s.reason)}</li>`).join('')}</ul></div>`;
}

function briefHtml(b, llm) {
  const s = b.source;
  const ov = b.overlap;
  const v = ov && (verdict()[ov.verdict] || [ov.verdict, '']);
  return `
    <div class="card">
      ${sectionHead(b.gapTopic ? t('briefGapTitle') : t('briefOutlierTitle'), t('briefSub'))}
      ${b.gapTopic ? `<div class="section-sub">${t('gapQuestion', { topic: esc(b.gapTopic) })}</div>` : ''}
      <div class="tiles">
        ${tile(t('tileMultiplier'), mult(s.outlierScore), esc(s.outlierBand || ''))}
        ${tile(t('tileViews'), compact(s.views))}
        ${tile(t('tileFormat'), s.isShort ? 'Short' : t('formatLong'), s.lengthSeconds ? t('seconds', { s: num(s.lengthSeconds) }) : '')}
        ${ov ? tile(t('tileTopic'), `<span class="chip ${v[1]}">${esc(v[0])}</span>`,
            t('similarInDb', { count: num(ov.matchCount) })) : ''}
      </div>
      <div class="vcard-title" style="margin-top:10px"><a href="https://www.youtube.com/watch?v=${esc(s.videoId)}" target="_blank" rel="noopener">${esc(s.title)}</a></div>
      <div class="vcard-meta"><a href="#/channel/${esc(s.channelId)}">${esc(s.channelTitle || s.channelId)}</a>
        ${b.niche ? t('nicheMeta', { niche: esc(b.niche) }) : ''}${s.publishedAt ? ` · ${ago(s.publishedAt)}` : ''}</div>
    </div>

    <div class="card">
      ${sectionHead(t('hookTitle'), t('hookSub'))}
      ${b.hook.available ? `<blockquote class="brief-quote">${esc(b.hook.text)}</blockquote>`
        : `<div class="section-sub">${esc(b.hook.action)}</div>`}
    </div>

    ${b.why ? `<div class="card">${sectionHead(t('whyTitle'), t('whySub'))}
      <ul class="digest-list">${(b.why.hooks || []).map((h) => `<li style="white-space:normal">${esc(h)}</li>`).join('')}</ul>
      ${b.why.replicable_formula ? `<div class="section-sub">${t('whyFormula', { text: esc(b.why.replicable_formula) })}</div>` : ''}
      ${b.why.timing_factor ? `<div class="section-sub">${t('whyTiming', { text: esc(b.why.timing_factor) })}</div>` : ''}</div>` : ''}

    <div class="card">
      ${sectionHead(t('titlesTitle'), b.titles.suggestions.length ? t('titlesLlm') : t('titlesNoLlm'))}
      ${b.titles.suggestions.length ? `<ul class="digest-list">${b.titles.suggestions.map((sg) =>
        `<li style="white-space:normal"><b>${esc(sg.score)}</b> · ${esc(sg.title)}</li>`).join('')}</ul>` : ''}
      ${b.titles.patternSkeletons.length ? `<div class="section-sub">${t('patternSkeletons', {
        chips: b.titles.patternSkeletons.map((k) => `<span class="chip">${esc(k)}</span>`).join(' '),
      })}</div>` : ''}
      ${b.angle.matchedInTitle.length ? `<div class="section-sub">${t('matchedInTitle', {
        chips: b.angle.matchedInTitle.map((k) => `<span class="chip chip-good">${esc(k)}</span>`).join(' '),
      })}</div>` : ''}
      ${b.angle.bestTime.length ? `<div class="section-sub">${t('bestTime', {
        times: b.angle.bestTime.map((bt) => esc(fmtUtcSlot(bt.weekday, bt.hour))).join(', '),
      })}</div>` : ''}
    </div>

    ${ov && ov.matches.length ? `<div class="card">${sectionHead(b.gapTopic ? t('overlapGapTitle') : t('overlapTopicTitle'), t('overlapSub'))}
      <ul class="digest-list">${ov.matches.slice(0, 6).map((m) =>
        `<li style="white-space:normal"><a href="https://www.youtube.com/watch?v=${esc(m.videoId)}" target="_blank" rel="noopener">${esc(m.title)}</a>
          · ${esc(m.channelTitle || '')} · ×${esc(m.outlierScore ?? '—')}</li>`).join('')}</ul></div>` : ''}

    ${b.thumbnailReferences.length ? `<div class="card">${sectionHead(t('thumbRefsTitle'), t('thumbRefsSub'))}
      <div class="cards">${b.thumbnailReferences.map((r) => `<article class="vcard">
        <a class="thumb" href="https://www.youtube.com/watch?v=${esc(r.videoId)}" target="_blank" rel="noopener">
          ${r.thumbnail ? `<img src="${esc(r.thumbnail)}" alt="" loading="lazy">` : `<div class="thumb-fallback">${t('noThumb')}</div>`}</a>
        <div class="vcard-title">${esc(r.title)}</div>
        <div class="vcard-meta">${t('viewsSimilarity', { views: compact(r.views), pct: Math.round((r.similarity || 0) * 100) })}</div>
      </article>`).join('')}</div></div>` : ''}

    <div id="briefTraj"></div>
    <div id="briefRepeat"></div>
    <div id="briefTraits"></div>
    <div id="briefThumbs"></div>

    ${skippedBlock(b.skipped)}

    <div class="card" id="briefActions">
      ${b.draftId ? `<div class="section-sub">${t('savedDraft', { id: b.draftId })}</div>`
        : `<button class="btn" id="saveBrief" type="button">${t('saveDraft')}</button>
           ${llm ? '' : `<button class="btn btn-ghost" id="withLlm" type="button">${t('addLlm')}</button>`}
           <div class="section-sub" style="margin-top:8px">${t('saveNote')}</div>`}
    </div>`;
}

/* Похожие по картинке (план 13): у кого ещё превью выглядит так же, чужие каналы.
   Без векторов превью блок просто не показывается. */
async function fillSimilarThumbs(videoId) {
  const box = $('#briefThumbs');
  if (!box) return;
  try {
    const d = await api(`/api/videos/${encodeURIComponent(videoId)}/similar-thumbnails?limit=6&exclude_same_channel=true`);
    if (!d.similar || !d.similar.length) return;
    box.innerHTML = `<div class="card">${sectionHead(t('similarThumbsTitle'), t('similarThumbsSub'))}
      <div class="cards">${d.similar.map((r) => `<article class="vcard">
        <a class="thumb" href="https://www.youtube.com/watch?v=${esc(r.videoId)}" target="_blank" rel="noopener">
          ${r.thumbnail ? `<img src="${esc(r.thumbnail)}" alt="" loading="lazy">` : `<div class="thumb-fallback">${t('noThumb')}</div>`}</a>
        <div class="vcard-title">${esc(r.title)}</div>
        <div class="vcard-meta">${t('channelSimilarity', { channel: esc(r.channelTitle || ''), pct: Math.round(r.similarity * 100) })}</div>
      </article>`).join('')}</div>
      <div class="section-sub">${t('clipNote')}</div></div>`;
  } catch { /* не критично: бриф и без этого блока полный */ }
}

/* «Формат повторяем?» (план 21): сработал ли такой ролик у других каналов, или это
   разовая удача одного. Оценка по собранной базе, не данные YouTube. */
const repeatText = () => ({
  repeatable: ['chip-good', t('repeatRepeatable')],
  mixed: ['', t('repeatMixed')],
  one_off: ['chip-bad', t('repeatOneOff')],
  unknown: ['', t('repeatUnknown')],
});

async function fillRepeatability(videoId) {
  const box = $('#briefRepeat');
  if (!box) return;
  try {
    const d = await api(`/api/videos/${encodeURIComponent(videoId)}/repeatability`);
    const [cls, label] = repeatText()[d.verdict] || ['', d.verdict];
    const why = d.reason === 'no-embedding' ? t('repeatNoEmbedding')
      : d.verdict === 'unknown' ? t('repeatFewSimilar', { count: num(d.similarVideos) })
      : t('repeatStats', {
        hitScore: d.hitScore, hit: num(d.channelsHit), total: num(d.channels), median: d.medianChannelBest,
      });
    box.innerHTML = `<div class="card">${sectionHead(t('repeatTitle'), t('repeatSub'), qmark('repeatability'))}
      <div><span class="chip ${cls}">${esc(label)}</span> ${esc(why)}</div>
      ${d.examples?.length ? `<ul class="digest-list">${d.examples.slice(0, 6).map((m) =>
        `<li style="white-space:normal"><a href="https://www.youtube.com/watch?v=${esc(m.videoId)}" target="_blank" rel="noopener">${esc(m.title)}</a>
          · ${esc(m.channelTitle || '')} · ×${esc(m.outlierScore)} · ${t('similarityPct', { pct: Math.round((m.similarity || 0) * 100) })}</li>`).join('')}</ul>` : ''}
      ${d.titleOpening?.opening ? `<div class="section-sub">${t('titleOpening', {
        opening: esc(d.titleOpening.opening), count: num(d.titleOpening.otherChannels),
      })}</div>` : ''}
      <div class="section-sub">${t('repeatCaveat')}</div></div>`;
  } catch { /* не критично: бриф и без этого блока полный */ }
}

/* «Что общего у выстреливших» (план 29): признаки outlier'ов ниши того же формата,
   что исходное видео. Без ниши блок не показывается. */
async function fillOutlierTraits(b) {
  const box = $('#briefTraits');
  if (!box || !b.niche) return;
  try {
    const d = await api(`/api/niches/${encodeURIComponent(b.niche)}/outlier-traits`);
    box.innerHTML = outlierTraitsBlock(d, b.source?.isShort ? 'short' : 'long');
  } catch { /* не критично: бриф и без этого блока полный */ }
}

/* Траектория исходного видео (план 20): как оно набирало просмотры по сравнению
   с обычным видео своего канала. Без снимков блок не показывается. */
async function fillTrajectory(videoId) {
  const box = $('#briefTraj');
  if (!box) return;
  try {
    const d = await api(`/api/videos/trajectory?ids=${encodeURIComponent(videoId)}`);
    const v = d.videos[0];
    if (!v || v.points.length < 2) return;
    box.innerHTML = `<div class="card">${sectionHead(t('trajectoryTitle'), t('trajectorySub'),
        `${qmark('expectedCurve')} <a class="btn btn-ghost btn-sm" href="${compareHref(videoId)}">${t('compareOthers')}</a>`)}
      ${trajectoryChart(d.videos)}</div>`;
  } catch { /* не критично: бриф и без этого блока полный */ }
}

async function viewBrief(videoId, gapTopic = null) {
  let useLlm = false;
  const load = async (save) => api('/api/briefs', { method: 'POST', body: { videoId, save, useLlm, gapTopic } });
  const draw = (b) => {
    view.innerHTML = briefHtml(b, useLlm);
    fillSimilarThumbs(videoId);
    fillRepeatability(videoId);
    fillOutlierTraits(b);
    fillTrajectory(videoId);
    const saveBtn = $('#saveBrief');
    if (saveBtn) saveBtn.addEventListener('click', async () => {
      saveBtn.disabled = true;
      try { draw(await load(true)); toast(t('briefSaved'), 'ok'); }
      catch (e) { saveBtn.disabled = false; toast(e.message, 'err'); }
    });
    const llmBtn = $('#withLlm');
    if (llmBtn) llmBtn.addEventListener('click', async () => {
      llmBtn.disabled = true; llmBtn.textContent = t('computing');
      useLlm = true;
      try { draw(await load(false)); }
      catch (e) { useLlm = false; llmBtn.disabled = false; llmBtn.textContent = t('addLlm'); toast(e.message, 'err'); }
    });
  };
  try {
    draw(await load(false));
  } catch (e) {
    view.innerHTML = notice(`<div>${t('briefFailed', { error: esc(e.message) })}</div>`, 'error')
      + empty(t('briefFailedEmpty'));
  }
}

export { viewBrief };
