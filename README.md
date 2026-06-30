# soccer-vemon-plus

`soccer-vemon-plus` is a small hybrid Flashscore scraper.

It is no longer a desktop GUI app. The repo is now centered on three command-line entrypoints:

- `src/headless_all_odds_cli.py`
  Open Flashscore, switch to the Odds view, move by day offset, and save daily `all_odds` snapshot JSON.
- `src/headless_cli.py`
  Scrape one or more match URLs and save raw match JSON.
- `src/headless_league_cli.py`
  Scrape a league `results` page plus its `fixtures` page, expand `Show more matches`, and save the legacy `match_index.json` format.

## What this app does

For daily odds snapshots, it can extract:

- all visible matches from the Flashscore `Odds` view
- main `1 / X / 2` odds
- match URLs
- home and away teams
- per-match status
- competition and country context
- one merged `all_odds/YYYY-MM-DD.json` file per selected day

For matches, it can extract:

- breadcrumb
- match details
- H2H sections
- standings overall
- standings home
- standings away
- `last_matches`
- `h2h_standings`

For leagues, it can extract:

- league header
- all loaded results
- all loaded fixtures
- round grouping
- `match_index.json`
- optional Excel and CSV exports

## Why the app is hybrid

Flashscore does not reliably expose all needed content in plain HTML to a simple `requests` call.

So the app uses a hybrid boundary:

- Selenium:
  opens the page, accepts cookies, waits for rendered content, and captures final page HTML
- BeautifulSoup:
  parses that HTML into structured data

This keeps the browser usage short-lived and focused, while moving the real extraction logic into normal parsers.

## How the app works

### Match flow

1. You pass one or more match URLs to `src/headless_cli.py`, or you point it at a saved `all_odds` day file.
2. The CLI loads `.env`, loads `src/assets/config.json`, and sets an output root.
3. If an `all_odds` source is used, the CLI loads match URLs from:
   - `data/raw/all_odds/YYYY-MM-DD.json`
   - or a day offset
   - or an explicit json path
4. `MatchPipeline` builds direct routes from each match URL:
   - base match page
   - `h2h/overall`
   - `standings/standings/overall`
   - `standings/standings/home`
   - `standings/standings/away`
5. If `--rendered` is enabled, Selenium opens those pages, waits for real content, then returns `page_source`.
   For multi-match runs, one browser session is reused across the batch.
6. A capped cross-match page cache is kept during the batch unless you disable it.
   This reduces repeated fetches of the same historical H2H and standings pages without keeping the full day in memory.
7. BeautifulSoup parsers extract:
   - match hero data
   - H2H rows
   - standings rows
8. The pipeline also fetches supplemental historical pages to build:
   - `last_matches`
   - `h2h_standings`
9. The final payload is saved as raw JSON, and optional HTML snapshots can also be saved.
10. When the source was `all_odds`, the day file also acts as the batch checkpoint:
   - per-match attempt count
   - last status
   - last error
   - batch progress in `details_batch`
11. When the source was `all_odds` and raw JSON was saved successfully, that entry is marked:
   - `details_fetched = true`
   - `details_fetched_at = ...`

### Daily odds flow

1. You pass one or more day offsets to `src/headless_all_odds_cli.py`.
2. The CLI loads `.env`, loads `src/assets/config.json`, and sets an output root.
3. `SeleniumOddsPageFetcher` opens Flashscore home, accepts cookies, and switches to the `Odds` tab.
4. It moves the Flashscore day picker to the requested offset:
   - `0` = today
   - `1` = tomorrow
   - up to `5`
5. It expands collapsed league sections so hidden matches are included in the final HTML.
6. BeautifulSoup parses the rendered odds page into match rows with:
   - time or status
   - URL
   - home team
   - away team
   - `1 / X / 2` odds
   - visible full-time scores when the page shows them
   - competition
   - country
7. The pipeline merges that snapshot into:
   - `data/raw/all_odds/YYYY-MM-DD.json`
8. When scores are visible, they are merged into the same day file under `scores.ft_home` and `scores.ft_away` without deleting older score keys already stored for that match.
9. The same run updates:
   - `data/processed/all_odds_scores_state.json`
10. Optional HTML snapshots can also be saved for parser inspection.

