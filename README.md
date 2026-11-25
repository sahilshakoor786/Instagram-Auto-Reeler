# Auto Poster: Instagram Reels + YouTube Shorts 📱

Put your short videos in a Google Drive folder (or a folder on your PC). This program posts them for
you, a few every day, to **Instagram Reels**, **YouTube Shorts**, or **both**.

It works for **any topic**: gaming, cooking, football, pets, motivation, makeup, cars... you just
type what your videos are about. For every video it:

- makes it full-screen vertical (9:16) with a short, eye-catching text at the start,
- writes a caption, a question for comments, and 5 hashtags that fit your topic,
- adds a trending Instagram song (Instagram only),
- posts it at good times during the day, and **never posts the same video twice** on the same platform.

---

## ▶️ Start (Windows)

1. On GitHub, click the green **Code** button → **Download ZIP**. Unzip it, for example to
   `Documents\Instagram-Auto-Reeler`.
2. Open that folder and double-click **`START_HERE.bat`**.
   - The very first time it installs what it needs. If it says *"CLOSE this window and double-click
     START_HERE.bat again"*, do exactly that.
3. Answer the questions (type, then press **Enter**):
   - **Where are your videos?** Paste your Google Drive folder link, or type a folder on your PC.
   - **What are they about?** Pick a number (GTA 6, cooking, football, pets, motivation) or type
     your own topic, e.g. `makeup tutorials`.
   - **How many posts per day?** 5 is a good, safe number.
   - **Where to post?** 1 = Instagram, 2 = YouTube Shorts, 3 = both.
4. **Instagram login:** a browser window opens. Log in to Instagram like normal. The program
   continues by itself. You only do this once.
5. **YouTube login** (only if you chose YouTube): a browser window opens. Pick your Google account
   and channel and click **Allow**. Once only. (First do the one-time setup below.)
6. Leave the black window open. It keeps posting every day. If you close it or the PC restarts,
   double-click `START_HERE.bat` again: it continues where it stopped, without reposting anything.

**Next time** you start it, it shows your saved answers. Press **Enter** to use them, or type
**C** to change them.

### Google Drive folder
In Google Drive, right-click the folder → **Share** → under *General access* choose
**Anyone with the link** → **Copy link**. Paste that link when asked. New videos you add to the
folder are picked up automatically.

### Several accounts or topics
When it asks you to choose a project, type **N** for a new one (e.g. one for GTA, one for cooking).
Every project has its own login, its own videos and its own history, so they never mix.

### How many per day?
About **3 to 10 a day** is safe. If you type a big number (like 100), the program warns you:
posting that fast looks like spam and can get an account blocked from posting. When you ask it to
post several videos *right now*, it waits **20 minutes between each one** to keep the account safe.

### Better captions (optional)
Captions work without anything extra. For smarter captions that describe what happens in each
video, get a key at <https://console.anthropic.com>, open the file `.env` in the program folder with
Notepad, and put it after `ANTHROPIC_API_KEY=`. Save. (Never share this key with anyone.)

---

## 🔴 Turn on YouTube Shorts (one time, about 10 minutes)

YouTube only allows uploads through Google's official way, so you need a free "key file" from Google.

1. Go to <https://console.cloud.google.com> and sign in with the Google account of your YouTube channel.
2. At the top, click the project list → **New project** → name it `My Auto Poster` → **Create**.
3. In the search bar type **YouTube Data API v3** → open it → click **Enable**.
4. Left menu: **Google Auth Platform** (or **APIs & Services → OAuth consent screen**) → **Get started**.
   App name: `My Auto Poster`, your email, audience **External**, then **Create**.
5. Open **Audience** → click **Publish app** → **Confirm**. (Otherwise Google logs you out every 7 days.)
6. Open **Clients** (or **Credentials**) → **Create client** → type **Desktop app** → **Create** →
   **Download JSON**.
7. Rename the downloaded file to **`client_secret.json`** and put it in the program folder
   (next to `START_HERE.bat`).
