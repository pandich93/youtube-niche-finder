/* Service worker: единственное место, которое ходит в сеть.
 *
 * Почему не из content script: страница YouTube отдаётся по https, а бэкенд
 * слушает http://127.0.0.1 — из контекста страницы это смешанный контент.
 * У service worker'а свой origin (chrome-extension://), и с host_permissions
 * запрос к локальной машине проходит без CORS-плясок.
 *
 * Здесь же кэш: YouTube — SPA, и панель перерисовывается по несколько раз на
 * одно видео. Без кэша это были бы лишние запросы к базе на каждую перерисовку.
 */

const DEFAULTS = {
  baseUrl: 'http://127.0.0.1:8080',
  autoFetch: true,      // добирать из YouTube API то, чего нет в базе (1-2 units)
  showBadges: true,     // значки на карточках видео в выдаче
  onlyOutliers: false,  // показывать значок только при score >= 1.5
  showVph: true,        // второй значок — просмотров/час под превью
  timeoutMs: 12000,
};

const TTL = { video: 120e3, channel: 300e3, deep: 300e3, batch: 600e3, health: 30e3, comments: 1800e3,
             stats: 60e3, why: 1800e3 };

async function getSettings() {
  const stored = await chrome.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...stored };
}

// ------------------------------------------------------------------- кэш

const cache = new Map();

function cacheGet(key, ttl) {
  const hit = cache.get(key);
  if (hit && Date.now() - hit.t < ttl) return hit.v;
  if (hit) cache.delete(key);
  return null;
}

function cacheSet(key, v) {
  cache.set(key, { t: Date.now(), v });
  if (cache.size > 600) {
    const oldest = [...cache.entries()].sort((a, b) => a[1].t - b[1].t).slice(0, 200);
    for (const [k] of oldest) cache.delete(k);
  }
}

function cacheDropVideo(id) { cache.delete('video:' + id); cache.delete('card:' + id); }

// ------------------------------------------------------------------- сеть

