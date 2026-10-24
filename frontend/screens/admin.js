/* Экран «Админка» (SaaS, подпроект 1): тарифы, пользователи и (P1-07) журнал
   действий. Виден только админу; сервер всё равно проверяет права на каждом
   запросе. */
import { api, q, esc, toast, sectionHead, notice, table } from '../ui.js';
import { view, render } from '../shared.js';
import { getMe } from '../plan-state.js';
import { dict, fmtNum, fmtDateTime } from '../i18n.js';
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

/* ---------------------------------------------- журнал действий (P1-07) */

const AUDIT_PER_PAGE = 50;

// ключи словаря целиком: так их видит проверка словарей во frontend-smoke
function auditLabels() {
  return {
    auth: t('auditGroupAuth'), tokens: t('auditGroupTokens'), plans: t('auditGroupPlans'),
    accounts: t('auditGroupAccounts'), admin: t('auditGroupAdmin'),
    login: t('evLogin'), login_failed: t('evLoginFailed'), signup: t('evSignup'),
    password_change: t('evPasswordChange'), password_reset: t('evPasswordReset'),
    email_change_requested: t('evEmailChangeRequested'), email_change: t('evEmailChange'),
    token_create: t('evTokenCreate'), token_revoke: t('evTokenRevoke'),
    plan_change: t('evPlanChange'), account_create: t('evAccountCreate'),
    account_disable: t('evAccountDisable'), account_enable: t('evAccountEnable'),
    account_delete: t('evAccountDelete'), account_self_delete: t('evAccountSelfDelete'),
    plan_create: t('evPlanCreate'), plan_update: t('evPlanUpdate'),
    plan_archive: t('evPlanArchive'), plan_restore: t('evPlanRestore'),
    '2fa_enable': t('ev2faEnable'), '2fa_disable': t('ev2faDisable'), '2fa_failed': t('ev2faFailed'),
    '2fa_backup_used': t('ev2faBackupUsed'), '2fa_backup_regenerate': t('ev2faBackupRegenerate'),
  };
}

function auditWho(id, email) {
  if (id == null) return `<span style="color:var(--muted)">${t('auditNobody')}</span>`;
  if (id === 0) return t('auditOperator');
  return email ? esc(email) : `#${id} <span style="color:var(--muted)">${t('auditGone')}</span>`;
}

function auditDetails(d) {
  const pairs = Object.entries(d || {}).filter(([, v]) => v !== null && v !== '');
  if (!pairs.length) return '';
  return pairs.map(([k, v]) => `${esc(k)}: ${esc(Array.isArray(v) ? v.join(', ') : typeof v === 'object' ? JSON.stringify(v) : String(v))}`).join(' · ');
}

async function loadAudit(box, filters, page) {
  const labels = auditLabels();
  box.querySelector('#auditRows').innerHTML = `<p class="row-sub">${t('auditLoading')}</p>`;
  let out;
  try {
    out = await api(`/api/admin/audit${q({ ...filters, page, perPage: AUDIT_PER_PAGE })}`);
  } catch (e) {
    box.querySelector('#auditRows').innerHTML = notice(esc(e.message), 'error');
    return;
  }
  const pages = Math.max(1, Math.ceil(out.total / out.perPage));
  box.querySelector('#auditRows').innerHTML = `${table([
    { label: t('colWhen'), render: (e) => esc(fmtDateTime(e.at)) },
    { label: t('colEvent'), render: (e) => esc(labels[e.event] || e.event) },
    { label: t('colActor'), render: (e) => auditWho(e.actorId, e.actorEmail) },
    { label: t('colTarget'), render: (e) => (e.targetId == null && !e.targetEmail ? '' : auditWho(e.targetId, e.targetEmail)) },
    { label: 'IP', render: (e) => esc(e.ip || '') },
    { label: t('colDetails'), wrap: true, render: (e) => auditDetails(e.details) },
  ], out.entries)}
    <div style="display:flex;gap:8px;align-items:center;margin-top:8px">
      <button class="btn btn-ghost btn-sm" id="auditPrev" ${out.page <= 1 ? 'disabled' : ''}>${t('auditPrev')}</button>
      <span class="row-sub">${t('auditPage', { page: fmtNum(out.page), pages: fmtNum(pages), total: fmtNum(out.total) })}${out.retentionDays ? ` · ${t('auditKept', { days: fmtNum(out.retentionDays) })}` : ''}</span>
      <button class="btn btn-ghost btn-sm" id="auditNext" ${out.page >= pages ? 'disabled' : ''}>${t('auditNext')}</button>
    </div>`;
  box.querySelector('#auditPrev').addEventListener('click', () => loadAudit(box, filters, out.page - 1));
  box.querySelector('#auditNext').addEventListener('click', () => loadAudit(box, filters, out.page + 1));
}

function mountAudit(box) {
  const labels = auditLabels();
  const groups = ['auth', 'tokens', 'plans', 'accounts', 'admin'];
  const events = Object.keys(labels).filter((k) => !groups.includes(k));
  box.innerHTML = `${sectionHead(t('auditTitle'), t('auditSub'))}
    <form id="auditFilters" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <select name="event">
        <option value="">${t('auditAllEvents')}</option>
        <optgroup label="${esc(t('auditGroups'))}">${groups.map((g) => `<option value="${g}">${esc(labels[g])}</option>`).join('')}</optgroup>
        <optgroup label="${esc(t('auditEvents'))}">${events.map((e) => `<option value="${e}">${esc(labels[e])}</option>`).join('')}</optgroup>
      </select>
      <input name="user" placeholder="${esc(t('auditUserPlaceholder'))}" style="width:220px">
      <label>${t('auditSince')} <input type="date" name="since"></label>
      <label>${t('auditUntil')} <input type="date" name="until"></label>
      <button class="btn btn-sm" type="submit">${t('auditShow')}</button>
    </form>
    <div id="auditRows" style="margin-top:8px"></div>`;
  const form = box.querySelector('#auditFilters');
  const filters = () => {
    const f = new FormData(form);
    return { event: f.get('event'), user: (f.get('user') || '').trim(), since: f.get('since'), until: f.get('until') };
  };
  form.addEventListener('submit', (e) => { e.preventDefault(); loadAudit(box, filters(), 1); });
  loadAudit(box, filters(), 1);
}

