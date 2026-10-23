"""Passwords too easy to guess (multi-user plan P0-03). The 10-character
minimum already rules out most of the usual top lists; what is left are the
long favourites, runs along the keyboard or the alphabet, one character
repeated, and the email's own name. Local only: no breach service is asked.
"""

MIN_LENGTH = 10

# long enough to pass the minimum, still at the top of every leaked list
COMMON = frozenset("""
password12 password123 password1234 password12345 passw0rd12 passw0rd123 p@ssw0rd12
p@ssword123 password!1 password2024 password2025 password2026 passwordpassword
iloveyou12 iloveyou123 iloveyou1234 qwerty1234 qwerty12345 qwerty123456 qwertyqwerty
1q2w3e4r5t 1q2w3e4r5t6y 1qaz2wsx3edc zaq12wsxcde zaq1zaq1zaq1 qazwsxedc123 qazwsxedcrfv
123qweasdzxc 1234qwerasdf 123456789a 123456789q 1234567890q a1234567890 a123456789
abc1234567 abcd123456 abc123456789 welcome123 welcome1234 letmein123 letmein1234
changeme123 changeme12 administrator admin12345 admin123456 adminadmin root123456
football12 football123 baseball12 baseball123 basketball soccer1234 princess12 princess123
sunshine12 sunshine123 superman12 superman123 starwars12 starwars123 batman1234
michael123 jennifer12 jordan2323 charlie123 shadow1234 dragon1234 monkey1234 master1234
trustno1234 whatever12 freedom123 computer12 computer123 internet12 samsung123
youtube123 youtube1234 google1234 facebook12 instagram1 instagram12 microsoft1
nichefinder niche-finder nichefinder1 nichefinder123 creator123 subscriber
1111111111 0000000000 1212121212 1231231231 1234512345 1234554321 9876543210
123123123123 112233445566 147258369a 159753159753 147852369a 741852963a
""".split())

# a password that is just a stretch of one of these (or of it reversed) is a walk
_RUNS = ("01234567890123456789", "abcdefghijklmnopqrstuvwxyz",
         "qwertyuiopasdfghjklzxcvbnm", "1qaz2wsx3edc4rfv5tgb6yhn7ujm8ik9ol0p",
         "qazwsxedcrfvtgbyhnujmikolp", "йцукенгшщзхъфывапролджэячсмитьбю")


def problem(password: str, email: str = None):
    """Why this password is refused, or None. English, like the other
    account messages (they reach the CLI and the sign-in API)."""
    if not password or len(password) < MIN_LENGTH:
        return f"the password needs at least {MIN_LENGTH} characters"
    low = password.lower()
    if low in COMMON or low.replace(" ", "") in COMMON:
        return "this password is on the lists attackers try first; choose another"
    if len(set(low)) == 1:
        return "the password is one character repeated; choose another"
    if any(low in run or low in run[::-1] for run in _RUNS):
        return "the password is a run along the keyboard or the alphabet; choose another"
    name = (email or "").strip().lower().split("@")[0]
    if len(name) >= 4 and name in low:
        return "the password contains the email's name; choose another"
    return None
