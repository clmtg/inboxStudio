# Changelog

## 0.2.0

- Install and update through versioned GHCR images, with amd64 and arm64 support.
- Release attachments provide deployment Compose and environment examples.
- Manage multiple IMAP accounts and test connections in the web UI.
- Use separate rules, settings, and scan results for each mailbox.
- Add ordered conditional branches, sender email matching, and Move to Trash.
- Larger mobile touch targets and dialogs that fit the visible screen.

Existing accounts and rules remain in the same Docker data volume. Keep the
stack/project name and volume when replacing the Compose file. Back up the
volume before upgrading. Account passwords are included in volume backups;
store them privately. See README.md for migration and authentication details.
