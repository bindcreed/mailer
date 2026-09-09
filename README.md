# Mailer

A small, self-hosted tool for sending personalized bulk email — upload a
spreadsheet of names and emails, and send each person their own individual
message (with an optional attachment) through your own SMTP server. It
keeps track of who's already been emailed in each "sending session," so you
can safely re-run a batch later without accidentally double-emailing people,
unless you explicitly choose to resend to them.

Built with Django and PostgreSQL. No paid services, no third-party email
API — it sends through whatever SMTP server you give it.

## Why this exists

Sending 100+ personalized emails one at a time doesn't scale, and a plain
mail-merge script has no memory: run it twice and everyone gets emailed
twice. This app adds a thin layer of state on top of a simple SMTP send —
sessions, recipients, and status — so bulk sending becomes something you can
stop, resume, and repeat safely.

## How it works, in short

1. **Add an SMTP server** — host, port, username/password, and the address
   you want to send from. There's a "Test Connection" button to check it
   works before you rely on it.
2. **Create a sending session** — give it a name, pick the SMTP server,
   write a subject and body using the built-in rich text editor (or switch
   to "Raw HTML" to paste HTML directly), and optionally attach a file. Use
   `{{name}}` anywhere you want the recipient's name inserted.
3. **Upload a spreadsheet** (.xlsx or .csv) with a name column and an email
   column. The app tries to detect which column is which automatically; if
   it can't tell, it asks you to pick.
4. **Review before anything is sent.** If some of those email addresses
   were already emailed in this session, you'll see them marked
   "Already sent" with a checkbox — leave it unticked to skip them, tick it
   to resend.
5. **Start sending.** Emails go out one at a time, as individual messages —
   no CC, no BCC, one address per email — with a short pause between each
   to stay polite to the mail server. A progress bar shows sent/failed/
   pending counts live.
6. If you close the app or it restarts partway through, the session is
   left in a resumable state — already-sent recipients are never touched
   again unless you explicitly re-opt them in through a new upload.

## Requirements

- Python 3.10 or later
- PostgreSQL, running and reachable (this app does not install or manage
  Postgres for you — install it separately if it isn't already there)
- An SMTP server/account you're allowed to send from

## Setup (first time on a new computer)

Run each numbered step in order. Where macOS/Linux and Windows commands
differ, both are shown — use the one for your system.

**1. Get the code**

```bash
git clone <this repo's URL> mailer
cd mailer
```

**2. Create and activate a virtual environment**

This keeps this project's Python packages separate from anything else on
your machine.

macOS/Linux:
```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows (Command Prompt or PowerShell):
```bat
python -m venv .venv
.venv\Scripts\activate
```

Either way, you'll know it worked because your terminal prompt now starts
with `(.venv)`. Re-run the activate command any time you open a new
terminal to work on this project — the virtual environment doesn't stay
active between sessions.

**3. Install dependencies**

```bash
pip install -r requirements.txt
```

**4. Create your `.env` file**

Copy the example file:

- macOS/Linux: `cp .env.example .env`
- Windows: `copy .env.example .env`

Now open `.env` in a text editor and fill in:

- `DJANGO_SECRET_KEY` — generate one by running
  `python -c "import secrets; print(secrets.token_urlsafe(50))"` and
  pasting the output in.
- `FERNET_KEY` — generate one the same way, just with a different command:
  `python manage.py generate_fernet_key`. Copy what it prints and paste it
  in as the value of `FERNET_KEY=`. (This is what encrypts SMTP passwords
  before they're stored — see "Security notes" below.)
- `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGHOST`, `PGPORT` — point these at
  your PostgreSQL installation.

Save the file once all of these are filled in — don't leave `DJANGO_SECRET_KEY`
or `FERNET_KEY` as `change-me`, or you'll hit errors later when the app
tries to use them.

**5. Create the database**

If it doesn't already exist:

```bash
createdb mailer
```

If `createdb` isn't recognized (common on Windows, unless Postgres's `bin`
folder is on your PATH), use `psql` instead:

```bash
psql -U postgres -c "CREATE DATABASE mailer;"
```

**6. Set up the database tables**

```bash
python manage.py migrate
```

**7. Create your login**

```bash
python manage.py createsuperuser
```

You'll be asked for a username, email, and password — this is what you'll
log in with.

**8. Run it**

```bash
python manage.py runserver
```

Open http://127.0.0.1:8000/ and log in with the account you just created.

## A note on `{{name}}`

Both the subject and the body support `{{name}}` as a placeholder. It's
replaced with whatever was in the "name" column of your spreadsheet for
that row. If a row has no name, it's simply left blank.

## Important limitations (v1)

- **Sending runs as a background thread inside the Django process**, not a
  separate task queue. This keeps the install simple (just Python +
  Postgres), but it means:
  - Don't run this behind multiple `gunicorn`/`uwsgi` worker processes —
    stick to one worker, or move to a real task queue (Celery + Redis)
    first. A single `python manage.py runserver`, or one production worker,
    is fine.
  - If the process is killed mid-send, the session is left marked
    "sending." Once its last-activity timestamp goes stale (about a
    minute), a "Resume Sending" button appears so you can safely continue
    from where it left off.
- There's no per-provider rate-limit tuning — just one configurable delay
  (`SEND_DELAY_SECONDS` in `.env`) between every send, regardless of SMTP
  server.
- Emails are sent as HTML (with an automatically generated plain-text
  fallback for mail clients that don't render HTML).

## Possible future improvements

- A real task queue (Celery + Redis) for resilience across restarts and
  proper retries
- Per-SMTP-provider sending-rate presets
- CSV export of a session's send results
- Multiple attachments per session

## Third-party front-end libraries

The rich text editor and recipients table are built with two vendored,
open-source front-end libraries, served locally from `static/vendor/` (no
CDN, so the app keeps working fully offline):

- [Quill](https://quilljs.com/) — BSD-3-Clause license
- [ag-Grid Community](https://www.ag-grid.com/) — MIT license

Both are included as plain built files with no build step required; there's
nothing extra to install for them.

## Security notes

- SMTP passwords are encrypted at rest in the database using the
  `FERNET_KEY` from `.env` — they are never stored in plain text, and
  never appear in any log.
- The app requires login for every page. If you expose it beyond your own
  machine, put it behind HTTPS (e.g. a reverse proxy) — Django serves plain
  HTTP by default in development.
- `.env` is git-ignored on purpose. Never commit real credentials —
  `.env.example` should always contain placeholders only.

## License

MIT — see `LICENSE`. Contributions welcome.
