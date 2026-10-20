# Contributing

Thanks for helping. Keptwise is a small project, so please open an issue before starting
anything large.

Keptwise is free software under the [GNU GPL v3 or later](LICENSE). By sending a pull
request, you agree that your contribution is released under the same licence.

## Setting up

```sh
git clone https://github.com/indigoape/keptwise.git
cd keptwise
uv run pytest            # 100+ tests, a few seconds
uv run ruff check .      # lint
uv run ruff format .     # format
uv run keptwise demo     # the app with made-up data
```

Every pull request runs the same checks on Linux with Python 3.10 to 3.13, and on macOS
and Windows with Python 3.13.

## Never include real financial data

Don't put real statements, account numbers, sort codes, names or amounts in issues,
commits or test files. Make up a few rows that keep your bank's exact column names and
formats, and change everything else.

## Adding a bank's CSV format

Most banks only need their column names added.

1. Open `src/keptwise/importer.py` and find `COLUMN_ALIASES`. Add your bank's column
   names, in lower case, to the right lists: `date`, `description`, `amount` (one signed
   column) or `debit` and `credit` (separate money-out and money-in columns), and
   optionally `balance` and `tx_type`.
2. Add a made-up sample to `tests/data/`, named after the bank, such as
   `tests/data/examplebank_style.csv`. Keep the header and any lines above it exactly as
   the bank exports them, including odd quoting, £ signs or date formats.
3. Add a test to `tests/test_importer.py` that parses your sample and checks the dates,
   descriptions and amounts. The existing Monzo, Starling and Nationwide tests show how.
4. Run `uv run pytest`.

**Don't change `importer.fingerprint`.** It's how re-imports recognise transactions
already in someone's database. If it changes, everyone's next import would add
duplicates.

## Starter rules

`src/keptwise/seed.py` holds the categories and rules a new database starts with. Good
additions are big UK chains and billers that most people would categorise the same way.
Keep patterns specific enough that they don't catch unrelated shops.

## Other countries

Keptwise is UK-only today: pounds, a tax year from 6 April, and ISA tracking. Supporting
other currencies and tax years is planned. If you'd like to help with your country,
open an issue first, describing its currency, tax year and any tax-free allowance like
the ISA, so we can agree on a design.

## Database changes

The schema is upgraded with `PRAGMA user_version` (see `MIGRATIONS` in `db.py`). Add a
new migration at the end of the list. Never edit or remove an existing one: people's
databases may be at any earlier version.

## Style

- Plain, short sentences in the interface, written for someone who isn't an accountant.
- Money is stored as whole pence (integers), never floats.
- No new dependencies without discussing it first.
