/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { api, esc, sectionHead, table } from '../ui.js';
import { view } from '../shared.js';
import { dict } from '../i18n.js';
import ru from '../i18n/ru/help.js';
import en from '../i18n/en/help.js';

const t = dict({ ru, en });

/* ------------------------------------------------------------- Справка */

/* Пункты «Экраны»: [ключ заголовка, ключи абзацев...]. Только имена ключей --
   текст берётся t() при рендере. */
const SCREENS = [
  ['scrOverviewH', 'scrOverview'],
  ['scrFindH', 'scrFind'],
  ['scrViralH', 'scrViral'],
  ['scrChannelsH', 'scrChannels'],
  ['scrCategoriesH', 'scrCategories'],
  ['scrKeywordsH', 'scrKeywords'],
  ['scrTagsH', 'scrTags'],
  ['scrTrackerH', 'scrTracker'],
  ['scrAlertsH', 'scrAlerts'],
  ['scrTopicsH', 'scrTopics'],
  ['scrIdeasH', 'scrIdeas'],
  ['scrCalendarH', 'scrCalendar'],
  ['scrCollabsH', 'scrCollabs'],
  ['scrProfitH', 'scrProfit'],
  ['scrTraitsH', 'scrTraits'],
  ['scrEnterH', 'scrEnter'],
  ['scrLanguageH', 'scrLanguage'],
  ['scrTranscriptsH', 'scrTranscripts'],
  ['scrClustersH', 'scrClusters'],
  ['scrTitlesH', 'scrTitles'],
  ['scrSavedH', 'scrSaved'],
  ['scrThumbsH', 'scrThumbs'],
  ['scrPackagingH', 'scrPackaging'],
  ['scrSponsorsH', 'scrSponsors'],
  ['scrGapsH', 'scrGaps'],
  ['scrBriefH', 'scrBrief'],
  ['scrTrajectoryH', 'scrTrajectory'],
  ['scrRepeatableH', 'scrRepeatable'],
  ['scrMetadataH', 'scrMetadata'],
  ['scrNichesH', 'scrNiches'],
  ['scrNicheTrendH', 'scrNicheTrend'],
  ['scrOwnH', 'scrOwn', 'scrOwnFormats'],
  ['scrYppH', 'scrYpp'],
  ['scrMilestonesH', 'scrMilestones'],
  ['scrPolicyH', 'scrPolicy'],
  ['scrTemplatedH', 'scrTemplated'],
  ['scrDataH', 'scrData'],
];

/* Глоссарий: [ключ термина, ключ пояснения]. */
const GLOSSARY = [
  ['glMultiplierT', 'glMultiplier'],
  ['glApproxT', 'glApprox'],
  ['glVsrT', 'glVsr'],
  ['glVphT', 'glVph'],
  ['glAccelT', 'glAccel'],
  ['glMomentumT', 'glMomentum'],
  ['glRpmT', 'glRpm'],
  ['glPeriodT', 'glPeriod'],
  ['glFunnelT', 'glFunnel'],
];

const START = ['start1', 'start2', 'start3', 'start4'];

/* FAQ: [ключ вопроса, ключ ответа]. */
const FAQ = [
  ['faqEmptyQ', 'faqEmptyA'],
  ['faq24hQ', 'faq24hA'],
  ['faqApproxQ', 'faqApproxA'],
  ['faqQuotaQ', 'faqQuotaA'],
  ['faqCodeQ', 'faqCodeA'],
  ['faqDockerQ', 'faqDockerA'],
  ['faqVelocityQ', 'faqVelocityA'],
  ['faqNexlevQ', 'faqNexlevA'],
  ['faqLlmQ', 'faqLlmA'],
  ['faqKeyQ', 'faqKeyA'],
];

async function viewHelp() {
  view.innerHTML = `
    <div class="card">
      ${sectionHead(t('aboutTitle'), t('aboutSub'))}
      <div class="prose">
        <p class="lead">${t('aboutLead')}</p>
        <p>${t('aboutForms')}</p>
        <p>${t('aboutLlm')}</p>
      </div>
    </div>

    <div class="card">
      ${sectionHead(t('screensTitle'), t('screensSub'))}
      <div class="prose">
        ${SCREENS.map(([h, ...ps]) => `<h4>${t(h)}</h4>
        ${ps.map((p) => `<p>${t(p)}</p>`).join('\n        ')}`).join('\n        ')}
      </div>
    </div>

    <div class="card">
      ${sectionHead(t('glossaryTitle'), t('glossarySub'))}
      <dl class="glossary">
        ${GLOSSARY.map(([dt, dd]) => `<div><dt>${t(dt)}</dt>
          <dd>${t(dd)}</dd></div>`).join('\n        ')}
      </dl>
    </div>

    <div class="card">
      ${sectionHead(t('startTitle'), t('startSub'))}
      <div class="prose">
        ${START.map((p) => `<p>${t(p)}</p>`).join('\n        ')}
      </div>
    </div>

    <div class="card">
      ${sectionHead(t('faqTitle'), t('faqSub'))}
      <div class="faq">
        ${FAQ.map(([qk, ak]) => `<details>
          <summary>${t(qk)}</summary>
          <div class="a">${t(ak)}</div>
        </details>`).join('\n        ')}
      </div>
    </div>
    <div class="card" id="helpScores"></div>`;
  fillScores();
}

/* План 23: каждое число -- данные YouTube или оценка niche-finder, и как оно
   посчитано. Тот же каталог, что у подсказок «?» и MCP-инструмента explain_scores. */
async function fillScores() {
  const box = document.querySelector('#helpScores');
  try {
    const d = await api('/api/scores');
    const rows = Object.values(d.scores).sort((a, b) => (a.source === b.source ? 0 : a.source === 'youtube' ? -1 : 1));
    box.innerHTML = `${sectionHead(t('scoresTitle'), t('scoresSub'))}
      ${table([
        { label: t('colNumber'), wrap: true, render: (r) => `<b>${esc(r.name)}</b>` },
        { label: t('colSource'), render: (r) => (r.source === 'youtube' ? t('srcYoutube')
          : `<span class="est">${t('srcEstimate')}</span>`) },
        { label: t('colFormula'), wrap: true, render: (r) => esc(r.formula) },
        { label: t('colSample'), wrap: true, render: (r) => esc(r.minSample || '—') },
      ], rows)}`;
  } catch { box.remove(); }
}

export { viewHelp };
