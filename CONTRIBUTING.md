# Contributing

Keep changes focused and include a description of the problem, behavior after
the change, and validation performed. Do not include personal mail, credentials,
rule exports, or deployment hostnames in patches or issues.

Run the Python test suite with Lua available and check JavaScript syntax as
shown in README.md. Matching and action changes should cover preview mode,
priority, unreadable input, and relevant boundary cases. Use synthetic fixtures.
Document any Docker/iCloud checks separately from mocked tests.

Authentication is delegated to a trusted reverse proxy. Do not add direct public
exposure as a default. Existing saved configurations must remain compatible, or
have an explicit migration plan that preserves user rules.

Contributions are provided under the project’s GPL-3.0-only license.
