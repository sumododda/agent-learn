"""In-memory tracker for failed login attempts per email.

Enforces a lockout after MAX_ATTEMPTS within WINDOW_SECONDS.
Entries auto-expire after the window elapses.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

WINDOW_SECONDS = 900  # 15 minutes
MAX_ATTEMPTS = 5
_MAX_ENTRIES = 5000


@dataclass
class _LoginEntry:
    attempts: int = 0
    first_attempt_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


_tracker: dict[str, _LoginEntry] = {}


def _evict_expired() -> None:
    """Remove expired entries when tracker is at capacity."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=WINDOW_SECONDS)
    expired = [k for k, v in _tracker.items() if v.first_attempt_at < cutoff]
    for k in expired:
        del _tracker[k]


def is_locked_out(email: str) -> bool:
    """Check if the email is currently locked out due to too many failed attempts."""
    key = email.lower()
    entry = _tracker.get(key)
    if entry is None:
        return False
    now = datetime.now(timezone.utc)
    # Window expired — clear entry
    if now - entry.first_attempt_at > timedelta(seconds=WINDOW_SECONDS):
        del _tracker[key]
        return False
    return entry.attempts >= MAX_ATTEMPTS


def record_failure(email: str) -> None:
    """Record a failed login attempt."""
    key = email.lower()
    now = datetime.now(timezone.utc)
    entry = _tracker.get(key)
    if entry is None:
        if len(_tracker) >= _MAX_ENTRIES:
            _evict_expired()
            if len(_tracker) >= _MAX_ENTRIES:
                oldest_key = next(iter(_tracker))
                del _tracker[oldest_key]
        _tracker[key] = _LoginEntry(attempts=1, first_attempt_at=now)
        return
    # Window expired — start fresh
    if now - entry.first_attempt_at > timedelta(seconds=WINDOW_SECONDS):
        _tracker[key] = _LoginEntry(attempts=1, first_attempt_at=now)
        return
    entry.attempts += 1


def reset(email: str) -> None:
    """Clear failed attempts on successful login."""
    _tracker.pop(email.lower(), None)
