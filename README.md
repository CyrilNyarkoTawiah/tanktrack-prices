# tanktrack-prices

No-API fuel price file for the [TankTrack](https://github.com/BlackChyna/TankTrack) Android app.

A small scraper reads public Ghana fuel-price sources around each NPA pricing window
(1st and 16th of the month, plus a catch-up on the 20th) and writes `fuel-prices.json`
in the exact format TankTrack's Settings > "Fuel price file address" accepts.

## Point the app at this file (once)

In TankTrack: More > Settings > Online features:
1. In **Fuel price file address**, paste:
   `https://raw.githubusercontent.com/BlackChyna/tanktrack-prices/main/fuel-prices.json`
2. Tap **Save address**, then **Download now**.

No API key needed, no CediRates credits used. You can still type prices by hand anytime.

## Sources and attribution

- **GOIL** — pump prices from GOIL's own notice at goil.com.gh (official).
- **MyJoyOnline / Joy Business** — pump prices announced by Star Oil, Shell, TotalEnergies
  and others are read from their fuel-prices reporting (myjoyonline.com).
- **NPA** — the official NPA minimum ex-pump price (floor) from npa.gov.gh, when the
  current window's PDF is published. Companies never sell below it.

Prices are only written when a source states them. If a company has not announced a
price, its value is carried over from the previous run **with its old date**, and the
app flags it as possibly out of date. MISA Energy and So Energy are rarely reported
anywhere free; leave those to hand-typing in the app.

All sources are credited in the file's `source` field, which the app shows on the
Home price card. Public data, personal non-commercial use, two-to-three hits per site
per window — be polite to these servers when editing the scraper.

## Files

- `fuel-prices.json` — the file the app downloads (canonical).
- `PRICES.md` — human-readable copy of the same data.
- `history/` — every generated file, one per run.
- `scraper.py` — the pipeline. Edit with care: the output is checked against the same
  strict rules the app applies (`schemaVersion` 1, GHS, per litre, GH3–GH60 range).
