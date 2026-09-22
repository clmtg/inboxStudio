# inboxStudio

A self-hosted rule editor for iCloud Mail and password-based IMAP accounts, powered by IMAPFilter. Add, edit,
reorder, and preview rules in your browser. Runs as a Docker Compose stack,
including under Dockge.

**Early release:** designed for a single trusted administrator behind an
authenticated reverse proxy. The app has no built-in login. It is not intended
for direct public exposure or multiple independent users.

## Features

- Multiple mail accounts with separate rules, schedules, and scan results.
- Account setup and read-only connection testing in the web UI.
- Sender domain and sender email matching, subject/body text, received age, and flags.
- Move immediately or after a configurable delay, move to Trash, keep in Inbox, permanently delete, or continue.
- Ordered If / Else if / Otherwise actions within a rule.
- Preview mode, manual scans, scan interval, results, and readable logs.
- Draft edits, explicit save, conflict detection, JSON export, and one previous backup.

Only Inbox is scanned. Rules run top to bottom. Move, keep, and delete stop later
rules; continue lets the next rule run. New installations start in preview mode
with **no rules**. Existing installations retain the rules in their data volume.

## Requirements

- Docker Engine with Docker Compose.
- An iCloud Mail account and Apple app-specific password, or an IMAP account supporting password authentication over implicit TLS. OAuth-only accounts and STARTTLS are not currently supported.
- Traefik connected to an external Docker network named `proxy`.
- An authenticated Traefik middleware. The supplied example uses `tinyauth@docker`,
  the `websecure` entrypoint, and a certificate resolver named `cloudflare`.

Adapt these labels to your existing proxy configuration. Merely setting local
DNS does not restrict an otherwise internet-accessible Traefik entrypoint.

## Setup

