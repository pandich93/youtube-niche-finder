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
import { viewIdeas } from './screens/ideas.js';
import { viewTranscripts } from './screens/transcripts.js';
import { viewNicheClusters } from './screens/clusters.js';
import { viewTitleScoring } from './screens/titles.js';
import { viewSaved } from './screens/saved.js';
import { viewMetadata } from './screens/metadata.js';
import { viewNiches } from './screens/niches.js';
import { viewNiche } from './screens/niche.js';
import { viewChannel } from './screens/channel.js';
import { viewData } from './screens/data.js';
import { viewHelp } from './screens/help.js';
import { viewMcp } from './screens/mcp.js';

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
  ideas: { title: 'Проверка идей', run: viewIdeas },
  transcripts: { title: 'Транскрипты', run: viewTranscripts },
  clusters: { title: 'Карта ниш', run: viewNicheClusters },
  titles: { title: 'Проверить заголовки', run: viewTitleScoring },
  saved: { title: 'Избранное', run: viewSaved },
  metadata: { title: 'Разбор метаданных', run: viewMetadata },
  niches: { title: 'Ниши', run: viewNiches },
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
  const [name, arg] = raw.split('/');
  if (name === 'channel' && arg) {
    $('#crumbSection').textContent = 'Канал'; setActive(null);
    return guard(() => viewChannel(decodeURIComponent(arg)));
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