8. Start `START_HERE.bat`, choose your project, type **C**, and pick **3** (both) or **2** (YouTube).

When you log in, Google may say *"Google hasn't verified this app"*. That's your own app: click
**Advanced** → **Go to My Auto Poster** → **Allow**.

> ⚠️ **Important Google rule:** videos uploaded by a brand-new Google project are put on
> **Private** until Google approves the project. Approval is free: fill in the
> [YouTube API audit form](https://support.google.com/youtube/contact/yt_api_form) (say it is a
> personal tool that uploads your own videos to your own channel). Until then, your Shorts are
> uploaded but only you can see them, and you can make them public yourself in YouTube Studio.
> The program tells you when this happens.

A video counts as a Short when it is vertical (or square) and **up to 3 minutes** long. The program
makes every video vertical, keeps it under that limit, and adds **#Shorts**. YouTube also limits
how many uploads a new Google project can make per day, so keep the number per day modest.

---

## ☁️ YouTube Shorts with your PC off

GitHub's servers post **5 Shorts a day** for you (09:43, 12:43, 15:43, 18:43, 21:43 India time).
Your PC can be off.

**Adding videos:** paste links in a Claude chat ("add these to my YouTube queue"), or add them
yourself on GitHub in the file `queue/links.txt`, one per line. These work: Google Drive file or
folder links (shared "Anyone with the link"), Dropbox links, direct .mp4 links, and
YouTube/TikTok/Instagram links. Only use videos you own or have permission to post. Each video is
posted once.

**One-time setup:**
1. In Google Cloud, open **Google Auth Platform → Audience** and click **Publish app**. (If it's
   grey, fill *App name* and *Developer contact email* on the **Branding** page and Save first.)
   Without this, Google logs the cloud out after 7 days.
2. On your PC, delete `.reeler\<project>\youtube_token.json`, then start `START_HERE.bat` once and
   click **Allow** in the Google window. That makes a fresh, long-lasting login.
3. Open that `youtube_token.json` with Notepad and copy everything (Ctrl+A, Ctrl+C).
4. On GitHub: your project → **Settings → Secrets and variables → Actions → New repository secret**.
   Name: `YOUTUBE_TOKEN_JSON`, Secret: paste (Ctrl+V) → **Add secret**.
   (Never paste it into a chat.)
5. Test it: **Actions → Cloud YouTube Shorts → Run workflow** → **Run**. Green tick = it posted.

Settings (topic, times) are in `config.youtube-cloud.yaml`. History is saved in `state/youtube_cloud.db`.

---

## ⬆️ Updating from the old GTA-only version

