"""Projects ("profiles"): one per account/topic, each with its own settings, history and logins.

Everything lives in .reeler/ so updating the program (replacing the other files) never touches it:

    .reeler/profiles/<name>.yaml      the answers from the setup questions
    .reeler/<name>/state.db           posting history (which clip went where)
    .reeler/<name>/account.env        that account's saved Instagram session
    .reeler/<name>/youtube_token.json that project's YouTube sign-in

The original single GTA setup (.reeler/pc_state.db + .reeler/instagram.session.json) becomes the
project "gta6" automatically, with its history untouched.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Callable

import yaml

from reeler.config import PRESETS_DIR

PRESET_CHOICES = [
    ("gta6", "GTA 6 gameplay clips"),
    ("cooking", "Cooking / recipes"),
    ("football", "Football (soccer) skills & goals"),
    ("pets", "Funny / cute pets"),
    ("motivation", "Motivational speeches"),
]
PLATFORM_CHOICES = {"1": ["instagram"], "2": ["youtube"], "3": ["instagram", "youtube"]}
LEGACY_NAME = "gta6"


def profiles_dir(root: Path) -> Path:
    return root / ".reeler" / "profiles"


def profile_path(root: Path, name: str) -> Path:
    return profiles_dir(root) / f"{name}.yaml"


def list_profiles(root: Path) -> list[str]:
    d = profiles_dir(root)
    return sorted(p.stem for p in d.glob("*.yaml")) if d.exists() else []


def read_profile(root: Path, name: str) -> dict:
    with open(profile_path(root, name), encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def write_profile(root: Path, name: str, data: dict) -> Path:
    p = profile_path(root, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    header = ("# Made by START_HERE.bat. You can run START_HERE again and choose 'change' instead of\n"
              "# editing this file. Advanced settings from config.example.yaml can be added here too.\n")
    p.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return p


def last_profile(root: Path) -> str | None:
    p = profiles_dir(root) / "_last.txt"
    try:
        name = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return name if name in list_profiles(root) else None


def remember_last(root: Path, name: str) -> None:
    p = profiles_dir(root) / "_last.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(name, encoding="utf-8")


def clean_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "", name.strip().lower().replace(" ", "-"))[:30]


def is_drive_link(text: str) -> bool:
    t = text.strip()
    return "drive.google.com" in t or bool(re.fullmatch(r"[A-Za-z0-9_-]{25,}", t))


def summary(data: dict) -> str:
    where = " + ".join({"instagram": "Instagram", "youtube": "YouTube Shorts"}.get(p, p)
                       for p in data.get("platforms") or ["instagram"])
    src = "Google Drive folder" if (data.get("drive") or {}).get("folder") else f"folder {data.get('videos_dir')}"
    per_day = data.get("posts_per_day") or len((data.get("schedule") or {}).get("post_times") or []) or "?"
    topic = data.get("niche")
    if not topic and data.get("preset"):
        try:
            topic = (yaml.safe_load((PRESETS_DIR / f"{data['preset']}.yaml").read_text(encoding="utf-8")) or {}).get("niche")
        except OSError:
            topic = None
    return f"{topic or data.get('preset') or '?'}  ->  {where}, {per_day}/day, from {src}"


# ----------------------------------------------------------------------------
# Migration of the original single-project setup
# ----------------------------------------------------------------------------
def migrate_legacy(root: Path) -> str | None:
    """Turn the pre-profiles GTA setup into the project 'gta6' (keeps its history and login)."""
    if list_profiles(root):
        return None
    work = root / ".reeler"
    legacy_db = work / "pc_state.db"
    if not (legacy_db.exists() or (work / "instagram.session.json").exists()):
        return None
    old: dict = {}
    pc = root / "config.pc.yaml"
    if pc.exists():
        old = yaml.safe_load(pc.read_text(encoding="utf-8")) or {}
    drive = dict(old.get("drive") or {})
    data = {
        "profile": LEGACY_NAME,
        "preset": "gta6",
        "platforms": ["instagram"],
        "drive": {"folder": drive.get("folder") or "https://drive.google.com/drive/folders/1f1X03l5Xg80wq-fnJOz0RWbk0SiTf68k",
                  "manifest": drive.get("manifest", "drive_manifest.json")},
        "uploader": old.get("uploader", "instagrapi"),
        "order": old.get("order", "random"),
        # same folders as before, so the saved login, browser profile and history keep working
        "videos_dir": old.get("videos_dir", "./.cache/videos"),
        "work_dir": "./.reeler",
        "state_file": "./.reeler/pc_state.db",
        "account_env": "./.reeler/account.env",
        "after_post": old.get("after_post", "delete"),
        "schedule": old.get("schedule") or {"timezone": "local", "post_times": ["10:30", "13:30", "16:30", "19:30", "22:00"],
                                            "jitter_minutes": 12, "max_posts_per_day": 5, "min_gap_minutes": 90},
        "audio": {"mode": "trending"},
        "trends": {"live_research": False},
    }
    for key in ("video", "caption", "ai"):
        if old.get(key):
            data[key] = old[key]
    write_profile(root, LEGACY_NAME, data)
    # the Instagram session used to live in the shared .env - give it to this project
    env = root / ".env"
    acct = work / "account.env"
    if env.exists() and not acct.exists():
        keep = [ln for ln in env.read_text(encoding="utf-8").splitlines()
                if ln.split("=", 1)[0] in ("IG_SESSIONID", "IG_USERNAME", "IG_PASSWORD") and "=" in ln
                and ln.split("=", 1)[1].strip()]
        if keep:
            acct.write_text("\n".join(keep) + "\n", encoding="utf-8")
    if (root / "trends.yaml").exists() and not (work / "trends.yaml").exists():
        shutil.copyfile(root / "trends.yaml", work / "trends.yaml")
    remember_last(root, LEGACY_NAME)
    return LEGACY_NAME


# ----------------------------------------------------------------------------
# Questions (used by START_HERE.bat via `python -m reeler start`)
# ----------------------------------------------------------------------------
Ask = Callable[[str], str]


def _ask_until(ask: Ask, prompt: str, ok: Callable[[str], bool], error: str, default: str = "") -> str:
    while True:
        ans = ask(prompt).strip().strip('"').strip("'") or default
        if ok(ans):
            return ans
        print(error)


def ask_source(ask: Ask, current: dict | None = None) -> dict:
    cur = ""
    if current:
        cur = (current.get("drive") or {}).get("folder") or current.get("videos_dir") or ""
    print("\nWHERE ARE YOUR VIDEOS?")
    print("  Paste a Google Drive folder link (shared as 'Anyone with the link'),")
    print("  OR type a folder on this PC, e.g.  C:\\Users\\you\\Videos\\clips")
    prompt = f"  Link or folder{f' [Enter = keep {cur}]' if cur else ''}: "

    def ok(a: str) -> bool:
        return bool(a) and (is_drive_link(a) or Path(a).expanduser().is_dir())

    ans = _ask_until(ask, prompt, ok, "  That is not a Drive folder link or an existing folder. Try again.", cur)
    if is_drive_link(ans):
        return {"drive": {"folder": ans, "manifest": ""}, "after_post": "delete"}
    return {"drive": {"folder": ""}, "videos_dir": str(Path(ans).expanduser()), "after_post": "keep"}


def ask_niche(ask: Ask, current: dict | None = None) -> dict:
    print("\nWHAT ARE YOUR VIDEOS ABOUT?")
    for i, (_, label) in enumerate(PRESET_CHOICES, 1):
        print(f"  {i}) {label}")
    print("  Or just type your own topic, e.g.  makeup tutorials,  car repairs,  dance videos")
    cur = (current or {}).get("niche") or ""
    ans = _ask_until(ask, f"  Number or topic{f' [Enter = keep {cur}]' if cur else ''}: ",
                     lambda a: bool(a), "  Please type a number or a topic.", cur)
    if ans.isdigit() and 1 <= int(ans) <= len(PRESET_CHOICES):
        preset = PRESET_CHOICES[int(ans) - 1][0]
        preset_data = yaml.safe_load((PRESETS_DIR / f"{preset}.yaml").read_text(encoding="utf-8")) or {}
        return {"preset": preset, "niche": preset_data.get("niche", preset)}
    if current and ans == cur:
        return {k: current[k] for k in ("preset", "niche") if current.get(k)}
    return {"preset": "", "niche": ans}


def ask_per_day(ask: Ask, current: dict | None = None) -> int:
    from reeler.scheduler import MAX_PER_DAY, pacing_warning

    cur = int((current or {}).get("posts_per_day") or 5)
    print("\nHOW MANY POSTS PER DAY?  (5 is a good, safe number)")
    while True:
        ans = ask(f"  Posts per day [Enter = {cur}]: ").strip() or str(cur)
        if not ans.isdigit() or int(ans) < 1:
            print("  Please type a number, e.g. 5")
            continue
        n = int(ans)
        warn = pacing_warning(n)
        if warn:
            print("\n  " + warn)
            if ask("  Keep this number anyway? (y/n): ").strip().lower() not in ("y", "yes"):
                continue
        return min(n, MAX_PER_DAY)


def ask_platforms(ask: Ask, root: Path, current: dict | None = None) -> list[str]:
    cur = (current or {}).get("platforms") or ["instagram"]
    cur_key = next((k for k, v in PLATFORM_CHOICES.items() if v == cur), "1")
    print("\nWHERE SHOULD THEY BE POSTED?")
    print("  1) Instagram Reels   2) YouTube Shorts   3) Both")
    ans = _ask_until(ask, f"  1, 2 or 3 [Enter = {cur_key}]: ", lambda a: a in PLATFORM_CHOICES,
                     "  Please type 1, 2 or 3.", cur_key)
    plats = PLATFORM_CHOICES[ans]
    if "youtube" in plats and not (root / "client_secret.json").exists():
        print("\n  YouTube needs a one-time setup first (about 10 minutes):")
        print("  open README.md -> 'Turn on YouTube Shorts', then put client_secret.json in this folder.")
        if "instagram" in plats:
            if ask("  Post to Instagram only until then? (y/n): ").strip().lower() in ("y", "yes", ""):
                return ["instagram"]
        else:
            print("  (Posting will start once client_secret.json is there.)")
    return plats


def new_profile_data(name: str, root: Path, answers: dict) -> dict:
    tz = "local"
    try:
        if LEGACY_NAME in list_profiles(root):
            tz = (read_profile(root, LEGACY_NAME).get("schedule") or {}).get("timezone") or "local"
    except OSError:
        pass
    base = f"./.reeler/{name}"
    data = {
        "profile": name,
        "preset": "",
        "niche": "",
        "platforms": ["instagram"],
        "posts_per_day": 5,
        "uploader": "instagrapi",
        "order": "random",
        "work_dir": base,
        "state_file": f"{base}/state.db",
        "account_env": f"{base}/account.env",
        "videos_dir": f"{base}/downloads",
        "after_post": "delete",
        "drive": {"folder": "", "manifest": ""},
        "schedule": {"timezone": tz, "jitter_minutes": 12},
        "audio": {"mode": "trending"},
        "trends": {"live_research": False},
    }
    data.update(answers)
    return data


def apply_answers(data: dict, answers: dict) -> dict:
    out = dict(data)
    for k, v in answers.items():
        out[k] = v
    if "posts_per_day" in answers:  # the per-day number replaces any hand-written times
        sched = dict(out.get("schedule") or {})
        for k in ("post_times", "max_posts_per_day", "min_gap_minutes"):
            sched.pop(k, None)
        out["schedule"] = sched
    if not out.get("preset"):
        out.pop("preset", None)
    # drive projects keep downloads in their own folder; a local folder is used in place
    if (out.get("drive") or {}).get("folder") and not out.get("videos_dir"):
        out["videos_dir"] = f"{out.get('work_dir', './.reeler')}/downloads"
    return out


def ask_all(ask: Ask, root: Path, current: dict | None = None) -> dict:
    answers: dict = {}
    src = ask_source(ask, current)
    if (src.get("drive") or {}).get("folder") and current and not (current.get("drive") or {}).get("folder"):
        src["videos_dir"] = f"{current.get('work_dir', './.reeler')}/downloads"  # switching local -> Drive
    if src["drive"].get("folder") and current and (current.get("drive") or {}).get("folder"):
        src.pop("after_post", None)  # keep whatever the project already did with Drive downloads
        src["drive"]["manifest"] = (current.get("drive") or {}).get("manifest", "") \
            if src["drive"]["folder"] == current["drive"]["folder"] else ""
    answers.update(src)
    answers.update(ask_niche(ask, current))
    answers["posts_per_day"] = ask_per_day(ask, current)
    answers["platforms"] = ask_platforms(ask, root, current)
    return answers


def choose_profile(ask: Ask, root: Path) -> str:
    """Pick (or create, or change) a project. Returns its name."""
    migrated = migrate_legacy(root)
    if migrated:
        print(f"\nYour existing setup is now the project '{migrated}' (history and login kept).")
    names = list_profiles(root)
    if not names:
        print("\nLet's set up your first project (takes a minute).")
        return create_profile(ask, root)

    last = last_profile(root) or names[0]
    print("\nYOUR PROJECTS:")
    for i, n in enumerate(names, 1):
        print(f"  {i}) {n:12}  {summary(read_profile(root, n))}")
    print(f"  N) New project (another account or topic)")
    default = str(names.index(last) + 1)
    ans = _ask_until(ask, f"  Choose [Enter = {default}]: ",
                     lambda a: a.lower() == "n" or (a.isdigit() and 1 <= int(a) <= len(names)),
                     "  Type a number from the list, or N.", default)
    if ans.lower() == "n":
        return create_profile(ask, root)
    name = names[int(ans) - 1]
    data = read_profile(root, name)
    print(f"\n  {name}: {summary(data)}")
    if ask("  Use these settings? (Enter = yes, C = change): ").strip().lower() in ("c", "change", "n", "no"):
        write_profile(root, name, apply_answers(data, ask_all(ask, root, data)))
        print(f"  Saved. ({summary(read_profile(root, name))})")
    remember_last(root, name)
    return name


def create_profile(ask: Ask, root: Path) -> str:
    existing = list_profiles(root)
    print("\nPROJECT NAME  (one word, e.g. cooking, cars, myshop)")
    if existing:
        print("  Each project = its own Instagram/YouTube login and its own posting history.")
    name = _ask_until(ask, "  Name: ", lambda a: bool(clean_name(a)) and clean_name(a) not in existing,
                      "  Please use letters/numbers, and a name that isn't used yet.")
    name = clean_name(name)
    data = new_profile_data(name, root, {})
    data = apply_answers(data, ask_all(ask, root, None))
    write_profile(root, name, data)
    remember_last(root, name)
    print(f"\n  Project '{name}' saved: {summary(data)}")
    return name