async function viewAdmin() {
  const me = getMe();
  if (!me.multiUser || !me.user?.isAdmin) {
    view.innerHTML = notice(t('adminOnly'));
    return;
  }
  const [{ plans, catalog }, { users, pool, secrets }] = await Promise.all([
    api('/api/admin/plans'), api('/api/admin/users')]);
  const options = (sel) => plans.filter((p) => !p.archived || p.id === sel).map((p) =>
    `<option value="${p.id}" ${p.id === sel ? 'selected' : ''}>${esc(p.name)}${p.archived ? t('archivedSuffix') : ''}</option>`).join('');
  const unlimited = `<span style="color:var(--muted)">${t('noLimits')}</span>`;
  // P1-12: секреты, которые не читаются текущими ключами, видны сразу, а не по молчащим алертам
  const secretsNote = [
    secrets?.unreadable ? notice(t('secretsUnreadable', { n: fmtNum(secrets.unreadable), total: fmtNum(secrets.total) }), 'error') : '',
    secrets?.old ? notice(t('secretsOld', { n: fmtNum(secrets.old) })) : '',
  ].join('');
  view.innerHTML = `${secretsNote}
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
      ${pool ? `<p class="row-sub">${t('poolLine', { open: fmtNum(pool.openToUsers), limit: fmtNum(pool.dailyLimit),
        reserve: fmtNum(pool.workerReserve), used: fmtNum(pool.usedToday), total: fmtNum(pool.userLimitsTotal),
        unlimited: fmtNum(pool.unlimitedUsers) })}${pool.overbooked || pool.unlimitedUsers
        ? ` <span class="chip">${t('poolWarn')}</span>` : ''}</p>` : ''}
      ${table([
        { label: 'Email', render: (u) => `${esc(u.email)}${u.isAdmin ? ` <span class="chip">${t('adminChip')}</span>` : ''}${u.disabled ? ` <span class="chip">${t('disabledChip')}</span>` : ''}` },
        { label: t('colPlan'), render: (u) => (u.isAdmin ? unlimited : `<select class="uplan" data-id="${u.id}">${options(u.plan.id)}</select>`) },
        { label: t('colUntil'), render: (u) => (u.isAdmin ? '' : `<input type="date" class="uexp" data-id="${u.id}" value="${u.expiresAt ? u.expiresAt.slice(0, 10) : ''}">`) },
        { label: t('colToday'), render: (u) => `${u.today.ytUnits} units · $${u.today.llmUsd}` },
        { label: '', render: (u) => (u.isAdmin ? '' : `<button class="btn btn-ghost btn-sm usave" data-id="${u.id}">${t('saveUser')}</button>`) },
        // многопользовательский план P0-04: блокировка и удаление; себя -- только на странице «Тариф»
        { label: '', render: (u) => (u.id === me.user.id ? '' : `<button class="btn btn-ghost btn-sm ublock" data-id="${u.id}" data-on="${u.disabled ? 0 : 1}">${u.disabled ? t('enableUser') : t('disableUser')}</button>
            ${u.id === 1 ? '' : `<button class="btn btn-ghost btn-sm udel" data-id="${u.id}" data-email="${esc(u.email)}">${t('deleteUser')}</button>`}`) },
      ], users)}
      <form id="userNew" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px">
        <input type="email" name="email" required placeholder="${esc(t('newUserEmail'))}" style="width:240px">
        <label><input type="checkbox" name="isAdmin"> ${t('newUserAdmin')}</label>
        <button class="btn btn-sm" type="submit">${t('createUser')}</button>
      </form>
      <div id="userNewResult"></div>
    </div>
    <div class="card" style="margin-top:12px" id="auditCard"></div>`;
  mountAudit(view.querySelector('#auditCard'));

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
  view.querySelectorAll('.ublock').forEach((b) => b.addEventListener('click', async () => {
    const on = b.dataset.on === '1';
    try {
      await api(`/api/admin/users/${b.dataset.id}/${on ? 'disable' : 'enable'}`, { method: 'POST' });
      toast(on ? t('userDisabled') : t('userEnabled'), 'ok'); render();
    } catch (e) { toast(e.message, 'err'); }
  }));
  view.querySelectorAll('.udel').forEach((b) => b.addEventListener('click', async () => {
    const typed = prompt(t('deleteUserConfirm', { email: b.dataset.email }));
    if (typed === null) return;
    try {
      await api(`/api/admin/users/${b.dataset.id}`, { method: 'DELETE', body: { confirmEmail: typed } });
      toast(t('userDeleted'), 'ok'); render();
    } catch (e) { toast(e.message, 'err'); }
  }));
  view.querySelector('#userNew').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      const made = await api('/api/admin/users', { method: 'POST',
        body: { email: f.get('email'), isAdmin: f.get('isAdmin') === 'on' } });
      await render();
      // пароль показывается один раз: перерисовка выше его не стирает
      view.querySelector('#userNewResult').innerHTML = notice(t('userCreated', {
        email: esc(made.email), password: `<code>${esc(made.password)}</code>` }));
    } catch (err) { toast(err.message, 'err'); }
  });
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