1. Close the black window if it is running.
2. Download the new ZIP (green **Code** button → **Download ZIP**) and unzip it.
3. Copy everything from the new folder **into your old folder** and choose **Replace** when Windows
   asks. **Do not delete** the `.reeler` folder or the `.env` file in your old folder: they hold your
   Instagram login and your posting history. (If you can't see `.reeler`, that's fine: just don't delete anything.)
4. Double-click `START_HERE.bat`. Your GTA setup becomes the project **gta6** automatically, with the
   same Drive folder, login and history, so nothing gets posted twice. Press **Enter** to keep going.

---

## Ready-made topics (presets)

`presets/` has ready-made settings (categories, caption style, hashtags, music vibe) for:
**gta6**, **cooking**, **football**, **pets**, **motivation**. For any other topic just type it;
the program builds a matching hashtag list from your topic (and, with a Claude key, asks Claude once
for a full starter list of hashtags, categories and hook styles, then reuses it).

---

## For advanced users

Everything below is optional. `START_HERE.bat` runs `python -m reeler start`; each project's settings
are saved in `.reeler/profiles/<name>.yaml` and can be used with any command via `-p <name>`, e.g.
`python -m reeler -p cooking queue`. The project's history is in `.reeler/<name>/state.db`, its
Instagram session in `.reeler/<name>/account.env`, its YouTube login in
`.reeler/<name>/youtube_token.json`. The old `config.pc.yaml` still works:
`python -m reeler -c config.pc.yaml pc-run`.

### How "trending" works

| What | Source |
|---|---|
| Hashtags | Seed list for your niche (preset, your `trends.yaml`, a Claude starter kit, or words from your niche). With the Instagram Graph API connected and `trends.live_research: true`, it is re-scored every 24 h from live Instagram data (engagement + posts in the last 24 h) and finds new tags that keep showing up on top posts in **your** niche. Spam tags (#fyp, #follow4follow…) are blocked. |
| Audio | Instagram's trending music, searched with vibe words that fit the clip and your niche. A track is not reused within the last 6 posts. |
| Copy | Claude writes hooks, captions and YouTube titles for your niche. Without an API key, built-in templates that work for any topic are used. |

### ☁️ Cloud mode (GitHub Actions, currently switched off)

`.github/workflows/autopost.yml` can post from a Google Drive folder without a PC
(`config.cloud.yaml`, official Instagram Graph API). Its schedule is off because the PC runner does
the posting. To use it: set `preset`/`niche` and the Drive link in `config.cloud.yaml`, add the
secrets `IG_USER_ID`, `IG_ACCESS_TOKEN` (and optionally `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`) in
**Repo → Settings → Secrets and variables → Actions**, and uncomment the `schedule:` lines.

## 1. Install

You need **Python 3.10+** and **ffmpeg**:

- Windows: `winget install Python.Python.3.12` and `winget install Gyan.FFmpeg`
- macOS: `brew install python ffmpeg`
- Linux: `sudo apt install python3-venv ffmpeg`

```bash
git clone <this repo> && cd Instagram-Auto-Reeler
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m reeler init          # creates config.yaml, .env and videos/
```

Copy your clips into `videos/`. Sub-folders are fine. Set `niche:` (or `preset:`) in `config.yaml`.

## 2. Connect Instagram (official API, one-time, about 15 minutes)

The official API is free. It is the safe option: no password sharing and no ban risk.

1. On your phone: **Instagram → Settings → Account type → switch to Professional** (Creator or Business).
2. **Link it to a Facebook Page.** Create an empty Page if you don't have one
   (Instagram → Edit profile → Page → Connect).
3. Go to <https://developers.facebook.com/apps>, then **Create app → Other → Business**.
   Add the products **Facebook Login for Business** and **Instagram**.
4. Open the **Graph API Explorer** (<https://developers.facebook.com/tools/explorer>). Select your app, then
   *Get User Access Token* with these permissions:
   `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`,
   `business_management`.
5. In the Explorer, run `me/accounts?fields=instagram_business_account{id,username}`.
   The `instagram_business_account.id` is your **IG_USER_ID**.
6. Put the token and ID in `.env`, together with `FB_APP_ID` and `FB_APP_SECRET` from
   *App settings → Basic*. Then run:
   ```bash
   python -m reeler refresh-token     # swaps the 1-hour token for a ~60-day one
   ```
   Run `refresh-token` again roughly every 50 days.

> The app can stay in **Development mode**. You are an admin of your own app, so it can post to your own
> account without App Review.

If you set up **Instagram Login** instead of Facebook Login, set `graph.host: graph.instagram.com` in
`config.yaml`. Posting still works, but Meta only offers the trending-audio API to Facebook Login apps.

## 3. Connect Claude (AI captions)

Get an API key at <https://console.anthropic.com> and put it in `.env` as `ANTHROPIC_API_KEY`.
At the default `effort: low`, each clip costs a few cents. If you leave this out, the tool still
works and uses its built-in caption templates.

## 4. Check, preview, go

```bash
python -m reeler check             # verifies ffmpeg, Claude, your IG account, audio and hashtag access
python -m reeler trends --refresh  # see today's hashtag ranking
python -m reeler prepare --limit 3 # analyse and render the next 3 clips, and print their captions
python -m reeler preview 3         # full details for clip 3 (rendered file is in .reeler/clips/00003/reel.mp4)
python -m reeler post --now --dry-run   # full run without uploading
python -m reeler post --now        # post one Reel right now
```

To run it automatically, pick one option:

- **Keep a window open:** `python -m reeler run` (Windows: double-click `scripts\run_forever.bat`).
  This prepares the next clip ahead of time and posts at every slot.
- **Windows Task Scheduler:** `powershell -ExecutionPolicy Bypass -File scripts\install_task_windows.ps1`.
  It runs `reeler post` every 15 minutes, which uploads only when a slot is due.
- **cron (macOS/Linux):** see `scripts/cron_example.txt`.

### Other commands

| Command | What it does |
|---|---|
| `queue` | Clips waiting, recently posted (with links and the song used), and the next slot |
| `set-caption 5 "text\nsecond line #tag"` | Override the caption of one clip |
| `projects` | List the projects made by START_HERE (use any command with `-p <name>`) |
| `skip 5` / `retry 5` | Never post a clip / re-queue a failed one |
| `prepare 5 --reanalyse` | Re-run the AI on a clip |

## 5. Configure (`config.yaml`)

The most useful settings:

```yaml
schedule:
  post_times: ["11:00", "15:00", "19:00", "22:00"]   # your audience's peak hours
  timezone: Asia/Kolkata
video:
  fit: blur          # blur | crop (fill screen, cuts edges) | pad
  hook_text: true
  watermark: "@yourhandle"
niche: "football skills"   # or preset: football
posts_per_day: 5
platforms: [instagram, youtube]
caption:
  signature: "Follow @yourhandle for daily clips"
audio:
  mode: trending     # or off = keep the clip's own sound
  audio_volume: 35   # song
  video_volume: 70   # the clip's own sound
order: random
```

Hashtags for your niche come from (first match wins): your own `trends.yaml` in the project's
work folder (same format as `reeler/data/gta6_tags.yaml`), `hashtags:` in the config or preset,
a starter list Claude writes once for your niche (cached in `niche_kit.json`), or tags made from
the words of your niche.

## Good to know

- **Posting frequency:** the API allows up to 100 posts per day, but 2–4 Reels a day grows an account
  better than spamming. `max_posts_per_day` and `min_gap_minutes` enforce this.
- **Trending audio:** Meta only exposes tracks it has licensed for third-party apps, so the very
  hottest in-app sounds may not be in the list. The track plays from its start. If Instagram rejects a
  track, the Reel is re-uploaded with the game audio, so a post never fails because of the song.
- **Unofficial uploader:** `uploader: instagrapi` (`pip install instagrapi`, then put your username and
  password in `.env`) works without a Meta app. It goes against Instagram's terms and can trigger
  checkpoints or bans, and it can't attach trending audio. Use it at your own risk.
- **Content:** only post videos you have the rights to (your own, or allowed by the owner).
- All state lives in `.reeler/`. Each project has its own folder there.

## Development

```bash
pip install -e ".[dev]" && pytest -q
```

The tests cover the scheduler and pacing, presets and niche handling, hashtag scoring and selection,
live trend discovery (mocked), the Graph API upload and its audio fallback, the YouTube upload
(mocked API), per-platform history (each clip once per platform), migration of old databases and
the old single GTA setup, the setup questions, the Claude request shape, ffmpeg rendering, and a
full dry-run post.

### Built on

The Instagram Graph API [content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/)
and [resumable upload](https://developers.facebook.com/docs/instagram-platform/content-publishing/resumable-uploads/)
flow, the [Instagram Audio API](https://developers.facebook.com/docs/instagram-platform/content-publishing/audio-api/),
the Claude API (vision + structured outputs), and ffmpeg. The design borrows ideas from open-source
projects such as
[Instagram-Reels-Scraper-Auto-Poster](https://github.com/avnsh1111/Instagram-Reels-Scraper-Auto-Poster)
(auto-hashtags) and [instapost](https://pypi.org/project/instapost/) (Graph API Reels).
