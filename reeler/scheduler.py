"""Work out when the next Reel should go out (fixed daily slots + jitter + daily cap)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo


def get_tz(name: str) -> tzinfo:
    if not name or name == "local":
        return datetime.now().astimezone().tzinfo  # type: ignore[return-value]
    return ZoneInfo(name)


def _jitter(day: str, slot: str, minutes: int) -> int:
    """Deterministic per-day jitter so restarts don't move a slot."""
    if minutes <= 0:
        return 0
    h = int(hashlib.sha1(f"{day}|{slot}".encode()).hexdigest(), 16)
    return (h % (2 * minutes + 1)) - minutes


def slots_for_day(day: datetime, post_times: list[str], jitter_minutes: int, tz: tzinfo) -> list[datetime]:
    out = []
    for slot in post_times:
        hh, mm = (int(x) for x in slot.split(":"))
        base = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
        out.append(base + timedelta(minutes=_jitter(base.date().isoformat(), slot, jitter_minutes)))
    return sorted(out)


def next_slot(now: datetime, sched, posted_times: list[datetime]) -> datetime:
    """First slot after `now` that respects max_posts_per_day and min_gap_minutes."""
    tz = now.tzinfo
    max_per_day = int(sched.get("max_posts_per_day", 99))
    min_gap = timedelta(minutes=int(sched.get("min_gap_minutes", 0)))
    last = max(posted_times) if posted_times else None
    for offset in range(0, 14):
        day = (now + timedelta(days=offset))
        posted_today = sum(1 for t in posted_times if t.astimezone(tz).date() == day.date())
        if posted_today >= max_per_day:
            continue
        for s in slots_for_day(day, sched["post_times"], int(sched.get("jitter_minutes", 0)), tz):
            if s <= now:
                continue
            if last and s - last < min_gap:
                continue
            return s
    return now + timedelta(days=1)


def due_now(now: datetime, sched, posted_times: list[datetime], grace_minutes: int = 45) -> bool:
    """True if a slot passed within the last `grace_minutes` and hasn't been used (for cron mode)."""
    tz = now.tzinfo
    max_per_day = int(sched.get("max_posts_per_day", 99))
    if sum(1 for t in posted_times if t.astimezone(tz).date() == now.date()) >= max_per_day:
        return False
    min_gap = timedelta(minutes=int(sched.get("min_gap_minutes", 0)))
    last = max(posted_times) if posted_times else None
    for s in slots_for_day(now, sched["post_times"], int(sched.get("jitter_minutes", 0)), tz):
        if s <= now <= s + timedelta(minutes=grace_minutes):
            if last is None or (last < s - timedelta(minutes=1) and now - last >= min_gap):
                return True
    return False


SAFE_PER_DAY = 10      # above this we warn: Instagram/YouTube may limit fast-posting accounts
RISKY_PER_DAY = 25     # above this: very likely to get the account restricted
MAX_PER_DAY = 50       # hard cap (Instagram's own API limit is 50 posts / 24 h for many accounts)


def spread_times(n: int, start: str = "09:00", end: str = "23:00") -> tuple[list[str], int]:
    """n posting times spread evenly over the day, plus a matching minimum gap in minutes."""
    n = max(1, min(int(n), MAX_PER_DAY))
    sh, sm = (int(x) for x in start.split(":"))
    eh, em = (int(x) for x in end.split(":"))
    a, b = sh * 60 + sm, eh * 60 + em
    if n == 1:
        return [f"{(a + b) // 2 // 60:02d}:{(a + b) // 2 % 60:02d}"], 120
    step = (b - a) / (n - 1)
    times = []
    for i in range(n):
        m = int(round(a + i * step))
        times.append(f"{m // 60:02d}:{m % 60:02d}")
    gap = max(10, min(120, int(step * 0.6)))
    return times, gap


def pacing_warning(n: int) -> str | None:
    """Plain-language warning for very high posting counts (None when the pace is fine)."""
    if n > MAX_PER_DAY:
        return (f"{n} a day is more than Instagram allows. It will be limited to {MAX_PER_DAY}, "
                "but even that is very risky.")
    if n > RISKY_PER_DAY:
        return (f"WARNING: {n} posts a day looks like spam to Instagram and YouTube. The account can get "
                "blocked from posting or lose reach. 3-10 a day is much safer and grows better.")
    if n > SAFE_PER_DAY:
        return (f"Heads up: {n} posts a day is a lot. Accounts that post this fast sometimes get "
                "temporarily limited. 3-10 a day is the safe range.")
    return None
