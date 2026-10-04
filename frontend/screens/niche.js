/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, num, compact, mult, ago, esc, toast, sectionHead, notice, empty, barList, scatterChart, sponsorBlock, state, nicheTemplateRiskBlock, nichePolicyBlock, outlierTraitsBlock } from '../ui.js';
import { view, plabel, nicheOverviewBlock, render } from '../shared.js';

/* Доля хитов по тегам одной группы -- barList из tag_stats. Группа вводится
   свободным текстом (теги произвольные: theme/trigger/format/...), поэтому
   запоминаем последнюю выбранную в localStorage. */
function tagStatsSection(stats, tagGroup) {
  const body = stats.found === false
    ? empty(stats.hint || 'тегов в этой группе пока нет')
    : barList((stats.tags || []).map((t) => ({
        name: t.tag, value: t.hitRate, display: `${t.hitRate}%`,
        tip: `${t.tag}: ${t.videos} видео, hitRate ${t.hitRate}%, lift ${t.lift ?? '—'}`,
      })));
  return `
    <div class="card">
      ${sectionHead('Доля хитов по тегам', stats.found === false ? '' :
          `группа «${esc(tagGroup)}» · база по нише ${stats.baseRate}%`,
        `<input type="text" id="tagGroupInput" value="${esc(tagGroup)}"
           placeholder="группа тегов" style="width:140px">`)}
      ${body}
    </div>`;
}

/* Ручная правка тегов у видео -- каждое изменение шлёт replace:true с полным
   новым набором тегов этой группы для видео (см. application/tags.py:
   replace сохраняет защищённые manual/claude-mcp теги даже когда сам вызов
   идёт с другим source, здесь source всегда 'manual'). */
/* Панель Инсайты из комментариев (этап 04) -- строго по клику, никогда не
   автоматически: тратит квоту YouTube + может тратить деньги на LLM. */
function insightsPanelHtml(d) {
  if (d.hint) return notice(esc(d.hint));
  const pains = d.pains || [];
  const requests = d.requests || [];
  const ideas = d.video_ideas || [];
  const s = d.sentiment;
  const meta = d.cached ? 'из кеша' : `квота потрачена: ${d.quotaSpent ?? 0}`;
  if (!pains.length && !requests.length && !ideas.length) {
    return empty(`LLM не нашёл значимых сигналов в комментариях (${meta})`);
  }
  return `
    <div style="border:1px solid var(--border);border-radius:10px;padding:12px">
      ${s ? `<div class="row-sub" style="margin-bottom:8px">
        Тональность: ${Math.round(s.positive * 100)}% позитив ·
        ${Math.round(s.neutral * 100)}% нейтрально ·
        ${Math.round(s.negative * 100)}% негатив · ${meta}</div>` : ''}
      ${pains.length ? `<div style="margin-bottom:8px"><b>Боли</b>
        <ul style="margin:4px 0 0 18px">${pains.map((p) => `<li>${esc(p.text)}
          (~${p.count_estimate})${p.quotes?.[0] ? ` <span class="row-sub">«${esc(p.quotes[0])}»</span>` : ''}</li>`).join('')}</ul></div>` : ''}
      ${requests.length ? `<div style="margin-bottom:8px"><b>Запросы</b>
        <ul style="margin:4px 0 0 18px">${requests.map((r) => `<li>${esc(r.topic)}
          <span class="row-sub">«${esc(r.evidence)}»</span></li>`).join('')}</ul></div>` : ''}
      ${ideas.length ? `<div><b>Идеи видео</b>
        <ul style="margin:4px 0 0 18px">${ideas.map((i) => `<li>${esc(i.title)}
          <span class="row-sub">— ${esc(i.why)}</span></li>`).join('')}</ul></div>` : ''}
    </div>`;
}

