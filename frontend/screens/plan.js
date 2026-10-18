/* Экран «Тариф» (SaaS, подпроект 1): текущий тариф, расход и выбор другого.
   Платные тарифы видны, но выбрать их можно будет после подключения оплаты. */
import { api, esc, toast, sectionHead, notice } from '../ui.js';
import { view, render } from '../shared.js';
import { getMe, refreshMe } from '../plan-state.js';

const fmt = (v, unit) => (v == null ? 'без лимита' : v === 0 ? 'нет' : `${v} ${unit}`);

function card(p, current, catalog) {
  const price = p.price > 0 ? `${p.price} ${esc(p.currency)} / мес` : 'Бесплатно';
  const lim = catalog.limits
    .map((l) => `<li>${esc(l.label)}: ${esc(fmt(p.effectiveLimits[l.key], l.unit))}</li>`).join('');
  const off = catalog.features.filter((f) => p.effectiveFeatures[f.key] === false)
    .map((f) => `<li style="color:var(--muted)">без: ${esc(f.label)}</li>`).join('');
  const btn = current
    ? '<span class="chip chip-accent">ваш тариф</span>'
    : p.price > 0
      ? '<button class="btn btn-sm" disabled title="оплата скоро">Оплата скоро</button>'
      : `<button class="btn btn-sm choose" data-id="${p.id}">Выбрать</button>`;
  return `<div class="card" style="min-width:220px;flex:1">
      <div class="section-title">${esc(p.name)}</div>
      <div class="section-sub">${price}${p.periodDays ? ` · ${p.periodDays} дн.` : ''}</div>
      <p>${esc(p.description || '')}</p>
      <ul>${lim}${off}</ul>
      ${btn}
    </div>`;
}

async function viewPlan() {
  if (!getMe().multiUser) {
    view.innerHTML = notice('Тарифы работают только в многопользовательском режиме (NF_MULTI_USER=1).');
    return;
  }
  const d = await api('/api/plan');
  const used = d.usage.daily;
  const until = d.expiresAt ? ` · до ${new Date(d.expiresAt).toLocaleDateString('ru-RU')}` : '';
  view.innerHTML = `
    <div class="card">
      ${sectionHead(`Ваш тариф: ${d.plan.name}${until}`, 'расход сегодня и что входит')}
      <ul>
        <li>Квота YouTube: ${used.yt_units} / ${esc(fmt(d.limits.yt_units, 'units'))}</li>
        <li>Поисковые запросы: ${used.search_calls} / ${esc(fmt(d.limits.search_calls, ''))}</li>
        <li>LLM: $${used.llm_usd} / ${esc(fmt(d.limits.llm_usd, '$'))}</li>
        ${d.catalog.limits.filter((l) => l.kind === 'count').map((l) =>
          `<li>${esc(l.label)}: ${d.usage.counts[l.key]} / ${esc(fmt(d.limits[l.key], l.unit))}</li>`).join('')}
      </ul>
    </div>
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:12px">
      ${d.plans.map((p) => card(p, p.id === d.plan.id, d.catalog)).join('')}
    </div>`;
  view.querySelectorAll('.choose').forEach((b) => b.addEventListener('click', async () => {
    try {
      await api('/api/plan/choose', { method: 'POST', body: { planId: Number(b.dataset.id) } });
      toast('Тариф сменён', 'ok');
      await refreshMe();
      render();
    } catch (e) { toast(e.message, 'err'); }
  }));
}

export { viewPlan };
