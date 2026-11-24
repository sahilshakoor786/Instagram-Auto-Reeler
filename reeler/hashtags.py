"""Trend engine: score hashtags (seed priors + live Instagram data) and pick the best mix per clip."""

from __future__ import annotations

import hashlib
import logging
import re
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from reeler.graph import GraphClient, GraphError
from reeler.state import State

log = logging.getLogger(__name__)

SEED_FILE = Path(__file__).parent / "data" / "gta6_tags.yaml"
WEEK = 7 * 24 * 3600
TAG_RE = re.compile(r"#([A-Za-z0-9_]{2,40})")

# Engagement-bait / spam tags that can get reach throttled.
BLOCKLIST = {
    "follow4follow", "followforfollow", "like4like", "likeforlike", "f4f", "l4l", "followback",
    "followme", "likes", "instalike", "tagsforlikes", "spam4spam", "s4s", "explorepage",
    "viral", "fyp", "foryou", "foryoupage", "explore", "trending", "reels", "reelsinstagram",
}
# Kept for the GTA 6 preset (presets/gta6.yaml lists the same words as trends.discovery_hints).
GAMING_HINTS = ("gta", "game", "gaming", "rockstar", "vice", "leonida", "ps5", "xbox", "playstation",
                "lucia", "jason", "clip", "stunt", "heist", "chase", "openworld", "graphics")


