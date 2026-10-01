# supaero-calsync

Keep your **ISAE-SUPAERO timetable** in **Google Calendar** automatically, with graded sessions and exams colour-coded so you can never miss them. Because Google Calendar is also what Apple Calendar displays, the same timetable shows up on your Mac and iPhone with no second sync and no duplicates.

```
Auriga portal ──(your login)──▶ supaero-calsync ──▶ Google Calendar ──▶ Apple Calendar / phone
                                  classify · diff                         (just another view)
```

## Why this exists

The school portal has no calendar export, so students re-type their timetable or import a one-off `.ics` file that goes stale the moment a room changes. This tool turns the timetable into a live, self-correcting calendar:

- **One command to refresh.** Re-run it after a schedule change: moved sessions are updated in place, cancelled ones are removed, new ones are added.
- **Zero duplicates, by design.** Every event is tagged with the portal's own session id. Re-running is idempotent, and anything you add to those calendars by hand is never touched.
- **Priority at a glance.** Graded labs (BEN) and exams go to a red calendar with early reminders, plain labs (BE) to an orange one, everything else to a neutral one. Rules are regular expressions you can edit.
- **Your password never touches the tool.** You sign in on the school's own page in a real browser window; the session is remembered, so most runs need no interaction at all.

## How it works

1. **Browser login** (`browser.py`): opens the portal in your own Chrome/Edge via Playwright with a dedicated, persistent profile. The portal's single-page app authenticates API calls with a bearer token; the tool reads that header from the app's first authenticated request. The token stays in memory and is never written to disk.
2. **Fetch** (`portal.py`): pulls the sessions month by month over plain HTTPS and normalises them into `Lesson` objects (UTC to Europe/Paris, rooms, instructors, groups).
3. **Classify** (`classify.py`, `config.py`): each lesson goes to the first matching category; unmatched lessons go to the default one.
4. **Plan** (`sync.py`): a pure function compares the desired events with those already in Google (identified by a private marker and the session id) and produces `create / update / delete` lists. A content fingerprint avoids rewriting events that did not change.
5. **Apply** (`google.py`): runs the plan through the Google Calendar API, with retry and backoff on rate limits.

A guard refuses to delete more than half of your existing events in one run, so a truncated portal response can never wipe your calendar (override with `--force-delete`).

## Requirements

- Python 3.11+
- Google Chrome or Microsoft Edge installed (no browser download needed)
- A Google account, and a one-time Google Cloud setup (below)

## Install

```bash
git clone https://github.com/<your-username>/supaero-calsync.git
cd supaero-calsync
python3 -m venv .venv && source .venv/bin/activate
pip install -e .          # note the final dot: it means "this folder"
```

## Google setup (one time, about 10 minutes)

The tool writes to your calendar through the official Google Calendar API, so you need your own OAuth client.

