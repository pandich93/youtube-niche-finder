"""Командная строка к niche-finder -- чтобы проверить и погонять всё
без подключения к Claude Desktop.

    python cli.py doctor                      проверка ключа, сети, базы
    python cli.py collect-channel @handle     собрать канал (дёшево)
    python cli.py collect "ai automation" --period 24h   [--no-embed]
    python cli.py refresh                     обновить счётчики (история)
    python cli.py embed-videos                досчитать эмбеддинги (0 quota)
    python cli.py fix-tracked [--apply]       почистить watchlist от handle/url вместо id
    python cli.py rotate-secrets [--apply]    перешифровать секреты текущим OWN_TOKENS_KEY
    python cli.py viral --period 24h
    python cli.py categories --period 7d --rank-by channels
    python cli.py keywords --period 24h
    python cli.py channels --period 24h       outlier-каналы
    python cli.py stats
    python cli.py notify-test                 тестовое сообщение в Telegram/webhook
    python cli.py digest-send                 отправить дайджест за сутки прямо сейчас
    python cli.py export-niche my-niche --format csv --out out.csv
    python cli.py seed                        синтетические данные для примера

В Docker:  docker compose run --rm mcp python cli.py doctor
"""
import argparse
import json
import os
import sys

import infrastructure.postgres as db
from domain.i18n import tr
from domain.users import LOCAL_USER_ID


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
        _say("ОК -- провайдер ответил")
    else:
        _say("ОШИБКА или бюджет исчерпан -- см. лог выше")
        problems.append(tr("Пинг LLM-провайдера не удался -- проверьте ключ/модель/URL "
                           "и LLM_DAILY_BUDGET_USD"))


def _say(text, **params):
    """One indented line of doctor's report, in the CLI's language."""
    print("   " + tr(text, **params))


def cmd_doctor(args):
    """Всё, что может пойти не так, по порядку -- и что именно делать."""
    from application import discovery as trends
    from application import search as query
    problems = []
    key = _key()

    print(tr("1. Ключ"))
    if not key:
        _say("ОТСУТСТВУЕТ")
        problems.append(tr("Впишите YOUTUBE_API_KEY в .env рядом с docker-compose.yml"))
    elif not key.startswith("AIza") or len(key) < 35:
        _say("подозрительная форма: длина {length}, начало {start}",
             length=len(key), start=repr(key[:4]))
        problems.append(tr("Ключ Google обычно 39 символов и начинается на AIza -- "
                           "похоже, скопирован не полностью"))
    else:
        _say("есть, длина {length}, начинается на {start}…", length=len(key), start=key[:4])

    print(tr("2. Сеть и сам ключ"))
    if key:
        import infrastructure.youtube.client as yt
        try:
            cats = yt.video_categories(key, region_code="US")
            _say("ОК -- API ответил, {count} категорий (потрачен 1 unit)", count=len(cats))
        except Exception as e:
            msg = str(e).replace(key, "<KEY>")
            _say("ОШИБКА: {error}", error=msg[:400])
            if "accessNotConfigured" in msg or "has not been used" in msg:
                problems.append(tr("В этом проекте Google Cloud не включён YouTube Data "
                                   "API v3: APIs & Services -> Library -> YouTube Data "
                                   "API v3 -> Enable"))
            elif "API key not valid" in msg or "keyInvalid" in msg:
                problems.append(tr("Ключ недействителен -- скопирован не целиком или "
                                   "из другого проекта"))
            elif "referer" in msg.lower() or "referrer" in msg.lower() or "ipRefererBlocked" in msg:
                problems.append(tr("У ключа стоит ограничение по HTTP referrer / IP -- "
                                   "из контейнера так не работает. Credentials -> ключ "
                                   "-> Application restrictions -> None"))
            elif "quota" in msg.lower():
                problems.append(tr("Квота на сегодня исчерпана, сбросится в полночь "
                                   "по тихоокеанскому времени"))
            else:
                problems.append(tr("Сеть до googleapis.com не проходит или ключ отклонён "
                                   "-- см. текст ошибки выше"))
    else:
        _say("пропущено (нет ключа)")

    print(tr("3. База"))
    try:
        db.init_db()
        s = query.db_stats(user_id=None)
        print(f"   {s['db_path']}")
        _say("каналов {channels}, видео {videos}, ниш {niches}, снапшотов {snapshots}",
             channels=s["channels"], videos=s["videos"], niches=s["niches"],
             snapshots=s["video_stat_snapshots"])
        if s["videos"] == 0:
            problems.append(tr("База пустая -- начните с "
                               "`python cli.py collect-channel @какой-нибудь-канал`"))
        elif not s["history_since"]:
            problems.append(tr("Истории нет -- запустите воркер "
                               "(`docker compose up -d worker`), иначе vph24h, "
                               "ускорение и рост каналов останутся null"))
    except Exception as e:
        _say("ОШИБКА: {error}", error=e)
        problems.append(tr("База не открывается: {error}", error=e))

    print(tr("4. LLM (опционально)"))
    llm_provider = os.environ.get("LLM_PROVIDER", "none").strip().lower()
    if llm_provider == "none":
        _say("выключен (LLM_PROVIDER=none) -- это по умолчанию, ничего чинить не нужно")
    elif llm_provider == "ollama":
        from infrastructure.llm import factory as llm_factory
        provider = llm_factory.get_provider()  # always an OllamaProvider for this branch
        print(f"   provider=ollama model={llm_factory.display_model()} url={provider.base_url}")
        ok, msg = provider.available()
        print(f"   {msg}")
        if not ok:
            problems.append(tr("Ollama: {msg} -- запустите `ollama serve` и "
                               "`ollama pull <модель>`, либо проверьте OLLAMA_URL", msg=msg))
        elif args.llm:
            _ping_llm(problems)
        else:
            _say("сервер и модель на месте, пинг пропущен (передайте --llm)")
    else:
        from infrastructure.llm import factory as llm_factory
        has_key = bool(os.environ.get("OPENROUTER_API_KEY", "").strip())
        print(f"   provider={llm_provider} model={llm_factory.display_model()} "
              f"key={tr('есть') if has_key else tr('ОТСУТСТВУЕТ')}")
        if not has_key:
            problems.append(tr("LLM_PROVIDER=openrouter, но OPENROUTER_API_KEY не задан -- "
                               "используется NullProvider"))
        elif args.llm:
            _ping_llm(problems)
        else:
            _say("ключ есть, пинг пропущен (передайте --llm, чтобы проверить и "
                 "потратить немного бюджета)")

    print(tr("5. Покрытие окна 24h"))
    try:
        c = trends.coverage("24h")
        _say("видео за 24ч: {published} из {total}; метрики скорости {velocity}",
             published=c["videosPublishedInPeriod"], total=c["videosTotal"],
             velocity=tr("доступны") if c["velocityMetricsAvailable"] else tr("НЕТ (нужна история)"))
    except Exception as e:
        _say("ОШИБКА: {error}", error=e)

    print()
    if problems:
        print(tr("ЧТО ПОЧИНИТЬ:"))
        for i, p in enumerate(problems, 1):
            print(f"  {i}. {p}")
        return 1
    print(tr("Всё в порядке."))
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
    out(query.db_stats(user_id=None))


