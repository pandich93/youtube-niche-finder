/* Язык интерфейса: русский и английский. Без зависимостей и без сборки.

   Словари лежат по неймспейсам в i18n/<locale>/<ns>.js, каждый модуль сам
   импортирует свои два словаря и получает t() через dict({ ru, en }):

     import ru from '../i18n/ru/overview.js';
     import en from '../i18n/en/overview.js';
     const t = dict({ ru, en });
     t('title');                 // строка
     t('channels', { n: 3 });    // склонение: значение -- объект форм

   Склонения -- Intl.PluralRules текущей локали: ru { one, few, many, other },
   en { one, other }. {n} подставляется числом в формате локали.
   Перевод -- только при рендере: текст, вычисленный при импорте модуля, при
   смене языка не обновится. */

const SUPPORTED = ['ru', 'en'];
// языки, носителям которых по умолчанию удобнее русский интерфейс
const RU_FAMILY = ['ru', 'uk', 'be', 'kk', 'uz', 'ky', 'tg'];
const STORE_KEY = 'nf.locale';

function storedLocale() {
  try {
    const v = localStorage.getItem(STORE_KEY);
    return SUPPORTED.includes(v) ? v : null;
  } catch { return null; }
}

function browserLocale() {
  const langs = navigator.languages?.length ? navigator.languages : [navigator.language || 'en'];
  for (const l of langs) {
    const base = String(l).toLowerCase().split('-')[0];
    if (RU_FAMILY.includes(base)) return 'ru';
    if (base === 'en') return 'en';
  }
  return 'en';
}

let locale = storedLocale() || browserLocale();
document.documentElement.lang = locale;

function getLocale() { return locale; }

/* Выбор пользователя, сохранённый в браузере, есть? Тогда язык с сервера
   (users.locale) его не перебивает. */
function hasStoredLocale() { return storedLocale() !== null; }

function intlLocale() { return locale === 'ru' ? 'ru-RU' : 'en-US'; }

const listeners = new Set();
function onLocaleChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }

function setLocale(next, { persist = true } = {}) {
  if (!SUPPORTED.includes(next) || next === locale) return false;
  locale = next;
  if (persist) { try { localStorage.setItem(STORE_KEY, next); } catch { /* приватный режим */ } }
  document.documentElement.lang = next;
  formatters.clear();
  plurals = null;
  listeners.forEach((fn) => fn(next));
  return true;
}

/* ------------------------------------------------------------ форматтеры */

const formatters = new Map();
function numberFormat(opts) {
  const key = JSON.stringify(opts || {});
  if (!formatters.has(key)) formatters.set(key, new Intl.NumberFormat(intlLocale(), opts));
  return formatters.get(key);
}

function fmtNum(n, opts) {
  return n === null || n === undefined || Number.isNaN(+n) ? '—' : numberFormat(opts).format(n);
}

const toDate = (v) => (v instanceof Date ? v : new Date(v));
function fmtDate(v, opts) { return v ? toDate(v).toLocaleDateString(intlLocale(), opts) : '—'; }
function fmtDateTime(v, opts) { return v ? toDate(v).toLocaleString(intlLocale(), opts) : '—'; }
function fmtTime(v, opts) { return v ? toDate(v).toLocaleTimeString(intlLocale(), opts) : '—'; }

/* P2-03: слот «день недели + час» в UTC от сервера (лучшее время публикации:
   'Mon', 14) -- в часовом поясе браузера. Берётся ближайший такой момент, чтобы
   сдвиг учёл летнее время этой недели; день может смениться («Пн 23:00» UTC ->
   «Вт 02:00»), бывают и получасовые пояса (Индия, +5:30). */
const UTC_WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
function fmtUtcSlot(weekday, hour) {
  const idx = UTC_WEEKDAYS.indexOf(weekday);
  if (idx < 0) return `${weekday} ${String(hour).padStart(2, '0')}:00`;
  const now = new Date();
  const ahead = (idx - ((now.getUTCDay() + 6) % 7) + 7) % 7;
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + ahead, hour));
  return `${fmtDate(d, { weekday: 'short' })} ${fmtTime(d, { hour: '2-digit', minute: '2-digit' })}`;
}

/* Часовой пояс браузера (IANA) или '' -- если браузер его не называет. */
function browserTimeZone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch { return ''; }
}

/* ------------------------------------------------------------- перевод */

let plurals = null;
function pluralCategory(n) {
  plurals = plurals || new Intl.PluralRules(intlLocale());
  return plurals.select(Math.abs(Number(n) || 0));
}

const warned = new Set();
function warnOnce(key) {
  if (warned.has(key)) return;
  warned.add(key);
  console.warn(`[i18n] missing "${key}" for ${locale}`);
}

function pick(entry, params) {
  if (entry && typeof entry === 'object') {
    const form = entry[pluralCategory(params.n ?? params.count ?? 0)];
    return form ?? entry.other ?? entry.many ?? entry.one ?? '';
  }
  return entry;
}

function interpolate(text, params) {
  return String(text).replace(/\{(\w+)\}/g, (m, k) => {
    if (!(k in params)) return m;
    const v = params[k];
    return (k === 'n' || k === 'count') && typeof v === 'number' ? fmtNum(v) : String(v ?? '');
  });
}

function dict(tables) {
  return function t(key, params = {}) {
    let entry = tables[locale]?.[key];
    if (entry === undefined) {
      warnOnce(key);
      entry = tables.en?.[key] ?? tables.ru?.[key] ?? key;
    }
    return interpolate(pick(entry, params), params);
  };
}

/* Статический каркас index.html: data-i18n="key" -- текст элемента,
   data-i18n-title / data-i18n-placeholder / data-i18n-aria -- атрибуты. */
function translateStatic(t, root = document) {
  root.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
  root.querySelectorAll('[data-i18n-title]').forEach((el) => { el.title = t(el.dataset.i18nTitle); });
  root.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  root.querySelectorAll('[data-i18n-aria]').forEach((el) => {
    el.setAttribute('aria-label', t(el.dataset.i18nAria));
  });
}

export {
  SUPPORTED, getLocale, hasStoredLocale, setLocale, onLocaleChange, intlLocale,
  dict, translateStatic, fmtNum, fmtDate, fmtDateTime, fmtTime, fmtUtcSlot, browserTimeZone,
  pluralCategory,
};
