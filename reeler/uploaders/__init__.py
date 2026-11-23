"""Upload backends."""

from __future__ import annotations

from reeler.uploaders.base import UploadResult, Uploader


def make_uploader(cfg, state=None) -> Uploader:
    kind = (cfg.uploader or "graph").lower()
    if kind == "graph":
        from reeler.uploaders.graph_api import GraphUploader
        return GraphUploader(cfg)
    if kind == "instagrapi":
        from reeler.uploaders.instagrapi_up import InstagrapiUploader
        return InstagrapiUploader(cfg)
    if kind in ("dry-run", "dryrun", "dry_run"):
        from reeler.uploaders.base import DryRunUploader
        return DryRunUploader(cfg)
    raise ValueError(f"Unknown uploader '{cfg.uploader}' (use graph, instagrapi or dry-run)")


__all__ = ["make_uploader", "Uploader", "UploadResult"]
