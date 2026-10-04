/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { $, api, esc, ago, toast, sectionHead, notice, empty } from '../ui.js';
import { view } from '../shared.js';

/* ---------------------------------------------------------------- MCP */

async function viewMcp() {
  view.innerHTML = `
    <div class="card">
      ${sectionHead('MCP-подключение', 'как дать Claude Desktop доступ к вашей базе')}
      <div class="prose">
        <p class="lead">MCP (Model Context Protocol) — это то, что превращает niche-finder из
          дашборда в набор инструментов, которыми Claude пользуется прямо в диалоге: собирает
          данные, ищет вирусные видео, разбирает каналы и сам решает, что из найденного релевантно
          вашей теме. Дашборд (страница «Справка и FAQ» рядом) и MCP-сервер читают одну и ту же
          базу Postgres — можно собирать данные откуда угодно, а смотреть результат в другом месте.</p>
        <p>Всего 85 инструментов. Тратят квоту YouTube только инструменты <strong>сбора</strong>;
          всё остальное — <strong>разделы</strong>, трекинг и анализ каналов, теги, идеи, заголовки,
          алерты, избранное, транскрипты — читает уже собранную базу бесплатно. Отдельная группа
          <strong>LLM-функций</strong> работает, только если задан <code>LLM_PROVIDER</code>.
          Краткий обзор групп — ниже.</p>
      </div>
    </div>

    <div class="card">
      ${sectionHead('Подключение через Docker', 'рекомендуемый способ — тот же образ, что и у дашборда')}
      <div class="prose">
        <p>Откройте
          <code>~/Library/Application Support/Claude/claude_desktop_config.json</code>
          и добавьте:</p>
        <pre><code>{
  "mcpServers": {
    "niche-finder": {
      "command": "/path/to/youtube-niche-finder/scripts/mcp-docker.sh"
    }
  }
}</code></pre>
        <p>Скрипт запускает <code>server.py</code> в образе <code>niche-finder:latest</code>,
          подключает контейнер к сети <code>niche-finder_default</code> (там живёт Postgres из
          compose), монтирует <code>backend/</code> только для чтения и общий том
          <code>niche-finder-models</code>. Переменные из <code>.env</code> он разбирает сам: снимает
          кавычки вокруг значений (у <code>docker run --env-file</code> они уехали бы в ключ
          вместе со значением) и пропускает <code>NICHE_DATABASE_URL</code> — это адрес базы с хоста,
          внутри контейнера он не работает. Поэтому вместо ручной команды <code>docker run</code>
          используйте именно скрипт.</p>
        <p>Сеть появляется только после первого <code>docker compose up</code> — сначала поднимите
          хотя бы базу: <code>docker compose up -d postgres</code>.</p>
        <p>После правки конфига полностью перезапустите Claude Desktop (не просто закрыть окно —
          выйти из приложения), иначе он не перечитает список серверов.</p>
      </div>
    </div>

    <div class="card">
      ${sectionHead('Подключение по HTTPS', 'для клиентов, которым удобнее URL, а не процесс')}
      <div class="prose">
        <pre><code>docker compose --profile http up -d mcp-http mcp-https</code></pre>
        <p>Сервер слушает <code>https://localhost:8765/mcp</code> (только 127.0.0.1). Конфиг клиента:</p>
        <pre><code>{
  "mcpServers": {
    "niche-finder": { "url": "https://localhost:8765/mcp", "type": "http" }
  }
}</code></pre>
        <p>TLS терминирует Caddy сертификатом своего локального CA (<code>tls internal</code>),
          поэтому корневой сертификат этого CA нужно один раз добавить в доверенные в системе.</p>
      </div>
    </div>

    <div class="card">
      ${sectionHead('Подключение без Docker', 'если запускаете backend напрямую, через venv')}
      <div class="prose">
        <pre><code>cd /path/to/youtube-niche-finder/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # впишите YOUTUBE_API_KEY</code></pre>
        <p>Базе Postgres всё равно нужно где-то работать — проще всего поднять её из compose
          (<code>docker compose up -d postgres</code>, на хосте она на порту 5433) и прописать в
          <code>.env</code> <code>NICHE_DATABASE_URL=postgresql://niches:niches@localhost:5433/niches</code>.</p>
        <p>Конфиг:</p>
        <pre><code>{
  "mcpServers": {
    "niche-finder": {
      "command": "/path/to/youtube-niche-finder/backend/.venv/bin/python3",
      "args": ["/path/to/youtube-niche-finder/backend/server.py"]
    }
  }
}</code></pre>
        <p>В этом режиме история не собирается сама — фоновый воркер не запущен, запускайте
          <code>python3 worker.py</code> отдельно (или по cron), иначе поля скорости (VPH за 24ч,
          ускорение, рост) останутся пустыми.</p>
      </div>
    </div>

    <div class="card">
      ${sectionHead('Сценарии', 'готовые рабочие процессы: Claude сам вызывает нужные инструменты по порядку')}
      <div class="prose">
        <p>В Claude Desktop нажмите «+» в поле ввода → <b>niche-finder</b> и выберите сценарий, заполните
          поля. В чат попадёт пошаговая инструкция: какие инструменты вызвать, сколько это стоит в квоте
          и в каком виде ответить. Сценарии, которые собирают данные, сначала проверяют остаток квоты
          через <code>db_stats</code> и останавливаются, если его не хватает.</p>
        <ul>
          <li><b>Найти нишу</b> (<code>find_niche</code>: тема, страниц поиска) — сбор, если ниши ещё нет,
            насыщенность и тренд, вирусные видео мелких каналов, риск шаблонности. Квота: 1 поиск из 100
            на страницу, только если данных нет.</li>
          <li><b>Разобрать конкурента</b> (<code>analyze_competitor</code>: канал) — рост, лучшие видео,
            паттерны заголовков, лучшее время, похожие каналы. Квота: ~2–3 units.</li>
          <li><b>Проверить идею</b> (<code>validate_idea</code>: идея, ниша) — снимали ли уже, оценка
            заголовков, будущие конкуренты. Квота: 0.</li>
          <li><b>Outlier → своё видео</b> (<code>outlier_to_video</code>: id видео) — бриф, в черновики
            только после вашего согласия. Квота: 0.</li>
          <li><b>Обзор недели</b> (<code>weekly_review</code>: период) — outlier'ы, ускорения,
            перепаковки, новые сильные каналы. Квота: 0.</li>
          <li><b>Пробелы в контенте</b> (<code>find_content_gaps</code>: ниша) — вопросы зрителей без
            видео и бриф под выбранный. Квота: 0 из кэша; чтение комментариев — 1 unit на видео и только
            с вашего согласия.</li>
          <li><b>Здоровье ниши</b> (<code>niche_health</code>: ниша) — тренд, шаблонность, спонсоры,
            крючки outlier'ов. Квота: 0.</li>
        </ul>
        <p>Новые сценарии появятся после полного перезапуска Claude Desktop: скрипт монтирует код из
          репозитория. Пересобирать образ (<code>docker compose build</code>) нужно, только если в вашем
          конфиге нет монтирования <code>backend/</code>.</p>
      </div>
    </div>

    <div class="card">
      ${sectionHead('Инструменты', 'сбор тратит квоту, разделы и трекинг — бесплатны')}
      <div class="prose">
        <h4>Сбор</h4>
        <ul>
          <li><code>collect_niche</code> — поиск по теме → видео, каналы, эмбеддинги. 1 поисковый
            вызов из 100 в сутки.</li>
          <li><code>collect_channel</code> — загрузки канала через uploads-плейлист. ~1 unit / 50 видео,
            поиск не тратит. Основной способ набрать корпус.</li>
          <li><code>collect_trending</code> — снапшот чарта mostPopular (Музыка/Фильмы/Игры).</li>
          <li><code>refresh_stats</code> — перечитать счётчики видео, дописать снимок в историю.</li>
          <li><code>refresh_channels</code> — снапшот подписчиков/просмотров каналов.</li>
          <li><code>refresh_categories</code> — актуальная карта id → название категории.</li>
          <li><code>video_comments</code> — комментарии одного видео вживую, без сохранения (1 unit).</li>
          <li><code>backfill_embeddings</code> — эмбеддинги для видео, собранных без них (квоту не
            тратит, считается локально).</li>
        </ul>
        <h4>Разделы</h4>
        <ul>
          <li><code>viral_videos_small_channels</code>, <code>recently_added_outlier_channels</code>,
            <code>high_future_competition</code> — вирусные видео/каналы за период.</li>
          <li><code>most_popular_categories</code>, <code>trending_keywords</code>,
            <code>top_tags_by_category</code> — топ категорий, растущие фразы и теги.</li>
          <li><code>search_outliers</code>, <code>similar_channels</code>, <code>similar_videos</code> —
            смысловой поиск по базе (pgvector).</li>
          <li><code>niche_overview</code>, <code>niche_overview_from_channel</code>,
            <code>niche_videos</code>, <code>niche_map</code> — насыщенность ниши, её видео и карта
            ниш по кластерам каналов. Поле <code>saturation_v2</code> — тренд: последние 30 дней против
            90 до них (растёт / держится / остывает / забита / мало данных) с причинами и числами.</li>
          <li><code>template_risk</code>, <code>niche_template_risk</code> — насколько загрузки канала (или каналов ниши) похожи на один шаблон: риск «неаутентичного контента» YouTube. Эвристика, не вердикт.</li>
          <li><code>check_ideas</code> — пакетная проверка идей по базе.</li>
          <li><code>list_niches</code>, <code>db_stats</code>, <code>data_coverage</code> — что собрано и
            хватает ли данных на окно.</li>
        </ul>
        <h4>Теги</h4>
        <ul>
          <li><code>tag_videos</code>, <code>list_video_tags</code>, <code>tag_stats</code>,
            <code>list_proposed_tags</code> / <code>resolve_proposed_tag</code> — своя разметка видео
            и какой угол реально выстреливает.</li>
        </ul>
        <h4>Трекинг и анализ каналов</h4>
        <ul>
          <li><code>track_channel</code> / <code>untrack_channel</code> / <code>list_tracked_channels</code> —
            вотчлист.</li>
          <li><code>channel_analytics</code>, <code>compare_channels</code>, <code>channel_velocity</code> —
            профиль, каденс, рост, momentum, грейд, проекции, доход.</li>
          <li><code>title_changes</code>, <code>title_patterns</code>, <code>best_time_to_publish</code>,
            <code>calibrate_maturity_curve</code> — более тонкие разборы.</li>
        </ul>
        <h4>Алерты, заголовки, избранное, транскрипты</h4>
        <ul>
          <li><code>sponsor_map</code>, <code>channel_sponsors</code> — какие бренды называют авторы в описаниях видео ниши или канала: спонсоры и промокоды отдельно от партнёрских ссылок, средние просмотры «со спонсором / без». Нижняя граница: только то, что написано в описании.</li>
          <li><code>packaging_changes</code> — смены заголовков и обложек после публикации: «было / стало» и что стало с просмотрами.</li>
          <li><code>own_channels</code>, <code>own_vs_niche</code>, <code>rpm_calibration</code>,
            <code>sync_own_channels</code> — реальные цифры своих каналов из YouTube Analytics (после подключения
            на экране «Мои каналы»): просмотры, удержание, доход, RPM рядом с нишей и с нашей оценкой RPM.</li>
          <li><code>similar_thumbnails</code>, <code>search_thumbnails</code>, <code>thumbnail_styles</code>,
            <code>embed_thumbnails</code> — похожие превью по картинке, поиск превью по описанию и стили превью
            ниши с тем, как они заходят. Нужны векторы превью (воркер с <code>WORKER_THUMB_EMBED=1</code>
            или <code>embed_thumbnails</code>); квота не тратится.</li>
          <li><code>daily_digest</code> — сводка за сутки (то же, что утренний дайджест в Telegram), без отправки.</li>
          <li><code>scan_for_alerts</code>, <code>list_events</code>, <code>mark_events_seen</code> —
            новые outlier'ы, ускорение, смена заголовка, возвращение канала после паузы, пропавший канал или видео.</li>
          <li><code>build_brief</code> — бриф для своего видео из одного outlier: крючок, паттерны ниши, занята ли тема, заголовки и референсы превью; пропущенные части названы честно, без LLM нет угла и новых заголовков.</li>
          <li><code>score_titles</code>, <code>review_metadata</code>, <code>save_draft</code> /
            <code>list_drafts</code> / <code>link_draft</code>, <code>draft_outcomes</code> — проверка
            заголовков и метаданных, черновики и сверка прогноза с итогом.</li>
          <li><code>save_item</code>, <code>list_saved_items</code>, <code>delete_saved_item</code> —
            swipe file.</li>
          <li><code>request_transcript</code>, <code>list_transcript_queue</code>,
            <code>search_transcripts</code> — ручная очередь транскриптов и гибридный поиск по ним.</li>
          <li><code>hook_report</code>, <code>niche_hook_benchmark</code>, <code>score_hook_text</code> — оценка вступления (первые ~30 секунд вставленного транскрипта или ваш текст) по признакам текста: вопрос, цифра, обещание, интрига. Оценка слов, не картинки; LLM-разбор только по явному запросу и стоит денег.</li>
        </ul>
        <h4>LLM-функции (нужен <code>LLM_PROVIDER</code>)</h4>
        <ul>
          <li><code>explain_outlier</code>, <code>comment_insights</code>,
            <code>niche_comment_insights</code> — почему видео выстрелило и что просят в комментариях.</li>
          <li><code>content_gaps</code> — вопросы и просьбы зрителей из комментариев топовых видео ниши, на которые в базе ещё нет видео: спрос, примеры, ближайшее видео. Работает и без LLM (правила, больше шума). По умолчанию только кэш; <code>fetch=true</code> — 1 unit квоты на непрочитанное видео.</li>
          <li><code>suggest_titles</code>, <code>enrich_channels</code>, <code>tag_new_videos</code> —
            генерация заголовков, AI-метки каналов и авто-теги.</li>
        </ul>
        <p>Полные описания, стоимость по квоте и формулы — в <code>backend/README.md</code> в папке
          проекта.</p>
      </div>
    </div>

    <div class="card">
      ${sectionHead('Проверка подключения', 'диагностика и первые команды')}
      <div class="prose">
        <p>Если Claude Desktop не видит сервер или инструменты падают с ошибкой — сначала
          диагностика, не гадание:</p>
        <pre><code>make doctor
# или: docker compose run --rm mcp python cli.py doctor</code></pre>
        <p>Она по порядку проверяет формат ключа, что API реально отвечает, включён ли YouTube
          Data API v3, ограничения по IP/referrer у ключа, состояние базы и покрытие окна 24 часа —
          и печатает список того, что чинить, а не просто «ошибка». С флагом <code>--llm</code>
          (<code>python cli.py doctor --llm</code>) она дополнительно пингует настроенный LLM.</p>
        <p>В самом Claude Desktop, если сервер подключился, можно просто попросить обычным языком —
          например: <em>«Собери канал @Inkexplainer96 и покажи его вирусные видео за 30 дней»</em>
          или <em>«Какие категории сейчас растут быстрее всего за последнюю неделю?»</em> — модель
          сама выберет и вызовет нужные инструменты.</p>
      </div>
    </div>

    <div class="card">
      ${notice('MCP-сервер — не то же самое, что веб-дашборд: Claude Desktop запускает свежий процесс на каждый диалог, поэтому правки в <code>backend/*.py</code> подхватываются в MCP сами собой при следующем запуске Claude Desktop. А вот у постоянно работающего дашборда (<code>docker compose up -d web</code>) после правок Python-файлов нужен <code>docker compose restart web</code> — подробнее в разделе «Справка и FAQ».')}
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
    ${sectionHead('Личные токены', 'для браузерного расширения и MCP по HTTP на сервере с входом',
      `<input type="text" id="tokenName" placeholder="название: ноутбук, расширение…" style="width:220px">
       <button class="btn btn-sm" id="tokenCreate" type="button">Создать токен</button>`)}
    <div id="tokenFresh"></div>
    ${d.tokens.length ? `<ul class="digest-list">${d.tokens.map((t) => `<li style="white-space:normal">
        <b>${esc(t.name || 'без названия')}</b> · создан ${esc(ago(t.createdAt))}
        · ${t.lastUsedAt ? `использован ${esc(ago(t.lastUsedAt))}` : 'ещё не использовался'}
        <button class="btn btn-ghost btn-sm js-token-off" data-id="${t.id}" type="button">Отозвать</button></li>`).join('')}</ul>`
      : empty('токенов пока нет')}
    <div class="section-sub">Расширение: вставьте токен в его настройках (поле «Токен доступа»). MCP по HTTP:
      заголовок <code>Authorization: Bearer &lt;токен&gt;</code>. Токен действует как вы — ваши каналы, черновики,
      алерты и ваша квота. Утёк — отзовите его здесь.</div>
  </div>`;
  $('#tokenCreate').addEventListener('click', async () => {
    try {
      const t = await api('/api/auth/tokens', { method: 'POST', body: { name: $('#tokenName').value.trim() } });
      await loadTokens();
      $('#tokenFresh').innerHTML = notice(`Скопируйте сейчас — больше он не покажется: <code>${esc(t.token)}</code>`, 'ok');
    } catch (e) { toast(e.message, 'err'); }
  });
  box.querySelectorAll('.js-token-off').forEach((btn) => btn.addEventListener('click', async () => {
    if (btn.dataset.armed !== '1') { btn.dataset.armed = '1'; btn.textContent = 'Точно отозвать?'; return; }
    try { await api(`/api/auth/tokens/${btn.dataset.id}`, { method: 'DELETE' }); loadTokens(); }
    catch (e) { toast(e.message, 'err'); }
  }));
}

export { viewMcp };
