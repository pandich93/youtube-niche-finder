"""Pure rules for the niche sponsor map (plan 09): find the brands a creator
names in a video description. No DB, no network.

Three kinds of signal, all read from the description text only:
  sponsor     "this video is sponsored by X", "thanks to X for sponsoring",
              "today's sponsor X", "sponsor: X", the Russian "спонсор ... - X" /
              "при поддержке X", and Russian ads with legal requisites
              (ООО / ИП / ИНН / erid) -- the brand is then taken from the link.
  promo_code  "use code ...", "promo code", "промокод" -- only when a brand can
              be found next to the code; a bare "Use code X at checkout" says
              nothing about who pays.
  affiliate   amzn.to / geni.us / Amazon links with a tag, ShareASale, Impact,
              affiliate.* hosts, tracking parameters (aff, rfsn, irclickid...),
              utm_medium=affiliate, "affiliate link: <domain>". Kept apart from
              sponsors: a commission link is not a paid integration.

The result is a LOWER BOUND: an integration that is only spoken in the video and
never written in the description is invisible here.

Bumping SPONSOR_RULES_VERSION makes application/sponsors.py rescan every video.
"""
import hashlib
import re
from urllib.parse import parse_qs, urlsplit

SPONSOR_RULES_VERSION = 1
EVIDENCE_MAX = 200
_MAX_LINE = 1000
_MAX_BRAND = 40

# Hosts that are where a creator lives or sells, never a paying brand.
SERVICE_DOMAINS = frozenset("""
youtube.com youtu.be music.youtube.com instagram.com facebook.com fb.com fb.me
twitter.com x.com t.me telegram.me telegram.org tiktok.com twitch.tv kick.com
discord.gg discord.com vk.com vk.me vkvideo.ru ok.ru dzen.ru rutube.ru reddit.com
open.spotify.com spotify.com music.apple.com podcasts.apple.com soundcloud.com
bandcamp.com patreon.com boosty.to ko-fi.com buymeacoffee.com paypal.me
linktr.ee linktree.com solo.to fanlink.tv lnk.to smarturl.it ffm.to orcd.co
song.link distrokid.com bsky.app threads.net snapchat.com pinterest.com
whatsapp.com wa.me tumblr.com vimeo.com medium.com substack.com
store.steampowered.com steampowered.com steamcommunity.com epidemicsound.com
github.com roblox.com donationalerts.com streamlabs.com beacons.ai carrd.co
docs.google.com drive.google.com forms.gle forms.google.com wikipedia.org
""".split())

# Shorteners: the brand behind them is unknown.
SHORTENERS = frozenset("""
bit.ly goo.gl tinyurl.com clck.ru vk.cc clc.li ali.click t.co cutt.ly spoti.fi
ow.ly is.gd rebrand.ly buff.ly shorturl.at tiny.cc rb.gy
""".split())

# Redirectors where the brand is the sub-domain (seatgeek.onelink.me).
REDIRECTORS = ("onelink.me", "page.link", "app.link", "bnc.lt")

# Names (not links) that are platforms, not sponsors.
SERVICE_NAMES = frozenset("""
youtube youtu instagram facebook twitter tiktok twitch kick discord telegram vk
reddit spotify patreon boosty kofi linktree linktr soundcloud github roblox
epidemicsound bluesky bsky threads snapchat pinterest whatsapp tumblr vimeo
rutube dzen
""".split())

_HOST_BRANDS = {"amzn.to": "amazon", "amzn.eu": "amazon", "amzn.com": "amazon",
                "a.co": "amazon", "geni.us": "geni.us"}

# Affiliate networks: the brand is the network (the advertiser is not visible).
_AFFILIATE_NETWORKS = {"shareasale.com": "shareasale", "impact.com": "impact",
                       "pxf.io": "impact", "sjv.io": "impact", "ojrq.net": "impact",
                       "prf.hn": "partnerize", "awin1.com": "awin", "howl.me": "howl"}
_AFFILIATE_PARAMS = frozenset(("aff", "aff_id", "affid", "affiliate", "affiliate_id",
                               "rfsn", "irclickid", "clickid", "ref_id"))

