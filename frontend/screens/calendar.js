/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, esc, toast, sectionHead, empty, notice } from '../ui.js';
import { view } from '../shared.js';
import { dict, fmtDate, fmtTime } from '../i18n.js';
import ru from '../i18n/ru/calendar.js';
import en from '../i18n/en/calendar.js';

const t = dict({ ru, en });

/* --------------------------------------------- Контент-календарь (план 33) */

// Время хранится в UTC, показывается в часовом поясе браузера.
// Второй элемент -- ключ словаря: подпись переводится при рендере.
const STATE = { planned: ['', 'stPlanned'], overdue: ['chip-bad', 'stOverdue'],
  published: ['chip-good', 'stPublished'], unplanned: ['', 'stUnplanned'] };
// Сервер отдаёт день недели слота как 'Mon'..'Sun'; название -- через Intl текущей локали.
const WEEKDAY_IDX = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 };
const weekdayName = (w) => (w in WEEKDAY_IDX
  // 2024-01-01 -- понедельник
  ? fmtDate(new Date(Date.UTC(2024, 0, 1 + WEEKDAY_IDX[w])), { weekday: 'short', timeZone: 'UTC' })
  : w);
// Заголовок ячейки: короткое название дня с заглавной буквы («Пн», «Mon»).
const dayName = (d) => {
  const s = fmtDate(d, { weekday: 'short' });
  return s.charAt(0).toUpperCase() + s.slice(1);
};

let mode = 'week';
let anchor = startOfWeek(new Date());
let selected = null;
let lastPlan = null;

function startOfWeek(d) {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  x.setDate(x.getDate() - ((x.getDay() + 6) % 7));
  return x;
}

function range() {
  if (mode === 'week') {
    const end = new Date(anchor); end.setDate(end.getDate() + 7);
    return [anchor, end];
  }
  const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
  const start = startOfWeek(first);
  const end = new Date(start); end.setDate(end.getDate() + 42);
  return [start, end];
}

const dayKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const hhmm = (iso) => fmtTime(new Date(iso), { hour: '2-digit', minute: '2-digit' });
const localInput = (iso) => {
  const d = new Date(iso);
  return `${dayKey(d)}T${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

function chip(x) {
  const [cls] = STATE[x.state] || [''];
  const when = x.plannedAt || x.publishedAt;
  return `<div class="chip ${cls} cal-item" draggable="${x.state === 'published' ? 'false' : 'true'}" data-id="${x.id}"
    style="display:block;margin:3px 0;cursor:pointer;white-space:normal;text-align:left"
    title="${esc(x.title || '')}">${when ? `<b>${hhmm(when)}</b> ` : ''}${esc(x.title || t('draftChip', { id: x.id }))}</div>`;
}

function grid(d, start, days) {
  const byDay = {};
  for (const x of d.items) {
    const key = dayKey(new Date(x.plannedAt || x.publishedAt));
    (byDay[key] ||= []).push(x);
  }
  const today = dayKey(new Date());
  const cells = [];
  for (let i = 0; i < days; i += 1) {
    const day = new Date(start); day.setDate(day.getDate() + i);
    const key = dayKey(day);
    const other = mode === 'month' && day.getMonth() !== anchor.getMonth();
    cells.push(`<div class="cal-day" data-day="${key}" style="border:1px solid var(--border);border-radius:8px;
      padding:6px;min-height:${mode === 'week' ? 160 : 90}px;${other ? 'opacity:.5;' : ''}${key === today ? 'border-color:var(--series);' : ''}">
      <div class="section-sub" style="margin:0">${esc(dayName(day))} ${day.getDate()}</div>
      ${(byDay[key] || []).map(chip).join('')}</div>`);
  }
  return `<div style="display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px">${cells.join('')}</div>`;
}

function editor(d) {
  if (!selected) return '';
  const x = [...d.items, ...d.unplanned].find((i) => i.id === selected);
  if (!x) return '';
  const [cls, labelKey] = STATE[x.state] || ['', null];
  const label = labelKey ? t(labelKey) : x.state;
  const plan = lastPlan && lastPlan.id === x.id ? lastPlan : null;
  const slotList = plan?.bestSlots?.length
    ? plan.bestSlots.map((s) => `${weekdayName(s.weekday)} ${String(s.hour).padStart(2, '0')}:00`).join(', ')
    : '';
  const slots = slotList
    ? t(plan.fitsBestSlot === true ? 'bestSlotsFit' : plan.fitsBestSlot === false ? 'bestSlotsMiss' : 'bestSlots',
      { slots: slotList })
    : '';
  return `<div class="card">${sectionHead(x.title || t('draftTitle', { id: x.id }), x.niche ? t('nicheSub', { niche: x.niche }) : '',
      `<span class="chip ${cls}">${esc(label)}</span>`)}
    ${x.state === 'published' ? `<div class="section-sub">${t('published', {
        link: `<a href="https://www.youtube.com/watch?v=${esc(x.videoId)}" target="_blank" rel="noopener">${esc(x.videoId)}</a>` })}</div>`
      : `<div style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end">
        <label class="field-label">${t('whenLabel')}<br>
          <input type="datetime-local" id="calWhen" value="${x.plannedAt ? localInput(x.plannedAt) : ''}"></label>
        <button class="btn" id="calSave">${t('save')}</button>
        ${x.plannedAt ? `<button class="btn btn-ghost" id="calUnplan">${t('unplan')}</button>` : ''}
      </div>
      ${slots ? `<div class="section-sub" style="margin-top:8px">${esc(slots)}</div>` : ''}
      <div class="section-sub" style="margin-top:8px">${t('reminderNote')}</div>`}
  </div>`;
}

async function plan(id, iso) {
  try {
    lastPlan = await api(`/api/drafts/${id}/plan`, { method: 'POST', body: { plannedAt: iso } });
    selected = id;
    toast(iso ? t('toastPlanned') : t('toastUnplanned'), 'ok');
    viewCalendar();
  } catch (e) { toast(e.message, 'err'); }
}

async function viewCalendar() {
  const [start, end] = range();
  const days = Math.round((end - start) / 864e5);
  const d = await api('/api/calendar' + q({ start: start.toISOString(), end: end.toISOString() }));
  const title = mode === 'week'
    ? `${fmtDate(start, { day: 'numeric', month: 'long' })} — ${fmtDate(new Date(end - 864e5), { day: 'numeric', month: 'long' })}`
    : fmtDate(anchor, { month: 'long', year: 'numeric' });
  view.innerHTML = `
    <div class="card">
      ${sectionHead(t('title'), t('sub'), `
        <button class="btn btn-ghost btn-sm" id="calPrev">‹</button>
        <button class="btn btn-ghost btn-sm" id="calToday">${t('today')}</button>
        <button class="btn btn-ghost btn-sm" id="calNext">›</button>
        <button class="btn btn-ghost btn-sm" id="calMode">${mode === 'week' ? t('month') : t('week')}</button>`)}
      <div class="section-title" style="margin-bottom:8px">${esc(title)}</div>
      ${grid(d, start, days)}
    </div>
    <div class="card">
      ${sectionHead(t('unplannedTitle'), t('unplannedSub'))}
      ${d.unplanned.length ? `<div class="cal-day" data-day="" style="display:flex;flex-wrap:wrap;gap:6px">
        ${d.unplanned.map(chip).join('')}</div>` : empty(t('unplannedEmpty'))}
    </div>
    ${editor(d)}
    ${notice(t('tzNote'))}`;

  const move = (delta) => {
    if (mode === 'week') anchor.setDate(anchor.getDate() + 7 * delta);
    else anchor = new Date(anchor.getFullYear(), anchor.getMonth() + delta, 1);
    viewCalendar();
  };
  $('#calPrev').addEventListener('click', () => move(-1));
  $('#calNext').addEventListener('click', () => move(1));
  $('#calToday').addEventListener('click', () => {
    anchor = mode === 'week' ? startOfWeek(new Date()) : new Date(new Date().getFullYear(), new Date().getMonth(), 1);
    viewCalendar();
  });
  $('#calMode').addEventListener('click', () => {
    mode = mode === 'week' ? 'month' : 'week';
    anchor = mode === 'week' ? startOfWeek(anchor) : new Date(anchor.getFullYear(), anchor.getMonth(), 1);
    viewCalendar();
  });

  const all = [...d.items, ...d.unplanned];
  view.querySelectorAll('.cal-item').forEach((el) => {
    el.addEventListener('click', () => { selected = Number(el.dataset.id); viewCalendar(); });
    el.addEventListener('dragstart', (e) => e.dataTransfer.setData('text/plain', el.dataset.id));
  });
  view.querySelectorAll('.cal-day[data-day]').forEach((cell) => {
    if (!cell.dataset.day) return;
    cell.addEventListener('dragover', (e) => e.preventDefault());
    cell.addEventListener('drop', (e) => {
      e.preventDefault();
      const id = Number(e.dataTransfer.getData('text/plain'));
      const x = all.find((i) => i.id === id);
      if (!x) return;
      // время сохраняем, меняем только день; у черновика без даты -- 12:00
      const old = x.plannedAt ? new Date(x.plannedAt) : null;
      const [y, m, dd] = cell.dataset.day.split('-').map(Number);
      const when = new Date(y, m - 1, dd, old ? old.getHours() : 12, old ? old.getMinutes() : 0);
      plan(id, when.toISOString());
    });
  });
  $('#calSave')?.addEventListener('click', () => {
    const v = $('#calWhen').value;
    if (!v) { toast(t('pickDate'), 'err'); return; }
    plan(selected, new Date(v).toISOString());
  });
  $('#calUnplan')?.addEventListener('click', () => plan(selected, null));
}

export { viewCalendar };