def cmd_embed_videos(args):
    from application import collecting as collector
    out(collector.backfill_embeddings(limit=args.limit))


def cmd_fix_tracked(args):
    from application import channel_tracking as tracking
    out(tracking.fix_tracked(_key() or None, apply=args.apply))


def cmd_rotate_secrets(args):
    """P1-12: re-encrypt with OWN_TOKENS_KEY what only a key from
    OWN_TOKENS_KEY_OLD reads. Exit code 1 while anything stays unreadable."""
    from application import secrets_rotation as SR
    from infrastructure import secrets as SEC
    try:
        res = SR.rotate(apply=args.apply)
    except SEC.SecretsNotConfigured as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    out(res)
    if args.apply:
        print(tr("Перешифровано: {rotated}; уже на текущем ключе: {current}; не читаются: "
                 "{unreadable}", **res), file=sys.stderr)
    else:
        print(tr("Пробный прогон, база не менялась. Будет перешифровано: {rotated}; уже на "
                 "текущем ключе: {current}; не читаются: {unreadable}. Записать: --apply",
                 **res), file=sys.stderr)
    if res["unreadable"]:
        print(tr("Не читаются ни текущим, ни старыми ключами: верните прежний ключ в "
                 "OWN_TOKENS_KEY_OLD или попросите пользователей подключить каналы и "
                 "ввести токены уведомлений заново."), file=sys.stderr)
        return 1
    return 0


def cmd_digest_send(args):
    from application import digest as digest_mod
    out(digest_mod.send_digest(force=True, user_id=LOCAL_USER_ID))


def cmd_notify_test(args):
    from infrastructure.notify import factory as notify_factory
    from infrastructure.notify.null import NullNotifier
    notifier = notify_factory.get_notifier()
    if isinstance(notifier, NullNotifier):
        out({"sent": False,
            "hint": "no NOTIFY_TELEGRAM_BOT_TOKEN/NOTIFY_TELEGRAM_CHAT_ID or "
                    "NOTIFY_WEBHOOK_URL set in .env"})
        return
    ok = notifier.send(tr("\U0001F9EA niche-finder: тестовое сообщение. Если вы это видите, "
                          "алерты настроены верно."))
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


