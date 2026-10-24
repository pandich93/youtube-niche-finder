"""Ручные запуски работы сервера обычным пользователем (многопользовательский
план P2-11). Чистые правила: какие операции можно отдать пользователю и когда
следующий запуск. Счётчик -- в Postgres (application/manual_runs.py).

До P2-11 всё это запускал вручную только администратор (P0-02), а остальное
делает worker по расписанию. Теперь часть операций доступна и пользователю --
по ЕГО каналам и не чаще лимита тарифа `manual_runs` (раз в час на каждую
операцию). Лимит 0 -- по-прежнему только администратор; администратора лимит
не касается.

Тяжёлые операции над всем корпусом (LLM-разметка, эмбеддинги обложек,
пересчёт карты ниш) остаются администратору -- таблица решений в
docs/manual-runs.md.
"""
import math
from datetime import timedelta

from domain.i18n import tr

WINDOW = timedelta(hours=1)

# что пользователь может запустить сам: refresh -- счётчики своих каналов и их
# видео (POST /api/refresh), scan -- алерты по своим каналам (POST /api/events/scan)
OPERATIONS = ("refresh", "scan")


def next_at(starts, limit, now):
    """Когда можно следующий запуск: None -- уже сейчас. starts -- времена
    прошлых запусков (aware datetime), limit -- запусков за WINDOW; None --
    без лимита."""
    if limit is None:
        return None
    recent = sorted(s for s in starts if s > now - WINDOW)
    if len(recent) < limit:
        return None
    if limit <= 0:
        return None                       # 0 -- не лимит, а «только администратор»
    return recent[len(recent) - limit] + WINDOW


def retry_after(at, now) -> int:
    """Секунд до `at`, не меньше одной -- для заголовка Retry-After."""
    return max(1, math.ceil((at - now).total_seconds()))


class ManualRunLimited(Exception):
    """Лимит ручных запусков исчерпан. HTTP отвечает 429 с Retry-After."""

    def __init__(self, operation: str, limit: int, at, now):
        self.operation, self.limit, self.at = operation, limit, at
        self.retry_after = retry_after(at, now)
        minutes = max(1, math.ceil(self.retry_after / 60))
        super().__init__(tr("Вручную это можно запускать не больше {limit} раз в час -- "
                            "снова можно через {minutes} мин. Остальное сделает фоновый "
                            "сборщик по расписанию.", limit=limit, minutes=minutes))