### League flow

1. You pass a league URL to `src/headless_league_cli.py`.
2. The CLI normalizes it to a `results` URL and derives the related `fixtures` URL.
3. Selenium opens both pages.
4. On each page it:
   - waits for league content
   - scrolls
   - clicks `Show more matches` until no more rows load
5. BeautifulSoup parses the final rendered HTML into:
   - header metadata
   - round rows
   - match rows
6. The league pipeline writes:
   - `match_index.json`
   - optional Excel and CSV exports
   - optional HTML snapshots

## Main files

- `src/headless_all_odds_cli.py`
  Daily `all_odds` collection CLI.
- `src/headless_cli.py`
  Match scraping CLI.
- `src/headless_league_cli.py`
  League scraping CLI.
- `src/headless/pipeline/all_odds_pipeline.py`
  Daily odds orchestration and merge/save flow.
- `src/headless/pipeline/match_pipeline.py`
  Match orchestration and payload assembly.
- `src/headless/pipeline/league_pipeline.py`
  League orchestration and export flow.
- `src/headless/odds_fetch.py`
  Short-lived Selenium fetcher for the Flashscore Odds view and day picker.
- `src/headless/selenium_fetch.py`
  Short-lived rendered fetcher for match pages.
- `src/headless/league_fetch.py`
  League fetcher with `Show more matches` expansion.
- `src/headless/parsers/`
  BeautifulSoup parsers for all_odds, match, H2H, standings, and league pages.
- `src/utils/file_saver.py`
  Raw match JSON saver.
- `src/utils/all_odds_store.py`
  Daily odds merge/save helpers.
- `src/utils/export.py`
  League `match_index.json`, Excel, and CSV export helpers.

## Repo shape

- `src/assets`
  Runtime config and local assets.
- `src/core`
  Minimal config loading only.
- `src/formatters`
  Existing formatter logic kept for downstream compatibility.
- `src/headless`
  Active scraping code.
- `src/processors`
  Formatter runner.
- `src/tests`
  Small parser and route tests.
- `src/utils`
  Save/export/path helpers.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

### Daily all_odds snapshots

Today:

```powershell
.\.venv\Scripts\python.exe src\headless_all_odds_cli.py --browser chrome --day 0

python src/headless_all_odds_cli.py --browser chrome --day 0

python src/headless_cli.py --rendered --browser chrome --all-odds-date 2026-06-20

```

Tomorrow:

```powershell
.\.venv\Scripts\python.exe src\headless_all_odds_cli.py --browser chrome --day 1

python src/headless_all_odds_cli.py --browser chrome --day 1

python src/headless_cli.py --rendered --browser chrome --all-odds-date 2026-06-21

```


Multiple days in one browser session:

```powershell
.\.venv\Scripts\python.exe src\headless_all_odds_cli.py --browser chrome --day 0 --day 1 --day 2
```

Automatic recheck of open score-state days:

```powershell
.\.venv\Scripts\python.exe src\headless_all_odds_cli.py --browser chrome --recheck-open-days
```

Useful flags:

- `--day`
  Repeat for multiple offsets. Allowed: `-7..5`.
- `--recheck-open-days`
  Load day offsets from `data/processed/all_odds_scores_state.json` for dates still marked `pending` or `incomplete`.
- `--recheck-limit`
  Limit how many open score-state dates are loaded.
- `--include-failed-days`
  Include `failed_dates` from the score-state file when using `--recheck-open-days`.
- `--browser`
  Override the configured browser for this run.
- `--no-save-html`
  Skip saving the rendered odds HTML snapshot.
- `--no-save-json`
  Skip saving the merged `all_odds` JSON.
- `--print-json`
  Print pipeline output to stdout.
- `--base-dir`
  Override the output root for the run.

Rerunning the same day refreshes both the daily match list and the score progress for that date. `--recheck-open-days` uses the saved score-state file to revisit only days that still need another pass.

### Match scraping

Single match:

```powershell
.\.venv\Scripts\python.exe src\headless_cli.py --rendered --url "https://www.flashscore.com/match/football/.../?mid=XXXXXXXX"
```

Multiple matches:

```powershell
.\.venv\Scripts\python.exe src\headless_cli.py --rendered --url "URL_1" --url "URL_2"
```