_TLDS = frozenset("""
com net org edu gov info biz io ai co app dev shop store online site xyz pro tech
cc fm gl gg tv me to ly click link us uk de fr es it nl ru ua by kz uz pl br mx jp
kr cn in au ca ch se no fi dk cz tr gr il ae sa za ng ph id vn th tw hk sg my nz
ie pt at be hu ro bg sk lt lv ee eu live life world media studio tools cloud fun
one space page bio ink lol gift deals sale
""".split())
# Bare "here.it" is far more likely a missing space after a period than a link.
_AMBIGUOUS_TLDS = frozenset("it in is be by at no so as my do go us to am me".split())
_SLD = frozenset(("co", "com", "org", "net", "gov", "edu", "ac"))

_STOPWORDS = frozenset("""
the a an our my your this that these those you us we i all everyone everybody them
him her it their his patrons supporters viewers members fans people friends today
todays what who whom and or by for to of in on at with is was are were be been
video episode channel sponsor sponsors sponsoring sponsorship link links
description below above here there code promo use get check go click visit more
learn see watch also as well so if not no never anyone nobody many some one several
others companies brands partners learn sign start try download join shop buy order
find save claim follow subscribe thanks thank big special huge exclusive please now
free discount checkout cart payment purchase signup registration home support
supporting then when just very really much super
наш наши мой мои все всем нашим зрителям подписчикам патронам спонсорам друзьям
этого канала видео выпуска нашего вас вам
""".split())

_TAIL = " \t\r\n.,;:!?\"'«»“”()[]<>*_|"


def description_hash(text) -> str:
    """md5 hex of the description; equals md5(coalesce(description,'')) in SQL."""
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ hosts

def _clean_host(value: str) -> str:
    s = (value or "").strip().lower()
    s = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", s)
    s = re.split(r"[/?#\s]", s, maxsplit=1)[0]
    s = s.rsplit("@", 1)[-1]
    s = re.sub(r":\d+$", "", s).strip(".")
    return s[4:] if s.startswith("www.") else s


def _suffix_match(host: str, names) -> bool:
    return any(host == n or host.endswith("." + n) for n in names)


def is_service_domain(host: str) -> bool:
    """True for social/streaming/donation hosts and link shorteners -- hosts
    that never identify a paying brand. Accepts a host or a full URL."""
    h = _clean_host(host)
    return bool(h) and (_suffix_match(h, SERVICE_DOMAINS) or _suffix_match(h, SHORTENERS))


def _registrable_label(host: str) -> str:
    labels = host.split(".")
    if len(labels) < 2:
        return ""
    cut = 2 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SLD else 1
    rest = labels[:-cut]
    return rest[-1] if rest else ""


def _host_brand(host: str) -> str:
    h = _clean_host(host)
    if not h or is_service_domain(h):
        return ""
    for suffix, brand in _HOST_BRANDS.items():
        if h == suffix or h.endswith("." + suffix):
            return brand
    for suffix in REDIRECTORS:
        if h.endswith("." + suffix):
            sub = h[: -len(suffix) - 1].split(".")[-1]
            return re.sub(r"[\W_]+", "", sub)[:_MAX_BRAND]
        if h == suffix:
            return ""
    return re.sub(r"[\W_]+", "", _registrable_label(h))[:_MAX_BRAND]


def _host_like(s: str):
    """The host of s when s reads as a domain or URL, else None."""
    if not s or re.search(r"\s", s) or s.startswith("@"):
        return None
    has_scheme = bool(re.match(r"^[a-z][a-z0-9+.\-]*://", s, re.I))
    host = _clean_host(s)
    if not re.fullmatch(r"[a-z0-9\-\u00c0-\uffff]+(\.[a-z0-9\-\u00c0-\uffff]+)+", host):
        return None
    if has_scheme or "/" in s or host.rsplit(".", 1)[-1] in _TLDS:
        return host
    return None


def normalize_brand(value) -> str:
    """One lower-case key per brand: 'nordvpn.com/xyz', 'NordVPN' and 'nordvpn'
    -> 'nordvpn'; 'Gamer Supps' == 'gamersupps.gg'; '@RemedyGames' ->
    'remedygames'; 'us.asus.click' -> 'asus'; 'seatgeek.onelink.me/x' ->
    'seatgeek'; 'amzn.to/x' -> 'amazon'. Shorteners and service hosts/names
    (bit.ly, youtube.com, Patreon) -> ''."""
    s = (value or "").strip().strip(_TAIL)
    if not s:
        return ""
    host = _host_like(s)
    if host:
        return _host_brand(host)
    key = re.sub(r"[\W_]+", "", s.lower())[:_MAX_BRAND]
    return "" if key in SERVICE_NAMES else key


