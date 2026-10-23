"""Public privacy policy and terms pages (plan 25).

Google's OAuth app verification asks for a privacy policy on the service's
own domain, and YouTube's API Services terms ask an API client to link to
the YouTube Terms of Service and the Google Privacy Policy, say how users
revoke access and how their data is deleted. These pages say it for an
installation run as a service; the operator's name and contact come from
NF_SERVICE_NAME, NF_OPERATOR_NAME and NF_CONTACT_EMAIL (escaped).

Two languages (i18n plan): ?lang=en|ru first, then the request's locale
(X-NF-Locale / Accept-Language, set by the HTTP middleware), else
NF_DEFAULT_LOCALE; each page links to the other language. Each document is
written out whole per language rather than assembled from tr() fragments: a
legal text is read and reviewed as a whole, the two versions must state the
same facts paragraph by paragraph, and English is what Google's reviewers
read. Change one -- change the other.
"""
import html
import os

from domain import i18n

YT_TERMS = "https://www.youtube.com/t/terms"
GOOGLE_PRIVACY = "https://policies.google.com/privacy"
USER_DATA_POLICY = "https://developers.google.com/terms/api-services-user-data-policy"
PERMISSIONS = "https://myaccount.google.com/permissions"

# the other language's link on each page, in that language
_SWITCH = {"en": ("ru", "Русский"), "ru": ("en", "English")}


def page_locale(lang: str = None) -> str:
    """?lang= when it names a language we serve, else the request's locale."""
    return i18n.normalize(lang) or i18n.current_locale()


def _ctx(locale: str) -> dict:
    name = os.environ.get("NF_SERVICE_NAME", "").strip() or "niche-finder"
    operator = os.environ.get("NF_OPERATOR_NAME", "").strip() or (
        "его оператор" if locale == "ru" else f"the operator of {name}")
    email = os.environ.get("NF_CONTACT_EMAIL", "").strip()
    no_email = ("адрес, указанный оператором" if locale == "ru"
                else "the contact address given by the operator")
    return {"name": html.escape(name), "operator": html.escape(operator),
            "contact": (f'<a href="mailto:{html.escape(email)}">{html.escape(email)}</a>'
                        if email else no_email)}


def _page(title: str, body: str, locale: str, path: str) -> str:
    other, label = _SWITCH[locale]
    switch = (f'<p class="lang"><a href="{path}?lang={other}" hreflang="{other}" '
              f'lang="{other}">{label}</a></p>')
    return f"""<!doctype html><html lang="{locale}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<link rel="alternate" hreflang="{other}" href="{path}?lang={other}">
<style>body{{font:16px/1.6 system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 16px;
color:#1a1a19;background:#fff}}h1{{font-size:28px}}h2{{font-size:19px;margin-top:28px}}
a{{color:#2a78d6}}.lang{{text-align:right;margin:0;font-size:14px}}
@media (prefers-color-scheme:dark){{body{{color:#e8e7e1;background:#0d0d0d}}
a{{color:#3987e5}}}}</style></head><body>{switch}{body}</body></html>"""


# ------------------------------------------------------------ privacy policy

def _privacy_en(c: dict) -> str:
    return f"""
<h1>Privacy policy</h1>
<p>{c['name']} is run by {c['operator']}. It uses <b>YouTube API Services</b>. By connecting a
YouTube channel you also agree to the <a href="{YT_TERMS}">YouTube Terms of Service</a>, and Google
handles your data under the <a href="{GOOGLE_PRIVACY}">Google Privacy Policy</a>.</p>

<h2>What we collect</h2>
<ul>
<li><b>Your account:</b> your email and a hash of your password (never the password), and a session
cookie while you are signed in.</li>
<li><b>Your YouTube channel, only if you connect it:</b> its id and title, an OAuth refresh token
stored encrypted, and YouTube Analytics numbers of your channel and videos (views, watch time,
retention, subscribers gained, by day and by format). Revenue numbers only if you grant the separate
revenue permission.</li>
<li><b>Public YouTube data</b> about the channels and videos you research (titles, descriptions,
counters), fetched with the service's API key.</li>
<li>Your own work in the service: watchlist, saved items, drafts, watched topics, alert settings.</li>
</ul>

<h2>How we use it</h2>
<p>Only to show you your analytics and research inside {c['name']}. We do not sell your data, do not
use it for advertising, and do not share it with anyone, except Google's own APIs that answer the
requests made for you. People at {c['name']} do not read your data unless you ask us to, for security,
or when the law requires it.</p>
<p>{c['name']}'s use and transfer of information received from Google APIs to any other app will adhere
to the <a href="{USER_DATA_POLICY}">Google API Services User Data Policy</a>, including the Limited Use
requirements.</p>

<h2>How long we keep it</h2>
<p>Your channel's YouTube Analytics data is kept while your channel stays connected. Public YouTube data
is refreshed from YouTube regularly. Disconnecting a channel ("My channels" → "Disconnect") revokes our
access at Google and deletes everything stored for that channel at once.</p>

<h2>Your choices</h2>
<ul>
<li>Revoke {c['name']}'s access to your Google account at any time at
<a href="{PERMISSIONS}">{PERMISSIONS}</a>.</li>
<li>Delete your account and all your data yourself: "Delete account" on the Plan page.
Or ask us: write to {c['contact']}. We do it within 7 days.</li>
</ul>

<h2>Contact</h2>
<p>{c['contact']}</p>"""


