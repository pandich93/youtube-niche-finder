"""Tests for domain/sponsors.py (plan 09): reading sponsor / promo-code /
affiliate signals from video descriptions. Pure functions -- no DB, no
network. The descriptions are shaped after real ones (English and Russian).
Run with pytest, or directly: python3 tests/test_sponsors_domain.py
"""
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import pytest  # noqa: E402

from domain import sponsors as S  # noqa: E402


def sig(text, own=()):
    return {(s["brand"], s["kind"]) for s in S.extract_sponsor_signals(text, own_names=own)}


# ------------------------------------------------------------ positives

def test_sponsored_by_brilliant():
    text = ("Today we look at black holes.\n\n"
            "This video is sponsored by Brilliant. To try everything Brilliant has to offer "
            "for free, visit https://brilliant.org/veritasium/")
    assert sig(text) == {("brilliant", "sponsor")}


def test_thanks_to_two_word_name_for_sponsoring():
    assert sig("Thanks to Titan Mattress for sponsoring!") == {("titanmattress", "sponsor")}


def test_thank_you_to_at_handle():
    assert sig("Thank you to @RemedyGames for sponsoring this video.") == {
        ("remedygames", "sponsor")}


def test_name_beats_domain_in_the_same_block():
    text = ("Go to BUYRAYCON.com/gamegrumpsOPEN and save 15% on your order\n"
            "Thanks to Raycon for sponsoring! #ad")
    assert sig(text) == {("raycon", "sponsor")}


def test_sponsor_and_promo_code_share_one_brand():
    text = ("Thanks to SeatGeek for sponsoring! Get tickets here: https://seatgeek.onelink.me/RrnK/xyz\n"
            "Use my code MMG for $20 off your first order.")
    assert sig(text) == {("seatgeek", "sponsor"), ("seatgeek", "promo_code")}


def test_todays_sponsor_with_click_redirect():
    text = "Today's sponsor ASUS!\nCheck the deal: us.asus.click/abc123"
    assert sig(text) == {("asus", "sponsor")}


def test_promo_code_with_on_brand_name():
    assert sig("Use code S7 on Gamer Supps for 10% OFF") == {("gamersupps", "promo_code")}


def test_russian_promo_code_with_domain():
    text = "Забирайте бонус по промокоду НИКИТОС на сайте: https://hyperpc.ru/go/nikitos"
    assert sig(text) == {("hyperpc", "promo_code")}


def test_affiliate_host_prefix():
    text = "Broker I recommend: https://affiliate.mybrokerbuddy.com/x"
    assert sig(text) == {("mybrokerbuddy", "affiliate")}


def test_affiliate_rfsn_and_utm_medium():
    text = "My chair: https://secretlab.co/?rfsn=1234.abc&utm_source=1234&utm_medium=affiliate"
    assert sig(text) == {("secretlab", "affiliate")}


def test_amazon_links_dedupe_to_one_row():
    out = S.extract_sponsor_signals(
        "Amazon Affiliate links:\nhttps://amzn.to/a\nhttps://amzn.to/b")
    assert [(s["brand"], s["kind"]) for s in out] == [("amazon", "affiliate")]


def test_amazon_link_with_tag_only():
    assert sig("Mic: https://www.amazon.com/dp/B0/?tag=mychan-20") == {("amazon", "affiliate")}
    assert sig("Mic: https://www.amazon.com/dp/B0/") == set()


def test_geniuslink_is_affiliate():
    assert sig("Camera https://geni.us/mycam") == {("geni.us", "affiliate")}


def test_russian_ad_with_requisites():
    text = ("Реклама. ООО «Альфа» ИНН 7728168971 ERID 2Vtzqx\n"
            "Оформить карту: https://alfa.me/abc?erid=2Vtzqx")
    assert sig(text) == {("alfa", "sponsor")}


def test_russian_sponsor_dash():
    assert sig("Спонсор видео — Ozon, ссылка на скидку в закрепе") == {("ozon", "sponsor")}


def test_russian_at_support_of():
    assert sig("Видео сделано при поддержке Skillbox") == {("skillbox", "sponsor")}


def test_sponsor_colon():
    assert sig("Sponsor: NordVPN") == {("nordvpn", "sponsor")}


def test_promo_brand_from_link_in_same_line():
    assert sig("Use code TOM at nordvpn.com/tom for 4 months free") == {("nordvpn", "promo_code")}


def test_promo_brand_from_neighbouring_line_link():
    text = "Get 20% off: https://www.squarespace.com/mark\nEnter promo code MARK at checkout"
    assert sig(text) == {("squarespace", "promo_code")}


def test_promo_brand_from_quotes():
    assert sig('Промокод «TEST5» в магазине "Lamoda" даёт скидку') == {("lamoda", "promo_code")}


def test_affiliate_link_with_domain_next_to_marker():
    assert sig("Affiliate link: nordvpn.com/best") == {("nordvpn", "affiliate")}


