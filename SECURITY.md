# Security

## How Keptwise is meant to run

Keptwise is a single-user app for your own computer.

- It listens on `127.0.0.1`, and refuses any request that didn't come from this computer
  or isn't addressed to `127.0.0.1` or `localhost`. Other devices on your network can't
  reach it.
- It refuses changes sent from other websites open in your browser.
- A password is optional. Without one, anyone who can use your computer (including other
  user accounts on it) can open Keptwise. With one, Keptwise asks for it in each new
  browser session, slows down repeated wrong guesses, and stores only a salted hash.
  Changing the password logs out every other browser.
- Your data stays in a SQLite file on your computer. On macOS and Linux, the database and
  its backups can only be read by your user account. The file isn't encrypted: anyone
  who can read it can see your data without the password. This is also why
  `keptwise password` can reset a forgotten password without asking for the old one.
- The app makes no network requests of its own.

## Using it from other devices

`keptwise serve --host 0.0.0.0 --allow-network` lets other devices on your network
connect. It only starts once a password is set, and every device has to log in.

The connection is plain HTTP, so someone on the same network could read your password
and data as they pass. Only do this on a network you trust, such as your home Wi-Fi,
and never expose Keptwise to the internet, for example with port forwarding, a
reverse proxy or a tunnel.

## Reporting a problem

If you find a security problem, please report it privately rather than in a public
issue, using **Report a vulnerability** on the repository's
[Security tab](https://github.com/indigoape/keptwise/security/advisories/new). Don't include
real financial data in a report.