def _privacy_ru(c: dict) -> str:
    return f"""
<h1>Политика конфиденциальности</h1>
<p>Сервисом {c['name']} управляет {c['operator']}. Сервис использует <b>YouTube API Services</b>.
Подключая канал YouTube, вы также соглашаетесь с <a href="{YT_TERMS}">Условиями использования
YouTube</a>, а Google обрабатывает ваши данные по <a href="{GOOGLE_PRIVACY}">Политике
конфиденциальности Google</a>.</p>

<h2>Что мы собираем</h2>
<ul>
<li><b>Ваша учётная запись:</b> ваш email и хеш пароля (никогда не сам пароль), а также cookie сессии,
пока вы вошли в систему.</li>
<li><b>Ваш канал YouTube, только если вы его подключили:</b> его id и название, OAuth refresh-токен
в зашифрованном виде и показатели YouTube Analytics вашего канала и видео (просмотры, время просмотра,
удержание, прирост подписчиков — по дням и по форматам). Данные о доходах — только если вы выдали
отдельное разрешение на доступ к доходам.</li>
<li><b>Публичные данные YouTube</b> о каналах и видео, которые вы исследуете (названия, описания,
счётчики), полученные с API-ключом сервиса.</li>
<li>Ваша собственная работа в сервисе: список отслеживания, избранное, черновики, отслеживаемые темы,
настройки алертов.</li>
</ul>

<h2>Как мы их используем</h2>
<p>Только чтобы показывать вам вашу аналитику и исследования внутри {c['name']}. Мы не продаём ваши
данные, не используем их для рекламы и ни с кем ими не делимся, кроме собственных API Google, которые
отвечают на запросы, сделанные для вас. Сотрудники {c['name']} не читают ваши данные, если только вы
сами нас об этом не попросите, если этого не требует безопасность или закон.</p>
<p>Использование и передача {c['name']} информации, полученной от API Google, в любое другое
приложение будет соответствовать <a href="{USER_DATA_POLICY}">Политике Google в отношении
пользовательских данных API-сервисов</a> (Google API Services User Data Policy), включая требования
ограниченного использования (Limited Use).</p>

<h2>Сколько мы их храним</h2>
<p>Данные YouTube Analytics вашего канала хранятся, пока канал остаётся подключённым. Публичные данные
YouTube регулярно обновляются из YouTube. Отключение канала («Мои каналы» → «Отключить») отзывает наш
доступ в Google и сразу удаляет всё, что хранится для этого канала.</p>

<h2>Ваш выбор</h2>
<ul>
<li>Отозвать доступ {c['name']} к вашему аккаунту Google можно в любой момент на странице
<a href="{PERMISSIONS}">{PERMISSIONS}</a>.</li>
<li>Удалить учётную запись и все ваши данные самостоятельно: «Удалить учётную запись» на
странице «Тариф». Или попросить нас: напишите на {c['contact']}. Мы сделаем это в течение
7 дней.</li>
</ul>

<h2>Контакты</h2>
<p>{c['contact']}</p>"""


def privacy_html(lang: str = None) -> str:
    locale = page_locale(lang)
    c = _ctx(locale)
    if locale == "ru":
        return _page(f"Политика конфиденциальности — {c['name']}", _privacy_ru(c), locale, "/privacy")
    return _page(f"Privacy policy — {c['name']}", _privacy_en(c), locale, "/privacy")


# ------------------------------------------------------------ terms of service

def _terms_en(c: dict) -> str:
    return f"""
<h1>Terms of service</h1>
<p>{c['name']} is run by {c['operator']}. It uses <b>YouTube API Services</b>: by using it you agree to
be bound by the <a href="{YT_TERMS}">YouTube Terms of Service</a>, and our
<a href="/privacy?lang=en">privacy policy</a> describes what we do with your data.</p>
<h2>What the service is</h2>
<p>Research and analytics for YouTube creators. Numbers marked as estimates of {c['name']} (outlier
scores, trends, revenue ranges, forecasts, policy signals) are our calculations, not YouTube data and
not YouTube's decisions.</p>
<h2>Your account</h2>
<p>Keep your password and personal API tokens private. We may suspend an account that abuses the
service or YouTube's terms.</p>
<h2>No warranty</h2>
<p>The service is provided as is, without any warranty; we are not liable for decisions made from its
numbers.</p>
<h2>Contact</h2>
<p>{c['contact']}</p>"""


def _terms_ru(c: dict) -> str:
    return f"""
<h1>Условия использования</h1>
<p>Сервисом {c['name']} управляет {c['operator']}. Сервис использует <b>YouTube API Services</b>:
пользуясь им, вы соглашаетесь соблюдать <a href="{YT_TERMS}">Условия использования YouTube</a>, а наша
<a href="/privacy?lang=ru">политика конфиденциальности</a> описывает, что мы делаем с вашими данными.</p>
<h2>Что это за сервис</h2>
<p>Исследования и аналитика для авторов YouTube. Числа, отмеченные как оценки {c['name']}
(outlier-оценки, тренды, вилки дохода, прогнозы, сигналы политики), — это наши расчёты, а не данные
YouTube и не решения YouTube.</p>
<h2>Ваша учётная запись</h2>
<p>Храните пароль и персональные API-токены в тайне. Мы можем приостановить учётную запись, которая
злоупотребляет сервисом или нарушает условия YouTube.</p>
<h2>Без гарантий</h2>
<p>Сервис предоставляется «как есть», без каких-либо гарантий; мы не отвечаем за решения, принятые
на основе его чисел.</p>
<h2>Контакты</h2>
<p>{c['contact']}</p>"""


def terms_html(lang: str = None) -> str:
    locale = page_locale(lang)
    c = _ctx(locale)
    if locale == "ru":
        return _page(f"Условия использования — {c['name']}", _terms_ru(c), locale, "/terms")
    return _page(f"Terms of service — {c['name']}", _terms_en(c), locale, "/terms")
