# Security

## How Keptwise is meant to run

Keptwise is a single-user app for your own computer.

- It listens on `127.0.0.1`, so other devices can't reach it.
- It has no login. Instead it only answers requests addressed to `127.0.0.1` or
  `localhost`, and refuses changes sent from other websites open in your browser.
- Your data stays in a SQLite file on your computer. The app makes no network requests
  of its own.

**Don't expose it on a network**, for example with `--host 0.0.0.0`, a reverse proxy or
a tunnel. Anyone who could reach it could read and change your data.

## Reporting a problem

If you find a security problem, please report it privately rather than in a public
issue, using **Report a vulnerability** on the repository's
[Security tab](https://github.com/indigoape/keptwise/security/advisories/new). Don't include
real financial data in a report.