async function api(path, { method = 'GET', body = null } = {}) {
  const s = await getSettings();
  const base = (s.baseUrl || DEFAULTS.baseUrl).replace(/\/+$/, '');
  let res;
  try {
    res = await fetch(base + path, {
      method,
      // X-NF-Client на каждом запросе: бэкенд принимает POST/DELETE только с ним
      // или с JSON-телом, иначе 403 (защита от чужих сайтов, api.py local_only_guard)
      headers: body
        ? { 'Content-Type': 'application/json', 'X-NF-Client': 'extension' }
        : { 'X-NF-Client': 'extension' },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(s.timeoutMs),
    });
  } catch (e) {
    const err = new Error(`бэкенд не отвечает (${base}). Запущен ли он? make dev`);
    err.offline = true;
    throw err;
  }
  const text = await res.text();
  let data;
  try { data = JSON.parse(text); } catch { data = { detail: text.slice(0, 300) }; }
  if (!res.ok) {
    const err = new Error(data.detail || data.error || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

// --------------------------------------------------------------- сценарии

async function inspectVideo(videoId, refresh) {
  const key = 'video:' + videoId;
  if (!refresh) {
    const hit = cacheGet(key, TTL.video);
    if (hit) return hit;
  }
  const s = await getSettings();
  const q = new URLSearchParams({ video_id: videoId, fetch: String(s.autoFetch) });
  if (refresh) q.set('refresh', 'true');
  const data = await api('/api/inspect/video?' + q);
  cacheSet(key, data);
  return data;
}

async function inspectChannel(ref, refresh) {
  const key = 'channel:' + ref;
  if (!refresh) {
    const hit = cacheGet(key, TTL.channel);
    if (hit) return hit;
  }
  const s = await getSettings();
  const q = new URLSearchParams({ ref, fetch: String(s.autoFetch) });
  if (refresh) q.set('refresh', 'true');
  const data = await api('/api/inspect/channel?' + q);
  cacheSet(key, data);
  return data;
}

/* Глубокая аналитика канала — только если в базе уже есть его ролики.
 * Четыре запроса параллельно, каждый читает локальный Postgres и ничего не
 * стоит по квоте. Падение любого из них не должно ронять панель целиком. */
async function channelDeep(channelId) {
  const key = 'deep:' + channelId;
  const hit = cacheGet(key, TTL.deep);
  if (hit) return hit;
  const tzOffset = -new Date().getTimezoneOffset() / 60;
  const soft = (p) => api(p).catch(() => null);
  const [analytics, similar, bestTime, patterns] = await Promise.all([
    soft(`/api/channels/${channelId}?period=30d`),
    soft(`/api/channels/${channelId}/similar?limit=6`),
    soft(`/api/best-time?channel_id=${channelId}&period=180d&timezone_offset_hours=${tzOffset}`),
    soft(`/api/title-patterns?channel_id=${channelId}&period=180d&top_n=8`),
  ]);
  const data = { analytics, similar, bestTime, patterns };
  cacheSet(key, data);
  return data;
}

async function similarVideos(videoId) {
  const key = 'similar:' + videoId;
  const hit = cacheGet(key, TTL.deep);
  if (hit) return hit;
  const data = await api(`/api/videos/${videoId}/similar?limit=6`).catch(() => null);
  if (data) cacheSet(key, data);
  return data;
}

async function videoComments(videoId, maxResults = 50) {
  /* POST because it spends 1 unit of quota (commentThreads.list) -- unlike
     everything else the panel shows automatically, this only runs when the
     person clicks "Показать комментарии", and is cached afterwards so
     re-opening the panel within TTL.comments is free. */
  const key = 'comments:' + videoId;
  const hit = cacheGet(key, TTL.comments);
  if (hit) return hit;
  const data = await api(`/api/videos/${videoId}/comments`, {
    method: 'POST', body: { max_results: maxResults },
  });
  cacheSet(key, data);
  return data;
}

async function stats() {
  const hit = cacheGet('stats', TTL.stats);
  if (hit) return hit;
  const data = await api('/api/stats');
  cacheSet('stats', data);
  return data;
}

async function explainOutlier(videoId, refresh) {
  /* Stage 05 -- zero YouTube quota, but a real LLM call on a cache miss (the
     server also caches for LLM_WHY_VIRAL_TTL_DAYS; this is just the
     extension's own short-lived copy so re-opening the panel is free). The
     server returns 204 with no body when LLM_PROVIDER=none -- api() below
     resolves that to null, same as a cache miss looks to the caller. */
  const key = 'why:' + videoId;
  if (!refresh) {
    const hit = cacheGet(key, TTL.why);
    if (hit !== undefined) return hit;
  }
  const data = await api(`/api/video/${videoId}/why${refresh ? '?force_refresh=true' : ''}`);
  cacheSet(key, data);
  return data;
}

async function inspectBatch(ids) {
  const results = {};
  const need = [];
  for (const id of ids) {
    const hit = cacheGet('card:' + id, TTL.batch);
    if (hit) results[id] = hit; else need.push(id);
  }
  if (need.length) {
    const s = await getSettings();
    const data = await api('/api/inspect/videos', {
      method: 'POST',
      body: { ids: need, fetch: s.autoFetch },
    });
    for (const [id, val] of Object.entries(data.results || {})) {
      cacheSet('card:' + id, val);
      results[id] = val;
    }
  }
  return { results };
}

async function health() {
  const hit = cacheGet('health', TTL.health);
  if (hit) return hit;
  const data = await api('/api/health');
  cacheSet('health', data);
  return data;
}

/* Алерты (8.9) -- не кэшируется: счётчик на иконке и попап должны видеть
   свежее состояние, а не то, что было на прошлый опрос. */
async function listEvents(unseenOnly) {
  const q = unseenOnly ? '?unseen_only=true' : '';
  return api('/api/events' + q);
}

async function markEventsSeen(ids, all) {
  return api('/api/events/seen', { method: 'POST', body: { ids, all } });
}

/* Разбор метаданных (8.8) -- набирается в попапе, не кэшируется: каждый
   вызов должен читать текущее состояние базы, а не вчерашний ответ. */
async function reviewMetadata(payload) {
  return api('/api/metadata/review', { method: 'POST', body: payload });
}

async function saveDraft(payload) {
  return api('/api/drafts', { method: 'POST', body: payload });
}

// ------------------------------------------------------- значок на иконке

async function refreshActionBadge() {
  try {
    await api('/api/health');
    const events = await listEvents(true).catch(() => null);
    const n = events && events.unseenCount ? events.unseenCount : 0;
    if (n > 0) {
      await chrome.action.setBadgeBackgroundColor({ color: '#065fd4' });
      await chrome.action.setBadgeText({ text: n > 99 ? '99+' : String(n) });
    } else {
      await chrome.action.setBadgeText({ text: '' });
    }
  } catch (e) {
    await chrome.action.setBadgeBackgroundColor({ color: '#c62828' });
    await chrome.action.setBadgeText({ text: '!' });
  }
}

chrome.runtime.onInstalled.addListener(() => {
  refreshActionBadge();
  chrome.alarms.create('health', { periodInMinutes: 5 });
  chrome.alarms.create('events', { periodInMinutes: 10 });
});
chrome.runtime.onStartup.addListener(refreshActionBadge);
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === 'health' || a.name === 'events') refreshActionBadge();
});

