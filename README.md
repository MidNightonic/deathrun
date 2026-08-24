# Deathrun Guild Tracker

Tracks Shop Titans guild member investment over time and flags members who've gone
quiet (no investment change in 7+ days). Pulls from the TitansDB public guild API,
stores history as JSON, and renders it as a static dashboard via GitHub Pages —
no server needed.

## How it works

- `scripts/fetch_snapshot.py` — hits `https://www.titansdb.com/api/my_guild`
  (your API key determines which guild comes back — no guild ID needed),
  appends each member's current investment total into `data/members.json`
  under today's date, and updates the current member roster in
  `data/guild-members.json`.
- `scripts/build_report.py` — reads that JSON and writes a Markdown report per
  guild (`reports/deathrun.md`) plus a summary at `reports/SUMMARY.md`. A member
  is flagged inactive if their investment total hasn't changed in
  `--inactive-threshold` days (default 7).
- `index.html` — a static dashboard that reads `data/members.json` and
  `data/guild-members.json` directly in the browser (via `fetch`), sorts by
  investment, and highlights idle members. No backend, works on GitHub Pages.
- `.github/workflows/daily-snapshot.yml` — runs the fetch + report daily via
  GitHub Actions and commits the updated JSON/Markdown back to the repo.

## Setup

1. **Get a TitansDB API key.** TitansDB requires an API key for guild data
   requests — check their site/Discord for how members request one. Without a
   key, `fetch_snapshot.py` will fail with `missing_api_key`.

2. **`config/guilds.json` is already set up** for a single guild called
   Deathrun with no guild ID needed — `my_guild` resolves your guild from the
   API key itself. (If you ever add a second guild here, note that `my_guild`
   only returns *your* key's guild — you'd need a separate key per guild, or
   switch back to the `guild/{guild_id}` endpoint for guilds you don't own.)

4. **Push this to a new GitHub repo.**

5. **Add the API key as a repo secret:**
   Repo → Settings → Secrets and variables → Actions → New repository secret
   → name it `TITANSDB_API_KEY`, paste your key.

6. **Enable GitHub Pages:**
   Repo → Settings → Pages → Source: `Deploy from a branch` → Branch: `main` /
   root. Your dashboard will be live at `https://<you>.github.io/<repo>/`.

7. **Test the workflow manually:**
   Repo → Actions → "Daily Guild Snapshot" → Run workflow. Check that
   `data/members.json` and `reports/deathrun.md` get committed.

The cron in `daily-snapshot.yml` runs at 09:00 UTC by default — edit the cron
expression to whatever local time works for your guild.

## Running locally

```bash
cp .env.example .env        # then edit .env with your real API key
cd scripts
python3 fetch_snapshot.py
python3 build_report.py
cd ..
python3 -m http.server 8000 # then open http://localhost:8000
```

## Adjusting the inactivity threshold

Default is 7 days. Change it in two places if you want a different window:
- `scripts/build_report.py` → `INACTIVE_THRESHOLD_DAYS`
- `index.html` → `INACTIVE_THRESHOLD_DAYS` (top of the `<script>` block)