function wireCommentInsights() {
  view.querySelectorAll('.insights-btn').forEach((btn) => btn.addEventListener('click', async () => {
    const videoId = btn.dataset.insightsVideoId;
    const panel = view.querySelector(`.insights-panel[data-insights-video-id="${CSS.escape(videoId)}"]`);
    if (!panel) return;
    if (!panel.hidden) { panel.hidden = true; return; }
    panel.hidden = false;
    panel.innerHTML = empty('Загружаю… (тратит квоту YouTube и, если включён LLM, деньги)');
    btn.disabled = true;
    try {
      const d = await api(`/api/videos/${encodeURIComponent(videoId)}/insights`,
        { method: 'POST', body: {} });
      panel.innerHTML = insightsPanelHtml(d);
    } catch (e) {
      panel.innerHTML = notice(esc(e.message));
    } finally {
      btn.disabled = false;
    }
  }));
  const nicheBtn = $('#nicheInsightsBtn');
  if (nicheBtn) nicheBtn.addEventListener('click', async () => {
    const panel = $('#nicheInsightsPanel');
    if (!panel) return;
    if (!panel.hidden) { panel.hidden = true; return; }
    panel.hidden = false;
    panel.innerHTML = empty('Загружаю…');
    nicheBtn.disabled = true;
    try {
      const d = await api(`/api/niches/${encodeURIComponent(nicheBtn.dataset.slug)}/insights`);
      panel.innerHTML = d.found === false ? notice(esc(d.hint)) : insightsPanelHtml(d);
    } catch (e) {
      panel.innerHTML = notice(esc(e.message));
    } finally {
      nicheBtn.disabled = false;
    }
  });
}

/* Пробелы в контенте (план 03): вопросы зрителей из комментариев топовых видео
   ниши, на которые в базе ещё нет видео. Экран читает только кэш (GET, 0 квоты);
   комментарии непрочитанных видео -- строго по кнопке (POST, 1 unit на видео). */
const GAP_STATUS = { free: ['свободно', 'chip-good'], partial: ['частично', 'chip-warn'] };
const GAP_SKIP = {
  'not-fetched': 'комментарии ещё не читали',
  'quota-exhausted': 'квота YouTube кончилась',
  'no-comments': 'комментариев нет',
  'llm-unavailable': 'LLM недоступен',
  error: 'ошибка',
};

function gapItemHtml(g) {
  const st = GAP_STATUS[g.status] || [g.status, ''];
  const src = g.sourceVideos[0];
  const near = g.nearestVideo || g.nearestTranscript;
  return `<li style="white-space:normal;margin-bottom:10px">
    <div><span class="chip ${st[1]}">${esc(st[0])}</span> <b>${esc(g.topic)}</b></div>
    <div class="row-sub">вопросов: ${g.askers} · лайков: ${compact(g.likes)} · видео-источников: ${g.sourceVideos.length}
      · спрос ${esc(g.demand)}${g.coverage != null ? ` · покрытие ${Math.round(g.coverage * 100)}%` : ''}</div>
    ${g.examples.filter((e) => e !== g.topic).map((e) => `<div class="row-sub">«${esc(e)}»</div>`).join('')}
    ${near ? `<div class="row-sub">Ближе всего: ${g.nearestVideo
      ? `<a href="https://www.youtube.com/watch?v=${esc(g.nearestVideo.videoId)}" target="_blank" rel="noopener">${esc(g.nearestVideo.title)}</a>`
      : `транскрипт «${esc(g.nearestTranscript.title || g.nearestTranscript.videoId)}»`}
      (${Math.round((near.similarity || 0) * 100)}%)</div>` : ''}
    <a class="btn btn-ghost btn-sm" style="margin-top:4px"
       href="#/brief/${encodeURIComponent(src)}?gap=${encodeURIComponent(g.topic)}">В бриф</a>
  </li>`;
}

