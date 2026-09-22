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
        } else resolve(res || { ok: false, error: 'пустой ответ' });
      });
    } catch (e) { resolve({ ok: false, error: String(e) }); }
  });

  const RU = new Intl.NumberFormat('ru-RU');
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

  function compact(n) {
    if (n == null || Number.isNaN(n)) return '—';
    const a = Math.abs(n);
    const cut = (v, s) => (v).toFixed(v >= 100 ? 0 : 1).replace('.', ',').replace(',0', '') + s;
    if (a >= 1e9) return cut(n / 1e9, ' млрд');
    if (a >= 1e6) return cut(n / 1e6, ' млн');
    if (a >= 1e3) return cut(n / 1e3, ' тыс.');
    return RU.format(Math.round(n));
  }

  function decimal(n, digits = 2) {
    if (n == null || Number.isNaN(n)) return '—';
    return Number(n).toFixed(digits).replace('.', ',').replace(/,?0+$/, '') || '0';
  }

  function pct(n) { return n == null ? '—' : decimal(n, 2) + '%'; }

  function plural(n, one, few, many) {
    const a = Math.abs(Math.round(n || 0)) % 100, b = a % 10;
    if (a > 10 && a < 20) return many;
    if (b > 1 && b < 5) return few;
    if (b === 1) return one;
    return many;
  }

  function duration(sec) {
    if (!sec) return '—';
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return (h ? h + ':' + String(m).padStart(2, '0') : String(m)) + ':' + String(s).padStart(2, '0');
  }

  function ageText(days) {
    if (days == null) return '—';
    if (days < 1) return Math.max(1, Math.round(days * 24)) + ' ч';
    if (days < 45) return Math.round(days) + ' дн.';
    if (days < 400) return Math.round(days / 30) + ' мес.';
    return decimal(days / 365, 1) + ' г.';
  }

  const BANDS = {
    'normal': ['обычное', 1],
    'above average': ['выше среднего', 2],
    'noteworthy': ['заметное', 3],
    'strong outlier': ['сильный выброс', 4],
    'viral': ['вирусное', 5],
    'mega outlier': ['мега-выброс', 6],
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
      <button class="nf-icon-btn nf-refresh" title="Обновить (потратит 1-2 units квоты)">⟳</button>
    </div>`;

  const skeleton = (text) => header(text) + `
    <div class="nf-body"><div class="nf-skeleton"></div><div class="nf-skeleton"></div>
    <div class="nf-skeleton nf-short"></div></div>`;

  function errorHtml(res) {
    const offline = res.offline;
    return header('нет связи с бэкендом') + `
      <div class="nf-body">
        <div class="nf-error">${esc(res.error || 'ошибка')}</div>
        ${offline ? `<div class="nf-hint">Запустите бэкенд проекта:<br>
          <code>make dev</code> или <code>docker compose up -d web</code>,<br>
          затем нажмите «Повторить».</div>` : ''}
        <button class="nf-btn nf-retry">Повторить</button>
      </div>`;
  }

  function statusChip(d) {
    const fromApi = d.fetchedFromApi;
    const label = fromApi ? `дозагружено · ${d.quotaUnits || 0} units` : 'из локальной базы';
    return `<span class="nf-chip ${fromApi ? 'nf-chip-api' : ''}" title="${
      fromApi ? 'Данных не было в базе или они устарели — добрали из YouTube Data API и сохранили'
              : 'Ответ полностью из локального Postgres, квота не потрачена'}">${esc(label)}</span>`;
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
      ? `медиана последних ${m.baselineSample} роликов канала: ${compact(m.baselineMedianViews)}`
      : (c.avgViews ? `среднее по каналу за всё время: ${compact(c.avgViews)}` : 'базы сравнения нет');

    const vph = m.vph24h != null ? m.vph24h : m.vphLifetime;
    const vphLabel = m.vph24h != null ? 'просмотров/час (24ч)' : 'просмотров/час (за всё время)';
    const accel = m.acceleration;

    const tags = (v.tags || []).slice(0, 18);
    const tagsHtml = tags.length
      ? `<div class="nf-tags">${tags.map((t) => `<span class="nf-tag">${esc(t)}</span>`).join('')}</div>
         <button class="nf-link nf-copy-tags">Скопировать все теги (${(v.tags || []).length})</button>`
      : `<div class="nf-hint">Тегов нет — автор их не задал или они скрыты.</div>`;

    return header(v.isShort ? 'Shorts' : 'Ролик', statusChip(d)) + `
      <div class="nf-body">
        <div class="nf-hero nf-band-${lvl}">
          <div class="nf-score">${score != null ? '×' + decimal(score, score < 10 ? 2 : 1) : '—'}</div>
          <div class="nf-hero-txt">
            <div class="nf-band">${esc(bandName(m.outlierBand))}</div>
            <div class="nf-hint">${esc(baseText)}</div>
          </div>
        </div>

        <div class="nf-grid">
          ${cell('просмотры', compact(v.views))}
          ${cell(vphLabel, compact(vph), 'Считается по нашим же снапшотам, если история есть')}
          ${cell('на подписчика', m.viewsPerSubscriber != null ? '×' + decimal(m.viewsPerSubscriber, 2) : '—',
            'VSR: >1 значит, что ролик вышел далеко за пределы своей аудитории')}
          ${cell('вовлечённость', pct(m.engagementRate), '(лайки + комментарии) / просмотры')}
          ${cell('прогноз к 30 дню', compact(m.projected30dViews),
            'Просмотры, приведённые к общему горизонту 30 дней по кривой созревания')}
          ${cell('возраст', ageText(v.ageDays) + (v.durationSeconds ? ' · ' + duration(v.durationSeconds) : ''))}
        </div>

        ${accel ? `<div class="nf-note ${accel >= 1.5 ? 'nf-hot' : (accel <= 0.7 ? 'nf-cold' : '')}">
          Ускорение ×${decimal(accel, 2)} — ${accel >= 1.5 ? 'ролик разгоняется' : accel <= 0.7 ? 'ролик остывает' : 'ровный темп'}
          <span class="nf-hint">(24ч против недели)</span></div>` : ''}

        <div class="nf-row">
          <span class="nf-muted">Доход, оценка:</span>
          <b>$${decimal(d.revenue.lifetime.low_usd, 0)}–$${decimal(d.revenue.lifetime.high_usd, 0)}</b>
          <span class="nf-hint">за всё время, диапазон Social Blade</span>
        </div>

        <div class="nf-channel">
          <div class="nf-ch-title">${esc(c.title || 'канал')}</div>
          <div class="nf-hint">
            ${c.subscribersHidden ? 'подписчики скрыты'
              : compact(c.subscribers) + ' ' + plural(c.subscribers, 'подписчик', 'подписчика', 'подписчиков')} ·
            ${compact(c.avgViews)} в среднем на ролик ·
            ${c.videosStored ? c.videosStored + ' ' + plural(c.videosStored, 'ролик', 'ролика', 'роликов') + ' в базе'
                             : 'в базе роликов нет'}
          </div>
        </div>

        ${tagsHtml}

        <div class="nf-actions">
          <button class="nf-btn nf-collect">${c.videosStored ? 'Обновить канал в базе' : 'Собрать канал (100 видео)'}</button>
          <button class="nf-btn nf-track">${c.tracked ? 'Не отслеживать' : 'Отслеживать'}</button>
          <button class="nf-btn nf-ghost nf-save">В избранное</button>
          <a class="nf-btn nf-ghost nf-dash" target="_blank" rel="noopener">Дашборд</a>
        </div>
        ${similarList.length ? `<div class="nf-sec">
          <div class="nf-sec-h">Похожие видео</div>
          ${similarList.map((s) => `<a class="nf-item" href="/watch?v=${esc(s.videoId)}">
            <span class="nf-item-t">${esc(s.title || s.videoId)}</span>
            <span class="nf-hint">${compact(s.views)} · ${decimal(s.similarity * 100, 0)}%${s.sameChannel ? ' · свой канал' : ''}</span></a>`).join('')}
          <div class="nf-hint">Ищем только в собранной базе, не по всему YouTube — пусто не значит «похожих нет».</div>
        </div>` : ''}

        <div class="nf-sec nf-comments-sec">
          <button class="nf-btn nf-ghost nf-comments-btn">Показать комментарии (1 unit квоты)</button>
          <div class="nf-comments-body"></div>
        </div>

        ${llmOn ? `<div class="nf-sec nf-why-sec">
          <button class="nf-btn nf-ghost nf-why-btn">Почему выстрелило?</button>
          <div class="nf-why-body"></div>
        </div>` : ''}

        <div class="nf-foot">${d.history.points > 1
          ? `история: ${d.history.points} ${plural(d.history.points, 'снапшот', 'снапшота', 'снапшотов')}`
          : 'история пустая — запустите воркер, чтобы появились скорость и ускорение'}</div>
      </div>`;
  }

  function notFoundHtml(d, what) {
    return header('нет данных', '') + `
      <div class="nf-body">
        <div class="nf-hint">${esc(d.hint || 'не нашли ' + what)}</div>
        <button class="nf-btn nf-retry">Повторить</button>
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
    root.innerHTML = skeleton('анализ ролика…');

    const res = await send({ type: 'video', videoId, refresh: !!opts.refresh });
    if (!root.isConnected || getVideoId() !== videoId) return;

    if (!res.ok) {
      root.innerHTML = errorHtml(res);
      wireCommon(root, () => renderWatch(videoId, { refresh: true }));
      return;
    }
    const d = res.data;
    if (!d.found) {
      root.innerHTML = notFoundHtml(d, 'ролик');
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

    const copy = root.querySelector('.nf-copy-tags');
    if (copy) copy.addEventListener('click', async () => {
      await navigator.clipboard.writeText((d.video.tags || []).join(', '));
      copy.textContent = 'Скопировано';
      setTimeout(() => { copy.textContent = `Скопировать все теги (${(d.video.tags || []).length})`; }, 1500);
    });

    const collect = root.querySelector('.nf-collect');
    if (collect) collect.addEventListener('click', async () => {
      collect.disabled = true; collect.textContent = 'Собираю…';
      const r = await send({ type: 'collect', channel: d.channel.channelId, maxVideos: 100 });
      collect.textContent = r.ok ? `Готово: ${r.data.videos_stored} роликов` : 'Ошибка';
      setTimeout(() => renderWatch(videoId, { refresh: true }), 1200);
    });

    const track = root.querySelector('.nf-track');
    if (track) track.addEventListener('click', async () => {
      track.disabled = true;
      const r = await send({ type: d.channel.tracked ? 'untrack' : 'track', channelId: d.channel.channelId });
      track.disabled = false;
      if (r.ok) { d.channel.tracked = !d.channel.tracked; track.textContent = d.channel.tracked ? 'Не отслеживать' : 'Отслеживать'; }
    });

    const save = root.querySelector('.nf-save');
    if (save) save.addEventListener('click', async () => {
      save.disabled = true;
      const r = await send({ type: 'save', kind: 'video', refId: d.videoId, payload: d });
      save.textContent = r.ok ? 'Сохранено' : 'Ошибка';
      setTimeout(() => { save.disabled = false; save.textContent = 'В избранное'; }, 1500);
    });

    const commentsBtn = root.querySelector('.nf-comments-btn');
    if (commentsBtn) commentsBtn.addEventListener('click', async () => {
      commentsBtn.disabled = true;
      commentsBtn.textContent = 'Загружаю…';
      const body = root.querySelector('.nf-comments-body');
      const r = await send({ type: 'comments', videoId, maxResults: 50 });
      if (!root.isConnected) return;
      if (!r.ok) {
        commentsBtn.disabled = false;
        commentsBtn.textContent = 'Не получилось, повторить';
        if (body) body.innerHTML = `<div class="nf-error">${esc(r.error || 'ошибка')}</div>`;
        return;
      }
      commentsBtn.remove();
      if (body) body.innerHTML = commentsHtml(r.data, d.video.publishedAt);
    });

    const whyBtn = root.querySelector('.nf-why-btn');
    if (whyBtn) whyBtn.addEventListener('click', async () => {
      whyBtn.disabled = true;
      whyBtn.textContent = 'Спрашиваю…';
      const body = root.querySelector('.nf-why-body');
      const r = await send({ type: 'why', videoId });
      if (!root.isConnected) return;
      if (!r.ok || !r.data || !r.data.hooks) {
        whyBtn.disabled = false;
        whyBtn.textContent = 'Не получилось, повторить';
        if (body) body.innerHTML = `<div class="nf-hint">${esc(
          (r.ok ? r.data?.hint : r.error) || 'LLM недоступен')}</div>`;
        return;
      }
      whyBtn.remove();
      if (body) body.innerHTML = whyViralHtml(r.data);
    });
  }

  function whyViralHtml(d) {
    const hooks = d.hooks || [];
    return `
      ${hooks.length ? `<div class="nf-hint"><b>Зацепки:</b></div>
        <ul class="nf-why-list">${hooks.map((h) => `<li>${esc(h)}</li>`).join('')}</ul>` : ''}
      ${d.title_pattern ? `<div class="nf-hint"><b>Паттерн заголовка:</b> ${esc(d.title_pattern)}</div>` : ''}
      ${d.timing_factor ? `<div class="nf-hint"><b>Время публикации:</b> ${esc(d.timing_factor)}</div>` : ''}
      ${d.replicable_formula ? `<div class="nf-hint"><b>Формула:</b> ${esc(d.replicable_formula)}</div>` : ''}
      <div class="nf-hint">уверенность: ${Math.round((d.confidence || 0) * 100)}%</div>`;
  }

  function commentsHtml(data, videoPublishedAt) {
    const comments = data.comments || [];
    if (!comments.length) {
      return `<div class="nf-hint">Комментариев нет или они отключены.</div>`;
    }
    let first24h = null;
    const pub = videoPublishedAt ? new Date(videoPublishedAt).getTime() : null;
    if (pub) {
      const cutoff = pub + 24 * 3600 * 1000;
      const early = comments.filter((c) => c.publishedAt && new Date(c.publishedAt).getTime() <= cutoff);
      first24h = `${early.length} из ${comments.length} загруженных — в первые сутки после публикации`;
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
      <div class="nf-hint">Загружено ${comments.length} — сырой текст, без анализа тональности; читайте сами, что просят и на что жалуются.</div>`;
  }

  // ------------------------------------------------------- страница канала

  function channelHtml(d, deep) {
    const p = d.profile, m = d.metrics;
    const a = deep?.analytics?.found ? deep.analytics : null;
    const similar = (deep?.similar?.similar || []).slice(0, 5);
    const best = (deep?.bestTime?.best || []).slice(0, 4);
    const patterns = (deep?.patterns?.patterns || []).slice(0, 5);
    const outliers = (a?.topOutliers || []).slice(0, 5);

    const growthLine = m.subsGrowth
      ? `${m.subsGrowth.per_day > 0 ? '+' : ''}${compact(m.subsGrowth.per_day)} подписчиков/день`
      : 'роста пока не видно — мало снапшотов';

    return header('Канал', statusChip(d)) + `
      <div class="nf-body">
        <div class="nf-grid">
          ${cell('подписчики', p.subscribersHidden ? 'скрыты' : compact(p.subscribers))}
          ${cell('всего просмотров', compact(p.totalViews))}
          ${cell('роликов', compact(p.videoCount))}
          ${cell('в среднем на ролик', compact(m.avgViewsPerVideo))}
          ${cell('возраст канала', ageText(p.ageDays))}
          ${cell('оценка роста', m.grade || '—', 'Импульс Social Blade: недавний темп против пожизненного')}
        </div>

        <div class="nf-row"><span class="nf-muted">Динамика:</span> ${esc(growthLine)}
          <span class="nf-hint">${d.snapshots} ${plural(d.snapshots, 'снапшот', 'снапшота', 'снапшотов')}</span></div>

        <div class="nf-row"><span class="nf-muted">Доход в месяц, оценка:</span>
          <b>$${decimal(m.revenue.low_usd, 0)}–$${decimal(m.revenue.high_usd, 0)}</b></div>

        ${a ? `<div class="nf-row"><span class="nf-muted">Ритм:</span>
          ${decimal(a.cadence.uploadsPerWeekLifetime, 1)} видео/нед. ·
          Shorts ${decimal(a.cadence.shortsSharePercent, 0)}% ·
          последняя загрузка ${ageText(a.cadence.daysSinceLastUpload)} назад</div>` : ''}

        ${a?.profile?.aiLabels ? `<div class="nf-row"><span class="nf-muted">AI-разметка:</span>
          <span class="nf-chip">${esc(a.profile.aiLabels.isFaceless ? 'faceless' : 'on-camera')} · ${esc(a.profile.aiLabels.contentFormat)}</span></div>` : ''}

        ${outliers.length ? `<div class="nf-sec">
          <div class="nf-sec-h">Лучшие выбросы канала</div>
          ${outliers.map((o) => `<a class="nf-item" href="/watch?v=${esc(o.videoId)}">
            <span class="nf-mult nf-band-${bandLevel(o.band)}">×${decimal(o.outlierScore, 1)}</span>
            <span class="nf-item-t">${esc(o.title)}</span>
            <span class="nf-hint">${compact(o.views)}</span></a>`).join('')}
        </div>` : ''}

        ${best.length ? `<div class="nf-sec">
          <div class="nf-sec-h">Когда публиковать</div>
          <div class="nf-slots">${best.map((b) => `<span class="nf-slot" title="медианный выброс ×${decimal(b.medianOutlier, 2)}, выборка ${b.samples}">
            ${esc(b.weekday)} ${String(b.hour).padStart(2, '0')}:00
            <b>${b.score}</b></span>`).join('')}</div>
          <div class="nf-hint">Оценка относительная, лучший слот = 100. Время в вашем часовом поясе. Корреляция, не причина.</div>
        </div>` : ''}

        ${patterns.length ? `<div class="nf-sec">
          <div class="nf-sec-h">Фразы в заголовках, которые чаще дают выброс</div>
          ${patterns.map((x) => `<div class="nf-item">
            <span class="nf-mult">×${decimal(x.outlierLift, 1)}</span>
            <span class="nf-item-t">${esc(x.keyword)}</span>
            <span class="nf-hint">${x.videos} ${plural(x.videos, 'видео', 'видео', 'видео')}</span></div>`).join('')}
        </div>` : ''}

        ${similar.length ? `<div class="nf-sec">
          <div class="nf-sec-h">Похожие каналы</div>
          ${similar.map((s) => `<a class="nf-item" href="/channel/${esc(s.channelId)}">
            <span class="nf-item-t">${esc(s.title || s.channelId)}</span>
            <span class="nf-hint">${compact(s.subscriberCount)} · ${decimal(s.similarity * 100, 0)}%</span></a>`).join('')}
        </div>` : ''}

        ${!d.hasDeepAnalytics ? `<div class="nf-hint">
          В базе ${d.stored.count} ${plural(d.stored.count, 'ролик', 'ролика', 'роликов')} этого канала — для выбросов, времени публикации и
          паттернов нужно хотя бы 5. Нажмите «Собрать канал».</div>` : ''}

        <div class="nf-actions">
          <button class="nf-btn nf-collect">${d.stored.count ? 'Обновить канал в базе' : 'Собрать канал (100 видео)'}</button>
          <button class="nf-btn nf-track">${d.tracked ? 'Не отслеживать' : 'Отслеживать'}</button>
          <button class="nf-btn nf-ghost nf-save">В избранное</button>
          <a class="nf-btn nf-ghost nf-dash" target="_blank" rel="noopener">Дашборд</a>
        </div>
      </div>`;
  }

  async function renderChannel(ref, opts = {}) {
    const mount = await waitFor('#page-header, #channel-header, ytd-two-column-browse-results-renderer');
    if (!mount || channelRef() !== ref) return;
    const root = ensurePanel(mount, 'nf-panel-channel', 'after');
    root.innerHTML = skeleton('анализ канала…');

    const res = await send({ type: 'channel', ref, refresh: !!opts.refresh });
    if (!root.isConnected || channelRef() !== ref) return;

    if (!res.ok) {
      root.innerHTML = errorHtml(res);
      wireCommon(root, () => renderChannel(ref, { refresh: true }));
      return;
    }
    const d = res.data;
    if (!d.found) {
      root.innerHTML = notFoundHtml(d, 'канал');
      wireCommon(root, () => renderChannel(ref, { refresh: true }));
      return;
    }

    let deep = null;
    if (d.hasDeepAnalytics) {
      const dr = await send({ type: 'deep', channelId: d.channelId });
      if (dr.ok) deep = dr.data;
      if (!root.isConnected || channelRef() !== ref) return;
    }

    root.innerHTML = channelHtml(d, deep);
    wireCommon(root, () => renderChannel(ref, { refresh: true }));

    const dash = root.querySelector('.nf-dash');
    if (dash) dash.href = (SETTINGS?.baseUrl || 'http://127.0.0.1:8080');

    const collect = root.querySelector('.nf-collect');
    if (collect) collect.addEventListener('click', async () => {
      collect.disabled = true; collect.textContent = 'Собираю…';
      const r = await send({ type: 'collect', channel: d.channelId, maxVideos: 100 });
      collect.textContent = r.ok ? `Готово: ${r.data.videos_stored} роликов` : 'Ошибка';
      setTimeout(() => renderChannel(ref, { refresh: true }), 1200);
    });

    const track = root.querySelector('.nf-track');
    if (track) track.addEventListener('click', async () => {
      track.disabled = true;
      const r = await send({ type: d.tracked ? 'untrack' : 'track', channelId: d.channelId });
      track.disabled = false;
      if (r.ok) { d.tracked = !d.tracked; track.textContent = d.tracked ? 'Не отслеживать' : 'Отслеживать'; }
    });

    const save = root.querySelector('.nf-save');
    if (save) save.addEventListener('click', async () => {
      save.disabled = true;
      const r = await send({ type: 'save', kind: 'channel', refId: d.channelId, payload: d });
      save.textContent = r.ok ? 'Сохранено' : 'Ошибка';
      setTimeout(() => { save.disabled = false; save.textContent = 'В избранное'; }, 1500);
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
        <div class="nf-sub">Итоги выдачи · ${total} ${plural(total, 'ролик', 'ролика', 'роликов')} проверено</div>
      </div>
      <div class="nf-body">
        <div class="nf-grid">
          ${cell('выбросов (×≥2)', total ? `${outliers.length} из ${withScore.length}` : '—')}
          ${cell('медиана просмотров', medianViews != null ? compact(medianViews) : '—')}
          ${cell('медианный возраст', medianAge != null ? ageText(medianAge) : '—')}
          ${cell('Shorts', total ? shortsShare + '%' : '—')}
          ${cell('каналов в выдаче', channels.size || '—')}
          ${cell('медиана подписчиков', medianSubs != null ? compact(medianSubs) : '—')}
        </div>
        <div class="nf-hint">Считается по уже загруженным значкам — прокрутите ленту, чтобы захватить больше карточек.</div>
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
        `${compact(info.views)} просмотров · ${ageText(info.ageDays)}`,
        info.viewsPerSubscriber != null ? `×${decimal(info.viewsPerSubscriber, 2)} к подписчикам` : '',
        `${bandName(info.outlierBand)} относительно среднего по каналу`,
      ].filter(Boolean).join('\n');
      if (getComputedStyle(anchor).position === 'static') anchor.style.position = 'relative';
      anchor.appendChild(badge);

      if (SETTINGS.showVph) {
        const vph = info.vph24h != null ? info.vph24h : info.vphLifetime;
        if (vph != null && vph > 0) {
          const vBadge = document.createElement('span');
          vBadge.className = 'nf-badge nf-badge-vph';
          vBadge.textContent = compact(vph) + '/ч';
          vBadge.title = info.vph24h != null
            ? 'просмотров в час за последние 24 часа (наши снапшоты)'
            : 'просмотров в час в среднем за всё время — истории снапшотов ещё нет';
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
