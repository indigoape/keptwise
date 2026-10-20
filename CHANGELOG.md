# Changelog

## 1.2.0 (2026-10-20)

The first public release.

- Renamed from Bookkeeping to **Keptwise**. The `bookkeeping` command still works, and
  so do an existing `data/bookkeeping.db` and the `BOOKKEEPING_DB` variable.
- New `keptwise demo` command: a made-up year of data in its own database, for trying the
  app.
- Dates display correctly on Windows.
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