# ------------------------------------------------------------------ links

_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"'«»()\[\]]+")
_BARE_RE = re.compile(
    r"(?i)(?<![\w@./:\-])((?:[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?\.)+("
    + "|".join(sorted(_TLDS, key=len, reverse=True))
    + r"))(?![\w\-]|\.[a-z0-9])(/[^\s<>\"'«»()\[\]]*)?")


def _links(text: str) -> list:
    """[(host, url)] in order of appearance: schemed/www URLs plus bare
    domains such as 'BUYRAYCON.com/x' or 'us.asus.click/abc'."""
    found = []
    spans = []
    for m in _URL_RE.finditer(text):
        url = m.group(0).rstrip(_TAIL)
        spans.append((m.start(), m.end()))
        found.append((m.start(), _clean_host(url), url))
    for m in _BARE_RE.finditer(text):
        if any(a <= m.start() < b for a, b in spans):
            continue
        host, tld, path = m.group(1), m.group(2), m.group(3)
        if tld == tld.title() and tld != tld.lower():
            continue  # "Thanks.It" -- a capitalised TLD is a sentence, not a link
        if tld.lower() in _AMBIGUOUS_TLDS and not path and host.count(".") < 2:
            continue
        found.append((m.start(), _clean_host(host), host + (path or "")))
    found.sort()
    return [(h, u) for _, h, u in found if h]


def _first_brand_link(links):
    for host, _ in links:
        if _host_brand(host):
            return _host_brand(host)
    return ""


def _affiliate_brand(host: str, url: str) -> str:
    """Brand of an affiliate link, '' if the URL is not one."""
    if host in _HOST_BRANDS and _HOST_BRANDS[host] in ("amazon", "geni.us"):
        return _HOST_BRANDS[host]
    for suffix, brand in _AFFILIATE_NETWORKS.items():
        if host == suffix or host.endswith("." + suffix):
            return brand
    try:
        parts = urlsplit(url if "://" in url else "http://" + url)
        params = {k.lower(): [x.lower() for x in v] for k, v in parse_qs(parts.query).items()}
    except ValueError:
        params = {}
    labels = host.split(".")
    if labels[0] in ("affiliate", "affiliates") and len(labels) > 2:
        return _host_brand(host)
    if _registrable_label(host) == "amazon":
        return "amazon" if "tag" in params else ""
    if _AFFILIATE_PARAMS & params.keys() or "affiliate" in params.get("utm_medium", []):
        return _host_brand(host)
    return ""


# ------------------------------------------------------------------ rules

# Whole line is a call to action, a contact line, a disclaimer or a negation.
_HARD_SKIP = re.compile("|".join([
    r"\bbecome\s+(?:a|an|our)\s+(?:channel\s+)?(?:sponsor|member|patron|supporter)",
    r"\bjoin\s+(?:this|the|my|our)\s+channel\b",
    r"\b(?:channel|youtube)\s+(?:sponsor|member)s?\b",
    r"\bsponsor\s+(?:this|my|the|our)\s+(?:channel|videos?)\b",
    r"\b(?:want|interested|looking)\s+(?:to|in)\s+(?:sponsor|advertis)\w*",
    r"\bsponsor(?:ship)?s?\s+(?:inquir|enquir|request|contact|opportunit)\w*",
    r"\b(?:business|sponsorship|advertising|ad|collab(?:oration)?|brand|partnership|"
    r"press|booking)s?\s*(?:inquir|enquir|request|contact|e-?mail|opportunit|deal)\w*",
    r"\b(?:for|to)\s+(?:business|sponsorship|advertising)\b",
    r"стать\s+(?:спонсором|участником|патроном)",
    r"спонсор\w*\s+канала",
    r"спонсоры?\s+(?:на\s+)?(?:youtube|ютуб|boosty|бусти)",
    r"реклам\w*[,:;/\s]+(?:и\s+)?сотрудничеств",
    r"сотрудничеств",
    r"по\s+вопросам\s+(?:рекламы|сотрудничества)",
    r"\bдля\s+(?:рекламы|сотрудничества)\b",
    r"\bdisclaimer\b", r"\bdisclosure\b", r"дисклеймер", r"отказ\s+от\s+ответственности",
    r"\b(?:not|no|isn'?t|wasn'?t|never|without)\s+(?:been\s+)?sponsored\b",
    r"\bunsponsored\b", r"\bno\s+sponsor(?:s|ship)?\b",
    r"\bnot\s+(?:an?\s+)?(?:ad|advert|paid)\b",
    r"\bне\s+(?:является\s+)?(?:спонсир|рекламн)\w*",
]), re.I)