function gapsBlock(d, slug) {
  if (!d) return '';
  const notRead = (d.skippedVideos || []).filter((s) => s.reason === 'not-fetched').length;
  const other = (d.skippedVideos || []).filter((s) => s.reason !== 'not-fetched');
  const base = d.coverageBase || {};
  const sub = `${d.mode === 'llm' ? 'вопросы выделил LLM' : 'вопросы отобраны правилами'}
    · проверено по ${compact(base.videos)} видео и ${compact(base.transcripts)} транскриптам ниши`;
  const btn = notRead ? `<button class="btn btn-ghost btn-sm" id="gapsFetch" data-slug="${esc(slug)}" type="button">
    Прочитать комментарии: ${notRead} видео (квота ~${notRead}${d.mode === 'llm' ? ' + LLM' : ''})</button>` : '';
  const gaps = d.gaps || [];
  return `<div class="card" id="gapsCard">
    ${sectionHead('Пробелы в контенте', sub, btn)}
    ${d.modeNote ? `<div class="section-sub">Без LLM: больше шума и пропусков, особенно не на английском и русском.</div>` : ''}
    ${gaps.length ? `<ul class="digest-list">${gaps.map(gapItemHtml).join('')}</ul>`
      : empty(notRead ? 'комментарии топовых видео ещё не читали — нажмите кнопку выше'
        : 'в комментариях не нашлось вопросов, на которые нет видео')}
    ${d.coveredCount ? `<div class="section-sub">Уже закрыто видео из базы: ${d.coveredCount}.</div>` : ''}
    ${other.length ? `<div class="section-sub">Пропущено видео: ${other.map((s) =>
      `«${esc(s.title || s.videoId)}» — ${esc(GAP_SKIP[s.reason] || s.reason)}`).join('; ')}</div>` : ''}
    <div class="section-sub">Пробел реален настолько, насколько полна база: покрытие проверяется только по собранному здесь.
      ${d.quotaSpent ? ` Потрачено квоты: ${d.quotaSpent}.` : ''}</div>
  </div>`;
}

function wireGaps() {
  const btn = $('#gapsFetch');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    btn.disabled = true; btn.textContent = 'Читаю комментарии…';
    try {
      const d = await api(`/api/niches/${encodeURIComponent(btn.dataset.slug)}/content-gaps`,
        { method: 'POST', body: {} });
      $('#gapsCard').outerHTML = gapsBlock(d, btn.dataset.slug);
      wireGaps();
      toast(`Готово, квоты потрачено: ${d.quotaSpent || 0}`, 'ok');
    } catch (e) {
      btn.disabled = false; btn.textContent = 'Повторить';
      toast(e.message, 'err');
    }
  });
}

/* Стили превью (план 13): k-means по CLIP-векторам превью ниши и как каждый
   стиль заходит; поиск превью по описанию. Векторы считает воркер
   (WORKER_THUMB_EMBED) или кнопка здесь -- превью качаются с i.ytimg.com. */
function thumbCard(e, sub) {
  return `<article class="vcard">
    <a class="thumb" href="https://www.youtube.com/watch?v=${esc(e.videoId)}" target="_blank" rel="noopener">
      ${e.thumbnail ? `<img src="${esc(e.thumbnail)}" alt="" loading="lazy">` : '<div class="thumb-fallback">без обложки</div>'}</a>
    <div class="vcard-title">${esc(e.title || e.videoId)}</div>
    <div class="vcard-meta">${sub}</div>
  </article>`;
}

function thumbStylesBlock(d, slug) {
  if (!d) return '';
  const head = sectionHead('Стили превью', 'похожие по картинке превью ниши, сгруппированные, и как каждая группа заходит',
    `<input type="text" id="thumbQuery" placeholder="найти превью: red arrow, shocked face" style="width:260px">`);
  const embedBtn = `<button class="btn btn-ghost btn-sm" id="thumbEmbed" data-slug="${esc(slug)}" type="button">
    Посчитать векторы превью ниши (до 100, квота не тратится)</button>`;
  const body = d.found
    ? d.styles.map((s) => `<div style="margin-bottom:14px">
        <div class="row-sub"><b>Стиль ${s.style}</b> · ${num(s.videos)} видео (${Math.round(s.share * 100)}%)
          · медиана множителя ${s.medianOutlierScore != null ? mult(s.medianOutlierScore) : '—'}
          · медиана просмотров ${compact(s.medianViews)}</div>
        <div class="cards">${s.examples.map((e) => thumbCard(e,
          `${compact(e.views)} просмотров${e.outlierScore != null ? ` · ${mult(e.outlierScore)}` : ''}`)).join('')}</div>
      </div>`).join('')
      + `<div class="section-sub">Группы — это похожий вид превью, а не правило. Медиана множителя — связь, не причина.
          CLIP сравнивает стиль и содержимое картинки, а не надписи на ней.</div>`
    : `${empty(`векторы есть у ${num(d.embedded)} из ${num(d.videos)} превью, нужно хотя бы 12`)}${embedBtn}`;
  return `<div class="card" id="thumbStylesCard">${head}<div id="thumbSearchOut"></div>${body}</div>`;
}