1. Open the [Google Cloud console](https://console.cloud.google.com/) and create a project (any name).
2. **APIs & Services → Library**: enable the **Google Calendar API**.
3. Open **Google Auth Platform** (formerly "OAuth consent screen") and click **Get started**:
   - **Branding**: app name (e.g. `supaero-calsync`), your email as user support email, and your email as developer contact. Leave *Authorized domains* empty: it is only needed for web apps, and this one runs on your machine.
   - **Audience**: choose **External**.
4. Still under **Audience**, click **Publish app** (status "In production"). This matters: apps left in "Testing" have refresh tokens that expire after 7 days, which would force a new sign-in every week. You will see an "unverified app" warning at first sign-in; that is expected for a personal client.
5. Under **Clients**, **Create client**, application type **Desktop app**, then download the JSON file.
6. Save it as `~/.config/supaero-calsync/credentials.json`.

The first run opens a browser tab to authorise access and stores a token in `~/.config/supaero-calsync/google-token.json` (permissions `600`). Neither file is ever inside the repository.

## Usage

```bash
supaero-calsync --preview     # log in, fetch, show how sessions are classified; touches nothing
supaero-calsync --dry-run     # also compare with Google Calendar and list the changes, without applying
supaero-calsync               # sync for real
```

The default range is the current academic year (1 September to 31 August). Use `--start` and `--end` (`YYYY-MM-DD`) to change it.

On the first run, a browser window opens on the portal: sign in as usual. The tool detects the login by itself and carries on. Your session lasts roughly half a day; when it expires you are simply asked to sign in again.

Run `--preview` first: it lists every course title under the category it landed in, which is the fastest way to spot a rule that needs adjusting.

| Option | Meaning |
| --- | --- |
| `--start`, `--end` | Date range to sync |
| `--preview` | Fetch and classify only |
| `--dry-run` | Show the changes Google Calendar would receive |
| `--force-delete` | Allow deleting a large share of existing events |
| `--config FILE` | Use a specific config file |
| `--from-file JSON` | Read raw portal data from a file instead of logging in |
| `--save-raw JSON` | Save the raw portal response (debugging only; it contains personal data) |

## Customising categories and colours

```bash
supaero-calsync init      # writes ~/.config/supaero-calsync/config.toml
```

The file defines an ordered list of categories. The first one whose rules match a lesson wins; the rest fall into `[default]`:

```toml
[[categories]]
name = "critical"
calendar = "Supaero · Graded (BEN / Exams)"
color_id = "11"             # Google colour index 1-11 (11 = tomato red)
prefix = "🔴 "              # added to every event title
exam_flag = true            # also match sessions the portal flags as exams
reminders = [1440, 60]      # popup reminders, in minutes before the start
rules = ['\bBEN\b', '\bBE\s*not[ée]e?s?\b', '\bex(am(en)?s?)?\b']
```

Rules are case-insensitive regular expressions matched against `"<activity code> <course title> <session description>"`.

**Why one calendar per category instead of one colour per event?** Apple Calendar displays only the colour of a whole calendar and ignores per-event colours set in Google. Separate calendars make the colour show up everywhere.

## Seeing it in Apple Calendar

Add your Google account under *Settings → Calendar → Accounts* (iPhone) or *System Settings → Internet Accounts* (Mac) and enable **Calendars**. The new calendars appear automatically. If one is missing on iPhone, tick it at [google.com/calendar/syncselect](https://calendar.google.com/calendar/syncselect). For events you create on Apple devices to land in Google as well, set your Google calendar as the default calendar.

## Development

```bash
pip install -e ".[dev]"
pytest          # unit tests use synthetic data and an in-memory calendar, no network
ruff check .
```

The planner, classifier, parser and CLI are covered by tests, including the cases that matter most for a sync tool: a second run changes nothing, a changed room updates in place, a cancelled session is deleted, a session whose category changes is moved rather than duplicated, hand-made events are never modified, and an empty fetch cannot wipe the calendar.

The browser login and the live Google API calls are thin adapters around those tested parts; they are not covered by automated tests because they need a real portal account and Google credentials.

## Privacy and security

- Your school password is never seen, stored or transmitted by this tool.
- The portal token lives in memory only. The browser profile (`~/.config/supaero-calsync/browser-profile`) holds your portal session like any browser profile; delete the folder to sign out.
- Google OAuth files stay in `~/.config/supaero-calsync/`, outside the repository. Never commit them.
- The tool only reads your own timetable, with your own login, the same requests the portal's web page makes. It is not affiliated with or endorsed by ISAE-SUPAERO or the portal vendor.

## Acknowledgements

The shape of the portal's planning API was learned from [clemmbn/auriga-extract](https://github.com/clemmbn/auriga-extract) (MIT), which exports the timetable to an `.ics` file. This project is an independent implementation with a different goal: continuous synchronisation to Google Calendar, rule-based classification, and idempotent updates.

## License

MIT, see [LICENSE](LICENSE).
