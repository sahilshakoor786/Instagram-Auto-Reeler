"""Upload backends."""

from __future__ import annotations

from reeler.uploaders.base import UploadResult, Uploader

DRY = ("dry-run", "dryrun", "dry_run")
PLATFORMS = ("instagram", "youtube")


def make_uploader(cfg, state=None) -> Uploader:
    """The Instagram uploader chosen by `uploader:` in the config."""
    kind = (cfg.uploader or "graph").lower()
    if kind == "graph":
        from reeler.uploaders.graph_api import GraphUploader
        return GraphUploader(cfg)
    if kind == "instagrapi":
        from reeler.uploaders.instagrapi_up import InstagrapiUploader
        return InstagrapiUploader(cfg)
    if kind in DRY:
        from reeler.uploaders.base import DryRunUploader
        return DryRunUploader(cfg)
    raise ValueError(f"Unknown uploader '{cfg.uploader}' (use graph, instagrapi or dry-run)")


def make_uploaders(cfg) -> dict[str, Uploader]:
    """One uploader per platform in `platforms:` (instagram, youtube)."""
    from reeler.uploaders.base import DryRunUploader

    dry = (cfg.uploader or "").lower() in DRY
    out: dict[str, Uploader] = {}
    for platform in cfg.platforms:
        if platform == "instagram":
            out[platform] = make_uploader(cfg)
        elif platform == "youtube":
            if dry:
                out[platform] = DryRunUploader(cfg, platform="youtube")
            else:
                from reeler.uploaders.youtube import YouTubeUploader
                out[platform] = YouTubeUploader(cfg)
        else:
            raise ValueError(f"Unknown platform '{platform}' (use: {', '.join(PLATFORMS)})")
    return out


__all__ = ["make_uploader", "make_uploaders", "Uploader", "UploadResult", "PLATFORMS"]
