/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, esc, ago, toast, sectionHead, notice, empty } from '../ui.js';
import { view } from '../shared.js';
import { dict, fmtDate } from '../i18n.js';
import ru from '../i18n/ru/mcp.js';
import en from '../i18n/en/mcp.js';

const t = dict({ ru, en });

/* ---------------------------------------------------------------- MCP */

/* Команды, JSON и пути в <pre> -- не переводятся; переводится только проза. */
const li = (keys) => keys.map((k) => `<li>${t(k)}</li>`).join('\n          ');

async function viewMcp() {
  view.innerHTML = `
    <div class="card">
      ${sectionHead(t('title'), t('sub'))}
      <div class="prose">
        <p class="lead">${t('introLead')}</p>
        <p>${t('introTools')}</p>
      </div>
    </div>

    <div class="card">
      ${sectionHead(t('dockerTitle'), t('dockerSub'))}
      <div class="prose">
        <p>${t('dockerOpen')}</p>
        <pre><code>{
  "mcpServers": {
    "niche-finder": {
      "command": "/path/to/youtube-niche-finder/scripts/mcp-docker.sh"
    }
  }
}</code></pre>
        <p>${t('dockerScript')}</p>
        <p>${t('dockerNetwork')}</p>
        <p>${t('dockerRestart')}</p>
      </div>
    </div>

    <div class="card">
      ${sectionHead(t('httpsTitle'), t('httpsSub'))}
      <div class="prose">
        <pre><code>docker compose --profile http up -d mcp-http mcp-https</code></pre>
        <p>${t('httpsListens')}</p>
        <pre><code>{
  "mcpServers": {
    "niche-finder": { "url": "https://localhost:8765/mcp", "type": "http" }
  }
}</code></pre>
        <p>${t('httpsTls')}</p>
      </div>
    </div>

    <div class="card">
      ${sectionHead(t('venvTitle'), t('venvSub'))}
      <div class="prose">
        <pre><code>cd /path/to/youtube-niche-finder/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # ${t('venvEnvComment')}</code></pre>
        <p>${t('venvDb')}</p>
        <p>${t('venvConfig')}</p>
        <pre><code>{
  "mcpServers": {
    "niche-finder": {
      "command": "/path/to/youtube-niche-finder/backend/.venv/bin/python3",
      "args": ["/path/to/youtube-niche-finder/backend/server.py"]
    }
  }
}</code></pre>
        <p>${t('venvWorker')}</p>
      </div>
    </div>

    <div class="card">
      ${sectionHead(t('scenariosTitle'), t('scenariosSub'))}
      <div class="prose">
        <p>${t('scenariosIntro')}</p>
        <ul>
          ${li(['scenarioFindNiche', 'scenarioCompetitor', 'scenarioIdea', 'scenarioOutlier',
            'scenarioWeekly', 'scenarioGaps', 'scenarioHealth'])}
        </ul>
        <p>${t('scenariosReload')}</p>
      </div>
    </div>

    <div class="card">
      ${sectionHead(t('toolsTitle'), t('toolsSub'))}
      <div class="prose">
        <h4>${t('groupCollect')}</h4>
        <ul>
          ${li(['toolCollectNiche', 'toolCollectChannel', 'toolCollectTrending', 'toolRefreshStats',
            'toolRefreshChannels', 'toolRefreshCategories', 'toolVideoComments', 'toolBackfillEmbeddings'])}
        </ul>
        <h4>${t('groupSections')}</h4>
        <ul>
          ${li(['toolViral', 'toolCategories', 'toolSemantic', 'toolNiche', 'toolTemplateRisk',
            'toolCheckIdeas', 'toolCoverage'])}
        </ul>
        <h4>${t('groupTags')}</h4>
        <ul>
          ${li(['toolTags'])}
        </ul>
        <h4>${t('groupTracking')}</h4>
        <ul>
          ${li(['toolTrack', 'toolChannelAnalytics', 'toolFineAnalysis'])}
        </ul>
        <h4>${t('groupAlerts')}</h4>
        <ul>
          ${li(['toolSponsors', 'toolPackaging', 'toolOwnChannels', 'toolThumbnails', 'toolDigest',
            'toolAlerts', 'toolBrief', 'toolTitles', 'toolSaved', 'toolTranscripts', 'toolHooks'])}
        </ul>
        <h4>${t('groupLlm')}</h4>
        <ul>
          ${li(['toolExplain', 'toolContentGaps', 'toolSuggest'])}
        </ul>
        <p>${t('toolsReadme')}</p>
      </div>
    </div>

    <div class="card">
      ${sectionHead(t('checkTitle'), t('checkSub'))}
      <div class="prose">
        <p>${t('checkIntro')}</p>
        <pre><code>make doctor
# ${t('checkOrComment')} docker compose run --rm mcp python cli.py doctor</code></pre>
        <p>${t('checkDoctor')}</p>
        <p>${t('checkAsk')}</p>
      </div>
    </div>

    <div class="card">
      ${notice(t('reloadNotice'))}
    </div>
    <div id="apiTokens"></div>`;
  loadTokens();
}