# Own store / own product: blocks promo codes, affiliate links and link-derived
# brands on that line, but not an explicit "sponsored by X" on the same line.
_MERCH = re.compile("|".join([
    r"\b(?:my|our)\s+(?:merch|store|shop|website|site|book|course)\b", r"\bmerch\b",
    r"\b(?:buy|get|grab|shop|order|check\s+out)\s+(?:my|our)\b",
    r"\b(?:мой|мо[её]|наш|наши|моя|нашу)\s+(?:магазин|мерч|сайт|курс|книг\w*)",
    r"\bмерч\b", r"\b(?:купить|купите|заказать)\s+(?:мо|наш)\w*",
]), re.I)

_W1 = r"[\w@][\w&'’\-]*(?:\.[a-z]{2,6}(?![\w]))?"
_W2 = r"[A-ZА-ЯЁ][\w&'’\-]*"
_NAME = r"(?P<name>" + _W1 + r"(?:[ \t]+" + _W2 + r")?)"
_SEP = r"[ \t]*[:\-–—]?[ \t]*(?:the[ \t]+)?[«\"“„]?"

_SPONSOR_PATTERNS = [re.compile(p) for p in (
    r"(?i:sponsored\s+by|brought\s+to\s+you\s+by|made\s+possible\s+(?:in\s+part\s+)?by|"
    r"paid\s+partnership\s+with|paid\s+promotion\s+(?:by|for|of)|sponsorship\s+from)"
    + _SEP + _NAME,
    r"(?i:thank(?:s|\s+you)(?:\s+so\s+much)?(?:\s+to|\s+goes\s+to)?)[ \t]+(?:the[ \t]+)?[«\"“„]?"
    + _NAME + r"[ \t]+(?i:for\s+(?:sponsoring|the\s+sponsorship|partnering|"
    r"supporting\s+this|making\s+this\s+(?:video|episode)\s+possible))",
    r"(?i:(?:today'?s|our|this\s+video'?s|main|special)\s+sponsors?(?:\s+(?:is|was|are))?)"
    + _SEP + _NAME,
    r"(?i:\bsponsors?)[ \t]*[:：][ \t]*(?:the[ \t]+)?[«\"“„]?" + _NAME,
    r"(?i:спонсор(?:ом|а|ы)?(?:\s+(?:этого|нашего|данного))?"
    r"(?:\s+(?:видео|выпуска|ролика|стрима|эпизода))?[ \t]*(?:является|[—–:\-])[ \t]*)"
    r"[«\"“„]?" + _NAME,
    r"(?i:при\s+поддержке)[ \t]+[«\"“„]?" + _NAME,
)]

_PROMO = re.compile(
    r"(?i)\b(?:use|enter|apply|using|with|redeem|type|put|get|grab)\s+"
    r"(?:(?:my|our|the|this|exclusive)\s+)*(?:(?:promo|discount|coupon|referral|creator)\s+)?code\b"
    r"|\b(?:promo|discount|coupon|referral)[\s\-]?code\b|промо-?код|по\s+коду")
_ON_AT_FROM = re.compile(r"\b(?:on|at|from)\s+(?:the\s+)?(?P<name>[A-Z][\w&'’\-]*(?:[ \t]+[A-Z][\w&'’\-]*)?)")
_QUOTED = re.compile(r"[«\"“„]([^»\"”“]{2,30})[»\"”“]")
_REQUISITES = re.compile(r"(?i)\berid\b|\bерид\b|\bинн\b\s*:?\s*\d|\bогрн(?:ип)?\b\s*:?\s*\d")
_AD_WORD = re.compile(r"(?i)\bреклама\b")
_LEGAL = re.compile(r"\b(?:ООО|ИП|АО|ПАО)\b|(?i:\bинн\b|\bогрн\b)")
_AFF_MARK = re.compile(r"(?i)\baffiliate\s+(?:links?|code|program)\b|партн[её]рск\w+\s+ссылк|"
                       r"реферальн\w+\s+ссылк")


def _name_brand(raw: str) -> str:
    """Brand key for a captured name, '' if it is a stop-word or a platform."""
    words = raw.split()
    kept = []
    for w in words:
        if w.strip(_TAIL).lower() in _STOPWORDS:
            break
        kept.append(w)
    if not kept:
        return ""
    return normalize_brand(" ".join(kept).strip(_TAIL))


