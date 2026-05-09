# job-scanner

Scans career pages every hour, sends a Telegram message when a new job appears.
Runs entirely on free GitHub Actions infrastructure - no server to maintain.

## How it works

1. GitHub Actions runs `scanner.py` once per hour.
2. The scanner fetches each URL in `sites.yaml`, picks out job links using a CSS selector, and compares them to `seen.json`.
3. Anything new gets posted to your Telegram chat.
4. Updated `seen.json` is committed back to the repo so state survives across runs.

When you add a new site, the **first scan records a baseline** and only sends one "now tracking" message. After that, only genuinely new postings trigger notifications.

## One-time setup

You need ~5 minutes and these three things: a GitHub repo, a Telegram bot, your Telegram chat ID.

### 1. Get this repo onto your GitHub

Create an empty repo at https://github.com/new (call it `job-scanner`, public is fine - it has no secrets in it). Then locally:

```bash
cd ~/job-scanner
git remote add origin https://github.com/YOUR_USERNAME/job-scanner.git
git branch -M main
git push -u origin main
```

### 2. Create a Telegram bot

1. In Telegram, open a chat with [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts. Pick any name and a unique username ending in `bot`.
3. BotFather replies with a token like `123456789:AAEx...`. **Copy it.**

### 3. Get your chat ID

1. Open a chat with your new bot and send it any message (e.g. "hi").
2. In your browser, visit:
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Find `"chat":{"id":123456789,...}` in the JSON. **That number is your chat ID.**

### 4. Add the secrets to your repo

In GitHub: **Settings -> Secrets and variables -> Actions -> New repository secret**. Add two:

- `TELEGRAM_BOT_TOKEN` - the token from step 2
- `TELEGRAM_CHAT_ID` - the number from step 3

### 5. Test it

In GitHub: **Actions** tab -> **Scan jobs** workflow -> **Run workflow**.

With an empty `sites.yaml` it will print "No sites configured." That's fine - it proves the workflow runs.

## Adding sites to track

Edit [sites.yaml](sites.yaml) and commit. The next hourly run will pick it up (or hit "Run workflow" to trigger immediately).

```yaml
sites:
  - name: Anthropic
    url: https://www.anthropic.com/jobs
    selector: "a[href*='/jobs/']"
```

The `sites.yaml` file has detailed comments on how to find a CSS selector for any site, plus examples for common ATS platforms (Greenhouse, Lever, Ashby).

You can edit `sites.yaml` directly in the GitHub web UI - click the file, then the pencil icon. No need to clone the repo just to add a site.

## Removing or renaming a site

- **Remove**: delete it from `sites.yaml`. Its history stays in `seen.json` but it stops being scanned.
- **Rename**: changing `name` is treated as a brand new site (you'll get a new "now tracking" baseline). To preserve history, manually rename the key in `seen.json` too.

## Troubleshooting

**"selector matched 0 jobs"** - the selector is wrong, or the page renders jobs with JavaScript. Open the URL in a browser, view source (`Cmd+U`), and search for a job title. If it's not in the raw HTML, you need a different URL - look for the underlying ATS link (Greenhouse / Lever / Ashby / Workday).

**No notifications arriving** - check the Actions tab for run logs. Most failures show up there. Make sure you sent the bot a message first; otherwise `getUpdates` is empty.

**Schedule seems delayed** - GitHub Actions cron can run 15-60 min late under platform load. This is normal and unavoidable on the free tier.

## Sharing this with someone else

Send them the repo URL. They click **Fork**, follow steps 2-4 above with their own Telegram bot, and edit their fork's `sites.yaml`. Each person runs independently with their own notifications.
