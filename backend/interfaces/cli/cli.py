"""Командная строка к niche-finder -- чтобы проверить и погонять всё
без подключения к Claude Desktop.

    python cli.py doctor                      проверка ключа, сети, базы
    python cli.py collect-channel @handle     собрать канал (дёшево)
    python cli.py collect "ai automation" --period 24h
    python cli.py refresh                     обновить счётчики (история)
    python cli.py embed-videos                досчитать эмбеддинги (0 quota)
    python cli.py fix-tracked [--apply]       почистить watchlist от handle/url вместо id
    python cli.py viral --period 24h
    python cli.py categories --period 7d --rank-by channels
    python cli.py keywords --period 24h
    python cli.py channels --period 24h       outlier-каналы
    python cli.py stats
    python cli.py notify-test                 тестовое сообщение в Telegram/webhook
    python cli.py export-niche my-niche --format csv --out out.csv
    python cli.py seed                        синтетические данные для примера

В Docker:  docker compose run --rm mcp python cli.py doctor
"""
import argparse
import json
import os
import sys

import infrastructure.postgres as db


def out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
    # Пустой результат почти всегда означает "порог по умолчанию", а не
    # "ничего не нашлось" -- вытаскиваем подсказку наверх, в stderr.
    if isinstance(obj, dict) and obj.get("hint"):
        print(f"\n>>> {obj['hint']}", file=sys.stderr)


def _key():
    from dotenv import load_dotenv
    load_dotenv()
    return os.environ.get("YOUTUBE_API_KEY", "").strip()


def _ping_llm(problems):
    """Shared by doctor's openrouter/ollama branches -- one real llm_gateway
    call, spending a little budget (openrouter) or a few local CPU/GPU
    seconds (ollama)."""
    from application import llm_gateway
    ping_schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
                   "required": ["ok"]}
    data = llm_gateway.run("doctor_ping", "Reply with JSON only.",
                           "Return {\"ok\": true}.", ping_schema)
    if data == {"ok": True}:
        print("   ОК -- провайдер ответил")
    else:
        print("   ОШИБКА или бюджет исчерпан -- см. лог выше")
        problems.append("Пинг LLM-провайдера не удался -- проверьте ключ/модель/URL "
                        "и LLM_DAILY_BUDGET_USD")


def cmd_doctor(args):
    """Всё, что может пойти не так, по порядку -- и что именно делать."""
    from application import search as query
    from application import discovery as trends
    problems = []
    key = _key()

    print("1. Ключ")
    if not key:
        print("   ОТСУТСТВУЕТ")
        problems.append("Впишите YOUTUBE_API_KEY в .env рядом с docker-compose.yml")
    elif not key.startswith("AIza") or len(key) < 35:
        print(f"   подозрительная форма: длина {len(key)}, начало {key[:4]!r}")
        problems.append("Ключ Google обычно 39 символов и начинается на AIza -- "
                        "похоже, скопирован не полностью")
    else:
        print(f"   есть, длина {len(key)}, начинается на {key[:4]}…")

    print("2. Сеть и сам ключ")
    if key:
        import infrastructure.youtube.client as yt
        try:
            cats = yt.video_categories(key, region_code="US")
            print(f"   ОК -- API ответил, {len(cats)} категорий (потрачен 1 unit)")
        except Exception as e:
            msg = str(e).replace(key, "<KEY>")
            print(f"   ОШИБКА: {msg[:400]}")
            if "accessNotConfigured" in msg or "has not been used" in msg:
                problems.append("В этом проекте Google Cloud не включён YouTube Data "
                                "API v3: APIs & Services -> Library -> YouTube Data "
                                "API v3 -> Enable")
            elif "API key not valid" in msg or "keyInvalid" in msg:
                problems.append("Ключ недействителен -- скопирован не целиком или "
                                "из другого проекта")
            elif "referer" in msg.lower() or "referrer" in msg.lower() or "ipRefererBlocked" in msg:
                problems.append("У ключа стоит ограничение по HTTP referrer / IP -- "
                                "из контейнера так не работает. Credentials -> ключ "
                                "-> Application restrictions -> None")
            elif "quota" in msg.lower():
                problems.append("Квота на сегодня исчерпана, сбросится в полночь "
                                "по тихоокеанскому времени")
            else:
                problems.append("Сеть до googleapis.com не проходит или ключ отклонён "
                                "-- см. текст ошибки выше")
    else:
        print("   пропущено (нет ключа)")

    print("3. База")
    try:
        db.init_db()
        s = query.db_stats()
        print(f"   {s['db_path']}")
        print(f"   каналов {s['channels']}, видео {s['videos']}, ниш {s['niches']}, "
              f"снапшотов {s['video_stat_snapshots']}")
        if s["videos"] == 0:
            problems.append("База пустая -- начните с "
                            "`python cli.py collect-channel @какой-нибудь-канал`")
        elif not s["history_since"]:
            problems.append("Истории нет -- запустите воркер "
                            "(`docker compose up -d worker`), иначе vph24h, "
                            "ускорение и рост каналов останутся null")
    except Exception as e:
        print(f"   ОШИБКА: {e}")
        problems.append(f"База не открывается: {e}")

    print("4. LLM (опционально)")
    llm_provider = os.environ.get("LLM_PROVIDER", "none").strip().lower()
    if llm_provider == "none":
        print("   выключен (LLM_PROVIDER=none) -- это по умолчанию, ничего чинить не нужно")
    elif llm_provider == "ollama":
        from infrastructure.llm import factory as llm_factory
        provider = llm_factory.get_provider()  # always an OllamaProvider for this branch
        print(f"   provider=ollama model={llm_factory.display_model()} url={provider.base_url}")
        ok, msg = provider.available()
        print(f"   {msg}")
        if not ok:
            problems.append(f"Ollama: {msg} -- запустите `ollama serve` и "
                            "`ollama pull <модель>`, либо проверьте OLLAMA_URL")
        elif args.llm:
            _ping_llm(problems)
        else:
            print("   сервер и модель на месте, пинг пропущен (передайте --llm)")
    else:
        from infrastructure.llm import factory as llm_factory
        has_key = bool(os.environ.get("OPENROUTER_API_KEY", "").strip())
        print(f"   provider={llm_provider} model={llm_factory.display_model()} "
              f"key={'есть' if has_key else 'ОТСУТСТВУЕТ'}")
        if not has_key:
            problems.append("LLM_PROVIDER=openrouter, но OPENROUTER_API_KEY не задан -- "
                            "используется NullProvider")
        elif args.llm:
            _ping_llm(problems)
        else:
            print("   ключ есть, пинг пропущен (передайте --llm, чтобы проверить и "
                  "потратить немного бюджета)")

    print("5. Покрытие окна 24h")
    try:
        c = trends.coverage("24h")
        print(f"   видео за 24ч: {c['videosPublishedInPeriod']} из "
              f"{c['videosTotal']}; метрики скорости "
              f"{'доступны' if c['velocityMetricsAvailable'] else 'НЕТ (нужна история)'}")
    except Exception as e:
        print(f"   ОШИБКА: {e}")

    print()
    if problems:
        print("ЧТО ПОЧИНИТЬ:")
        for i, p in enumerate(problems, 1):
            print(f"  {i}. {p}")
        return 1
    print("Всё в порядке.")
    return 0