From file:

```powershell
.\.venv\Scripts\python.exe src\headless_cli.py --rendered --urls-file urls.txt
```

From a saved daily `all_odds` file:

```powershell
.\.venv\Scripts\python.exe src\headless_cli.py --rendered --browser chrome --all-odds-date "2026-03-24"
```

From a day offset:

```powershell
.\.venv\Scripts\python.exe src\headless_cli.py --rendered --browser chrome --all-odds-day 0
```

Useful flags:

- `--rendered`
  Recommended for Flashscore match pages.
- `--all-odds-date`
  Load URLs from `data/raw/all_odds/YYYY-MM-DD.json`.
- `--all-odds-day`
  Load URLs from the saved `all_odds` file for day offset `0..5`.
- `--all-odds-file`
  Load URLs from an explicit `all_odds` json path.
- `--include-fetched`
  Include entries already marked `details_fetched=true`.
- `--only-failed`
  When using an `all_odds` source, retry only entries whose last status is `failed`.
- `--max-attempts`
  When using an `all_odds` source, skip unfetched entries that already reached this attempt count. Use `0` for unlimited.
- `--limit`
  Limit the final number of URLs processed.
- `--cache-size`
  Maximum number of cached page HTML responses to keep across the batch. Use `0` to disable cache reuse.
- `--clear-cache-per-match`
  Reset the page cache after every match instead of reusing it across the batch.
- `--delay-between-matches`
  Sleep this many seconds after each successful match.
- `--delay-after-failure`
  Sleep this many seconds after each failed match.
- `--browser`
  Override the configured browser for rendered mode.
- `--no-save-html`
  Skip HTML snapshots.
- `--no-save-json`
  Skip raw JSON writes.
- `--print-json`
  Print pipeline output to stdout.
- `--base-dir`
  Override the output root for the run.

### League scraping

```powershell
.\.venv\Scripts\python.exe src\headless_league_cli.py --url "https://www.flashscore.com/football/england/premier-league/results/"
```

With sheet export:

```powershell
.\.venv\Scripts\python.exe src\headless_league_cli.py --export-sheets --url "https://www.flashscore.com/football/england/premier-league/results/"
```

Useful flags:

- `--no-save-html`
  Skip saving rendered `results.html` and `fixtures.html`.
- `--no-save-json`
  Skip `match_index.json`.
- `--export-sheets`
  Also build Excel and CSV from the saved league JSON.
- `--print-json`
  Print pipeline output to stdout.
- `--base-dir`
  Override the output root for the run.

## Output layout

By default, both CLIs write into a repo-local isolated folder:

- `_headless_output/`

This avoids mixing test runs with older live data folders.

Typical layout:

```text
_headless_output/
  data/
    raw/
      all_odds/
        2026-03-18.json
      2026-03-17/
        2eDEHMBO.json
    processed/
      all_odds_scores_state.json
      headless_all_odds_html/
        2026-03-18/
          odds.html
      headless_html/
      headless_league_html/
  leaguetables/
    england/
      premier-league/
        2025-2026/
          match_index.json
          2025-2026-premierleague.xlsx
          2025-2026-premierleague.csv
```

You can override the root:

```powershell
.\.venv\Scripts\python.exe src\headless_cli.py --base-dir "C:\temp\soccer-test" --rendered --url "MATCH_URL"
```

```powershell
.\.venv\Scripts\python.exe src\headless_league_cli.py --base-dir "C:\temp\soccer-test" --url "LEAGUE_URL"
```

## Current status

Working locally:

- daily `all_odds` snapshots for day offsets `0..5`
- daily score refresh merged into the same `all_odds` day files
- processed `all_odds_scores_state.json` summaries
- match details
- H2H
- standings overall, home, and away
- `last_matches`
- `h2h_standings`
- league results
- league fixtures
- `Show more matches` expansion

Known constraint:

- some historical H2H matches simply do not expose standings rows on Flashscore, so those entries fall back to `has_table: false`


## VM deployment (Hetzner)

The daemon runs on a Hetzner CX23 VM (`soccer-venom`, Helsinki).

