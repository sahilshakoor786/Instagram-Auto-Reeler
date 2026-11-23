"""Pick a trending Instagram audio track that fits the clip's vibe (Instagram Audio API)."""

from __future__ import annotations

import json
import logging

from reeler.graph import GraphClient, GraphError

log = logging.getLogger(__name__)


def _audio_id(item: dict) -> str | None:
    return str(item.get("audio_id") or item.get("id") or "") or None


def _label(item: dict) -> str:
    title = item.get("title") or item.get("name") or item.get("audio_title") or ""
    artist = item.get("artist") or item.get("display_artist") or item.get("artist_name") or ""
    return " - ".join(x for x in (title, artist) if x) or (_audio_id(item) or "?")


def pick_trending_audio(graph: GraphClient, cfg, search_terms: list[str], recently_used: set[str]) -> dict | None:
    """Return {audio_id, label, query} or None. Never raises - audio is a bonus, not a blocker."""
    acfg = cfg.audio
    if acfg.mode != "trending":
        return None
    queries = [q for q in (search_terms or []) if q][:3] + [None]  # None = plain trending list
    for q in queries:
        try:
            items = graph.search_audio(audio_type=acfg.audio_type, query=q)
        except GraphError as exc:
            log.warning("Audio API unavailable (%s). Posting with the clip's own audio.", exc)
            return None
        for item in items:
            aid = _audio_id(item)
            if aid and aid not in recently_used:
                return {"audio_id": aid, "label": _label(item), "query": q or "trending"}
    return None


def audio_configuration(choice: dict | None, cfg) -> dict | None:
    if not choice:
        return None
    return {
        "audio_id": choice["audio_id"],
        "audio_volume": int(max(1, min(100, cfg.audio.audio_volume))),
        "video_volume": int(max(1, min(100, cfg.audio.video_volume))),
    }


def recently_used_audio(rows) -> set[str]:
    used = set()
    for r in rows:
        try:
            a = json.loads(r["audio"] or "null")
        except (TypeError, ValueError):
            a = None
        if isinstance(a, dict) and a.get("audio_id"):
            used.add(str(a["audio_id"]))
    return used
