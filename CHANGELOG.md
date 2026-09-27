# Changelog

## Unreleased

## 0.2.5

- Open the rule editor with focus on its heading instead of the name field,
  avoiding an automatic keyboard opening on mobile.

## 0.2.4

### Added

- Choose All conditions match (AND) or Any condition matches (OR) for each
  rule and If / Else-if branch. Rule summaries display AND or OR between
  conditions; existing rules retain All matching.
- Use the matching version's CHANGELOG.md section as the GitHub release
  description. Release publication stops before image publishing when that
  section is missing, empty, or duplicated.

### Behavior

- Action timing remains separate from condition matching: an OR match cannot
  bypass the action's age threshold.
- If no OR alternative definitely matches and a condition cannot be read,
  leave the message untouched instead of using Otherwise or a later rule.

## 0.2.2

### Added

- Configure every action to run immediately or when the email is older than
  an age entered in minutes. This applies to Move, Trash, Delete permanently,
  Keep in Inbox, and Continue, including conditional branches and Otherwise.
- Add repository Git hooks that include source and destination branch names
  in local merge commit messages, preserving the message body.

### Changed

- Enter action delays and received-age conditions in minutes, including values
  such as 34 minutes. Existing rules retain their configured durations.
- Present existing Move after a delay rules as Move with an age threshold.
- Keep waiting messages in Inbox and stop later rules from acting until the
  selected action's age threshold is reached. Timing is measured from receipt;
  the action runs on the next scan after the threshold.
- Use the `latest` Docker image tag for both services in compose.yaml instead
  of a fixed version selected through INBOXSTUDIO_VERSION.

## 0.2.1

### Added

- Add Move after a delay to rules, conditional branches, and Otherwise.
  Matching mail becomes eligible for moving when its received age exceeds the
  configured delay, and moves on the next scan. Unreadable received dates leave
  the message untouched.

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