def test_own_channel_brand_is_ignored():
    text = "This video is sponsored by MagicGamer."
    assert sig(text, own=("Magic Gamer",)) == set()
    assert sig(text) == {("magicgamer", "sponsor")}


def test_evidence_is_the_line_and_capped():
    out = S.extract_sponsor_signals("Sponsored by Brilliant " + "x" * 400)
    assert out and len(out[0]["evidence"]) <= 200
    assert out[0]["evidence"].startswith("Sponsored by Brilliant")


def test_dedupes_by_brand_and_kind():
    text = "Sponsored by Brilliant.\n\nSponsored by Brilliant again.\nSponsor: brilliant.org"
    assert sig(text) == {("brilliant", "sponsor")}
    assert len(S.extract_sponsor_signals(text)) == 1


# ------------------------------------------------------------ false positives

NEGATIVES = {
    "become a sponsor": "Become a channel sponsor: https://www.youtube.com/channel/UC1/join",
    "стать спонсором": "Стать спонсором канала: https://www.youtube.com/channel/UC1/join",
    "sponsorship inquiries": "Sponsorship inquiries: sponsors@example.com",
    "business inquiries": "Business inquiries: hello@example.com",
    "реклама сотрудничество": "Реклама, сотрудничество: @manager",
    "reklama in topic": "Как работает баннерная реклама и почему её не видно",
    "disclaimer": "DISCLAIMER: This video is for entertainment. Not financial advice.",
    "affiliate disclaimer": "Affiliate Disclaimer: some links above are affiliate links.",
    "own merch": "Buy my Cloak: https://cloak.shop/?ref_id=1",
    "мой магазин": "Мой магазин мерча: https://shop.example.com/?aff=1",
    "promo without brand": "Use code MAGIC10 at checkout",
    "lonely utm": "Read more: https://example.com/post?utm_source=youtube&utm_campaign=x",
    "lonely ref": "Site https://example.com/?ref=abc",
    "bit.ly only": "Link: https://bit.ly/3abcXYZ",
    "not sponsored": "This video is NOT sponsored by anyone.",
    "patrons": "Thanks to my patrons for sponsoring this channel.",
    "channel sponsors list": "Спонсоры канала: Иван, Пётр",
    "socials": "Instagram: https://instagram.com/x\nTelegram: t.me/x\nTikTok: tiktok.com/@x",
    "patreon name": "Sponsored by Patreon supporters like you",
}


@pytest.mark.parametrize("name", sorted(NEGATIVES))
def test_false_positives_give_no_signal(name):
    assert S.extract_sponsor_signals(NEGATIVES[name]) == []


@pytest.mark.parametrize("text", [None, "", "   ", "\n\n \t\n"])
def test_empty_descriptions(text):
    assert S.extract_sponsor_signals(text) == []


def test_missing_space_after_period_is_not_a_link():
    assert sig("Use code TOM here.It works everywhere") == set()


# ------------------------------------------------------------ helpers

@pytest.mark.parametrize("value,expected", [
    ("nordvpn.com/xyz", "nordvpn"), ("NordVPN", "nordvpn"), ("nordvpn", "nordvpn"),
    ("https://www.NordVPN.com/tom?x=1", "nordvpn"),
    ("Gamer Supps", "gamersupps"), ("gamersupps.gg", "gamersupps"),
    ("@RemedyGames", "remedygames"), ("us.asus.click", "asus"),
    ("seatgeek.onelink.me/x", "seatgeek"), ("amzn.to/x", "amazon"),
    ("https://www.amazon.co.uk/dp/1", "amazon"), ("shop.example.co.uk/a", "example"),
    ("bit.ly/abc", ""), ("youtube.com/watch?v=1", ""), ("Patreon", ""), ("", ""),
    (None, ""), ("  ", ""), ("onelink.me/x", ""),
])
def test_normalize_brand(value, expected):
    assert S.normalize_brand(value) == expected


@pytest.mark.parametrize("host,expected", [
    ("youtube.com", True), ("www.instagram.com", True), ("open.spotify.com", True),
    ("https://store.steampowered.com/app/1", True), ("bit.ly", True), ("t.co", True),
    ("discord.gg", True), ("nordvpn.com", False), ("asus.click", False),
    ("notyoutube.com", False), ("", False),
])
def test_is_service_domain(host, expected):
    assert S.is_service_domain(host) is expected


def test_description_hash_matches_sql_md5():
    assert S.description_hash("abc") == hashlib.md5(b"abc").hexdigest()
    assert S.description_hash(None) == hashlib.md5(b"").hexdigest()
    assert S.description_hash("Привет") == hashlib.md5("Привет".encode()).hexdigest()


def test_rules_version_is_an_int():
    assert isinstance(S.SPONSOR_RULES_VERSION, int) and S.SPONSOR_RULES_VERSION >= 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
