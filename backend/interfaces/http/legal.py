"""Public privacy policy and terms pages (plan 25).

Google's OAuth app verification asks for a privacy policy on the service's
own domain, and YouTube's API Services terms ask an API client to link to
the YouTube Terms of Service and the Google Privacy Policy, say how users
revoke access and how their data is deleted. These pages say it for an
installation run as a service; the operator's name and contact come from
NF_SERVICE_NAME, NF_OPERATOR_NAME and NF_CONTACT_EMAIL (escaped). English,
because that is what Google's reviewers read.
"""
import html
import os

YT_TERMS = "https://www.youtube.com/t/terms"
GOOGLE_PRIVACY = "https://policies.google.com/privacy"
USER_DATA_POLICY = "https://developers.google.com/terms/api-services-user-data-policy"
PERMISSIONS = "https://myaccount.google.com/permissions"


def _ctx() -> dict:
    name = os.environ.get("NF_SERVICE_NAME", "").strip() or "niche-finder"
    operator = os.environ.get("NF_OPERATOR_NAME", "").strip() or f"the operator of {name}"
    email = os.environ.get("NF_CONTACT_EMAIL", "").strip()
    return {"name": html.escape(name), "operator": html.escape(operator),
            "contact": (f'<a href="mailto:{html.escape(email)}">{html.escape(email)}</a>'
                        if email else "the contact address given by the operator")}


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 16px;
color:#1a1a19;background:#fff}}h1{{font-size:28px}}h2{{font-size:19px;margin-top:28px}}
a{{color:#2a78d6}}@media (prefers-color-scheme:dark){{body{{color:#e8e7e1;background:#0d0d0d}}
a{{color:#3987e5}}}}</style></head><body>{body}</body></html>"""


def privacy_html() -> str:
    c = _ctx()
    return _page(f"Privacy policy — {c['name']}", f"""
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
is refreshed from YouTube regularly. Disconnecting a channel ("Мои каналы" → "Отключить") revokes our
access at Google and deletes everything stored for that channel at once.</p>

<h2>Your choices</h2>
<ul>
<li>Revoke {c['name']}'s access to your Google account at any time at
<a href="{PERMISSIONS}">{PERMISSIONS}</a>.</li>
<li>Ask us to delete your account and all your data: write to {c['contact']}. We do it within 7 days.</li>
</ul>

<h2>Contact</h2>
<p>{c['contact']}</p>""")


def terms_html() -> str:
    c = _ctx()
    return _page(f"Terms of service — {c['name']}", f"""
<h1>Terms of service</h1>
<p>{c['name']} is run by {c['operator']}. It uses <b>YouTube API Services</b>: by using it you agree to
be bound by the <a href="{YT_TERMS}">YouTube Terms of Service</a>, and our
<a href="/privacy">privacy policy</a> describes what we do with your data.</p>
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
<p>{c['contact']}</p>""")
