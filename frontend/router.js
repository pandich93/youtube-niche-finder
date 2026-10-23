/* Hash-роутинг: таблица экранов, подсветка меню, загрузка с обработкой ошибок. */
import { $, esc, notice } from './ui.js';
import { view, setRender } from './shared.js';
import { viewOverview } from './screens/overview.js';
import { viewFind } from './screens/find.js';
import { viewViral } from './screens/viral.js';
import { viewCalendar } from './screens/calendar.js';
import { viewChannels } from './screens/channels.js';
import { viewCategories } from './screens/categories.js';
import { viewKeywords } from './screens/keywords.js';
import { viewTopTags } from './screens/tags.js';
import { viewTracker } from './screens/tracker.js';
import { viewAlerts } from './screens/alerts.js';
import { viewEnter } from './screens/enter.js';
import { viewIdeas } from './screens/ideas.js';
import { viewLanguage } from './screens/language.js';
import { viewTranscripts } from './screens/transcripts.js';
import { viewNicheClusters } from './screens/clusters.js';
import { viewTitleScoring } from './screens/titles.js';
import { viewSaved } from './screens/saved.js';
import { viewPackaging } from './screens/packaging.js';
import { viewMetadata } from './screens/metadata.js';
import { viewNiches } from './screens/niches.js';
import { viewNiche } from './screens/niche.js';
import { viewChannel } from './screens/channel.js';
import { viewBrief } from './screens/brief.js';
import { viewCompare } from './screens/compare.js';
import { viewData } from './screens/data.js';
import { viewHelp } from './screens/help.js';
import { viewMcp } from './screens/mcp.js';
import { viewOwn } from './screens/own.js';
import { viewPlan } from './screens/plan.js';
import { viewAdmin } from './screens/admin.js';
import { viewProfile } from './screens/profile.js';
import { routeAllowed } from './plan-state.js';
import { dict } from './i18n.js';
import ru from './i18n/ru/router.js';
import en from './i18n/en/router.js';

const t = dict({ ru, en });

async function guard(fn) {
  view.innerHTML = '<div class="skeleton-page"></div>';
  try {
    await fn();
  } catch (e) {
    view.innerHTML = notice(
      `<div><b>${t('loadFailed')}</b><br>${esc(e.message)}<br>
       <span style="color:var(--muted)">${t('checkService')}
       <code>docker compose up -d web</code></span></div>`, 'error');
  }
}

/* --------------------------------------------------------------- роутинг */

/* Название раздела -- ключ словаря router с тем же именем, переводится при рендере. */
const ROUTES = {
  overview: { run: viewOverview },
  find: { run: viewFind },
  viral: { run: viewViral },
  channels: { run: viewChannels },
  categories: { run: viewCategories },
  keywords: { run: viewKeywords },
  tags: { run: viewTopTags },
  tracker: { run: viewTracker },
  alerts: { run: viewAlerts },
  ideas: { run: viewIdeas },
  language: { run: viewLanguage },
  transcripts: { run: viewTranscripts },
  clusters: { run: viewNicheClusters },
  titles: { run: viewTitleScoring },
  saved: { run: viewSaved },
  packaging: { run: viewPackaging },
  metadata: { run: viewMetadata },
  calendar: { run: viewCalendar },
  niches: { run: viewNiches },
  enter: { run: viewEnter },
  own: { run: viewOwn },
  data: { run: viewData },
  help: { run: viewHelp },
  mcp: { run: viewMcp },
  plan: { run: viewPlan },
  admin: { run: viewAdmin },
  profile: { run: viewProfile },
};

const routeTitle = (name) => t(name);

function setActive(href) {
  document.querySelectorAll('.nav-item').forEach((a) =>
    a.classList.toggle('active', href != null && a.getAttribute('href') === href));
}

async function render() {
  const raw = (location.hash || '#/overview').slice(2);
  // «?…» после экрана -- его параметры (#/brief/<id>?gap=…, #/own?connected=…),
  // имя экрана и аргумент берём без них.
  const [path, query] = raw.split('?');
  const [name, arg] = path.split('/');
  if (name === 'channel' && arg) {
    $('#crumbSection').textContent = t('crumbChannel'); setActive(null);
    return guard(() => viewChannel(decodeURIComponent(arg)));
  }
  if (name === 'brief' && arg) {
    $('#crumbSection').textContent = t('crumbBrief'); setActive(null);
    // #/brief/<videoId>?gap=<вопрос зрителей> -- бриф под пробел (план 03).
    const gap = new URLSearchParams(query || '').get('gap');
    return guard(() => viewBrief(decodeURIComponent(arg), gap));
  }
  if (name === 'compare') {
    $('#crumbSection').textContent = t('crumbCompare'); setActive(null);
    return guard(() => viewCompare(arg || ''));
  }
  if (name === 'niche' && arg) {
    $('#crumbSection').textContent = t('crumbNiche'); setActive('#/niches');
    return guard(() => viewNiche(decodeURIComponent(arg)));
  }
  if (name && ROUTES[name] && !routeAllowed(name)) {
    $('#crumbSection').textContent = routeTitle(name); setActive(`#/${name}`);
    view.innerHTML = notice(`${t('notInPlan', { title: esc(routeTitle(name)) })}
      <a href="#/plan">${t('changePlan')}</a>`);
    return;
  }
  const key = ROUTES[name] ? name : 'overview';
  $('#crumbSection').textContent = routeTitle(key);
  setActive(`#/${name || 'overview'}`);
  return guard(ROUTES[key].run);
}

setRender(render);

export { render, routeTitle };