function wireThumbStyles(slug) {
  const input = $('#thumbQuery');
  if (input) input.addEventListener('change', async () => {
    const out = $('#thumbSearchOut');
    const query = input.value.trim();
    if (!query) { out.innerHTML = ''; return; }
    out.innerHTML = empty('Ищу… (первый поиск скачивает текстовую модель, ~0.25 ГБ)');
    try {
      const r = await api(`/api/thumbnails/search${q({ q: query, niche: slug, limit: 8 })}`);
      out.innerHTML = r.results.length
        ? `<div class="cards" style="margin-bottom:14px">${r.results.map((e) => thumbCard(e,
            `сходство ${Math.round(e.similarity * 100)}% · ${compact(e.views)} просмотров`)).join('')}</div>`
        : empty(r.hint || 'ничего похожего');
    } catch (e) { out.innerHTML = notice(esc(e.message)); }
  });
  const btn = $('#thumbEmbed');
  if (btn) btn.addEventListener('click', async () => {
    btn.disabled = true; btn.textContent = 'Считаю… (около секунды на превью)';
    try {
      const r = await api(`/api/thumbnails/embed${q({ limit: 100, niche: btn.dataset.slug })}`, { method: 'POST' });
      toast(`Готово: ${r.embedded} векторов, не прочитано ${r.failed}`, 'ok');
      const d = await api(`/api/niches/${encodeURIComponent(slug)}/thumbnail-styles`);
      $('#thumbStylesCard').outerHTML = thumbStylesBlock(d, slug);
      wireThumbStyles(slug);
    } catch (e) { btn.disabled = false; btn.textContent = 'Повторить'; toast(e.message, 'err'); }
  });
}

function nicheVideoTagsSection(videos, tagsByVideo, tagGroup, nicheSlug) {
  if (!videos.length) return '';
  return `
    <div class="card">
      ${sectionHead('Видео ниши', `теги группы «${esc(tagGroup)}» — правится вручную`,
        `<button class="btn btn-ghost btn-sm" id="nicheInsightsBtn" data-slug="${esc(nicheSlug)}">
           Инсайты по нише из кеша</button>`)}
      <div id="nicheInsightsPanel" hidden style="margin-bottom:12px"></div>
      <div class="rows">${videos.map((v) => `
        <div class="row" data-video-id="${esc(v.videoId)}" style="align-items:flex-start;flex-direction:column;gap:8px">
          <div style="display:flex;width:100%;align-items:flex-start;gap:12px">
            <div class="row-main">
              <div class="row-title">
                <a href="https://www.youtube.com/watch?v=${esc(v.videoId)}" target="_blank" rel="noopener">${esc(v.title)}</a>
              </div>
              <div class="row-sub">${compact(v.views)} просмотров · ${mult(v.outlierScore)}</div>
              <div class="tag-editor" style="margin-top:6px;display:flex;flex-wrap:wrap;gap:6px;align-items:center">
                ${(tagsByVideo[v.videoId] || []).map((t) => `
                  <span class="chip tag-chip" data-tag="${esc(t)}">${esc(t)}
                    <a href="#" class="tag-remove" data-tag="${esc(t)}" title="убрать тег">×</a>
                  </span>`).join('')}
                <input type="text" class="tag-add-input" placeholder="+ тег" style="width:100px">
              </div>
            </div>
            <button class="btn btn-ghost btn-sm insights-btn" data-insights-video-id="${esc(v.videoId)}">Инсайты из комментариев</button>
          </div>
          <div class="insights-panel" data-insights-video-id="${esc(v.videoId)}" hidden style="width:100%"></div>
        </div>`).join('')}</div>
    </div>`;
}