def normalize_tag(tag: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", tag.lower().lstrip("#"))


@dataclass
class Candidate:
    tag: str
    score: float
    bucket: str  # broad | niche | category | trending
    category: str = ""
    sources: set[str] = field(default_factory=set)


def load_seed(path: Path | None = None) -> dict:
    with open(path or SEED_FILE, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def seed_candidates(seed: dict) -> dict[str, Candidate]:
    out: dict[str, Candidate] = {}

    def add(tag: str, w: float, bucket: str, cat: str = ""):
        t = normalize_tag(tag)
        if not t or t in BLOCKLIST:
            return
        if t in out:
            out[t].score = max(out[t].score, float(w))
            if cat and not out[t].category:
                out[t].category = cat
            return
        out[t] = Candidate(t, float(w), bucket, cat, {"seed"})

    for tag, w in (seed.get("broad") or {}).items():
        add(tag, w, "broad")
    for tag, w in (seed.get("niche") or {}).items():
        add(tag, w, "niche")
    for cat, tags in (seed.get("categories") or {}).items():
        for tag, w in (tags or {}).items():
            add(tag, w, "category", cat)
    return out


# ----------------------------------------------------------------------------
# Live research via the Instagram Graph API
# ----------------------------------------------------------------------------

def _age_hours(ts: str | None) -> float:
    if not ts:
        return 999.0
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return 999.0
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


def analyze_media(media: list[dict], self_tag: str) -> dict:
    """Engagement + co-occurring hashtags for one hashtag's media list."""
    reels = [m for m in media if m.get("media_product_type") in (None, "REELS") or m.get("media_type") == "VIDEO"]
    pool = reels or media
    engagement = [(m.get("like_count") or 0) + 3 * (m.get("comments_count") or 0) for m in pool]
    co: Counter = Counter()
    for m, eng in zip(pool, engagement):
        tags = {normalize_tag(t) for t in TAG_RE.findall(m.get("caption") or "")}
        tags.discard(self_tag)
        recency = 1.0 if _age_hours(m.get("timestamp")) < 72 else 0.6
        for t in tags:
            co[t] += (1 + min(eng, 50_000) / 5_000) * recency
    return {
        "posts": len(pool),
        "median_engagement": statistics.median(engagement) if engagement else 0,
        "cooccurring": co,
    }


def _seed_file(name: str, cfg) -> Path:
    from reeler.config import DATA_DIR
    for c in (Path(name).expanduser(), cfg.root / name, DATA_DIR / name):
        if c.is_file():
            return c
    raise FileNotFoundError(f"hashtag seed file '{name}' not found")


def resolve_seed(cfg) -> dict:
    """Seed hashtags for THIS niche: your own file > config/preset > Claude starter kit > niche words."""
    from reeler import niche

    user = _user_seed_file(cfg)
    if user:
        return load_seed(user)
    if cfg.trends.get("seed_file"):
        return load_seed(_seed_file(cfg.trends.seed_file, cfg))
    if cfg.get("hashtags"):
        return dict(cfg["hashtags"])
    kit = niche.ensure_kit(cfg)
    if kit:
        return niche.kit_seed(kit)
    return niche.generic_seed(cfg.ai.niche)


def discovery_hints(cfg, seed: dict) -> tuple[str, ...]:
    """Words a newly discovered co-trending tag must contain to count as on-topic."""
    from reeler.niche import niche_words

    hints = [normalize_tag(h) for h in cfg.trends.get("discovery_hints") or []]
    if not hints:
        hints = [w.rstrip("s") for w in niche_words(cfg.ai.niche) if len(w) >= 3]
        for bucket in ("broad", "niche"):
            hints += [normalize_tag(t) for t in (seed.get(bucket) or {})]
    return tuple(h for h in dict.fromkeys(hints) if len(h) >= 3)


def refresh_trends(state: State, graph: GraphClient | None, cfg, force: bool = False) -> dict:
    """Re-score the hashtag pool. Uses live data when possible, otherwise seed priors."""
    tcfg = cfg.trends
    seed = resolve_seed(cfg)
    cands = seed_candidates(seed)
    for t in tcfg.get("seed_tags", []):
        t = normalize_tag(t)
        if t and t not in cands:
            cands[t] = Candidate(t, 0.6, "broad", "", {"config"})

    last = state.kv_get("trends_refreshed_at", 0)
    fresh_enough = time.time() - last < tcfg.refresh_hours * 3600
    report = {"live": False, "researched": [], "discovered": [], "error": None}

    if tcfg.live_research and graph is not None and (force or not fresh_enough):
        try:
            _live_research(state, graph, cfg, cands, report, hints=discovery_hints(cfg, seed))
            report["live"] = True
        except GraphError as exc:
            report["error"] = str(exc)
            log.warning("Live hashtag research unavailable (%s) - using seed list + cached scores", exc)

    # Blend in previously learned live/discovered scores so trends persist between refreshes.
    for row in state.hashtags():
        if row["source"] in ("live", "discovered") and row["tag"] not in cands:
            cands[row["tag"]] = Candidate(row["tag"], row["score"] * 0.9, "trending", row["category"] or "",
                                          {row["source"]})

    existing = {r["tag"]: r for r in state.hashtags()}
    for c in cands.values():
        prev = existing.get(c.tag)
        source = "live" if "live" in c.sources else ("discovered" if "discovered" in c.sources else "seed")
        if prev and prev["source"] in ("live", "discovered") and source == "seed":
            continue  # keep the live score from an earlier run
        state.save_hashtag(c.tag, round(c.score, 4), source, c.category or c.bucket)

    if report["live"]:
        state.kv_set("trends_refreshed_at", time.time())
    return report


def _user_seed_file(cfg) -> Path | None:
    """Your own hashtag list: trends.yaml in the project's work folder (or, without profiles, next to
    the config)."""
    cands = [cfg.work_path / "trends.yaml"]
    if not cfg.get("profile"):
        cands.append(cfg.root / "trends.yaml")
    return next((p for p in cands if p.exists()), None)


def _live_research(state: State, graph: GraphClient, cfg, cands: dict[str, Candidate], report: dict,
                   hints: tuple[str, ...] = GAMING_HINTS) -> None:
    budget = int(cfg.trends.weekly_query_budget)
    week_ago = time.time() - WEEK

    def hashtag_id(tag: str) -> str | None:
        row = state.get_lookup(tag)
        if row and row["hashtag_id"]:
            return row["hashtag_id"]  # cached id - no new unique-hashtag query needed
        if state.lookup_count_since(week_ago) >= budget:
            return None
        hid = graph.hashtag_id(tag)
        state.record_lookup(tag, hid)
        return hid

    research = [c.tag for c in sorted(cands.values(), key=lambda c: -c.score)
                if c.bucket in ("broad", "niche")][:8]
    stats: dict[str, dict] = {}
    co_total: Counter = Counter()
    for tag in research:
        hid = hashtag_id(tag)
        if not hid:
            continue
        top = graph.hashtag_media(hid, "top_media")
        recent = []
        try:
            recent = graph.hashtag_media(hid, "recent_media")
        except GraphError:
            pass
        a = analyze_media(top + recent, tag)
        a["recent_24h"] = sum(1 for m in recent if _age_hours(m.get("timestamp")) <= 24)
        stats[tag] = a
        co_total.update(a["cooccurring"])
        report["researched"].append(tag)

    if not stats:
        raise GraphError("no hashtag could be researched (budget exhausted or no access)")

    max_eng = max((s["median_engagement"] for s in stats.values()), default=1) or 1
    max_vel = max((s["recent_24h"] for s in stats.values()), default=1) or 1
    for tag, s in stats.items():
        live = 0.55 * (s["median_engagement"] / max_eng) + 0.45 * (s["recent_24h"] / max_vel)
        c = cands[tag]
        c.score = round(0.4 * c.score + 0.6 * live, 4)
        c.sources.add("live")

    # Tags that keep showing up next to this niche's tags on top-performing posts = what's trending now.
    max_co = max(co_total.values(), default=1) or 1
    for tag, weight in co_total.most_common(40):
        if tag in BLOCKLIST or len(tag) > 30 or not any(h in tag for h in hints):
            continue
        score = round(0.35 + 0.6 * (weight / max_co), 4)
        if tag in cands:
            cands[tag].score = max(cands[tag].score, score)
            cands[tag].sources.add("discovered")
        else:
            cands[tag] = Candidate(tag, score, "trending", "", {"discovered"})
            report["discovered"].append(tag)


# ----------------------------------------------------------------------------
# Selection
# ----------------------------------------------------------------------------

def pool_from_state(state: State) -> dict[str, Candidate]:
    out = {}
    for r in state.hashtags():
        cat = r["category"] or ""
        bucket = cat if cat in ("broad", "niche", "trending") else "category"
        out[r["tag"]] = Candidate(r["tag"], r["score"], bucket, "" if bucket != "category" else cat,
                                  {r["source"]})
    return out


def select_hashtags(pool: dict[str, Candidate], *, category: str = "", ai_picks: list[str] | None = None,
                    recently_used: list[list[str]] | None = None, max_n: int = 5, seed: str = "") -> list[str]:
    """Pick a balanced set: 1 broad anchor, category match, AI/trending picks, a niche tag."""
    if max_n <= 0:
        return []
    usage = Counter(t for tags in (recently_used or []) for t in tags)

    def eff(c: Candidate) -> float:
        penalty = 0.0 if c.bucket == "broad" else 0.08 * usage[c.tag]
        # small per-clip variation so consecutive posts don't carry identical tag sets
        noise = (int(hashlib.md5(f"{seed}|{c.tag}".encode()).hexdigest()[:6], 16) / 0xFFFFFF - 0.5) * 0.12 \
            if seed and c.score < 0.95 else 0.0
        return c.score - penalty + noise

    chosen: list[str] = []

    def take(cands: list[Candidate]) -> None:
        for c in sorted(cands, key=eff, reverse=True):
            if c.tag not in chosen:
                chosen.append(c.tag)
                return

    values = list(pool.values())
    take([c for c in values if c.bucket == "broad"])
    take([c for c in values if c.category == category and category])
    for raw in ai_picks or []:
        t = normalize_tag(raw)
        if len(chosen) >= max_n - 1:
            break
        if t and t not in BLOCKLIST and t not in chosen and len(t) <= 30:
            chosen.append(t)
            break
    take([c for c in values if c.bucket == "trending"])
    take([c for c in values if c.bucket == "niche"])
    rest = sorted(values, key=eff, reverse=True)
    for c in rest:
        if len(chosen) >= max_n:
            break
        if c.tag not in chosen:
            chosen.append(c.tag)
    return chosen[:max_n]