- **IP:** 77.42.70.63
- **Specs:** 2 vCPU (x86 AMD), 4 GB RAM, 40 GB SSD, Ubuntu 22.04
- **Cost:** ~$7.09/month (server + IPv4)
- **SSH key:** `C:\Users\Buyen\.ssh\oracle_vm`

> **Always use `.venv/bin/python`** on the VM — never bare `python`. `nohup` does not carry an activated venv.

### 1. SSH into the VM

```powershell
ssh -i C:\Users\Buyen\.ssh\oracle_vm root@77.42.70.63
```

### 2. Pull latest code

```bash
cd ~/soccer_vemon_plus
git pull
```

### 3. Start the daemon

Without alerts:

```bash
mkdir -p logs
nohup .venv/bin/python src/headless_daemon.py --browser chrome >> logs/daemon.log 2>&1 &
echo "Daemon PID: $!"
```

With ntfy push alerts (recommended — see ntfy setup below):

```bash
mkdir -p logs
nohup .venv/bin/python src/headless_daemon.py --browser chrome \
  --ntfy-url http://localhost/leagueflux-alerts \
  --idle-sleep-mins 15 >> logs/daemon.log 2>&1 &
echo "Daemon PID: $!"
```

### 4. Watch the logs

```bash
tail -f logs/daemon.log
```

Or check the last 100 lines:

```bash
tail -100 logs/daemon.log
```

### 5. Check if daemon is running

```bash
ps aux | grep headless_daemon
```

### 6. Stop the daemon

```bash
kill $(pgrep -f headless_daemon)
```

### 7. Restart the daemon (stop + start)

```bash
kill $(pgrep -f headless_daemon)

nohup .venv/bin/python src/headless_daemon.py --browser chrome \
  --ntfy-url http://localhost/leagueflux-alerts \
  --idle-sleep-mins 15 >> logs/daemon.log 2>&1 &
echo "Daemon PID: $!"
tail -f logs/daemon.log
```

### 8. Manual one-off runs on the VM

Fetch today's odds:

```bash
.venv/bin/python src/headless_all_odds_cli.py --day 0 --browser chrome
```

Fetch match details for tomorrow (limit 3):

```bash
.venv/bin/python src/headless_cli.py --all-odds-day 1 --limit 3 --rendered --browser chrome
```

Fetch half-time scores for a specific date (dry-run first):

```bash
.venv/bin/python src/headless_score_cli.py --date 2026-05-08 --limit 10 --dry-run
.venv/bin/python src/headless_score_cli.py --date 2026-05-08 --limit 10 --batch-size 5 --browser chrome
```

### 9. Reset and re-fetch match details for specific dates

