"""Provider-agnostic contract for alert delivery (stage 07).

Every caller goes through application/alerts.py's deliver(), never a
notifier directly. send() must never raise -- a delivery failure must not
take the worker down (see worker_cycle.py's _safe() wrapper, which this
mirrors at a smaller scale: this module's own contract, not just _safe's
blanket catch, since a partial failure across several alerts needs to know
which ones actually went out).
"""
from typing import Protocol


class Notifier(Protocol):
    def send(self, text: str) -> bool:
        """text is already-formatted, ready to send (HTML for Telegram,
        plain for a generic webhook's `text` field). Returns whether the
        send succeeded -- never raises."""
        ...
