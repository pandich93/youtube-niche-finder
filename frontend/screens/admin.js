/* Экран «Админка» (SaaS, подпроект 1): тарифы и пользователи. Виден только
   админу; сервер всё равно проверяет права на каждом запросе. */
import { api, esc, toast, sectionHead, notice, table } from '../ui.js';
import { view, render } from '../shared.js';
import { getMe } from '../plan-state.js';
import { dict, fmtNum } from '../i18n.js';
import ru from '../i18n/ru/admin.js';
import en from '../i18n/en/admin.js';

const t = dict({ ru, en });

function planForm(p, catalog) {
  const v = (x) => (x == null ? '' : esc(String(x)));
  const limits = catalog.limits.map((l) => {
    const has = p && l.key in p.limits;
    const val = has ? p.limits[l.key] : null;
    return `<label style="display:grid;grid-template-columns:220px 120px auto;gap:8px;align-items:center">
        <span>${esc(l.label)} (${esc(l.unit)})</span>
        <input type="number" min="0" step="${l.key === 'llm_usd' ? '0.01' : '1'}" data-limit="${l.key}"
          value="${has && val != null ? v(val) : ''}" placeholder="${esc(l.default == null ? t('unlimited') : t('defaultValue', { value: l.default }))}">
        <span><input type="checkbox" data-unlimited="${l.key}" ${has && val == null ? 'checked' : ''}> ${t('unlimited')}</span>
      </label>`;
  }).join('');
  const feats = catalog.features.map((f) => `<label><input type="checkbox" data-feature="${f.key}"
      ${!p || p.features[f.key] !== false ? 'checked' : ''}> ${esc(f.label)}</label>`).join('<br>');
  return `<form id="planForm" class="card" style="display:grid;gap:8px;margin-top:12px">
      <div class="section-title">${p ? t('planNamed', { name: esc(p.name) }) : t('newPlan')}</div>
      <input name="name" placeholder="${esc(t('namePlaceholder'))}" value="${v(p?.name)}" required>
      <input name="slug" placeholder="${esc(t('slugPlaceholder'))}" value="${v(p?.slug)}" required>
      <textarea name="description" placeholder="${esc(t('descriptionPlaceholder'))}">${v(p?.description)}</textarea>
      <div style="display:flex;gap:8px">
        <input name="price" type="number" min="0" step="0.01" placeholder="${esc(t('pricePlaceholder'))}" value="${v(p?.price ?? 0)}">
        <input name="currency" placeholder="USD" value="${v(p?.currency ?? 'USD')}" style="width:80px">
        <input name="periodDays" type="number" min="1" placeholder="${esc(t('periodPlaceholder'))}" value="${v(p?.periodDays)}">
        <input name="sortOrder" type="number" placeholder="${esc(t('sortPlaceholder'))}" value="${v(p?.sortOrder ?? 0)}" style="width:100px">
      </div>
      <label><input type="checkbox" name="isBase" ${p?.isBase ? 'checked' : ''}> ${t('isBase')}</label>
      <label><input type="checkbox" name="isPublic" ${!p || p.isPublic ? 'checked' : ''}> ${t('isPublic')}</label>
      <div class="section-sub">${t('limitsHint')}</div>
      ${limits}
      <div class="section-sub">${t('features')}</div>
      <div>${feats}</div>
      <div><button class="btn" type="submit">${t('save')}</button>
        <button class="btn btn-ghost" type="button" id="planCancel">${t('cancel')}</button></div>
    </form>`;
}

function readForm(form) {
  const f = new FormData(form);
  const limits = {};
  form.querySelectorAll('[data-limit]').forEach((inp) => {
    const key = inp.dataset.limit;
    if (form.querySelector(`[data-unlimited="${key}"]`).checked) limits[key] = null;
    else if (inp.value !== '') limits[key] = Number(inp.value);
  });
  const features = {};
  form.querySelectorAll('[data-feature]').forEach((c) => { features[c.dataset.feature] = c.checked; });
  return {
    name: f.get('name'), slug: f.get('slug'), description: f.get('description'),
    price: Number(f.get('price') || 0), currency: f.get('currency'),
    periodDays: f.get('periodDays') ? Number(f.get('periodDays')) : null,
    sortOrder: Number(f.get('sortOrder') || 0),
    isBase: f.get('isBase') === 'on', isPublic: f.get('isPublic') === 'on', limits, features,
  };
}