// ------------------------------------------------------------- маршрутизация

const HANDLERS = {
  'settings:get': () => getSettings(),
  'settings:set': async (m) => { await chrome.storage.sync.set(m.patch || {}); cache.clear(); return getSettings(); },
  'health': () => health(),
  'stats': () => stats(),
  'why': (m) => explainOutlier(m.videoId, m.refresh),
  'video': (m) => inspectVideo(m.videoId, m.refresh),
  'channel': (m) => inspectChannel(m.ref, m.refresh),
  'deep': (m) => channelDeep(m.channelId),
  'similarVideos': (m) => similarVideos(m.videoId),
  'comments': (m) => videoComments(m.videoId, m.maxResults),
  'batch': (m) => inspectBatch(m.ids || []),
  'collect': async (m) => {
    const res = await api('/api/collect/channel', {
      method: 'POST',
      body: { channel: m.channel, max_videos: m.maxVideos || 100, track: !!m.track },
    });
    cache.clear();
    return res;
  },
  'track': async (m) => {
    const res = await api('/api/channels/track', { method: 'POST', body: { channel_id: m.channelId } });
    cache.delete('channel:' + m.channelId);
    return res;
  },
  'untrack': async (m) => {
    const res = await api(`/api/channels/tracked/${m.channelId}`, { method: 'DELETE' });
    cache.delete('channel:' + m.channelId);
    return res;
  },
  'save': (m) => api('/api/saved', { method: 'POST', body: {
    kind: m.kind, refId: m.refId, payload: m.payload, note: m.note, folder: m.folder,
  } }),
  'metadataReview': (m) => reviewMetadata(m.payload),
  'saveDraft': (m) => saveDraft(m.payload),
  'events': (m) => listEvents(m.unseenOnly),
  'eventsSeen': (m) => markEventsSeen(m.ids, m.all),
  'eventsScan': () => api('/api/events/scan', { method: 'POST' }).then((r) => { refreshActionBadge(); return r; }),
  'cache:drop': (m) => { if (m.videoId) cacheDropVideo(m.videoId); else cache.clear(); return { ok: true }; },
};

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const handler = HANDLERS[msg && msg.type];
  if (!handler) { sendResponse({ ok: false, error: 'неизвестная команда' }); return false; }
  Promise.resolve(handler(msg))
    .then((data) => sendResponse({ ok: true, data }))
    .catch((e) => sendResponse({ ok: false, error: String(e.message || e), offline: !!e.offline, status: e.status }));
  return true; // ответ придёт асинхронно
});