function wireNicheTagEditor(slug, tagGroup) {
  const groupInput = $('#tagGroupInput');
  if (groupInput) {
    groupInput.addEventListener('change', () => {
      const g = groupInput.value.trim() || 'theme';
      localStorage.setItem('nf.tagGroup', g);
      render();
    });
  }
  const writeTags = async (videoId, tags) => {
    await api('/api/tags', { method: 'POST', body: {
      items: [{ video_id: videoId, tag_group: tagGroup, tags }],
      source: 'manual', replace: true,
    } });
    render();
  };
  view.querySelectorAll('[data-video-id]').forEach((rowEl) => {
    const videoId = rowEl.dataset.videoId;
    const current = () => [...rowEl.querySelectorAll('.tag-chip')].map((c) => c.dataset.tag);

    rowEl.querySelectorAll('.tag-remove').forEach((a) => a.addEventListener('click', async (e) => {
      e.preventDefault();
      try { await writeTags(videoId, current().filter((t) => t !== a.dataset.tag)); }
      catch (err) { toast(err.message, 'err'); }
    }));

    const input = rowEl.querySelector('.tag-add-input');
    input.addEventListener('keydown', async (e) => {
      if (e.key !== 'Enter') return;
      const tag = input.value.trim();
      if (!tag) return;
      try { await writeTags(videoId, [...current(), tag]); }
      catch (err) { toast(err.message, 'err'); }
    });
  });
}

/* Предложенные LLM-теги вне таксономии ниши (этап 03) -- принять/отклонить
   через POST /api/tags/proposed/resolve. Не привязана к tagGroup выбранной
   в tagStatsSection: proposed-теги могут быть в любой группе. */
function proposedTagsSection(proposed) {
  if (!proposed.length) return '';
  return `
    <div class="card">
      ${sectionHead('Предложенные теги', 'LLM предложил теги вне таксономии ниши — подтвердите или отклоните')}
      <div class="rows">${proposed.map((p) => `
        <div class="row" data-proposed-video-id="${esc(p.videoId)}" data-tag-group="${esc(p.tagGroup)}" data-tag="${esc(p.tag)}">
          <div class="row-main">
            <div class="row-title">${esc(p.tag)} <span class="chip">${esc(p.tagGroup)}</span></div>
            <div class="row-sub">видео <a href="https://www.youtube.com/watch?v=${esc(p.videoId)}"
              target="_blank" rel="noopener">${esc(p.videoId)}</a> · ${ago(p.createdAt)}</div>
          </div>
          <div class="row-metrics">
            <button class="btn btn-ghost btn-sm proposed-accept">принять</button>
            <button class="btn btn-ghost btn-sm proposed-reject">отклонить</button>
          </div>
        </div>`).join('')}</div>
    </div>`;
}

function wireProposedTags() {
  view.querySelectorAll('[data-proposed-video-id]').forEach((rowEl) => {
    const { proposedVideoId: videoId, tagGroup, tag } = rowEl.dataset;
    const resolve = async (accept) => {
      try {
        await api('/api/tags/proposed/resolve', { method: 'POST',
          body: { videoId, tagGroup, tag, accept } });
        toast(accept ? 'Тег принят' : 'Тег отклонён', 'ok');
        render();
      } catch (e) { toast(e.message, 'err'); }
    };
    rowEl.querySelector('.proposed-accept')?.addEventListener('click', () => resolve(true));
    rowEl.querySelector('.proposed-reject')?.addEventListener('click', () => resolve(false));
  });
}

/* этап 15: фильтры точечного графика живут в памяти вкладки, как
   channelFilters у трекера -- не настолько важны, чтобы переживать
   перезагрузку. */
let scatterFilters = { channels: '', includeShorts: true };

function scatterSection(videos, filters) {
  return `
    <div class="card">
      ${sectionHead('Просмотры по датам публикации', 'цвет — канал; полый контур — моложе 30 дней; крупный контур — аномалия', `
        <input type="text" id="scatterChannels" placeholder="ID каналов через запятую"
          value="${esc(filters.channels)}" style="width:220px">
        <label style="display:flex;align-items:center;gap:4px;font-size:12px;color:var(--muted)">
          <input type="checkbox" id="scatterHideShorts" ${filters.includeShorts ? '' : 'checked'}> скрыть Shorts
        </label>`)}
      ${scatterChart(videos)}
    </div>`;
}