def cmd_collect_channel(args):
    from application import collecting as collector
    out(collector.collect_channel(_key(), args.channel, max_videos=args.max_videos,
                                  niche=args.niche, embed=args.embed))


def cmd_collect(args):
    from application import collecting as collector
    out(collector.collect_niche(_key(), args.query, label=args.label,
                                language=args.language, period=args.period,
                                pages=args.pages, region=args.region,
                                embed=args.embed))


def cmd_refresh(args):
    from application import collecting as collector
    out(collector.refresh_stats(_key(), scope=args.scope, period=args.period,
                                limit=args.limit))
    out(collector.refresh_channels(_key(), only_tracked=True))


def cmd_viral(args):
    from application import discovery as trends
    out(trends.viral_videos_small_channels(
        period=args.period, period_by=args.period_by,
        max_subscribers=args.max_subscribers, min_views=args.min_views,
        min_views_per_subscriber=args.min_vsr, niche=args.niche,
        sort_by=args.sort_by, limit=args.limit))


def cmd_categories(args):
    from application import discovery as trends
    out(trends.most_popular_categories(period=args.period, period_by=args.period_by,
                                       niche=args.niche, rank_by=args.rank_by,
                                       limit=args.limit))


def cmd_keywords(args):
    from application import discovery as trends
    out(trends.trending_keywords(period=args.period, period_by=args.period_by,
                                 niche=args.niche, top_n=args.limit,
                                 sort_by=args.sort_by, min_videos=args.min_videos))


def cmd_channels(args):
    from application import channel_tracking as tracking
    out(tracking.recently_added_outlier_channels(
        period=args.period, period_by=args.period_by,
        min_multiplier=args.min_multiplier, limit=args.limit))


def cmd_stats(args):
    from application import search as query
    out(query.db_stats())


def cmd_embed_videos(args):
    from application import collecting as collector
    out(collector.backfill_embeddings(limit=args.limit))


def cmd_fix_tracked(args):
    from application import channel_tracking as tracking
    out(tracking.fix_tracked(_key() or None, apply=args.apply))


def cmd_notify_test(args):
    from infrastructure.notify import factory as notify_factory
    from infrastructure.notify.null import NullNotifier
    notifier = notify_factory.get_notifier()
    if isinstance(notifier, NullNotifier):
        out({"sent": False,
            "hint": "no NOTIFY_TELEGRAM_BOT_TOKEN/NOTIFY_TELEGRAM_CHAT_ID or "
                    "NOTIFY_WEBHOOK_URL set in .env"})
        return
    ok = notifier.send("\U0001F9EA niche-finder: тестовое сообщение. Если вы это видите, "
                       "алерты настроены верно.")
    out({"sent": ok, "channel": notify_factory.display_target()})