Use this when Flashscore does a site update and old fetched data needs to be refreshed.
Only resets matches without a final score (safe — won't touch completed matches).

Check status first:

```bash
.venv/bin/python -c "
import json
from pathlib import Path
for date in ['2026-06-05', '2026-06-06', '2026-06-07']:
    path = Path(f'_headless_output/data/raw/all_odds/{date}.json')
    if not path.exists():
        print(f'{date}: NOT FOUND')
        continue
    data = json.loads(path.read_text())
    matches = data.get('matches') or {}
    fetched = sum(1 for m in matches.values() if m.get('details_fetched'))
    has_ft = sum(1 for m in matches.values() if (m.get('scores') or {}).get('ft_home') is not None)
    print(f'{date}: {len(matches)} matches | fetched={fetched} | complete={has_ft}')
"
```

Reset non-complete matches (replace dates as needed):

```bash
.venv/bin/python -c "
import json
from pathlib import Path
for date in ['2026-06-05', '2026-06-06', '2026-06-07']:
    path = Path(f'_headless_output/data/raw/all_odds/{date}.json')
    if not path.exists():
        print(f'{date}: NOT FOUND')
        continue
    data = json.loads(path.read_text())
    reset = 0
    for m in data['matches'].values():
        scores = m.get('scores') or {}
        if scores.get('ft_home') is None:
            m['details_fetched'] = False
            m['details_last_status'] = 'pending'
            m['details_attempt_count'] = 0
            reset += 1
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f'{date}: reset {reset} matches')
"
```

Then restart the daemon — it will pick up all reset matches automatically:

```bash
kill $(pgrep -f headless_daemon)
nohup .venv/bin/python src/headless_daemon.py --browser chrome \
  --ntfy-url http://localhost/leagueflux-alerts \
  --idle-sleep-mins 15 >> logs/daemon.log 2>&1 &
echo "Daemon PID: $!"
tail -f logs/daemon.log
```

---

## Push notifications (ntfy)

The daemon sends match alerts to your phone ~30 minutes before kick-off. Each alert includes the signal combo (e.g. `O|AR|H`) and the FH-X2/1X vault rate pulled from Supabase.

### How it works

- Daemon Phase 4 scans today's all_odds for matches kicking off in the next 20–45 minutes
- Looks up the signal code and vault stats from `market_matches` + `signal_vault_master` in Supabase
- Fires a push notification via ntfy running on the same VM
- Tracks sent alerts in `daemon_state.json` — never fires twice for the same match

### Install ntfy on the VM (one-time)

```bash
apt install -y docker.io
systemctl enable --now docker

docker run -d --name ntfy --restart unless-stopped -p 80:80 \
  -v /opt/ntfy:/var/cache/ntfy \
  binwiederhier/ntfy serve --cache-file /var/cache/ntfy/cache.db

# Verify
docker ps
curl -s http://localhost/leagueflux-alerts/json?poll=1
```

### Subscribe on your phone

1. Install the **ntfy** app (Android: Play Store · iOS: App Store)
2. Tap `+` → enter `http://77.42.70.63/leagueflux-alerts` → Subscribe

### Daemon alert flags

| Flag | Default | Description |
|------|---------|-------------|
| `--ntfy-url` | _(none)_ | Full ntfy topic URL. Also reads `NTFY_URL` env var. Omit to disable alerts. |
| `--ntfy-token` | _(none)_ | Bearer token if ntfy access control is enabled. Also reads `NTFY_TOKEN` env var. |
| `--alert-lead-mins` | `30` | How many minutes before kick-off to fire the alert. |
| `--idle-sleep-mins` | `30` | Recommend `15` when alerts are enabled so the daemon wakes before alert windows. |

### Manage the ntfy container

```bash
# Check status
docker ps

# View ntfy logs
docker logs ntfy

# Restart ntfy
docker restart ntfy

# Stop ntfy
docker stop ntfy
```

---

## Hetzner VM — initial server setup (one-time)

Use this if you need to rebuild the server from scratch.

### 1. Create server on Hetzner

1. Go to [console.hetzner.cloud](https://console.hetzner.cloud)
2. Open project `soccer` → **Add Server**
3. Settings:
   - Location: **Helsinki**
   - Image: **Ubuntu 22.04**
   - Type: Shared AMD → **CX23** (2 vCPU, 4 GB RAM — ~$6.49/month)
   - SSH key: add your public key (`C:\Users\Buyen\.ssh\oracle_vm.pub`)
4. Click **Create & Buy Now**

### 2. Generate SSH key (if needed)

```powershell
ssh-keygen -t rsa -b 2048 -f C:\Users\Buyen\.ssh\oracle_vm -N '""'
# Public key to paste into Hetzner:
Get-Content C:\Users\Buyen\.ssh\oracle_vm.pub
```

### 3. Install system dependencies

```bash
apt update && apt upgrade -y
apt install -y python3 python3-pip python3-venv git curl unzip wget gnupg ca-certificates docker.io
systemctl enable --now docker
```

### 4. Install Google Chrome

```bash
wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list
apt update && apt install -y google-chrome-stable
google-chrome --version
```

### 5. Clone repo and set up Python environment

```bash
cd ~
git clone https://github.com/YOUR_GITHUB_USERNAME/soccer_vemon_plus.git
cd soccer_vemon_plus
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 6. Configure environment

```bash
cat > src/assets/.env << 'EOF'
SUPABASE_URL_LF=your_supabase_url_here
SUPABASE_SERVICE_KEY_LF=your_supabase_service_key_here
EOF
```

Supabase credentials: project → **Settings → API** → Project URL + service_role key.

### 7. Test before starting daemon

```bash
.venv/bin/python src/headless_all_odds_cli.py --browser chrome --day 0 --no-save-html
```

Should print `Supabase client initialized` and `X matches` parsed. If that works, start the daemon.