def _read_password(args):
    """From stdin with --password-stdin (scripts), else asked twice without
    echo. Never taken from argv, where it would land in shell history."""
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    import getpass
    first = getpass.getpass("password: ")
    if getpass.getpass("again: ") != first:
        print("error: the passwords differ", file=sys.stderr)
        sys.exit(1)
    return first


def cmd_create_user(args):
    from application import auth
    try:
        uid = auth.create_user(args.email, _read_password(args), is_admin=args.admin)
    except auth.AuthError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    from application import audit
    audit.log("account_create", actor_id=audit.OPERATOR_ID, target_id=uid,
              target_email=args.email.strip().lower(), details={"isAdmin": bool(args.admin)})
    out({"created": uid, "email": args.email.strip().lower(), "admin": args.admin})


def cmd_set_password(args):
    from application import auth
    try:
        uid = auth.set_password(args.email, _read_password(args))
    except auth.AuthError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    from application import audit
    audit.log("password_change", actor_id=audit.OPERATOR_ID, target_id=uid,
              details={"via": "cli"})
    out({"updated": uid, "sessionsEnded": True})


def cmd_create_token(args):
    from application import auth
    users = {u["email"]: u["id"] for u in auth.list_users()}
    uid = users.get(args.email.strip().lower())
    if uid is None:
        print(f"error: no user {args.email}", file=sys.stderr)
        sys.exit(1)
    try:
        out(auth.create_api_token(uid, args.name, expires_in_days=args.days, scopes=args.scopes))
    except auth.AuthError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_users(args):
    from application import auth
    out(auth.list_users())


def _account_call(fn, *args):
    """multi-user plan P0-04: the operator acts on accounts by email."""
    from application import users_admin as UA
    try:
        out(fn(UA.by_email(args[0]), *args[1:]))
    except UA.UserNotFound:
        print(f"error: no user {args[0]}", file=sys.stderr)
        sys.exit(1)
    except UA.UserAdminError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_disable_user(args):
    from application import users_admin as UA
    _account_call(lambda uid: UA.set_disabled(UA.OPERATOR, uid, True), args.email)


def cmd_enable_user(args):
    from application import users_admin as UA
    _account_call(lambda uid: UA.set_disabled(UA.OPERATOR, uid, False), args.email)


def cmd_delete_user(args):
    from application import users_admin as UA
    if not args.yes:
        print(tr("Удаление необратимо: учётная запись и все её данные. Повторите с --yes."),
              file=sys.stderr)
        sys.exit(1)
    _account_call(lambda uid: UA.delete(UA.OPERATOR, uid, args.email), args.email)


def cmd_seed(args):
    # cli.py now lives at backend/interfaces/cli/ (two levels deeper than the
    # old flat backend/cli.py), so climb back up to backend/ before reaching
    # into tests/.
    backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    sys.path.insert(0, os.path.join(backend_root, "tests"))
    from seed_demo import seed
    out(seed())