1. Download `compose.yaml` and `env.example` from the [latest release](https://github.com/clmtg/inboxStudio/releases/latest) into your stack directory. Source files are not required.
2. Copy `env.example` to `.env` and set
   `INBOXSTUDIO_HOST` to your chosen hostname. Keep `.env` private.
3. Point that hostname to your Traefik server and configure access in your
   authentication middleware.
4. Pull the selected version and start the stack:

   ```sh
   docker compose pull
   docker compose up -d
   docker compose logs --tail 50 web imapfilter
   ```

5. Open your configured HTTPS hostname. In **Accounts**, add a mailbox and test
   the connection. Save it, then add rules and select **Scan now**.
   Review **Scan activity** in preview before enabling live mode in Settings.

No web port is published to the host. The web container is reachable on `proxy`;
only trusted containers should share that network. Account passwords are stored in `accounts.json` in the data volume with owner-only
file permissions (0600). They are not encrypted at rest; protect the volume and
its backups. The web service saves/tests credentials and the worker uses them
for scans. Passwords are never returned by the accounts API or rule exports. Both containers run as a non-root user with read-only root
filesystems and a shared writable rules volume.

`DRY_RUN` and `INTERVAL_SECONDS` seed settings on the first start only. After
that, manage them in the UI. Deployment uses prebuilt images, so Dockge does not
need to build anything.

## Rule examples

- Sender domain `example.com` → move to `Newsletters`.
- Sender email **is** `alerts@example.com` → keep in Inbox.
- Sender email **contains** `notifications-` → move to `Notifications`.
- Sender domain `news.example.com` → move to `Newsletters` after 5 hours.
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
  Choose **Move to Trash** and select the account’s Trash folder for a recoverable action. For iCloud this is usually `Deleted Messages`. The provider may automatically empty that folder after its retention period.
- Destination folders must already exist. All selected destinations are checked
  before the scan performs any moves or deletions.
- Preview mode performs no mailbox mutations.
- Scans are not transactions. If a connection fails after some actions succeed,
  those actions are not rolled back. Check logs before retrying.
- Age is measured from the server's IMAP INTERNALDATE, not time spent in Inbox.
  Exactly 24 hours is not “more than 24 hours”; actions happen on the next scan
  after the threshold.
- Delayed moves use that same message age and strict boundary. A 5-hour delay
  moves the message on the first scan after it becomes more than 5 hours old.
- Unreadable conditions stop evaluation for that message, including Otherwise.
- Subjects support UTF-8, ASCII, Latin-1, and common Windows-1252 MIME encodings.
  Sender email parsing supports ordinary single mailbox headers; unusual or
  ambiguous formats are left untouched.
- Body matching uses raw message content. Encoded bodies or intervening HTML
  markup can prevent a phrase from matching.
- One worker scans accounts serially. Do not scale the worker service. A slow
  account can delay other accounts. Large inboxes
  involve many IMAP requests; scans time out after one hour.
- Rule counts include continue matches, so totals are not necessarily unique emails.

## Persistence, backups, and upgrades

The `rules-data` Docker volume holds accounts and passwords plus separate rules,
one previous rules version, scan requests, and status for each account. Export saved rules in Settings for an additional backup.
Do not run `docker compose down -v` unless you intend to remove this data.

Rule edits need no rebuild. Running scans finish with their original snapshot;
new settings apply to subsequent scans. Conflicting browser saves are rejected.

For updates, set `INBOXSTUDIO_VERSION` in `.env` to the desired release (for example
`0.2.0`), then use Dockge’s pull/recreate controls or run:

```sh
docker compose pull
docker compose up -d
```

Upgrading from the initial private setup: add `INBOXSTUDIO_HOST` to your existing
`.env`. Existing `IMAP_USERNAME` and `IMAP_PASSWORD` are imported once into a
default iCloud account by the worker. Its existing saved rules are preserved.
After verifying the imported account, remove those two environment values.
Further account changes are made in the UI and are not overwritten on restart. The legacy static Lua file is not part of
this repository; keep any personal copy outside the published project.

To restore an exported configuration, stop the worker and validate the backup.
Use the account ID from the export filename (`inbox-rules-<id>.json`); use
`default` for an account migrated from the original environment setup:

```sh
docker compose stop imapfilter
docker compose exec -T web python3 -c 'import json,sys,rules,accounts; key=sys.argv[1]; accounts.get(key); rules.atomic_json(rules.directory(key) / "rules.json", rules.validate(json.load(sys.stdin)))' ACCOUNT_ID < inbox-rules-ACCOUNT_ID.json
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

## Managing mail accounts

Use **Accounts → Add account**. Choose iCloud for prefilled connection settings,
or Other IMAP server to enter a TLS hostname and port (usually 993). Enter your
username and app-specific password, then **Test connection**. Testing logs in and
opens Inbox read-only; it does not save the form or run rules. Save separately.

Select an account in the top bar to edit its rules, view its scan activity, or
change its preview mode and interval. New accounts always start with no rules
and preview enabled. Save or discard rule drafts before switching accounts.

In Edit connection, leave the password blank to retain it. Changing the server,
port, or username requires re-entering the password so stored credentials cannot
be silently forwarded to a different destination. Disable automatic scans to
pause an account; an in-progress scan completes using its original settings.
All accounts are visible to the one trusted administrator; this is not a
multi-user service. Up to 20 accounts are supported.

## Switching an existing Dockge stack to release images

One final Compose replacement is needed. Keep the existing stack directory and
project name (for example `mailfiltering`), `.env`, and `rules-data` volume. Back
up the volume first. Replace only `compose.yaml` with the release attachment,
then add `INBOXSTUDIO_VERSION=0.2.0` to your existing `.env`. Keep
`INBOXSTUDIO_HOST` and any credentials still needed for the first account migration.
Run `docker compose pull` followed by `docker compose up -d`. Your old source
files can remain on disk; the release Compose file no longer uses them.

Future routine updates require only a version change and pull/recreate. Read the
release notes for any changes to Compose itself. Do not delete the data volume or
change the stack name: that could select a different, empty volume.

Pin an explicit version for predictable updates. `latest` is available but moves
with each release. To roll back application code, choose the previous version
and pull/recreate; any data-format rollback requirements will be stated in the
release notes. Mail already moved or deleted is not undone by rolling back.

## Building locally

The original source-build stack is kept in `compose.build.yaml`:

```sh
docker compose -f compose.build.yaml up -d --build
```

Use either deployment or build Compose, not both at once.

## Publishing a release (maintainers)

Commit tested changes, then create and push a new stable `vX.Y.Z` tag. Do not
reuse an existing version. The release workflow runs the full check suite, builds
`linux/amd64` and `linux/arm64` images, publishes version and `latest` tags to
`ghcr.io/clmtg/inboxstudio`, and creates a GitHub release with deployment files.
No personal registry token is required; the workflow uses its `GITHUB_TOKEN`.

On the first publication, make the `inboxstudio` container package **Public** in
GitHub Package settings. GitHub initially creates container packages as private.
Verify an anonymous image pull before advertising the release as installable.
A public source repository alone does not make its package public.
