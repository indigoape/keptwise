# Changelog

## 1.3.0 (2026-10-21)

- An optional password. Setup offers one, and Settings can add, change or remove it.
  Keptwise then asks for it in each new browser session ("Keep me logged in" lasts 30
  days), slows down repeated wrong guesses, and has a **Log out** button. Forgotten it?
  `keptwise password --remove` resets it from the terminal.
- New `keptwise password` command to set, change or remove the password.
- Refuses requests that don't come from this computer, even with a faked `Host` header.
- `keptwise serve --host` now refuses addresses other devices could reach, unless you add
  `--allow-network` and have set a password. Other devices must then log in.
- The database and its backups can now only be read by your user account (macOS and
  Linux).

## 1.2.1 (2026-10-20)

- Published on PyPI: try it with `uvx keptwise demo`, or install it with
  `uv tool install keptwise`.
- The README works on PyPI too, and notes that Keptwise isn't financial advice.

## 1.2.0 (2026-10-20)

The first public release.

- Renamed from Bookkeeping to **Keptwise**. The `bookkeeping` command still works, and
  so do an existing `data/bookkeeping.db` and the `BOOKKEEPING_DB` variable.
- New `keptwise demo` command: a made-up year of data in its own database, for trying the
  app.
- Dates display correctly on Windows.
- Refuses requests addressed to other hostnames, and changes sent from other websites,
  since the app has no login.
- Released as free software under the GPL-3.0-or-later, with a contributing guide, security policy, and CI on Linux, macOS and Windows.

## 1.1.0

- First-run setup, and a Settings page for your bank and investment platforms.
- The importer recognises Monzo, Starling and Nationwide layouts, and handles signed
  amounts, £ signs, brackets, CR/DR and semicolon-separated files.
- A starter set of UK categories and rules.
- The database goes in your user data folder when the app is installed as a package.
- Removed the old spreadsheet import.

## 1.0.0

- Monthly overview, "Worth a look" insights, rules with a live preview, planned against
  actual investments, tax years with ISA allowance tracking, a calendar reminder, daily
  backups, and light and dark mode.