/* План 15 (5.7-5.8): личные токены для расширения и MCP по HTTP -- только на
   сервере с входом. Токен показывается один раз, в базе остаётся только хеш. */
async function loadTokens() {
  const box = $('#apiTokens');
  if (!box) return;
  let me;
  try { me = await api('/api/auth/me'); } catch { return; }
  if (!me.multiUser || !me.user) return;
  let d = { tokens: [] };
  try { d = await api('/api/auth/tokens'); } catch (e) { box.innerHTML = notice(esc(e.message), 'error'); return; }
  box.innerHTML = `<div class="card">
    ${sectionHead(t('tokensTitle'), t('tokensSub'),
      `<input type="text" id="tokenName" placeholder="${esc(t('tokenNamePlaceholder'))}" style="width:220px">
       <select id="tokenDays" aria-label="${esc(t('tokenDaysLabel'))}">
         ${[30, 90, 365, 0].map((n) => `<option value="${n}"${n === 90 ? ' selected' : ''}>${t(n ? 'tokenDaysN' : 'tokenDaysNever', { n })}</option>`).join('')}
       </select>
       ${SCOPES.map((s) => `<label><input type="checkbox" class="js-token-scope" value="${s}" checked> ${t('tokenScope_' + s)}</label>`).join(' ')}
       <button class="btn btn-sm" id="tokenCreate" type="button">${t('tokenCreate')}</button>`)}
    <div id="tokenFresh"></div>
    ${d.tokens.length ? `<ul class="digest-list">${d.tokens.map((tok) => `<li style="white-space:normal">
        <b>${esc(tok.name || t('tokenUnnamed'))}</b> · ${t('tokenCreated', { ago: esc(ago(tok.createdAt)) })}
        · ${tok.lastUsedAt ? t('tokenUsed', { ago: esc(ago(tok.lastUsedAt)) }) : t('tokenNeverUsed')}
        · ${tok.expired ? `<b>${t('tokenExpired')}</b>` : tok.expiresAt ? t('tokenUntil', { date: esc(fmtDate(tok.expiresAt)) }) : t('tokenNoExpiry')}
        · ${t('tokenScopes', { scopes: esc((tok.scopes || []).map((s) => t('tokenScope_' + s)).join(', ')) })}
        <button class="btn btn-ghost btn-sm js-token-off" data-id="${tok.id}" type="button">${t('tokenRevoke')}</button></li>`).join('')}</ul>`
      : empty(t('tokensEmpty'))}
    <div class="section-sub">${t('tokensHelp')}</div>
  </div>`;
  $('#tokenCreate').addEventListener('click', async () => {
    try {
      const scopes = [...box.querySelectorAll('.js-token-scope:checked')].map((el) => el.value);
      const tok = await api('/api/auth/tokens', { method: 'POST', body: {
        name: $('#tokenName').value.trim(), expiresInDays: +$('#tokenDays').value, scopes } });
      await loadTokens();
      $('#tokenFresh').innerHTML = notice(t('tokenFresh', { token: esc(tok.token) }), 'ok');
    } catch (e) { toast(e.message, 'err'); }
  });
  box.querySelectorAll('.js-token-off').forEach((btn) => btn.addEventListener('click', async () => {
    if (btn.dataset.armed !== '1') { btn.dataset.armed = '1'; btn.textContent = t('tokenRevokeConfirm'); return; }
    try { await api(`/api/auth/tokens/${btn.dataset.id}`, { method: 'DELETE' }); loadTokens(); }
    catch (e) { toast(e.message, 'err'); }
  }));
}

// P1-03: what a token may do (backend/application/auth.py TOKEN_SCOPES)
const SCOPES = ['read', 'write', 'mcp'];

export { viewMcp };