async function viewAdmin() {
  const me = getMe();
  if (!me.multiUser || !me.user?.isAdmin) {
    view.innerHTML = notice(t('adminOnly'));
    return;
  }
  const [{ plans, catalog }, { users }] = await Promise.all([
    api('/api/admin/plans'), api('/api/admin/users')]);
  const options = (sel) => plans.filter((p) => !p.archived || p.id === sel).map((p) =>
    `<option value="${p.id}" ${p.id === sel ? 'selected' : ''}>${esc(p.name)}${p.archived ? t('archivedSuffix') : ''}</option>`).join('');
  const unlimited = `<span style="color:var(--muted)">${t('noLimits')}</span>`;
  view.innerHTML = `
    <div class="card">
      ${sectionHead(t('plansTitle'), t('plansSub'))}
      ${table([
        { label: t('colPlan'), render: (p) => `${esc(p.name)} ${p.isBase ? `<span class="chip">${t('baseChip')}</span>` : ''}${p.archived ? ` <span class="chip">${t('archiveChip')}</span>` : ''}` },
        { label: t('colPrice'), render: (p) => (p.price > 0 ? `${fmtNum(p.price)} ${esc(p.currency)}` : t('free')) },
        { label: t('colPeriod'), render: (p) => (p.periodDays ? t('periodDays', { n: p.periodDays }) : t('noExpiry')) },
        { label: t('colPublic'), render: (p) => (p.isPublic ? t('yes') : t('no')) },
        { label: t('colUsers'), render: (p) => p.users },
        { label: '', render: (p) => `<button class="btn btn-ghost btn-sm edit" data-id="${p.id}">${t('edit')}</button>
            ${p.isBase ? '' : `<button class="btn btn-ghost btn-sm arch" data-id="${p.id}" data-on="${p.archived ? 0 : 1}">${p.archived ? t('restore') : t('archive')}</button>`}` },
      ], plans)}
      <button class="btn btn-sm" id="planNew" style="margin-top:8px">${t('newPlan')}</button>
      <div id="planEditor"></div>
    </div>
    <div class="card" style="margin-top:12px">
      ${sectionHead(t('usersTitle'), t('usersSub'))}
      ${table([
        { label: 'Email', render: (u) => `${esc(u.email)}${u.isAdmin ? ` <span class="chip">${t('adminChip')}</span>` : ''}` },
        { label: t('colPlan'), render: (u) => (u.isAdmin ? unlimited : `<select class="uplan" data-id="${u.id}">${options(u.plan.id)}</select>`) },
        { label: t('colUntil'), render: (u) => (u.isAdmin ? '' : `<input type="date" class="uexp" data-id="${u.id}" value="${u.expiresAt ? u.expiresAt.slice(0, 10) : ''}">`) },
        { label: t('colToday'), render: (u) => `${u.today.ytUnits} units · $${u.today.llmUsd}` },
        { label: '', render: (u) => (u.isAdmin ? '' : `<button class="btn btn-ghost btn-sm usave" data-id="${u.id}">${t('saveUser')}</button>`) },
      ], users)}
    </div>`;

  const editor = view.querySelector('#planEditor');
  const open = (p) => {
    editor.innerHTML = planForm(p, catalog);
    editor.querySelector('#planCancel').addEventListener('click', () => { editor.innerHTML = ''; });
    editor.querySelector('#planForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      try {
        const body = readForm(e.target);
        await api(p ? `/api/admin/plans/${p.id}` : '/api/admin/plans', { method: p ? 'PUT' : 'POST', body });
        toast(t('planSaved'), 'ok'); render();
      } catch (err) { toast(err.message, 'err'); }
    });
  };
  view.querySelector('#planNew').addEventListener('click', () => open(null));
  view.querySelectorAll('.edit').forEach((b) => b.addEventListener('click', () =>
    open(plans.find((p) => p.id === Number(b.dataset.id)))));
  view.querySelectorAll('.arch').forEach((b) => b.addEventListener('click', async () => {
    try {
      await api(`/api/admin/plans/${b.dataset.id}/${b.dataset.on === '1' ? 'archive' : 'restore'}`, { method: 'POST' });
      render();
    } catch (e) { toast(e.message, 'err'); }
  }));
  view.querySelectorAll('.usave').forEach((b) => b.addEventListener('click', async () => {
    const id = b.dataset.id;
    const exp = view.querySelector(`.uexp[data-id="${id}"]`).value;
    try {
      await api(`/api/admin/users/${id}/plan`, { method: 'PUT', body: {
        planId: Number(view.querySelector(`.uplan[data-id="${id}"]`).value),
        expiresAt: exp ? new Date(`${exp}T23:59:59Z`).toISOString() : null } });
      toast(t('userPlanSaved'), 'ok'); render();
    } catch (e) { toast(e.message, 'err'); }
  }));
}

export { viewAdmin };