def _own_keys(own_names) -> set:
    keys = set()
    for n in own_names or ():
        k = re.sub(r"[\W_]+", "", str(n or "").lower())
        if len(k) >= 2:
            keys.add(k)
    return keys


def _is_own(brand: str, own: set) -> bool:
    for k in own:
        if brand == k:
            return True
        short, long_ = sorted((brand, k), key=len)
        if len(short) >= 5 and short in long_:
            return True
    return False


def _evidence(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return t if len(t) <= EVIDENCE_MAX else t[:EVIDENCE_MAX - 1] + "…"


def extract_sponsor_signals(description, own_names=()) -> list:
    """[{"brand", "kind", "evidence"}] found in one description, de-duplicated
    by (brand, kind), in order of appearance. own_names (the channel's title,
    handle) never count as a sponsor. Empty for None / blank text."""
    if not description or not str(description).strip():
        return []
    own = _own_keys(own_names)
    lines = []
    block = 0
    for raw in str(description).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        text = raw.strip()[:_MAX_LINE]
        if not text:
            block += 1
            continue
        hard = bool(_HARD_SKIP.search(text))
        merch = bool(_MERCH.search(text))
        lines.append({
            "text": text, "block": block, "skip": hard, "merch": merch or hard,
            "links": [] if (hard or merch) else _links(text),
            "names": [] if hard else _sponsor_names(text),
        })

    out, seen = [], set()

    def add(brand, kind, text):
        if not brand or len(brand) < 2 or _is_own(brand, own) or (brand, kind) in seen:
            return
        seen.add((brand, kind))
        out.append({"brand": brand, "kind": kind, "evidence": _evidence(text)})

    def near(i):
        for j in range(max(0, i - 2), min(len(lines), i + 3)):
            if j != i and lines[j]["block"] == lines[i]["block"]:
                yield lines[j]

    for i, ln in enumerate(lines):
        if ln["skip"]:
            continue
        text = ln["text"]
        for brand in ln["names"]:
            add(brand, "sponsor", text)

        if _REQUISITES.search(text) or (_AD_WORD.search(text) and _LEGAL.search(text)):
            brand = _first_brand_link(ln["links"]) or next(
                (b for n in near(i) if (b := _first_brand_link(n["links"]))), "")
            add(brand, "sponsor", text)
        for host, url in ln["links"]:
            if re.search(r"(?i)[?&]erid=", url):
                add(_host_brand(host), "sponsor", text)

        if not ln["merch"] and _PROMO.search(text):
            add(_promo_brand(ln, near(i)), "promo_code", text)

        if not ln["merch"]:
            for host, url in ln["links"]:
                add(_affiliate_brand(host, url), "affiliate", text)
            if _AFF_MARK.search(text):
                pool = list(ln["links"])
                nxt = lines[i + 1] if i + 1 < len(lines) else None
                if not pool and text.rstrip().endswith(":") and nxt and not nxt["merch"]:
                    pool = nxt["links"]
                add(_first_brand_link(pool), "affiliate", text)
    return out


def _sponsor_names(text: str) -> list:
    names = []
    for pat in _SPONSOR_PATTERNS:
        for m in pat.finditer(text):
            b = _name_brand(m.group("name"))
            if b and b not in names:
                names.append(b)
    return names


def _promo_brand(ln, neighbours) -> str:
    """Whose promo code is it: sponsor name on the line > a brand link on the
    line > sponsor name nearby > a brand link nearby > 'on/at/from Name' >
    a quoted name. '' when the line names nobody."""
    text = ln["text"]
    if ln["names"]:
        return ln["names"][0]
    b = _first_brand_link(ln["links"])
    if b:
        return b
    near = list(neighbours)
    for n in near:
        if n["names"]:
            return n["names"][0]
    for n in near:
        b = _first_brand_link(n["links"])
        if b:
            return b
    m = _ON_AT_FROM.search(text)
    if m:
        b = _name_brand(m.group("name"))
        if b:
            return b
    for m in _QUOTED.finditer(text):
        if re.fullmatch(r"[A-ZА-ЯЁ0-9_\-]+", m.group(1).strip()):
            continue  # an all-caps quoted token is the code itself
        b = _name_brand(m.group(1))
        if b:
            return b
    return ""
