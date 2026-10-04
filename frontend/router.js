/* Hash-роутинг: таблица экранов, подсветка меню, загрузка с обработкой ошибок. */
import { $, esc, notice } from './ui.js';
import { view, setRender } from './shared.js';
import { viewOverview } from './screens/overview.js';
import { viewFind } from './screens/find.js';
import { viewViral } from './screens/viral.js';
import { viewChannels } from './screens/channels.js';
import { viewCategories } from './screens/categories.js';
import { viewKeywords } from './screens/keywords.js';
import { viewTopTags } from './screens/tags.js';
import { viewTracker } from './screens/tracker.js';
import { viewAlerts } from './screens/alerts.js';
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

async function guard(fn) {
  view.innerHTML = '<div class="skeleton-page"></div>';
  try {
    await fn();
  } catch (e) {
    view.innerHTML = notice(
      `<div><b>Не удалось загрузить данные.</b><br>${esc(e.message)}<br>
       <span style="color:var(--muted)">Проверьте, что сервис поднят:
       <code>docker compose up -d web</code></span></div>`, 'error');
  }
}

/* --------------------------------------------------------------- роутинг */

const ROUTES = {
  overview: { title: 'Обзор', run: viewOverview },
  find: { title: 'Найти нишу', run: viewFind },
  viral: { title: 'Вирусные видео', run: viewViral },
  channels: { title: 'Outlier-каналы', run: viewChannels },
  categories: { title: 'Категории', run: viewCategories },
  keywords: { title: 'Ключевые слова', run: viewKeywords },
  tags: { title: 'Топ теги по категориям', run: viewTopTags },
  tracker: { title: 'Трекер каналов', run: viewTracker },
  alerts: { title: 'Алерты', run: viewAlerts },
  ideas: { title: 'Проверка идей', run: viewIdeas },
  language: { title: 'Другой язык', run: viewLanguage },
  transcripts: { title: 'Транскрипты', run: viewTranscripts },
  clusters: { title: 'Карта ниш', run: viewNicheClusters },
  titles: { title: 'Проверить заголовки', run: viewTitleScoring },
  saved: { title: 'Избранное', run: viewSaved },
  packaging: { title: 'Перепаковки', run: viewPackaging },
  metadata: { title: 'Разбор метаданных', run: viewMetadata },
  niches: { title: 'Ниши', run: viewNiches },
  own: { title: 'Мои каналы', run: viewOwn },
  data: { title: 'Данные', run: viewData },
  help: { title: 'Справка и FAQ', run: viewHelp },
  mcp: { title: 'MCP-подключение', run: viewMcp },
};

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
    $('#crumbSection').textContent = 'Канал'; setActive(null);
    return guard(() => viewChannel(decodeURIComponent(arg)));
  }
  if (name === 'brief' && arg) {
    $('#crumbSection').textContent = 'Бриф'; setActive(null);
    // #/brief/<videoId>?gap=<вопрос зрителей> -- бриф под пробел (план 03).
    const gap = new URLSearchParams(query || '').get('gap');
    return guard(() => viewBrief(decodeURIComponent(arg), gap));
  }
  if (name === 'compare') {
    $('#crumbSection').textContent = 'Сравнение'; setActive(null);
    return guard(() => viewCompare(arg || ''));
  }
  if (name === 'niche' && arg) {
    $('#crumbSection').textContent = 'Ниша'; setActive('#/niches');
    return guard(() => viewNiche(decodeURIComponent(arg)));
  }
  const route = ROUTES[name || 'overview'] || ROUTES.overview;
  $('#crumbSection').textContent = route.title;
  setActive(`#/${name || 'overview'}`);
  return guard(route.run);
}

setRender(render);

export { render };
