"""Is a crawl due? Exit 0 if yes, 1 if not.

launchd checks hourly (and at login) via `scripts/crawl.sh --if-due`, so a
scheduled run the Mac missed — off, asleep or offline — is caught up as soon as
it can be, without adding runs between the scheduled ones.
"""

import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import LAST_SUCCESS_FILE, SLOTS

TZ = ZoneInfo("Asia/Tbilisi")


def last_slot(now: datetime) -> datetime:
    """The most recent scheduled time at or before `now`."""
    local = now.astimezone(TZ)
    today = [local.replace(hour=h, minute=0, second=0, microsecond=0) for h in SLOTS]
    past = [t for t in today if t <= local]
    if past:
        return max(past)
    return (local - timedelta(days=1)).replace(hour=max(SLOTS), minute=0, second=0, microsecond=0)


def is_due(last_success: datetime | None, now: datetime) -> bool:
    return last_success is None or last_success < last_slot(now)


def main() -> int:
    try:
        last = datetime.fromisoformat(LAST_SUCCESS_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        last = None
    return 0 if is_due(last, datetime.now(timezone.utc)) else 1


if __name__ == "__main__":
    sys.exit(main())
