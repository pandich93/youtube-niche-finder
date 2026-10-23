/* Панели и значки поверх YouTube.
 *
 * Три экрана: страница ролика (панель в правой колонке), страница канала
 * (панель под шапкой) и любые списки видео (значок с множителем на превью).
 * В сеть отсюда никто не ходит — всё через service worker, см. background.js.
 *
 * YouTube — SPA: DOM переживает переходы между страницами, точки монтирования
 * появляются с задержкой и пересоздаются. Поэтому здесь везде waitFor() и
 * повторный рендер по событию yt-navigate-finish, а не разовый querySelector.
 */
(() => {
  if (window.top !== window) return;          // не работаем во встроенных плеерах
  if (window.__nicheFinderLoaded) return;
  window.__nicheFinderLoaded = true;

  let SETTINGS = null;
  let currentKey = '';

  // --------------------------------------------------------------- утилиты

  const send = (msg) => new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(msg, (res) => {
        if (chrome.runtime.lastError) {
          resolve({ ok: false, error: chrome.runtime.lastError.message });
        } else resolve(res || { ok: false, error: t('errEmptyResponse') });
      });
    } catch (e) { resolve({ ok: false, error: String(e) }); }
  });

  // i18n: тексты -- chrome.i18n из _locales, числа/даты/плюралы -- Intl на языке браузера.
  // Если для языка браузера нет своих _locales (uk, pl...), Chrome показывает en --
  // тогда и плюралы с числами берём по en, чтобы не вышло «21 subscriber» и «1,2 тис. views».
  const MSG_LOCALE = chrome.i18n.getMessage('localeCode') || 'en';
  const BROWSER_LANG = chrome.i18n.getUILanguage() || MSG_LOCALE;
  const UI_LANG = BROWSER_LANG.toLowerCase().split(/[-_]/)[0] === MSG_LOCALE ? BROWSER_LANG : MSG_LOCALE;
  function t(key, subs) {
    return chrome.i18n.getMessage(key, subs === undefined ? undefined : [].concat(subs).map(String)) || key;
  }
  // Плюралы: в chrome.i18n их нет, ключи key_one / key_few / key_many / key_other,
  // форму выбирает Intl.PluralRules; нет нужной формы в локали -- берём key_other.
  const PLURAL = new Intl.PluralRules(UI_LANG);
  function tn(base, n, subs) {
    const list = subs === undefined ? [n] : [].concat(subs);
    const args = list.map(String);
    const form = PLURAL.select(Math.abs(Math.round(Number(n) || 0)));
    return chrome.i18n.getMessage(`${base}_${form}`, args)
      || chrome.i18n.getMessage(`${base}_other`, args) || base;
  }

  const NUM = new Intl.NumberFormat(UI_LANG);
  const COMPACT = [0, 1].map((fd) => new Intl.NumberFormat(UI_LANG, { notation: 'compact', maximumFractionDigits: fd }));
  const DECIMAL = new Map();
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

  function compact(n) {
    if (n == null || Number.isNaN(n)) return '—';
    const a = Math.abs(n);
    if (a < 1e3) return NUM.format(Math.round(n));
    // как раньше: до 100 в своём разряде -- один знак после запятой, дальше целые
    const scaled = a >= 1e9 ? a / 1e9 : a >= 1e6 ? a / 1e6 : a / 1e3;
    return COMPACT[scaled >= 100 ? 0 : 1].format(n);
  }

  function decimal(n, digits = 2) {
    if (n == null || Number.isNaN(n)) return '—';
    // хвостовые нули срезаем только после запятой: 1000 -> «1000», 2,50 -> «2,5»
    if (!DECIMAL.has(digits)) {
      DECIMAL.set(digits, new Intl.NumberFormat(UI_LANG, { maximumFractionDigits: digits, useGrouping: false }));
    }
    return DECIMAL.get(digits).format(Number(n));
  }

  function pct(n) { return n == null ? '—' : decimal(n, 2) + '%'; }

  function duration(sec) {
    if (!sec) return '—';
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return (h ? h + ':' + String(m).padStart(2, '0') : String(m)) + ':' + String(s).padStart(2, '0');
  }

  function ageText(days) {
    if (days == null) return '—';
    if (days < 1) return t('ageHours', Math.max(1, Math.round(days * 24)));
    if (days < 45) return t('ageDays', Math.round(days));
    if (days < 400) return t('ageMonths', Math.round(days / 30));
    return t('ageYears', decimal(days / 365, 1));
  }

  const BANDS = {
    'normal': [t('bandNormal'), 1],
    'above average': [t('bandAboveAverage'), 2],
    'noteworthy': [t('bandNoteworthy'), 3],
    'strong outlier': [t('bandStrongOutlier'), 4],
    'viral': [t('bandViral'), 5],
    'mega outlier': [t('bandMegaOutlier'), 6],
  };
  const bandName = (b) => (BANDS[b] || ['—', 1])[0];
  const bandLevel = (b) => (BANDS[b] || ['—', 1])[1];

  function waitFor(selector, timeout = 12000) {
    return new Promise((resolve) => {
      const found = document.querySelector(selector);
      if (found) return resolve(found);
      const obs = new MutationObserver(() => {
        const el = document.querySelector(selector);
        if (el) { obs.disconnect(); clearTimeout(timer); resolve(el); }
      });
      obs.observe(document.documentElement, { childList: true, subtree: true });
      const timer = setTimeout(() => { obs.disconnect(); resolve(null); }, timeout);
    });
  }

  // -------------------------------------------------------- каркас панели

  function ensurePanel(mount, id, where = 'prepend') {
    let root = document.getElementById(id);
    const placed = where === 'prepend' ? root && root.parentElement === mount
                                       : root && mount.nextElementSibling === root;
    if (root && root.isConnected && placed) return root;
    if (root) root.remove();
    root = document.createElement('div');
    root.id = id;
    root.className = 'nf-panel';
    if (where === 'prepend') mount.prepend(root); else mount.after(root);
    return root;
  }

  function removePanels() {
    document.querySelectorAll('.nf-panel').forEach((el) => el.remove());
  }

  const header = (subtitle, statusHtml = '') => `
    <div class="nf-head">
      <div class="nf-logo">niche&#8209;finder</div>
      <div class="nf-sub">${esc(subtitle)}</div>
      ${statusHtml}
      <button class="nf-icon-btn nf-refresh" title="${esc(t('refreshTitle'))}">⟳</button>
    </div>`;

  const skeleton = (text) => header(text) + `
    <div class="nf-body"><div class="nf-skeleton"></div><div class="nf-skeleton"></div>
    <div class="nf-skeleton nf-short"></div></div>`;

  function errorHtml(res) {
    const offline = res.offline;
    return header(t('noBackendConnection')) + `
      <div class="nf-body">
        <div class="nf-error">${esc(res.error || t('errorGeneric'))}</div>
        ${offline ? `<div class="nf-hint">${esc(t('offlineHintStart'))}<br>
          <code>make dev</code> ${esc(t('wordOr'))} <code>docker compose up -d web</code>,<br>
          ${esc(t('offlineHintThen'))}</div>` : ''}
        <button class="nf-btn nf-retry">${esc(t('retry'))}</button>
      </div>`;
  }

  function statusChip(d) {
    const fromApi = d.fetchedFromApi;
    const label = fromApi ? t('chipFetched', d.quotaUnits || 0) : t('chipLocal');
    return `<span class="nf-chip ${fromApi ? 'nf-chip-api' : ''}" title="${
      esc(fromApi ? t('chipFetchedTitle') : t('chipLocalTitle'))}">${esc(label)}</span>`;
  }

  const cell = (label, value, hint = '') => `
    <div class="nf-cell" ${hint ? `title="${esc(hint)}"` : ''}>
      <div class="nf-cell-v">${value}</div>
      <div class="nf-cell-l">${esc(label)}</div>
    </div>`;

  // ------------------------------------------------------- страница ролика

  function videoHtml(d, similar, llmOn) {
    const v = d.video, c = d.channel, m = d.metrics;
    const similarList = (similar?.similar || []).slice(0, 5);
    const score = m.outlierScore;
    const lvl = bandLevel(m.outlierBand);
    const baseText = m.baselineMedianViews
      ? t('baseMedian', [m.baselineSample, compact(m.baselineMedianViews)])
      : (c.avgViews ? t('baseAvgAllTime', compact(c.avgViews)) : t('baseNone'));

    const vph = m.vph24h != null ? m.vph24h : m.vphLifetime;
    const vphLabel = m.vph24h != null ? t('vph24Label') : t('vphAllLabel');
    const accel = m.acceleration;

    const tags = (v.tags || []).slice(0, 18);
    const tagsHtml = tags.length
      ? `<div class="nf-tags">${tags.map((tag) => `<span class="nf-tag">${esc(tag)}</span>`).join('')}</div>
         <button class="nf-link nf-copy-tags">${esc(t('copyAllTags', (v.tags || []).length))}</button>`
      : `<div class="nf-hint">${esc(t('noTags'))}</div>`;

    return header(v.isShort ? 'Shorts' : t('videoLabel'), statusChip(d)) + `
      <div class="nf-body">
        <div class="nf-hero nf-band-${lvl}">
          <div class="nf-score">${score != null ? '×' + decimal(score, score < 10 ? 2 : 1) : '—'}</div>
          <div class="nf-hero-txt">
            <div class="nf-band">${esc(bandName(m.outlierBand))}</div>
            <div class="nf-hint">${esc(baseText)}</div>
          </div>
        </div>

        <div class="nf-grid">
          ${cell(t('cellViews'), compact(v.views))}
          ${cell(vphLabel, compact(vph), t('cellVphHint'))}
          ${cell(t('cellPerSub'), m.viewsPerSubscriber != null ? '×' + decimal(m.viewsPerSubscriber, 2) : '—',
            t('cellPerSubHint'))}
          ${cell(t('cellEngagement'), pct(m.engagementRate), t('cellEngagementHint'))}
          ${cell(t('cellProjected30'), compact(m.projected30dViews), t('cellProjected30Hint'))}
          ${cell(t('cellAge'), ageText(v.ageDays) + (v.durationSeconds ? ' · ' + duration(v.durationSeconds) : ''))}
        </div>

        ${accel ? `<div class="nf-note ${accel >= 1.5 ? 'nf-hot' : (accel <= 0.7 ? 'nf-cold' : '')}">
          ${esc(t('accelLine', [decimal(accel, 2),
            accel >= 1.5 ? t('accelUp') : accel <= 0.7 ? t('accelDown') : t('accelFlat')]))}
          <span class="nf-hint">${esc(t('accelVs'))}</span></div>` : ''}

        <div class="nf-row">
          <span class="nf-muted">${esc(t('revenueEstimate'))}</span>
          <b>$${decimal(d.revenue.lifetime.low_usd, 0)}–$${decimal(d.revenue.lifetime.high_usd, 0)}</b>
          <span class="nf-hint">${esc(t('lifetimeSbRange'))} · ${esc(AD_ONLY)}</span>
        </div>

        <div class="nf-channel">
          <div class="nf-ch-title">${esc(c.title || t('channelFallback'))}</div>
          <div class="nf-hint">
            ${esc(c.subscribersHidden ? t('subsHidden')
              : tn('subscribersCount', c.subscribers, compact(c.subscribers)))} ·
            ${esc(t('avgPerVideo', compact(c.avgViews)))} ·
            ${esc(c.videosStored ? tn('videosInDb', c.videosStored) : t('noVideosInDb'))}
          </div>
        </div>

        ${tagsHtml}

        <div class="nf-actions">
          <button class="nf-btn nf-collect">${esc(c.videosStored ? t('updateChannelInDb') : t('collectChannel100'))}</button>
          <button class="nf-btn nf-track">${esc(c.tracked ? t('untrack') : t('track'))}</button>
          <button class="nf-btn nf-ghost nf-save">${esc(t('addToSaved'))}</button>
          <button class="nf-btn nf-ghost nf-topic"
             title="${esc(t('watchTopicTitle'))}">${esc(t('watchTopic'))}</button>
          <a class="nf-btn nf-ghost nf-brief" target="_blank" rel="noopener"
             title="${esc(t('briefTitle'))}">${esc(t('brief'))}</a>
          <a class="nf-btn nf-ghost nf-dash" target="_blank" rel="noopener">${esc(t('dashboard'))}</a>
        </div>
        ${similarList.length ? `<div class="nf-sec">
          <div class="nf-sec-h">${esc(t('similarVideos'))}</div>
          ${similarList.map((s) => `<a class="nf-item" href="/watch?v=${esc(s.videoId)}">
            <span class="nf-item-t">${esc(s.title || s.videoId)}</span>
            <span class="nf-hint">${compact(s.views)} · ${decimal(s.similarity * 100, 0)}%${s.sameChannel ? ' · ' + esc(t('sameChannel')) : ''}</span></a>`).join('')}
          <div class="nf-hint">${esc(t('similarNote'))}</div>
        </div>` : ''}

        <div class="nf-sec nf-comments-sec">
          <button class="nf-btn nf-ghost nf-comments-btn">${esc(t('showComments'))}</button>
          <div class="nf-comments-body"></div>
        </div>

        <div class="nf-pk"></div>

        <div class="nf-sec nf-rep-sec">
          <button class="nf-btn nf-ghost nf-rep-btn" title="${esc(t('repTitle'))}">${esc(t('repBtn'))}</button>
          <div class="nf-rep-body"></div>
        </div>

        <div class="nf-sec nf-lg-sec">
          <button class="nf-btn nf-ghost nf-lg-btn" title="${esc(t('lgTitle'))}">${esc(t('lgBtn'))}</button>
          <div class="nf-lg-body"></div>
        </div>

        ${llmOn ? `<div class="nf-sec nf-why-sec">
          <button class="nf-btn nf-ghost nf-why-btn">${esc(t('whyBtn'))}</button>
          <div class="nf-why-body"></div>
        </div>` : ''}

        <div class="nf-foot">${esc(d.history.points > 1
          ? tn('historySnapshots', d.history.points)
          : t('historyEmpty'))}</div>
      </div>`;
  }

  function notFoundHtml(d, fallback) {
    return header(t('noData'), '') + `
      <div class="nf-body">
        <div class="nf-hint">${esc(d.hint || fallback)}</div>
        <button class="nf-btn nf-retry">${esc(t('retry'))}</button>
      </div>`;
  }

  function wireCommon(root, onRetry) {
    const retry = root.querySelector('.nf-retry');
    if (retry) retry.addEventListener('click', onRetry);
    const refresh = root.querySelector('.nf-refresh');
    if (refresh) refresh.addEventListener('click', onRetry);
  }

  async function renderWatch(videoId, opts = {}) {
    const mount = await waitFor('#secondary-inner, #secondary');
    if (!mount || getVideoId() !== videoId) return;
    const root = ensurePanel(mount, 'nf-panel-video');
    root.innerHTML = skeleton(t('analysingVideo'));

    const res = await send({ type: 'video', videoId, refresh: !!opts.refresh });
    if (!root.isConnected || getVideoId() !== videoId) return;

    if (!res.ok) {
      root.innerHTML = errorHtml(res);
      wireCommon(root, () => renderWatch(videoId, { refresh: true }));
      return;
    }
    const d = res.data;
    if (!d.found) {
      root.innerHTML = notFoundHtml(d, t('notFoundVideo'));
      wireCommon(root, () => renderWatch(videoId, { refresh: true }));
      return;
    }

    const sr = await send({ type: 'similarVideos', videoId });
    if (!root.isConnected || getVideoId() !== videoId) return;
    const statsRes = await send({ type: 'stats' });
    const llmOn = statsRes.ok && statsRes.data?.llm?.provider && statsRes.data.llm.provider !== 'none';
    if (!root.isConnected || getVideoId() !== videoId) return;

    root.innerHTML = videoHtml(d, sr.ok ? sr.data : null, llmOn);
    wireCommon(root, () => renderWatch(videoId, { refresh: true }));

    const dash = root.querySelector('.nf-dash');
    if (dash) dash.href = (SETTINGS?.baseUrl || 'http://127.0.0.1:8080');
    const brief = root.querySelector('.nf-brief');
    if (brief) brief.href = (SETTINGS?.baseUrl || 'http://127.0.0.1:8080') + '/#/brief/' + encodeURIComponent(videoId);

    const copy = root.querySelector('.nf-copy-tags');
    if (copy) copy.addEventListener('click', async () => {
      await navigator.clipboard.writeText((d.video.tags || []).join(', '));
      copy.textContent = t('copied');
      setTimeout(() => { copy.textContent = t('copyAllTags', (d.video.tags || []).length); }, 1500);
    });

    const collect = root.querySelector('.nf-collect');
    if (collect) collect.addEventListener('click', async () => {
      collect.disabled = true; collect.textContent = t('collecting');
      const r = await send({ type: 'collect', channel: d.channel.channelId, maxVideos: 100 });
      collect.textContent = r.ok ? t('collectDone', r.data.videos_stored) : t('errorShort');
      setTimeout(() => renderWatch(videoId, { refresh: true }), 1200);
    });

    const track = root.querySelector('.nf-track');
    if (track) track.addEventListener('click', async () => {
      track.disabled = true;
      const r = await send({ type: d.channel.tracked ? 'untrack' : 'track', channelId: d.channel.channelId });
      track.disabled = false;
      if (r.ok) { d.channel.tracked = !d.channel.tracked; track.textContent = d.channel.tracked ? t('untrack') : t('track'); }
    });

    const save = root.querySelector('.nf-save');
    if (save) save.addEventListener('click', async () => {
      save.disabled = true;
      const r = await send({ type: 'save', kind: 'video', refId: d.videoId, payload: d });
      save.textContent = r.ok ? t('saved') : t('errorShort');
      setTimeout(() => { save.disabled = false; save.textContent = t('addToSaved'); }, 1500);
    });

    const topic = root.querySelector('.nf-topic');
    if (topic) topic.addEventListener('click', async () => {
      topic.disabled = true;
      const r = await send({ type: 'watchTopic', text: (d.video?.title || '').slice(0, 300) });
      topic.textContent = r.ok ? t('topicAdded') : t('errorShort');
      setTimeout(() => { topic.disabled = false; topic.textContent = t('watchTopic'); }, 1500);
    });

    const commentsBtn = root.querySelector('.nf-comments-btn');
    if (commentsBtn) commentsBtn.addEventListener('click', async () => {
      commentsBtn.disabled = true;
      commentsBtn.textContent = t('loadingShort');
      const body = root.querySelector('.nf-comments-body');
      const r = await send({ type: 'comments', videoId, maxResults: 50 });
      if (!root.isConnected) return;
      if (!r.ok) {
        commentsBtn.disabled = false;
        commentsBtn.textContent = t('failedRetry');
        if (body) body.innerHTML = `<div class="nf-error">${esc(r.error || t('errorGeneric'))}</div>`;
        return;
      }
      commentsBtn.remove();
      if (body) body.innerHTML = commentsHtml(r.data, d.video.publishedAt);
    });

    // план 21: повторялся ли формат у других каналов, или это разовая удача
    const repBtn = root.querySelector('.nf-rep-btn');
    if (repBtn) repBtn.addEventListener('click', async () => {
      repBtn.disabled = true;
      repBtn.textContent = t('counting');
      const r = await send({ type: 'repeatability', videoId });
      if (!root.isConnected) return;
      const body = root.querySelector('.nf-rep-body');
      if (!r.ok) {
        repBtn.disabled = false;
        repBtn.textContent = t('failedRetry');
        if (body) body.innerHTML = `<div class="nf-hint">${esc(r.error || '')}</div>`;
        return;
      }
      const d = r.data;
      const label = { repeatable: t('repRepeatable'), mixed: t('repMixed'),
        one_off: t('repOneOff'), unknown: t('repUnknown') }[d.verdict] || d.verdict;
      repBtn.remove();
      if (body) body.innerHTML = `<div class="nf-row"><span class="nf-muted">${esc(t('repFormat'))}</span> <b>${esc(label)}</b>
        <span class="nf-hint">${esc(d.verdict === 'unknown' ? (d.reason === 'no-embedding' ? t('repNoEmbedding') : t('repFewSimilar'))
          : t('repOutlierAt', [d.channelsHit, d.channels]))} · ${esc(t('estimateFromDb'))}</span></div>`;
    });

    // план 26: снято ли похожее видео на другом языке (язык цели -- крупнейший другой в базе)
    const lgBtn = root.querySelector('.nf-lg-btn');
    if (lgBtn) lgBtn.addEventListener('click', async () => {
      lgBtn.disabled = true;
      lgBtn.textContent = t('searching');
      const r = await send({ type: 'languageGap', videoId });
      if (!root.isConnected) return;
      const body = root.querySelector('.nf-lg-body');
      if (!r.ok) {
        lgBtn.disabled = false;
        lgBtn.textContent = t('failedRetry');
        if (body) body.innerHTML = `<div class="nf-hint">${esc(r.error || '')}</div>`;
        return;
      }
      const d = r.data;
      lgBtn.remove();
      if (!body) return;
      if (!d.verdict) { body.innerHTML = `<div class="nf-hint">${esc(d.hint || t('lgNothing'))}</div>`; return; }
      const label = { open: t('lgOpen'), thin: t('lgThin'), covered: t('lgCovered') }[d.verdict] || d.verdict;
      const top = d.matches && d.matches[0];
      body.innerHTML = `<div class="nf-row"><span class="nf-muted">${esc(t('lgIn', d.targetLabel))}</span> <b>${esc(label)}</b>
        <span class="nf-hint">${top ? esc(top.title || '') + ' · ' : ''}${d.targetCorpus.thin ? esc(t('lgThinCorpus')) + ' · ' : ''}${esc(t('estimateFromDb'))}</span></div>`;
    });

    const whyBtn = root.querySelector('.nf-why-btn');
    if (whyBtn) whyBtn.addEventListener('click', async () => {
      whyBtn.disabled = true;
      whyBtn.textContent = t('asking');
      const body = root.querySelector('.nf-why-body');
      const r = await send({ type: 'why', videoId });
      if (!root.isConnected) return;
      if (!r.ok || !r.data || !r.data.hooks) {
        whyBtn.disabled = false;
        whyBtn.textContent = t('failedRetry');
        if (body) body.innerHTML = `<div class="nf-hint">${esc(
          (r.ok ? r.data?.hint : r.error) || t('llmUnavailable'))}</div>`;
        return;
      }
      whyBtn.remove();
      if (body) body.innerHTML = whyViralHtml(r.data);
    });

    // план 20: мини-график просмотров по снимкам вместо одной строки «история: N»
    const tj = await send({ type: 'trajectory', videoId });
    if (!root.isConnected || getVideoId() !== videoId) return;
    const foot = root.querySelector('.nf-foot');
    const tv = tj.ok && tj.data?.videos?.[0];
    if (foot && tv && tv.points.length >= 2) foot.innerHTML = sparkline(tv);

    const pk = await send({ type: 'packaging', videoId });
    if (!root.isConnected || getVideoId() !== videoId) return;
    const pkBox = root.querySelector('.nf-pk');
    if (pkBox && pk.ok && pk.data && pk.data.count > 0) pkBox.innerHTML = packagingHtml(pk.data);
  }

  function packagingHtml(p) {
    const titles = p.changes.filter((c) => c.field === 'title').length;
    const thumbs = p.changes.filter((c) => c.field === 'thumbnail_image').length;
    const parts = [];
    if (titles) parts.push(t('pkTitleCount', titles));
    if (thumbs) parts.push(t('pkThumbCount', thumbs));
    const dash = (SETTINGS?.baseUrl || 'http://127.0.0.1:8080') + '/#/packaging';
    return `<div class="nf-sec">
      <div class="nf-sec-h">${esc(tn('pkRepackaged', p.count))}</div>
      <div class="nf-hint">${esc(parts.join(' · '))} · ${esc(t('pkLastChange', ageText(
        (Date.now() - new Date(p.changes[0].changedAt).getTime()) / 864e5)))}</div>
      <a class="nf-btn nf-ghost" href="${esc(dash)}" target="_blank" rel="noopener">${esc(t('pkBeforeAfter'))}</a>
    </div>`;
  }

  function whyViralHtml(d) {
    const hooks = d.hooks || [];
    return `
      ${hooks.length ? `<div class="nf-hint"><b>${esc(t('whyHooks'))}</b></div>
        <ul class="nf-why-list">${hooks.map((h) => `<li>${esc(h)}</li>`).join('')}</ul>` : ''}
      ${d.title_pattern ? `<div class="nf-hint"><b>${esc(t('whyTitlePattern'))}</b> ${esc(d.title_pattern)}</div>` : ''}
      ${d.timing_factor ? `<div class="nf-hint"><b>${esc(t('whyTiming'))}</b> ${esc(d.timing_factor)}</div>` : ''}
      ${d.replicable_formula ? `<div class="nf-hint"><b>${esc(t('whyFormula'))}</b> ${esc(d.replicable_formula)}</div>` : ''}
      <div class="nf-hint">${esc(t('whyConfidence', Math.round((d.confidence || 0) * 100)))}</div>`;
  }

  function commentsHtml(data, videoPublishedAt) {
    const comments = data.comments || [];
    if (!comments.length) {
      return `<div class="nf-hint">${esc(t('commentsNone'))}</div>`;
    }
    let first24h = null;
    const pub = videoPublishedAt ? new Date(videoPublishedAt).getTime() : null;
    if (pub) {
      const cutoff = pub + 24 * 3600 * 1000;
      const early = comments.filter((c) => c.publishedAt && new Date(c.publishedAt).getTime() <= cutoff);
      first24h = t('commentsFirst24h', [early.length, comments.length]);
    }
    return `
      ${first24h ? `<div class="nf-hint">${esc(first24h)}</div>` : ''}
      <div class="nf-comments-list">
        ${comments.slice(0, 30).map((c) => `<div class="nf-comment">
          <div class="nf-comment-h"><b>${esc(c.author || '')}</b>
            <span class="nf-hint">${compact(c.likeCount)} ♥ · ${ageText((Date.now() - new Date(c.publishedAt || Date.now()).getTime()) / 86400000)}</span></div>
          <div class="nf-comment-t">${esc((c.text || '').slice(0, 300))}</div>
        </div>`).join('')}
      </div>
      <div class="nf-hint">${esc(t('commentsLoaded', comments.length))}</div>`;
  }

  // ------------------------------------------------------- страница канала

  const RISK_LEVEL = { low: t('riskLow'), medium: t('riskMedium'), high: t('riskHigh') };

  function riskRow(r) {
    if (!r || !r.found || r.level === 'insufficient-data') return '';
    const why = (r.reasons || []).map((x) => x.text).join('; ');
    return `<div class="nf-row"><span class="nf-muted">${esc(t('riskTemplated'))}</span>
      <b>${esc(RISK_LEVEL[r.level] || r.level)}</b> · ${r.score}/100
      <span class="nf-hint" title="${esc(why)}">${esc(why || t('riskNoSignals'))}.
        ${esc(t('riskHeuristic'))}</span></div>`;
  }

  // План 06: оценки дохода -- только реклама; вилка RPM ниши вместо одного числа.
  const AD_ONLY = t('adOnly');

  function nicheRpmRow(nm) {
    if (!nm || nm.monthly_usd_low == null || !nm.rpm_range) return '';
    const r = nm.rpm_range;
    return `<div class="nf-row"><span class="nf-muted">${esc(t('nicheRpm'))}</span>
      <b>$${decimal(nm.monthly_usd_low, 0)}–$${decimal(nm.monthly_usd_high, 0)}</b> ${esc(t('perMonth'))}
      <span class="nf-hint" title="${esc(r.basis || '')}">RPM $${decimal(r.low, 1)}–$${decimal(r.high, 1)} ·
        ${esc(t('rpmRough'))}</span></div>`;
  }

  // План 20: мини-график -- просмотры по возрасту из снимков, пунктир -- обычное
  // видео этого канала к тому же возрасту (оценка). Без библиотек, свой SVG.
  function sparkline(v) {
    const W = 300, H = 56, pad = 3;
    const x1 = Math.max(...v.points.map((p) => p.ageHours), 1);
    const inX = (pts) => pts.filter((p) => p.ageHours <= x1);
    const y1 = Math.max(...[...v.points, ...inX(v.expected)].map((p) => p.views), 1);
    const px = (h) => pad + (h / x1) * (W - 2 * pad);
    const py = (val) => H - pad - (val / y1) * (H - 2 * pad);
    const d = (pts) => inX(pts).map((p, i) => `${i ? 'L' : 'M'}${px(p.ageHours).toFixed(1)},${py(p.views).toFixed(1)}`).join('');
    const age = (h) => (h < 48 ? t('ageHours', Math.round(h)) : t('ageDaysShort', Math.round(h / 24)));
    return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" aria-label="${esc(t('sparkAria'))}">
        ${v.expected.length ? `<path d="${d(v.expected)}" fill="none" stroke="#898781" stroke-width="1.5" stroke-dasharray="4 3"/>` : ''}
        <path d="${d(v.points)}" fill="none" stroke="#3987e5" stroke-width="2"/></svg>
      <div class="nf-hint">${esc(tn('points', v.points.length))} · ${
        v.observedFromHours > 24 ? esc(t('sparkObservedFrom', age(v.observedFromHours))) + ' · ' : ''}${
        esc(t('sparkUpTo', age(x1)))} · ${esc(t('sparkDashed'))}</div>`;
  }

  // Пороги YPP (план 12) -- что видно по открытым данным, не статус монетизации.
  function yppRow(e) {
    if (!e) return '';
    const seen = compact(e.tiers?.full?.shortsViews90d?.seenAtLeast || 0);
    const text = {
      'below-threshold': t('yppBelow'),
      'subscribers-met': t('yppSubsMet'),
      'shorts-path-met': t('yppShortsMet', seen),
      unknown: t('yppUnknown'),
    }[e.status] || e.status;
    // план 17: с 01.02.2027 полный уровень -- 8 000 часов или 20 млн просмотров Shorts
    const next = e.upcoming ? ' · ' + t('yppUpcoming')
      : e.rules === '2027' ? ' · ' + t('yppRules2027') : '';
    return `<div class="nf-row"><span class="nf-muted">${esc(t('yppLabel'))}</span> ${esc(text + next)}
      <span class="nf-hint" title="${esc(e.note || '')}">${esc(t('yppNote'))}</span></div>`;
  }

  // Рубежи подписчиков (план 17) -- прямая по темпу наших снимков, не данные YouTube.
  function milestonesRow(ms) {
    const f = (ms?.forecasts || []).find((x) => !x.reached);
    if (!f) return '';
    const eta = f.eta30 || f.eta90;
    const text = eta ? `${compact(f.target)} ≈ ${new Date(eta).toLocaleDateString(UI_LANG)}`
      : t('milestoneNoEstimate', compact(f.target));
    return `<div class="nf-row"><span class="nf-muted">${esc(t('milestoneLabel'))}</span> ${esc(text)}
      <span class="nf-hint" title="${esc(ms.note || '')}">${esc(t('milestoneHint'))}</span></div>`;
  }

  function channelHtml(d, deep, risk) {
    const p = d.profile, m = d.metrics;
    const a = deep?.analytics?.found ? deep.analytics : null;
    const similar = (deep?.similar?.similar || []).slice(0, 5);
    const best = (deep?.bestTime?.best || []).slice(0, 4);
    const patterns = (deep?.patterns?.patterns || []).slice(0, 5);
    const outliers = (a?.topOutliers || []).slice(0, 5);

    const growthLine = m.subsGrowth
      ? t('growthPerDay', `${m.subsGrowth.per_day > 0 ? '+' : ''}${compact(m.subsGrowth.per_day)}`)
      : t('growthNone');

    return header(t('channelLabel'), statusChip(d)) + `
      <div class="nf-body">
        <div class="nf-grid">
          ${cell(t('cellSubscribers'), p.subscribersHidden ? esc(t('hidden')) : compact(p.subscribers))}
          ${cell(t('cellTotalViews'), compact(p.totalViews))}
          ${cell(t('cellVideos'), compact(p.videoCount))}
          ${cell(t('cellAvgPerVideo'), compact(m.avgViewsPerVideo))}
          ${cell(t('cellChannelAge'), ageText(p.ageDays))}
          ${cell(t('cellGrowthGrade'), m.grade || '—', t('cellGrowthGradeHint'))}
        </div>

        <div class="nf-row"><span class="nf-muted">${esc(t('dynamics'))}</span> ${esc(growthLine)}
          <span class="nf-hint">${esc(tn('snapshots', d.snapshots))}</span></div>
        ${riskRow(risk)}
        <div class="nf-policy"></div>
        ${yppRow(d.yppEligibility)}
        ${milestonesRow(d.milestones)}

        <div class="nf-row"><span class="nf-muted">${esc(t('monthlyRevenue'))}</span>
          <b>$${decimal(m.revenue.low_usd, 0)}–$${decimal(m.revenue.high_usd, 0)}</b>
          <span class="nf-hint">${esc(t('sbRange'))} · ${esc(AD_ONLY)}</span></div>
        ${nicheRpmRow(a?.revenue?.nicheModel)}
        <div class="nf-profit"></div>

        ${a ? `<div class="nf-row"><span class="nf-muted">${esc(t('cadence'))}</span>
          ${esc(t('cadenceLine', [decimal(a.cadence.uploadsPerWeekLifetime, 1),
            decimal(a.cadence.shortsSharePercent, 0), ageText(a.cadence.daysSinceLastUpload)]))}</div>` : ''}

        ${a?.profile?.aiLabels ? `<div class="nf-row"><span class="nf-muted">${esc(t('aiLabels'))}</span>
          <span class="nf-chip">${esc(a.profile.aiLabels.isFaceless ? 'faceless' : 'on-camera')} · ${esc(a.profile.aiLabels.contentFormat)}</span></div>` : ''}

        ${outliers.length ? `<div class="nf-sec">
          <div class="nf-sec-h">${esc(t('topOutliers'))}</div>
          ${outliers.map((o) => `<a class="nf-item" href="/watch?v=${esc(o.videoId)}">
            <span class="nf-mult nf-band-${bandLevel(o.band)}">×${decimal(o.outlierScore, 1)}</span>
            <span class="nf-item-t">${esc(o.title)}</span>
            <span class="nf-hint">${compact(o.views)}</span></a>`).join('')}
        </div>` : ''}

        ${best.length ? `<div class="nf-sec">
          <div class="nf-sec-h">${esc(t('whenToPost'))}</div>
          <div class="nf-slots">${best.map((b) => `<span class="nf-slot" title="${esc(t('slotTitle', [decimal(b.medianOutlier, 2), b.samples]))}">
            ${esc(b.weekday)} ${String(b.hour).padStart(2, '0')}:00
            <b>${b.score}</b></span>`).join('')}</div>
          <div class="nf-hint">${esc(t('slotsNote'))}</div>
        </div>` : ''}

        ${patterns.length ? `<div class="nf-sec">
          <div class="nf-sec-h">${esc(t('titlePhrases'))}</div>
          ${patterns.map((x) => `<div class="nf-item">
            <span class="nf-mult">×${decimal(x.outlierLift, 1)}</span>
            <span class="nf-item-t">${esc(x.keyword)}</span>
            <span class="nf-hint">${esc(tn('videosCount', x.videos))}</span></div>`).join('')}
        </div>` : ''}

        ${similar.length ? `<div class="nf-sec">
          <div class="nf-sec-h">${esc(t('similarChannels'))}</div>
          ${similar.map((s) => `<a class="nf-item" href="/channel/${esc(s.channelId)}">
            <span class="nf-item-t">${esc(s.title || s.channelId)}</span>
            <span class="nf-hint">${compact(s.subscriberCount)} · ${decimal(s.similarity * 100, 0)}%</span></a>`).join('')}
        </div>` : ''}

        ${!d.hasDeepAnalytics ? `<div class="nf-hint">
          ${esc(tn('needMoreVideos', d.stored.count))}</div>` : ''}

        <div class="nf-actions">
          <button class="nf-btn nf-collect">${esc(d.stored.count ? t('updateChannelInDb') : t('collectChannel100'))}</button>
          <button class="nf-btn nf-track">${esc(d.tracked ? t('untrack') : t('track'))}</button>
          <button class="nf-btn nf-ghost nf-save">${esc(t('addToSaved'))}</button>
          <a class="nf-btn nf-ghost nf-dash" target="_blank" rel="noopener">${esc(t('dashboard'))}</a>
        </div>
      </div>`;
  }

  async function renderChannel(ref, opts = {}) {
    const mount = await waitFor('#page-header, #channel-header, ytd-two-column-browse-results-renderer');
    if (!mount || channelRef() !== ref) return;
    const root = ensurePanel(mount, 'nf-panel-channel', 'after');
    root.innerHTML = skeleton(t('analysingChannel'));

    const res = await send({ type: 'channel', ref, refresh: !!opts.refresh });
    if (!root.isConnected || channelRef() !== ref) return;

    if (!res.ok) {
      root.innerHTML = errorHtml(res);
      wireCommon(root, () => renderChannel(ref, { refresh: true }));
      return;
    }
    const d = res.data;
    if (!d.found) {
      root.innerHTML = notFoundHtml(d, t('notFoundChannel'));
      wireCommon(root, () => renderChannel(ref, { refresh: true }));
      return;
    }

    let deep = null;
    if (d.hasDeepAnalytics) {
      const dr = await send({ type: 'deep', channelId: d.channelId });
      if (dr.ok) deep = dr.data;
      if (!root.isConnected || channelRef() !== ref) return;
    }

    let risk = null;
    if (d.hasDeepAnalytics) {
      const rr = await send({ type: 'templateRisk', channelId: d.channelId });
      if (rr.ok) risk = rr.data;
      if (!root.isConnected || channelRef() !== ref) return;
    }

    root.innerHTML = channelHtml(d, deep, risk);
    wireCommon(root, () => renderChannel(ref, { refresh: true }));

    // план 30: чистая прибыль в месяц по первому профилю расходов
    send({ type: 'profit', channelId: d.channelId }).then((pr) => {
      const box = root.querySelector('.nf-profit');
      const mo = pr.ok && pr.data?.found ? pr.data.month : null;
      if (!box || !mo) return;
      const label = { profitable: t('profitProfitable'), loss: t('profitLoss'), uncertain: t('profitUncertain') }[mo.verdict] || mo.verdict;
      const prof = pr.data.profile?.name ? t('profitProfile', pr.data.profile.name) : t('profitNoProfile');
      box.innerHTML = `<div class="nf-row"><span class="nf-muted">${esc(t('profitMonth'))}</span>
        <b>$${decimal(mo.profit.low, 0)}–$${decimal(mo.profit.high, 0)}</b> (${esc(label)})
        <span class="nf-hint">${esc(prof)} · ${esc(t('profitCosts'))} $${decimal(mo.cost, 0)}${pr.data.yppWarning ? ' · ' + esc(t('profitYppWarn')) : ''}</span></div>`;
    });

    // план 22: сигналы по правилам монетизации -- одной строкой, без процента риска
    if (d.hasDeepAnalytics) {
      send({ type: 'policySignals', channelId: d.channelId }).then((ps) => {
        const box = root.querySelector('.nf-policy');
        if (!box || !ps.ok || !ps.data?.found) return;
        const names = { generic_repetitive: t('policyGeneric'), unsatisfying: t('policyUnsatisfying'),
          ai_persona_sensitive: t('policyAiPersona') };
        const flagged = Object.entries(ps.data.categories)
          .filter(([, c]) => c.level === 'high' || c.level === 'watch')
          .map(([k, c]) => `${names[k]}${c.level === 'high' ? ' ' + t('policyNoticeable') : ''}`);
        box.innerHTML = `<div class="nf-row"><span class="nf-muted">${esc(t('policyLabel'))}</span>
          ${esc(flagged.length ? flagged.join(', ') : t('policyNone'))}
          <span class="nf-hint" title="${esc(ps.data.note || '')}">${esc(t('policyHint'))}</span></div>`;
      });
    }

    const dash = root.querySelector('.nf-dash');
    if (dash) dash.href = (SETTINGS?.baseUrl || 'http://127.0.0.1:8080');

    const collect = root.querySelector('.nf-collect');
    if (collect) collect.addEventListener('click', async () => {
      collect.disabled = true; collect.textContent = t('collecting');
      const r = await send({ type: 'collect', channel: d.channelId, maxVideos: 100 });
      collect.textContent = r.ok ? t('collectDone', r.data.videos_stored) : t('errorShort');
      setTimeout(() => renderChannel(ref, { refresh: true }), 1200);
    });

    const track = root.querySelector('.nf-track');
    if (track) track.addEventListener('click', async () => {
      track.disabled = true;
      const r = await send({ type: d.tracked ? 'untrack' : 'track', channelId: d.channelId });
      track.disabled = false;
      if (r.ok) { d.tracked = !d.tracked; track.textContent = d.tracked ? t('untrack') : t('track'); }
    });

    const save = root.querySelector('.nf-save');
    if (save) save.addEventListener('click', async () => {
      save.disabled = true;
      const r = await send({ type: 'save', kind: 'channel', refId: d.channelId, payload: d });
      save.textContent = r.ok ? t('saved') : t('errorShort');
      setTimeout(() => { save.disabled = false; save.textContent = t('addToSaved'); }, 1500);
    });
  }

  // ------------------------------------------- значки на карточках видео

  const seen = new Map();     // videoId -> набор превью (один ролик бывает в нескольких блоках)
  let queue = new Set();
  let flushTimer = null;
  const resultsInfo = new Map();   // videoId -> info, только на /results, для сводки по выдаче
  let summaryTimer = null;

  function videoIdFromHref(href) {
    if (!href) return null;
    const m = href.match(/[?&]v=([\w-]{11})/) || href.match(/\/shorts\/([\w-]{11})/);
    return m ? m[1] : null;
  }

  const cardObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      cardObserver.unobserve(e.target);
      const id = e.target.dataset.nfVid;
      if (id && !e.target.dataset.nfDone) queue.add(id);
    }
    scheduleFlush();
  }, { rootMargin: '200px' });

  function scheduleFlush() {
    if (flushTimer) return;
    flushTimer = setTimeout(flush, 500);
  }

  async function flush() {
    flushTimer = null;
    if (!SETTINGS?.showBadges || !queue.size) { queue.clear(); return; }
    const ids = [...queue].slice(0, 40);
    queue = new Set([...queue].slice(40));
    const res = await send({ type: 'batch', ids });
    if (res.ok) {
      for (const [id, info] of Object.entries(res.data.results || {})) applyBadge(id, info);
      if (location.pathname === '/results') {
        for (const [id, info] of Object.entries(res.data.results || {})) {
          if (info && info.found) resultsInfo.set(id, info);
        }
        scheduleSummaryRender();
      }
    }
    if (queue.size) scheduleFlush();
  }

  // ------------------------------------------------- сводка на странице выдачи

  function median(sorted) {
    const n = sorted.length;
    if (!n) return null;
    return n % 2 ? sorted[(n - 1) / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2;
  }

  function resultsSummaryHtml(list) {
    const total = list.length;
    const withScore = list.filter((v) => v.outlierScore != null);
    const outliers = withScore.filter((v) => v.outlierScore >= 2);
    const views = list.map((v) => v.views).filter((v) => v != null).sort((a, b) => a - b);
    const ages = list.map((v) => v.ageDays).filter((v) => v != null).sort((a, b) => a - b);
    const subs = list.map((v) => v.subscribers).filter((v) => v != null).sort((a, b) => a - b);
    const channels = new Set(list.map((v) => v.channelId).filter(Boolean));
    const shortsShare = total ? Math.round(list.filter((v) => v.isShort).length / total * 100) : 0;
    const medianViews = median(views), medianAge = median(ages), medianSubs = median(subs);

    return `
      <div class="nf-head">
        <div class="nf-logo">niche&#8209;finder</div>
        <div class="nf-sub">${esc(tn('resultsTitle', total))}</div>
      </div>
      <div class="nf-body">
        <div class="nf-grid">
          ${cell(t('cellOutliersX2'), total ? esc(t('outOf', [outliers.length, withScore.length])) : '—')}
          ${cell(t('cellMedianViews'), medianViews != null ? compact(medianViews) : '—')}
          ${cell(t('cellMedianAge'), medianAge != null ? ageText(medianAge) : '—')}
          ${cell('Shorts', total ? shortsShare + '%' : '—')}
          ${cell(t('cellChannelsInResults'), channels.size || '—')}
          ${cell(t('cellMedianSubs'), medianSubs != null ? compact(medianSubs) : '—')}
        </div>
        <div class="nf-hint">${esc(t('resultsNote'))}</div>
      </div>`;
  }

  async function renderResultsSummary() {
    if (location.pathname !== '/results') return;
    const mount = await waitFor('ytd-two-column-search-results-renderer #primary, #primary');
    if (!mount || location.pathname !== '/results') return;
    const root = ensurePanel(mount, 'nf-panel-results', 'prepend');
    root.innerHTML = resultsSummaryHtml([...resultsInfo.values()]);
  }

  function scheduleSummaryRender() {
    if (summaryTimer) return;
    summaryTimer = setTimeout(() => { summaryTimer = null; renderResultsSummary(); }, 400);
  }

  function applyBadge(videoId, info) {
    const anchors = seen.get(videoId);
    if (!anchors) return;
    for (const anchor of anchors) {
      if (!anchor.isConnected) continue;
      anchor.dataset.nfDone = '1';
      anchor.querySelectorAll('.nf-badge').forEach((b) => b.remove());
      if (!info || !info.found || info.outlierScore == null) continue;
      if (SETTINGS.onlyOutliers && info.outlierScore < 1.5) continue;

      const badge = document.createElement('span');
      badge.className = 'nf-badge nf-band-' + bandLevel(info.outlierBand);
      badge.textContent = '×' + decimal(info.outlierScore, info.outlierScore < 10 ? 1 : 0);
      badge.title = [
        info.channelTitle || '',
        t('badgeViewsAge', [compact(info.views), ageText(info.ageDays)]),
        info.viewsPerSubscriber != null ? t('badgeToSubs', decimal(info.viewsPerSubscriber, 2)) : '',
        t('badgeBandVsAvg', bandName(info.outlierBand)),
      ].filter(Boolean).join('\n');
      if (getComputedStyle(anchor).position === 'static') anchor.style.position = 'relative';
      anchor.appendChild(badge);

      if (SETTINGS.showVph) {
        const vph = info.vph24h != null ? info.vph24h : info.vphLifetime;
        if (vph != null && vph > 0) {
          const vBadge = document.createElement('span');
          vBadge.className = 'nf-badge nf-badge-vph';
          vBadge.textContent = t('vphBadge', compact(vph));
          vBadge.title = info.vph24h != null ? t('vphTitle24') : t('vphTitleAll');
          anchor.appendChild(vBadge);
        }
      }
    }
  }

  /* Одна карточка — один значок. Карточки ищем по именам кастомных элементов
     (ytd-video-renderer, yt-lockup-view-model и т.п.): они меняются заметно
     реже, чем css-классы, которые YouTube переименовывает каждые пару релизов.
     Внутри карточки берём ссылку-превью, а не заголовок и не главы из описания
     — иначе на один ролик прилетает до восьми значков. */
  const CARD_CONTAINERS = [
    'ytd-rich-item-renderer', 'ytd-video-renderer', 'ytd-compact-video-renderer',
    'ytd-grid-video-renderer', 'ytd-playlist-video-renderer', 'yt-lockup-view-model',
    'ytm-shorts-lockup-view-model', 'ytd-reel-item-renderer',
  ].join(', ');
  const THUMB_INSIDE = 'img, yt-image, ytd-thumbnail, .ytThumbnailViewModelHost';

  function pickThumbAnchor(card) {
    const list = [...card.querySelectorAll('a[href*="/watch?v="], a[href*="/shorts/"]')]
      .filter((a) => a.querySelector(THUMB_INSIDE));
    if (!list.length) return null;
    return list.find((a) => a.id === 'thumbnail')
      || list.sort((x, y) => y.getBoundingClientRect().width - x.getBoundingClientRect().width)[0];
  }

  function scanCards() {
    if (!SETTINGS?.showBadges) return;
    for (const card of document.querySelectorAll(CARD_CONTAINERS)) {
      const anchor = pickThumbAnchor(card);
      if (!anchor) continue;
      const id = videoIdFromHref(anchor.getAttribute('href'));
      if (!id) continue;
      if (card.dataset.nfCard === id) continue;   // ту же карточку второй раз не берём
      card.dataset.nfCard = id;                   // но переиспользованную лентой — берём
      anchor.dataset.nfVid = id;
      delete anchor.dataset.nfDone;
      anchor.querySelectorAll('.nf-badge').forEach((b) => b.remove());
      if (!seen.has(id)) seen.set(id, new Set());
      seen.get(id).add(anchor);
      cardObserver.observe(anchor);
    }
  }

  // Лента подгружается пачками — сканировать на каждое изменение DOM дорого.
  let scanTimer = null;
  function scheduleScan() {
    if (scanTimer) return;
    scanTimer = setTimeout(() => { scanTimer = null; scanCards(); }, 400);
  }

  // ------------------------------------------------------- маршрутизация

  function getVideoId() {
    if (location.pathname === '/watch') return new URLSearchParams(location.search).get('v');
    const m = location.pathname.match(/^\/shorts\/([\w-]{11})/);
    return m ? m[1] : null;
  }

  function channelRef() {
    const p = location.pathname;
    if (!/^\/(channel|c|user|@)/.test(p)) return null;
    const canon = document.querySelector('link[rel="canonical"]');
    const fromCanon = canon && canon.href.match(/channel\/(UC[\w-]{20,})/);
    if (fromCanon) return fromCanon[1];                 // UC-id не тратит квоту на резолв
    let m = p.match(/^\/channel\/(UC[\w-]+)/); if (m) return m[1];
    m = p.match(/^\/(@[^/?#]+)/); if (m) return decodeURIComponent(m[1]);
    m = p.match(/^\/(?:c|user)\/([^/?#]+)/); if (m) return decodeURIComponent(m[1]);
    return null;
  }

  async function route() {
    SETTINGS = (await send({ type: 'settings:get' })).data || SETTINGS;
    const key = location.pathname + location.search;
    if (key === currentKey) { scanCards(); return; }
    currentKey = key;
    removePanels();
    seen.clear(); queue.clear(); resultsInfo.clear();

    const videoId = getVideoId();
    if (videoId && location.pathname === '/watch') {
      renderWatch(videoId);
    } else if (location.pathname === '/results') {
      renderResultsSummary();
    } else {
      const ref = channelRef();
      if (ref) renderChannel(ref);
    }
    scanCards();
  }

  // YouTube шлёт своё событие после каждой SPA-навигации; интервал — страховка
  // на случай, если событие поменяют или страница откроется без него.
  window.addEventListener('yt-navigate-finish', () => setTimeout(route, 60));
  let lastHref = location.href;
  setInterval(() => {
    if (location.href !== lastHref) { lastHref = location.href; route(); }
  }, 900);

  // Новые карточки догружаются бесконечной лентой — ловим их точечно.
  const listObserver = new MutationObserver(() => scheduleScan());
  listObserver.observe(document.documentElement, { childList: true, subtree: true });

  chrome.storage.onChanged.addListener(async () => {
    SETTINGS = (await send({ type: 'settings:get' })).data || SETTINGS;
    document.querySelectorAll('.nf-badge').forEach((b) => b.remove());
    document.querySelectorAll('[data-nf-done]').forEach((el) => delete el.dataset.nfDone);
    document.querySelectorAll('[data-nf-card]').forEach((el) => delete el.dataset.nfCard);
    currentKey = '';
    route();
  });

  route();
})();
