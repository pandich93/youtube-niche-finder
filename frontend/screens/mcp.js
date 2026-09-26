/* Экран дашборда. Роутинг -- router.js, общее -- shared.js, компоненты -- ui.js. */
import { sectionHead, notice } from '../ui.js';
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
        <p>Всего 61 инструмент. Тратят квоту YouTube только инструменты <strong>сбора</strong>;
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
            ниш по кластерам каналов.</li>
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
          <li><code>scan_for_alerts</code>, <code>list_events</code>, <code>mark_events_seen</code> —
            новые outlier'ы, ускорение, смена заголовка, возвращение канала после паузы.</li>
          <li><code>score_titles</code>, <code>review_metadata</code>, <code>save_draft</code> /
            <code>list_drafts</code> / <code>link_draft</code>, <code>draft_outcomes</code> — проверка
            заголовков и метаданных, черновики и сверка прогноза с итогом.</li>
          <li><code>save_item</code>, <code>list_saved_items</code>, <code>delete_saved_item</code> —
            swipe file.</li>
          <li><code>request_transcript</code>, <code>list_transcript_queue</code>,
            <code>search_transcripts</code> — ручная очередь транскриптов и гибридный поиск по ним.</li>
        </ul>
        <h4>LLM-функции (нужен <code>LLM_PROVIDER</code>)</h4>
        <ul>
          <li><code>explain_outlier</code>, <code>comment_insights</code>,
            <code>niche_comment_insights</code> — почему видео выстрелило и что просят в комментариях.</li>
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
    </div>`;
}

export { viewMcp };
