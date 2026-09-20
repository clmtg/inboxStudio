# inboxStudio

A self-hosted rule editor for iCloud Mail, powered by IMAPFilter. Add, edit,
reorder, and preview rules in your browser. Runs as a Docker Compose stack,
including under Dockge.

**Early release:** designed for a single trusted administrator behind an
authenticated reverse proxy. The app has no built-in login. It is not intended
for direct public exposure or multiple independent users.

## Features

- Sender domain and sender email matching, subject/body text, received age, and flags.
- Move to an existing folder, keep in Inbox, permanently delete, or continue.
- Ordered If / Else if / Otherwise actions within a rule.
- Preview mode, manual scans, scan interval, results, and readable logs.
- Draft edits, explicit save, conflict detection, JSON export, and one previous backup.

Only Inbox is scanned. Rules run top to bottom. Move, keep, and delete stop later
rules; continue lets the next rule run. New installations start in preview mode
with **no rules**. Existing installations retain the rules in their data volume.

## Requirements

- Docker Engine with Docker Compose.
- An iCloud Mail account and Apple app-specific password.
- Traefik connected to an external Docker network named `proxy`.
- An authenticated Traefik middleware. The supplied example uses `tinyauth@docker`,
  the `websecure` entrypoint, and a certificate resolver named `cloudflare`.

Adapt these labels to your existing proxy configuration. Merely setting local
DNS does not restrict an otherwise internet-accessible Traefik entrypoint.

## Setup

1. Download or clone this project into your stack directory.
2. Copy `.env.example` to `.env`, fill in the credentials and set
   `INBOXSTUDIO_HOST` to your chosen hostname. Keep `.env` private.
3. Point that hostname to your Traefik server and configure access in your
   authentication middleware.
4. Build and start the stack:

   ```sh
   docker compose build
   docker compose up -d --no-build
   docker compose logs --tail 50 web imapfilter
   ```

5. Open your configured HTTPS hostname. Add rules, save, and select **Scan now**.
   Review **Scan activity** in preview before enabling live mode in Settings.

No web port is published to the host. The web container is reachable on `proxy`;
only trusted containers should share that network. IMAP credentials are supplied
only to the worker. Both containers run as a non-root user with read-only root
filesystems and a shared writable rules volume.

`DRY_RUN` and `INTERVAL_SECONDS` seed settings on the first start only. After
that, manage them in the UI. If Dockge cannot build because its Buildx directory
is read-only, run the build commands over SSH in the stack directory.

## Rule examples

- Sender domain `example.com` → move to `Newsletters`.
- Sender email **is** `alerts@example.com` → keep in Inbox.
- Sender email **contains** `notifications-` → move to `Notifications`.
- Sender domain `apple.com` → if received more than 24 hours ago, move to `Apple`;
  otherwise keep in Inbox.
- Flag status **is flagged** → keep in Inbox. Put this rule first to protect
  flagged messages from later actions.

All conditions in a group must match. Conditional branches are checked in order;
the first matching branch wins. Otherwise runs only when no branch matches.
Keep prevents later rules acting; continue permits them.

Sender email comparisons ignore capitalization and examine the mailbox address,
not the display name. Contains is a literal substring comparison. Sender domains
include subdomains. These conditions classify the From header; they do not
verify the sender's identity or email authentication.

## Mail safety and limitations

- **Delete permanently** uses IMAPFilter's delete operation, not a move to Trash.
  Use a move-to-trash-folder rule if you want a recoverable action.
- Destination folders must already exist. All selected destinations are checked
  before the scan performs any moves or deletions.
- Preview mode performs no mailbox mutations.
- Scans are not transactions. If a connection fails after some actions succeed,
  those actions are not rolled back. Check logs before retrying.
- Age is measured from the server's IMAP INTERNALDATE, not time spent in Inbox.
  Exactly 24 hours is not “more than 24 hours”; actions happen on the next scan
  after the threshold.
- Unreadable conditions stop evaluation for that message, including Otherwise.
- Subjects support UTF-8, ASCII, Latin-1, and common Windows-1252 MIME encodings.
  Sender email parsing supports ordinary single mailbox headers; unusual or
  ambiguous formats are left untouched.
- Body matching uses raw message content. Encoded bodies or intervening HTML
  markup can prevent a phrase from matching.
- One worker scans at a time. Do not scale the worker service. Large inboxes
  involve many IMAP requests; scans time out after one hour.
- Rule counts include continue matches, so totals are not necessarily unique emails.

## Persistence, backups, and upgrades

The `rules-data` Docker volume holds rules, one previous rules version, scan
requests, and status. Export saved rules in Settings for an additional backup.
Do not run `docker compose down -v` unless you intend to remove this data.

Rule edits need no rebuild. Running scans finish with their original snapshot;
new settings apply to subsequent scans. Conflicting browser saves are rejected.

For code updates, replace project files, preserve `.env` and the volume, then run:

```sh
docker compose up -d --build
```

Upgrading from the initial private setup: add `INBOXSTUDIO_HOST` to your existing
`.env`. Your saved rules are preserved. The legacy static Lua file is not part of
this repository; keep any personal copy outside the published project.

To restore an exported configuration, stop the worker and validate the backup:

```sh
docker compose stop imapfilter
docker compose exec -T web python3 -c 'import json,sys,rules; rules.atomic_json(rules.DATA / "rules.json", rules.validate(json.load(sys.stdin)))' < inbox-rules.json
docker compose restart web imapfilter
```

## Development and tests

Python 3.9+ is enough for the local editor, without any email connection:

```sh
DATA_DIR=./.local-data WEB_LISTEN=127.0.0.1 PORT=8765 python3 app/server.py
```

Open `http://localhost:8765`. Do not run the worker for a UI-only preview.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
node --check app/static/app.js
```

Install Lua 5.4 or set `LUA_BIN=/path/to/lua` to run engine tests. They use fake
IMAP data and a Python adapter for the supported regex subset, not a real IMAP
server or IMAPFilter's actual regex implementation. CI also builds the Docker
image and checks configuration syntax. These checks do not establish end-to-end
compatibility with every iCloud account. Test changes in preview on your server.

See [SECURITY.md](SECURITY.md) for the trust model and reporting guidance and
[CONTRIBUTING.md](CONTRIBUTING.md) for contribution expectations.

## License

Licensed under the GNU General Public License, version 3 (GPL-3.0-only).
See [LICENSE](LICENSE) for the full text. IMAPFilter and other dependencies
retain their own licenses.