def main():
    # .env first: NF_DEFAULT_LOCALE there sets the language of help and reports
    from dotenv import load_dotenv
    load_dotenv()
    ap = argparse.ArgumentParser(prog="cli.py", description=tr(__doc__),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor", help=tr("проверить ключ, сеть, базу"))
    p.add_argument("--llm", action="store_true",
                   help=tr("дополнительно пингануть настроенный LLM-провайдер (тратит бюджет)"))
    p.set_defaults(fn=cmd_doctor)
    sub.add_parser("stats", help=tr("что в базе")).set_defaults(fn=cmd_stats)
    sub.add_parser("notify-test", help=tr("тестовое сообщение в Telegram/webhook")).set_defaults(
        fn=cmd_notify_test)
    sub.add_parser("digest-send", help=tr("отправить дайджест за сутки прямо сейчас")).set_defaults(
        fn=cmd_digest_send)
    sub.add_parser("seed", help=tr("залить синтетические данные")).set_defaults(fn=cmd_seed)

    # plan 15: accounts for NF_MULTI_USER=1 -- by invitation, no sign-up page
    p = sub.add_parser("create-user", help=tr("создать пользователя (вход при NF_MULTI_USER=1)"))
    p.add_argument("email")
    p.add_argument("--admin", action="store_true")
    p.add_argument("--password-stdin", action="store_true", help=tr("читать пароль из stdin"))
    p.set_defaults(fn=cmd_create_user)
    p = sub.add_parser("set-password", help=tr("задать или сменить пароль (local -- ваши данные)"))
    p.add_argument("email")
    p.add_argument("--password-stdin", action="store_true", help=tr("читать пароль из stdin"))
    p.set_defaults(fn=cmd_set_password)
    sub.add_parser("users", help=tr("список пользователей")).set_defaults(fn=cmd_users)
    p = sub.add_parser("disable-user", help=tr("заблокировать: сессии и токены перестают работать"))
    p.add_argument("email")
    p.set_defaults(fn=cmd_disable_user)
    p = sub.add_parser("enable-user", help=tr("разблокировать"))
    p.add_argument("email")
    p.set_defaults(fn=cmd_enable_user)
    p = sub.add_parser("delete-user", help=tr("удалить учётную запись и все её данные"))
    p.add_argument("email")
    p.add_argument("--yes", action="store_true", help=tr("подтвердить: удаление необратимо"))
    p.set_defaults(fn=cmd_delete_user)
    p = sub.add_parser("create-token", help=tr("личный токен для расширения и MCP по HTTP"))
    p.add_argument("email")
    p.add_argument("--name", default=None)
    p.add_argument("--days", type=int, default=None,
                   help=tr("срок жизни в днях, 1..365 (по умолчанию 90); 0 -- бессрочный"))
    p.add_argument("--scopes", default=None,
                   help=tr("права через запятую: read, write, mcp (по умолчанию все)"))
    p.set_defaults(fn=cmd_create_token)

    p = sub.add_parser("embed-videos", help=tr("досчитать эмбеддинги для уже собранных видео (0 quota)"))
    p.add_argument("--limit", type=int, default=1000)
    p.set_defaults(fn=cmd_embed_videos)

    p = sub.add_parser("export-niche", help=tr("экспорт видео ниши в TSV/CSV"))
    p.add_argument("slug")
    p.add_argument("--out", default=None, help=tr("путь файла (по умолчанию <slug>_videos_ДАТА.EXT)"))
    p.add_argument("--format", choices=["tsv", "csv"], default="tsv")
    p.set_defaults(fn=cmd_export_niche)

    p = sub.add_parser("fix-tracked", help=tr("почистить watchlist от handle/url вместо channel_id"))
    p.add_argument("--apply", action="store_true",
                   help=tr("применить изменения (по умолчанию — только просмотр)"))
    p.set_defaults(fn=cmd_fix_tracked)

    p = sub.add_parser("rotate-secrets",
                       help=tr("перешифровать секреты текущим OWN_TOKENS_KEY (ротация ключа)"))
    p.add_argument("--apply", action="store_true",
                   help=tr("записать в базу (по умолчанию — только посчитать)"))
    p.set_defaults(fn=cmd_rotate_secrets)

    p = sub.add_parser("collect-channel", help=tr("собрать канал (1 unit / 50 видео)"))
    p.add_argument("channel")
    p.add_argument("--max-videos", type=int, default=100)
    p.add_argument("--niche")
    p.add_argument("--embed", action=argparse.BooleanOptionalAction, default=False,
                   help=tr("считать эмбеддинги сразу (по умолчанию нет)"))
    p.set_defaults(fn=cmd_collect_channel)

    p = sub.add_parser("collect", help=tr("собрать нишу поиском (1 из 100 поисков в сутки)"))
    p.add_argument("query")
    p.add_argument("--label")
    p.add_argument("--language")
    p.add_argument("--period")
    p.add_argument("--region")
    p.add_argument("--pages", type=int, default=1)
    p.add_argument("--embed", action=argparse.BooleanOptionalAction, default=True,
                   help=tr("считать эмбеддинги сразу (по умолчанию да)"))
    p.set_defaults(fn=cmd_collect)

    p = sub.add_parser("refresh", help=tr("обновить счётчики и дописать историю"))
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

    p = period_args(sub.add_parser("viral", help=tr("вирусные видео у маленьких каналов")))
    p.add_argument("--max-subscribers", type=int, default=10000)
    p.add_argument("--min-views", type=int, default=10000)
    p.add_argument("--min-vsr", type=float, default=1.0)
    p.add_argument("--sort-by", default="viral")
    p.set_defaults(fn=cmd_viral)

    p = period_args(sub.add_parser("categories", help=tr("популярные категории")))
    p.add_argument("--rank-by", default="views", choices=["views", "channels", "videos"])
    p.set_defaults(fn=cmd_categories)

    p = period_args(sub.add_parser("keywords", help=tr("трендовые ключевые слова")), "24h")
    p.add_argument("--sort-by", default="momentum")
    p.add_argument("--min-videos", type=int, default=3)
    p.set_defaults(fn=cmd_keywords)

    p = period_args(sub.add_parser("channels", help=tr("недавно добавленные outlier-каналы")), "24h")
    p.add_argument("--min-multiplier", type=float, default=2.0)
    p.set_defaults(fn=cmd_channels)
    p.set_defaults(period_by="discovered")

    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
