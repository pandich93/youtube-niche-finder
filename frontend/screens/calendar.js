/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, q, esc, toast, sectionHead, empty, notice } from '../ui.js';
import { view } from '../shared.js';

/* --------------------------------------------- Контент-календарь (план 33) */

// Время хранится в UTC, показывается в часовом поясе браузера.
const STATE = { planned: ['', 'запланировано'], overdue: ['chip-bad', 'просрочено'],
  published: ['chip-good', 'вышло'], unplanned: ['', 'без даты'] };
const DAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const WEEKDAY_RU = { Mon: 'пн', Tue: 'вт', Wed: 'ср', Thu: 'чт', Fri: 'пт', Sat: 'сб', Sun: 'вс' };

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
const hhmm = (iso) => new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
const localInput = (iso) => {
  const d = new Date(iso);
  return `${dayKey(d)}T${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

function chip(x) {
  const [cls] = STATE[x.state] || [''];
  const when = x.plannedAt || x.publishedAt;
  return `<div class="chip ${cls} cal-item" draggable="${x.state === 'published' ? 'false' : 'true'}" data-id="${x.id}"
    style="display:block;margin:3px 0;cursor:pointer;white-space:normal;text-align:left"
    title="${esc(x.title || '')}">${when ? `<b>${hhmm(when)}</b> ` : ''}${esc(x.title || `черновик ${x.id}`)}</div>`;
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
      <div class="section-sub" style="margin:0">${DAYS[i % 7]} ${day.getDate()}</div>
      ${(byDay[key] || []).map(chip).join('')}</div>`);
  }
  return `<div style="display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px">${cells.join('')}</div>`;
}

function editor(d) {
  if (!selected) return '';
  const x = [...d.items, ...d.unplanned].find((i) => i.id === selected);
  if (!x) return '';
  const [cls, label] = STATE[x.state] || ['', x.state];
  const plan = lastPlan && lastPlan.id === x.id ? lastPlan : null;
  const slots = plan?.bestSlots?.length
    ? `Лучшее время ниши (UTC): ${plan.bestSlots.map((s) => `${WEEKDAY_RU[s.weekday] || s.weekday} ${String(s.hour).padStart(2, '0')}:00`).join(', ')}${
      plan.fitsBestSlot === true ? ' — вы попали в одно из них.' : plan.fitsBestSlot === false ? ' — выбранное время не из них.' : ''}`
    : '';
  return `<div class="card">${sectionHead(x.title || `Черновик ${x.id}`, x.niche ? `ниша ${x.niche}` : '',
      `<span class="chip ${cls}">${esc(label)}</span>`)}
    ${x.state === 'published' ? `<div class="section-sub">Вышло: <a href="https://www.youtube.com/watch?v=${esc(x.videoId)}" target="_blank" rel="noopener">${esc(x.videoId)}</a></div>`
      : `<div style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end">
        <label class="field-label">Дата и время выпуска (ваш часовой пояс)<br>
          <input type="datetime-local" id="calWhen" value="${x.plannedAt ? localInput(x.plannedAt) : ''}"></label>
        <button class="btn" id="calSave">Сохранить</button>
        ${x.plannedAt ? '<button class="btn btn-ghost" id="calUnplan">Убрать из календаря</button>' : ''}
      </div>
      ${slots ? `<div class="section-sub" style="margin-top:8px">${esc(slots)}</div>` : ''}
      <div class="section-sub" style="margin-top:8px">Напоминание придёт в Telegram и дайджест за сутки и в момент выпуска,
        пока черновик не связан с вышедшим видео (кнопка «привязать» на экране «Разбор метаданных»).</div>`}
  </div>`;
}

async function plan(id, iso) {
  try {
    lastPlan = await api(`/api/drafts/${id}/plan`, { method: 'POST', body: { plannedAt: iso } });
    selected = id;
    toast(iso ? 'Черновик в календаре' : 'Черновик убран из календаря', 'ok');
    viewCalendar();
  } catch (e) { toast(e.message, 'err'); }
}

async function viewCalendar() {
  const [start, end] = range();
  const days = Math.round((end - start) / 864e5);
  const d = await api('/api/calendar' + q({ start: start.toISOString(), end: end.toISOString() }));
  const title = mode === 'week'
    ? `${start.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })} — ${new Date(end - 864e5).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })}`
    : anchor.toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' });
  view.innerHTML = `
    <div class="card">
      ${sectionHead('Календарь', 'черновики по дням выпуска · перетащите черновик на день', `
        <button class="btn btn-ghost btn-sm" id="calPrev">‹</button>
        <button class="btn btn-ghost btn-sm" id="calToday">Сегодня</button>
        <button class="btn btn-ghost btn-sm" id="calNext">›</button>
        <button class="btn btn-ghost btn-sm" id="calMode">${mode === 'week' ? 'Месяц' : 'Неделя'}</button>`)}
      <div class="section-title" style="margin-bottom:8px">${esc(title)}</div>
      ${grid(d, start, days)}
    </div>
    <div class="card">
      ${sectionHead('Без даты', 'черновики из брифа и разбора метаданных — перетащите на день или нажмите')}
      ${d.unplanned.length ? `<div class="cal-day" data-day="" style="display:flex;flex-wrap:wrap;gap:6px">
        ${d.unplanned.map(chip).join('')}</div>` : empty('все черновики уже в календаре — новые появляются из брифа и «Разбора метаданных»')}
    </div>
    ${editor(d)}
    ${notice('Время показано в часовом поясе браузера, хранится в UTC. Лучшее время ниши — корреляция, не гарантия.')}`;

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
    if (!v) { toast('Укажите дату и время', 'err'); return; }
    plan(selected, new Date(v).toISOString());
  });
  $('#calUnplan')?.addEventListener('click', () => plan(selected, null));
}

export { viewCalendar };
