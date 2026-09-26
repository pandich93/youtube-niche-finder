/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, compact, mult, ago, esc, toast, sectionHead, notice, empty, barList, scatterChart, state } from '../ui.js';
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
  const [d, stats, videoTags, proposed, scatter] = await Promise.all([
    api(`/api/niches/${encodeURIComponent(slug)}${q({ period: state.period, top_n: 30 })}`),
    api(`/api/tags/stats${q({ niche: slug, tag_group: tagGroup })}`),
    api(`/api/tags${q({ niche: slug })}`),
    api(`/api/tags/proposed${q({ niche: slug })}`),
    api(`/api/niches/${encodeURIComponent(slug)}/videos${q({
      period: state.period, channels: scatterFilters.channels || undefined,
      include_shorts: scatterFilters.includeShorts,
    })}`),
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
    + scatterSection(scatter.videos || [], scatterFilters)
    + tagStatsSection(stats, tagGroup)
    + nicheVideoTagsSection(d.top_videos_by_outlier_score || [], tagsByVideo, tagGroup, slug)
    + proposedTagsSection(proposed.proposed || []);

  wireNicheTagEditor(slug, tagGroup);
  wireProposedTags();
  wireScatterFilters(() => viewNiche(slug));
  wireCommentInsights();
}

export { viewNiche };