def cmd_export_niche(args):
    from application import niche_export as niche_export_uc
    try:
        result = niche_export_uc.export_niche(args.slug, fmt=args.format)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    path = args.out or result["filename"]
    with open(path, "wb") as f:
        f.write(result["content"])
    out({"written": path, "rows": result["rowCount"]})


def cmd_seed(args):
    # cli.py now lives at backend/interfaces/cli/ (two levels deeper than the
    # old flat backend/cli.py), so climb back up to backend/ before reaching
    # into tests/.
    backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    sys.path.insert(0, os.path.join(backend_root, "tests"))
    from seed_demo import seed
    out(seed())


def main():
    ap = argparse.ArgumentParser(prog="cli.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor", help="проверить ключ, сеть, базу")
    p.add_argument("--llm", action="store_true",
                   help="дополнительно пингануть настроенный LLM-провайдер (тратит бюджет)")
    p.set_defaults(fn=cmd_doctor)
    sub.add_parser("stats", help="что в базе").set_defaults(fn=cmd_stats)
    sub.add_parser("notify-test", help="тестовое сообщение в Telegram/webhook").set_defaults(
        fn=cmd_notify_test)
    sub.add_parser("seed", help="залить синтетические данные").set_defaults(fn=cmd_seed)

    p = sub.add_parser("embed-videos", help="досчитать эмбеддинги для уже собранных видео (0 quota)")
    p.add_argument("--limit", type=int, default=1000)
    p.set_defaults(fn=cmd_embed_videos)

    p = sub.add_parser("export-niche", help="экспорт видео ниши в TSV/CSV")
    p.add_argument("slug")
    p.add_argument("--out", default=None, help="путь файла (по умолчанию <slug>_videos_ДАТА.EXT)")
    p.add_argument("--format", choices=["tsv", "csv"], default="tsv")
    p.set_defaults(fn=cmd_export_niche)

    p = sub.add_parser("fix-tracked", help="почистить watchlist от handle/url вместо channel_id")
    p.add_argument("--apply", action="store_true",
                   help="применить изменения (по умолчанию — только просмотр)")
    p.set_defaults(fn=cmd_fix_tracked)

    p = sub.add_parser("collect-channel", help="собрать канал (1 unit / 50 видео)")
    p.add_argument("channel")
    p.add_argument("--max-videos", type=int, default=100)
    p.add_argument("--niche")
    p.add_argument("--embed", action="store_true")
    p.set_defaults(fn=cmd_collect_channel)

    p = sub.add_parser("collect", help="собрать нишу поиском (1 из 100 поисков в сутки)")
    p.add_argument("query")
    p.add_argument("--label")
    p.add_argument("--language")
    p.add_argument("--period")
    p.add_argument("--region")
    p.add_argument("--pages", type=int, default=1)
    p.add_argument("--embed", action="store_true", default=True)
    p.set_defaults(fn=cmd_collect)

    p = sub.add_parser("refresh", help="обновить счётчики и дописать историю")
    p.add_argument("--scope", default="recent", choices=["recent", "tracked", "niche", "all"])
    p.add_argument("--period", default="30d")
    p.add_argument("--limit", type=int, default=1000)
    p.set_defaults(fn=cmd_refresh)

    def period_args(p, default="7d"):
        p.add_argument("--period", default=default)
        p.add_argument("--period-by", default="published",
                       choices=["published", "discovered", "updated"])
        p.add_argument("--niche")
        p.add_argument("--limit", type=int, default=25)
        return p

    p = period_args(sub.add_parser("viral", help="вирусные видео у маленьких каналов"))
    p.add_argument("--max-subscribers", type=int, default=10000)
    p.add_argument("--min-views", type=int, default=10000)
    p.add_argument("--min-vsr", type=float, default=1.0)
    p.add_argument("--sort-by", default="viral")
    p.set_defaults(fn=cmd_viral)

    p = period_args(sub.add_parser("categories", help="популярные категории"))
    p.add_argument("--rank-by", default="views", choices=["views", "channels", "videos"])
    p.set_defaults(fn=cmd_categories)

    p = period_args(sub.add_parser("keywords", help="трендовые ключевые слова"), "24h")
    p.add_argument("--sort-by", default="momentum")
    p.add_argument("--min-videos", type=int, default=3)
    p.set_defaults(fn=cmd_keywords)

    p = period_args(sub.add_parser("channels", help="недавно добавленные outlier-каналы"), "24h")
    p.add_argument("--min-multiplier", type=float, default=2.0)
    p.set_defaults(fn=cmd_channels)
    p.set_defaults(period_by="discovered")

    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
