# Security

inboxStudio is an early single-administrator application. Its HTTP server does
not authenticate users: authentication and HTTPS must be enforced by your
reverse proxy. Do not publish the web container's port directly. Any client
that can reach it directly can read rules, change actions, and request scans.
Only trusted containers should share the proxy network.

Treat access to the UI, Docker host, and shared data volume as access to your
mail filtering controls. The worker holds the iCloud app-specific password;
Docker administrators can access it. Rule exports, folders, and logs may contain
private information. Do not attach them to public issues without reviewing them.

Mailbox contents and From headers are untrusted. From matching is not sender
identity verification. Rules accept structured data, not executable Lua.
Permanent deletion is intentional functionality and must be enabled with care.

Never include credentials, personal mail, or exploitable details in a public
issue. Use GitHub private vulnerability reporting if enabled on the repository;
otherwise ask the maintainer for a private reporting channel without disclosing
the vulnerability. There is no guaranteed response time or formal security audit.