function wireScatterFilters(rerender) {
  const apply = () => {
    scatterFilters = {
      channels: $('#scatterChannels').value.trim(),
      includeShorts: !$('#scatterHideShorts').checked,
    };
    rerender();
  };
  $('#scatterChannels').addEventListener('change', apply);
  $('#scatterHideShorts').addEventListener('change', apply);
}

async function viewNiche(slug) {
  const tagGroup = localStorage.getItem('nf.tagGroup') || 'theme';
  const [d, stats, videoTags, proposed, scatter, risk, sponsors, gaps, thumbStyles] = await Promise.all([
    api(`/api/niches/${encodeURIComponent(slug)}${q({ period: state.period, top_n: 30 })}`),
    api(`/api/tags/stats${q({ niche: slug, tag_group: tagGroup })}`),
    api(`/api/tags${q({ niche: slug })}`),
    api(`/api/tags/proposed${q({ niche: slug })}`),
    api(`/api/niches/${encodeURIComponent(slug)}/videos${q({
      period: state.period, channels: scatterFilters.channels || undefined,
      include_shorts: scatterFilters.includeShorts,
    })}`),
    api(`/api/niches/${encodeURIComponent(slug)}/template-risk`).catch(() => null),
    // Не критично для экрана: без спонсоров ниша всё равно открывается.
    api(`/api/niches/${encodeURIComponent(slug)}/sponsors${q({ period: state.period })}`)
      .catch(() => null),
    api(`/api/niches/${encodeURIComponent(slug)}/content-gaps`).catch(() => null),
    api(`/api/niches/${encodeURIComponent(slug)}/thumbnail-styles`).catch(() => null),
  ]);
  if (!d.found) { view.innerHTML = notice(esc(d.hint || 'ниша не найдена')); return; }

  const tagsByVideo = {};
  for (const t of (videoTags.tags || [])) {
    if (t.tagGroup !== tagGroup) continue;
    (tagsByVideo[t.videoId] ||= []).push(t.tag);
  }

  view.innerHTML = nicheOverviewBlock(d,
    sectionHead(`Ниша: ${slug}`, `${esc(d.query || '')} · ${plabel(state.period)}`, `
      <a class="btn btn-ghost btn-sm" href="/api/niche/${encodeURIComponent(slug)}/export.tsv">Экспорт TSV</a>
      <a class="btn btn-ghost btn-sm" href="/api/niche/${encodeURIComponent(slug)}/export.csv">Экспорт CSV</a>`))
    + nicheTemplateRiskBlock(risk)
    + '<div id="nichePolicy"></div>'
    + '<div id="nicheTraits"></div>'
    + scatterSection(scatter.videos || [], scatterFilters)
    + sponsorBlock(sponsors, 'Спонсоры ниши', plabel(state.period))
    + gapsBlock(gaps, slug)
    + thumbStylesBlock(thumbStyles, slug)
    + tagStatsSection(stats, tagGroup)
    + nicheVideoTagsSection(d.top_videos_by_outlier_score || [], tagsByVideo, tagGroup, slug)
    + proposedTagsSection(proposed.proposed || []);

  wireNicheTagEditor(slug, tagGroup);
  wireProposedTags();
  wireScatterFilters(() => viewNiche(slug));
  wireCommentInsights();
  wireGaps();
  wireThumbStyles(slug);
  // план 22: считается по всем каналам ниши -- отдельным запросом, экран не ждёт
  api(`/api/niches/${encodeURIComponent(slug)}/policy-signals`)
    .then((r) => { const box = $('#nichePolicy'); if (box) box.innerHTML = nichePolicyBlock(r); })
    .catch(() => {});
  // план 29: что общего у выстреливших -- по числам, отдельным запросом
  api(`/api/niches/${encodeURIComponent(slug)}/outlier-traits`)
    .then((r) => { const box = $('#nicheTraits'); if (box) box.innerHTML = outlierTraitsBlock(r); })
    .catch(() => {});
}

export { viewNiche };
