# Scheduling the YouTube collection

This guide explains how to run the YouTube collector automatically. **Nothing here is installed by the repo.** A team member sets it up by hand on the one machine that holds the `.env` file.

## What the scheduled jobs do

There are two passes:

| Pass | Command | Cost per product | Measures |
|------|---------|------------------|----------|
| main | `python3 src/collect_youtube.py --all --pass main` | 7 units, 1 search call | cumulative views, likes and comments of the top-50 relevance-ranked videos, plus top comments |
| recent | `python3 src/collect_youtube.py --all --pass recent` | 1 unit, 1 search call | uploads in the last 7 days (`videos_published_7d`, `videos_7d_on_target`) |

`python3 src/collect_youtube.py --plan` prints the quota for each schedule. For 60 products:
- **Recent daily:** 60 search calls/day, which fits.
- **Main weekly:** 60 search calls on its day, which fits on its own.
- **Both on the same day:** 120 search calls, which **exceeds the 90-call budget**. At most 45 products/day can get both passes.

**Recommended schedule:**
- **Daily at 14:00:** the recent pass.
- **Mondays and Tuesdays at 14:15:** the main pass. On Monday it stops cleanly at the cap after about 30 products (60 + 30 = 90 search calls). On Tuesday it continues with the other 30, stalest first.
- **Result:** every product gets a daily recent count and a weekly main snapshot.

Each job:
- It collects every product in the active registry (`ACTIVE_REGISTRY` in `src/config.py`, currently v3), using each product's active `youtube_query`. A future registry version takes effect on the next run without any schedule change. Products never collected come first, then the stalest; products already collected today go last.
- Before each product it checks both the unit budget (default 9,000 of the API's 10,000 units/day) and the `search.list` call budget (default 90 of the API's 100 calls/day). It stops cleanly if the next product would not fit, and logs `aborted_budget`. The next run continues with the products that were not collected.
- Running it twice on the same day costs no quota. Products with raw files for today are served from the cache and logged `skipped_cached`.
- The pilot measured about 6 seconds per product for the main pass and under 1 second for the recent pass. The search-call cap (90/day), not the unit budget, is the binding limit.

## When to run it

- Snapshot dates use India time (IST). Quota days reset at **midnight Pacific Time**, which is 12:30 IST in Pacific summer time and 13:30 IST in winter.
- To start every run with a fresh quota day, schedule it **after 14:00 IST**. The examples use 14:00.
- Choose **daily** (`* * *`) or **weekly** (e.g. Mondays) and keep it fixed. The analysis assumes a regular snapshot interval.

## Before you schedule

1. Find absolute paths: the repo (`pwd` in the repo) and Python (`which python3`). Schedulers do not load your shell profile, so `python3` alone may not be found.
2. Check that a manual run works from the repo root: `python3 src/collect_youtube.py --all --dry-run`.
3. Write logs **outside** the repo (they could contain error text). The examples use `~/Library/Logs/` on macOS and `~/.local/state/` on Linux.

The examples use this repo path and Python; replace them with yours:

```
REPO=/Users/ashwin/Documents/viral-product-intelligence-
PY=/usr/local/bin/python3
```

## macOS option 1: launchd (recommended)

launchd catches up after sleep. If the Mac is asleep at the scheduled time, the job runs when it wakes, and several missed runs are combined into one.

Create one plist per pass. Recent pass, daily: `~/Library/LaunchAgents/com.vpi.collect-youtube.plist`.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.vpi.collect-youtube</string>
  <key>WorkingDirectory</key>
  <string>/Users/ashwin/Documents/viral-product-intelligence-</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/python3</string>
    <string>src/collect_youtube.py</string>
    <string>--all</string>
    <string>--pass</string>
    <string>recent</string>
  </array>
  <!-- daily at 14:00 local time -->
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>14</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>/Users/ashwin/Library/Logs/vpi-collect-youtube.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/ashwin/Library/Logs/vpi-collect-youtube.log</string>
</dict>
</plist>
```

Main pass, Mondays and Tuesdays: copy the file to `com.vpi.collect-youtube-main.plist`. Set its `Label` to `com.vpi.collect-youtube-main`, change `recent` to `main`, and replace `StartCalendarInterval` with an array of two dates:

```xml
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>15</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>15</integer></dict>
  </array>
