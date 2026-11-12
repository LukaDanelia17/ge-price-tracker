"""Is a crawl due? Exit 0 if yes, 1 if not.

launchd checks hourly (and at login) via `scripts/crawl.sh --if-due`, so a
scheduled run the Mac missed — off, asleep or offline — or one that ended with a
failed category is caught up as soon as it can be, without adding runs between
the scheduled ones.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import LAST_ATTEMPT_FILE, LAST_SUCCESS_FILE, SLOTS

TZ = ZoneInfo("Asia/Tbilisi")

# After a run for a slot started but didn't finish cleanly, wait this long
# before retrying, so a category that's broken for good can't loop every hour.
RETRY_EVERY = timedelta(hours=2)


def last_slot(now: datetime) -> datetime:
    """The most recent scheduled time at or before `now`."""
    local = now.astimezone(TZ)
    today = [local.replace(hour=h, minute=0, second=0, microsecond=0) for h in SLOTS]
    past = [t for t in today if t <= local]
    if past:
        return max(past)
    return (local - timedelta(days=1)).replace(hour=max(SLOTS), minute=0, second=0, microsecond=0)


def is_due(last_success: datetime | None, now: datetime, last_attempt: datetime | None = None) -> bool:
    slot = last_slot(now)
    if last_success is not None and last_success >= slot:
        return False
    if last_attempt is not None and last_attempt >= slot and now - last_attempt < RETRY_EVERY:
        return False
    return True


def read_time(path: Path) -> datetime | None:
    try:
        return datetime.fromisoformat(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def main() -> int:
    due = is_due(read_time(LAST_SUCCESS_FILE), datetime.now(timezone.utc), read_time(LAST_ATTEMPT_FILE))
    return 0 if due else 1


if __name__ == "__main__":
    sys.exit(main())
