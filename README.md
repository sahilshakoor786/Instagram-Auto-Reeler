# Instagram Auto Reeler 🎮→📱

Drop your GTA 6 gameplay clips (~25 s each) into a folder. This tool turns each one into a
Reel and posts it to your Instagram account on a schedule. For every clip it:

1. **Looks at the clip.** It sends keyframes to Claude, which works out what happens
   (police chase, stunt, Vice City sunset…), how intense it is, and which frame makes the best cover.
2. **Makes it Reels-ready.** It converts the clip to 1080×1920 9:16 H.264/AAC, keeps the full gameplay
   on a blurred background, adds a bold on-screen hook for the first ~2.5 s ("COPS HAD NO CHANCE")
   and an optional watermark.
3. **Writes the caption.** You get a punchy line, a question that invites comments, and your signature.
4. **Picks trending hashtags for that clip.** It mixes one broad anchor (#gta6), a tag for the clip's
   category, the AI's pick, a tag that is trending right now, and a niche tag. Instagram now allows
   5 hashtags, and the tool stops the same set repeating across posts.
5. **Attaches a trending song that fits the vibe** (phonk for chases, synthwave for night drives, …).
   This uses Instagram's official Audio API. If no track can be attached, it posts with the game audio.
6. **Uploads it** through the **official Instagram Graph API**. This is a resumable upload straight from
   your PC, with no video hosting needed. It posts at the times you set, moves posted clips to
   `videos/posted/`, and never posts the same clip twice, even if you rename it.

```
videos/*.mp4 ─► scan ─► Claude vision ─► ffmpeg 9:16 render + hook ─► caption + trending tags
                                                                     └► trending audio pick
                                                     scheduler ─► Instagram Graph API (Reels)
```

## How "trending" works

| What | Source |
|---|---|
| Hashtags | A built-in GTA 6 tag list (`reeler/data/gta6_tags.yaml`), re-scored every 24 h from **live Instagram data**. The tool reads the top and recent posts for #gta6, #vicecity and similar tags, measures engagement and how many posts appeared in the last 24 h, and finds **new tags that keep showing up on top-performing GTA 6 posts** (e.g. a new `#gta6trailer3`). Spam tags (#fyp, #follow4follow…) are blocked. Run `python -m reeler trends --refresh` to see the ranking. |
| Audio | Instagram's Audio API trending list, searched with vibe words Claude picks for each clip. A track is not reused within the last 6 posts. |
| Copy | Claude writes hooks and captions in current Reels style. Without an API key, built-in templates based on the clip's motion and loudness are used instead. |

Instagram allows 30 *unique* hashtag lookups per 7 days. The tool caches hashtag IDs and keeps a
weekly budget (25 by default), so it never goes over that limit.

---

## 🖥️ Easiest: run it on your Windows PC (no Facebook needed)

1. Download the project: on GitHub, open **Code → Download ZIP**, then unzip it (for example to `Documents\Instagram-Auto-Reeler`).
2. Double-click **`START_HERE.bat`**.
   - The first time, it installs Python and ffmpeg. When it says so, close the window and double-click the file again.
3. Type your Instagram username and password when asked. They are saved only on your PC, in `.env`.
   If Instagram sends you a security code, type it into the window.
4. Choose how many Reels to post right away. After that it keeps posting **5 a day** from your Google Drive
   folder, with a trending Instagram song, a hook text overlay, a caption and 5 hashtags, until all clips
   are posted. Leave the window open; if you close it, double-click `START_HERE.bat` to pick up where it stopped.

To change the posting times or how many Reels go out per day, edit `config.pc.yaml`.
This mode logs in like the Instagram app does (unofficial). Keep the pace human.

## ☁️ Cloud mode: post from Google Drive with no PC needed

GitHub Actions runs `.github/workflows/autopost.yml` 4 times a day. Each run:

1. Picks the next clip from your Google Drive folder that hasn't been posted yet.
2. Downloads only that clip.
3. Processes it exactly like the desktop version (AI caption, hook text, trending hashtags and audio).
4. Posts it.
5. Commits the posting history (`state/state.db`) back to the repo, so no clip is ever posted twice.

Setup:
1. Share the Drive folder as **Anyone with the link → Viewer**, and set its link in `config.cloud.yaml`.
   New clips you add to the folder are picked up automatically.
2. Merge this branch into `main`. GitHub only runs scheduled workflows from the default branch.
3. In GitHub, go to **Repo → Settings → Secrets and variables → Actions → New repository secret** and add:
   `IG_USER_ID`, `IG_ACCESS_TOKEN` (see step 2 below for how to get them), `ANTHROPIC_API_KEY`
   (optional), and `GOOGLE_API_KEY` (optional; makes listing new Drive uploads more reliable).
4. Go to **Actions → Auto-post GTA 6 Reel → Run workflow**, keep "Dry run" ticked, and run it. When it
   finishes, download the `reel-preview` artifact and read the caption in the log.
5. If the preview looks good, you're done: the schedule takes over. To post one right away, run the
   workflow again with "Dry run" unticked.

To change the posting times, edit the `cron` lines in the workflow. They are in UTC: India time minus
5:30. Each run uses about 3–5 of the 2,000 free GitHub Actions minutes a private repo gets per month.

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

Copy your clips into `videos/`. Sub-folders are fine.

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
| `set-caption 5 "text\nsecond line #gta6"` | Override the caption of one clip |
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
caption:
  signature: "Follow @yourhandle for daily GTA 6 clips 🎮"
audio:
  mode: trending     # or off = keep the game sound
  audio_volume: 35   # song
  video_volume: 70   # gameplay
order: random
```

Add your own hashtags by copying `reeler/data/gta6_tags.yaml` to `trends.yaml` in the project root
and editing it.

## Good to know

- **Posting frequency:** the API allows up to 100 posts per day, but 2–4 Reels a day grows an account
  better than spamming. `max_posts_per_day` and `min_gap_minutes` enforce this.
- **Trending audio:** Meta only exposes tracks it has licensed for third-party apps, so the very
  hottest in-app sounds may not be in the list. The track plays from its start. If Instagram rejects a
  track, the Reel is re-uploaded with the game audio, so a post never fails because of the song.
- **Unofficial uploader:** `uploader: instagrapi` (`pip install instagrapi`, then put your username and
  password in `.env`) works without a Meta app. It goes against Instagram's terms and can trigger
  checkpoints or bans, and it can't attach trending audio. Use it at your own risk.
- **Content:** Rockstar generally allows sharing gameplay footage. Don't post leaked or pre-release
  material you don't have the rights to.
- All state lives in `.reeler/state.db`. Delete the folder to start fresh.

## Development

```bash
pip install -e ".[dev]" && pytest -q
```

The tests cover the scheduler, hashtag scoring and selection, live trend discovery (mocked), the
Graph API resumable upload and its audio fallback (mocked HTTP), the Claude request shape (mocked
HTTP), ffmpeg rendering, and a full dry-run post.

### Built on

The Instagram Graph API [content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/)
and [resumable upload](https://developers.facebook.com/docs/instagram-platform/content-publishing/resumable-uploads/)
flow, the [Instagram Audio API](https://developers.facebook.com/docs/instagram-platform/content-publishing/audio-api/),
the Claude API (vision + structured outputs), and ffmpeg. The design borrows ideas from open-source
projects such as
[Instagram-Reels-Scraper-Auto-Poster](https://github.com/avnsh1111/Instagram-Reels-Scraper-Auto-Poster)
(auto-hashtags) and [instapost](https://pypi.org/project/instapost/) (Graph API Reels).
