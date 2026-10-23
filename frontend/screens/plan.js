/* Экран «Тариф» (SaaS, подпроект 1): текущий тариф, расход и выбор другого.
   Платные тарифы видны, но выбрать их можно будет после подключения оплаты. */
import { api, esc, toast, sectionHead, notice } from '../ui.js';
import { view, render } from '../shared.js';
import { getMe, refreshMe } from '../plan-state.js';
import { dict, fmtNum, fmtDate } from '../i18n.js';
import ru from '../i18n/ru/plan.js';
import en from '../i18n/en/plan.js';

const t = dict({ ru, en });

const fmt = (v, unit) => (v == null ? t('unlimited') : v === 0 ? t('none') : `${fmtNum(v)} ${unit}`);

function card(p, current, catalog) {
  const price = p.price > 0 ? t('pricePerMonth', { price: fmtNum(p.price), currency: esc(p.currency) }) : t('free');
  const lim = catalog.limits
    .map((l) => `<li>${esc(l.label)}: ${esc(fmt(p.effectiveLimits[l.key], l.unit))}</li>`).join('');
  const off = catalog.features.filter((f) => p.effectiveFeatures[f.key] === false)
    .map((f) => `<li style="color:var(--muted)">${t('without', { feature: esc(f.label) })}</li>`).join('');
  const btn = current
    ? `<span class="chip chip-accent">${t('yourPlanChip')}</span>`
    : p.price > 0
      ? `<button class="btn btn-sm" disabled title="${esc(t('paymentSoonTip'))}">${t('paymentSoon')}</button>`
      : `<button class="btn btn-sm choose" data-id="${p.id}">${t('choose')}</button>`;
  return `<div class="card" style="min-width:220px;flex:1">
      <div class="section-title">${esc(p.name)}</div>
      <div class="section-sub">${price}${p.periodDays ? ` · ${t('periodDays', { n: p.periodDays })}` : ''}</div>
      <p>${esc(p.description || '')}</p>
      <ul>${lim}${off}</ul>
      ${btn}
    </div>`;
}

async function viewPlan() {
  if (!getMe().multiUser) {
    view.innerHTML = notice(t('singleUserOnly'));
    return;
  }
  const d = await api('/api/plan');
  const used = d.usage.daily;
  const until = d.expiresAt ? ` · ${t('until', { date: fmtDate(d.expiresAt) })}` : '';
  view.innerHTML = `
    <div class="card">
      ${sectionHead(t('yourPlan', { name: d.plan.name, until }), t('yourPlanSub'))}
      <ul>
        <li>${t('ytQuota', { used: fmtNum(used.yt_units), limit: esc(fmt(d.limits.yt_units, 'units')) })}</li>
        <li>${t('searches', { used: fmtNum(used.search_calls), limit: esc(fmt(d.limits.search_calls, '')) })}</li>
        <li>${t('llm', { used: used.llm_usd, limit: esc(fmt(d.limits.llm_usd, '$')) })}</li>
        ${d.catalog.limits.filter((l) => l.kind === 'count').map((l) =>
          `<li>${esc(l.label)}: ${fmtNum(d.usage.counts[l.key])} / ${esc(fmt(d.limits[l.key], l.unit))}</li>`).join('')}
      </ul>
    </div>
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:12px">
      ${d.plans.map((p) => card(p, p.id === d.plan.id, d.catalog)).join('')}
    </div>
    <div class="card" style="margin-top:12px">
      ${sectionHead(t('deleteTitle'), t('deleteSub'))}
      <form id="deleteMe" style="display:flex;gap:8px;flex-wrap:wrap">
        <input type="password" name="password" required autocomplete="current-password"
          placeholder="${esc(t('passwordPlaceholder'))}" style="width:240px">
        <button class="btn btn-ghost btn-sm" type="submit">${t('deleteMe')}</button>
      </form>
    </div>`;
  // многопользовательский план P0-04: удалить свою учётную запись и все свои данные
  view.querySelector('#deleteMe').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!confirm(t('deleteConfirm'))) return;
    try {
      await api('/api/auth/me', { method: 'DELETE', body: { password: new FormData(e.target).get('password') } });
      toast(t('deleted'), 'ok');
      setTimeout(() => location.reload(), 800);
    } catch (err) { toast(err.message, 'err'); }
  });
  view.querySelectorAll('.choose').forEach((b) => b.addEventListener('click', async () => {
    try {
      await api('/api/plan/choose', { method: 'POST', body: { planId: Number(b.dataset.id) } });
      toast(t('changed'), 'ok');
      await refreshMe();
      render();
    } catch (e) { toast(e.message, 'err'); }
  }));
}

export { viewPlan };