```

Manage them (repeat for `com.vpi.collect-youtube-main`):

```
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vpi.collect-youtube.plist   # enable
launchctl kickstart gui/$(id -u)/com.vpi.collect-youtube                                 # run once now
launchctl bootout gui/$(id -u)/com.vpi.collect-youtube                                    # disable
tail -n 50 ~/Library/Logs/vpi-collect-youtube.log
```

## macOS option 2: cron

cron runs in the system time zone (check it with `date`). It does **not** make up runs missed while the Mac was asleep.

`crontab -e`, then add one line:

```
# recent pass, daily at 14:00
0 14 * * * cd /Users/ashwin/Documents/viral-product-intelligence- && /usr/local/bin/python3 src/collect_youtube.py --all --pass recent >> $HOME/Library/Logs/vpi-collect-youtube.log 2>&1
# main pass, Mondays and Tuesdays at 14:15 (stops at the search cap on Monday, resumes on Tuesday)
15 14 * * 1,2 cd /Users/ashwin/Documents/viral-product-intelligence- && /usr/local/bin/python3 src/collect_youtube.py --all --pass main >> $HOME/Library/Logs/vpi-collect-youtube.log 2>&1
```

## macOS privacy permissions

The repo is in `~/Documents`, which macOS protects. If the log shows `Operation not permitted`, open System Settings → Privacy & Security → Full Disk Access and add the program that needs access: `/usr/sbin/cron` for cron, or the Python binary for launchd. Alternatively, move the repo out of `~/Documents`.

## Linux option 1: systemd user timer (recommended)

`~/.config/systemd/user/vpi-collect-youtube.service`:

```ini
[Unit]
Description=Viral Product Intelligence: YouTube collection

[Service]
Type=oneshot
WorkingDirectory=/home/USER/viral-product-intelligence-
ExecStart=/usr/bin/python3 src/collect_youtube.py --all --pass recent
StandardOutput=append:/home/USER/.local/state/vpi-collect-youtube.log
StandardError=append:/home/USER/.local/state/vpi-collect-youtube.log
```

`~/.config/systemd/user/vpi-collect-youtube.timer`:

```ini
[Unit]
Description=Run the YouTube collection daily after the quota reset

[Timer]
# recent pass daily at 14:00 IST
OnCalendar=*-*-* 14:00:00 Asia/Kolkata
# run at the next boot if the machine was off at the scheduled time
Persistent=true

[Install]
WantedBy=timers.target
```

```
mkdir -p ~/.local/state
systemctl --user daemon-reload
systemctl --user enable --now vpi-collect-youtube.timer
systemctl --user list-timers                      # check the next run
loginctl enable-linger "$USER"                    # keep user timers running while logged out
```

For the main pass, add `vpi-collect-youtube-main.service` with `--pass main` in place of `--pass recent`, and a matching `.timer` with `OnCalendar=Mon,Tue *-*-* 14:15:00 Asia/Kolkata`.

A time zone inside `OnCalendar` needs systemd 235 or newer. On older systems, drop `Asia/Kolkata` and use the machine's local time.

## Linux option 2: cron

```
# crontab -e
CRON_TZ=Asia/Kolkata
0 14 * * * cd /home/USER/viral-product-intelligence- && flock /tmp/vpi-collect.lock /usr/bin/python3 src/collect_youtube.py --all --pass recent >> $HOME/.local/state/vpi-collect-youtube.log 2>&1
15 14 * * 1,2 cd /home/USER/viral-product-intelligence- && flock /tmp/vpi-collect.lock /usr/bin/python3 src/collect_youtube.py --all --pass main >> $HOME/.local/state/vpi-collect-youtube.log 2>&1
```

`CRON_TZ` works with cronie (Fedora, RHEL, Arch). On Debian and Ubuntu cron, it may be ignored, so use the server's local time instead. `flock` makes the main job wait until the recent job has finished, so the two never run at once.

## After each run

- The log file and `data/collection_log.csv` show one row per product (`success`, `skipped_cached`, `aborted_budget`, `failed`, ...).
- `python3 src/pilot_report.py` summarizes the pilot products, joins and quota.
- `python3 src/check_query_versions.py` confirms every new raw file and row matches its registry query.
- `python3 src/clean_youtube.py` refreshes the cleaned tables in `data/processed/`, keeping active query versions and adding the on-target metrics.
- Once Trends exports exist: `python3 src/clean_trends.py` and `python3 src/build_aligned.py` rebuild the aligned table. Only snapshot dates inside the Trends window align, so plan Trends re-exports with that in mind (`docs/handoff_task1.md`).
- `python3 src/backup_local_data.py` archives the local-only data into `backups/`.
- The job does not commit or push anything. Data stays on the collecting machine until someone commits the shareable files (see README, "What is committed").
- If you see `quota_exceeded`, or `failed` with a key error, the job stops on its own. Fix the cause before the next scheduled run; do not loop retries.
