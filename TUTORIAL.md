# ARA 10ppm Window Report — Tutorial

Use this when you have downtime. It explains **what was rebuilt**, **where data comes from**, and **how the pieces fit together**.

For a broader Snowflake catalog (all DBs, teammate usage, swap/dashboard ideas), see **`snowflake_data_sources.ipynb`**.

For the multi-market **ULSD CIF** pack (day-driven discovery + Paper), see **`ulsd_cif_report.ipynb`** / `build_ulsd_cif_report.py`. Reference HTMLs live in `usld_cif_window/`.

Validated against the legacy HTML email report dated **2026-06-25**.

---

## 1. What this project does

Rebuilds the daily **Platts eWindow** assessment-style email (counterparty tables + charts) from Snowflake, starting with **ARA 10ppm Barges**.

Output today:

1. **Curves** — Platts ULSD FOB ARA barge MOPL differential history + latest-print callout  
2. **Barges** — daily volume (kt) + cycle-to-date Sankey flows  
3. **Strip sections** (Front End / Mid Window / Back End) — intraday bid/offer chart + counterparty activity table  

Run entrypoint: `ara_10ppm_report.ipynb`  
HTML output: `output/ara_10ppm_barges_YYYY-MM-DD.html`

---

## 2. File map

| File | Role |
|------|------|
| `report_config.py` | Product definition (`ARA_10PPM_BARGES`) + Snowflake table/symbol constants |
| `snowflake_data.py` | Connect + fetch raw eWindow orders for a report date |
| `chart_data.py` | Extra queries: MOPL curve, daily volume, Sankey flows, strip titles |
| `aggregate.py` | Counterparty activity table (Bids, Hits, Lifts, VWAP, …) |
| `report_charts.py` | Plotly charts → PNG (base64) |
| `render_html.py` | Assemble HTML report |
| `ara_10ppm_report.ipynb` | Glue: fetch → aggregate → chart → write HTML |
| `explore_databases.py` | One-off Snowflake exploration helper (not part of the report path) |
| `requirements.txt` | pandas, plotly, kaleido, snowflake-connector |

Auth / connection is reused from `dash_irr_database/` (`_snowflake.py`, credentials, RSA key).

---

## 3. Data sources (Snowflake)

### A. eWindow trades (most of the report)

```
SPGICI_EWINDOW_MARKETDATA.EWMD.EWINDOW_TRADEDATA
```

Key filters for ARA 10ppm:

| Field | Value |
|-------|--------|
| `MARKET` | `EU MidDist Barge` |
| `HUB` | `ARA Smr` |
| `PRODUCT` | `Platts ULSD Brg 1-3kt ICE LSGO M1` |
| `STRIP` | `FE` / `MW` / `BE` |
| `ORDER_CANCELLED` | `'F'` |

Useful columns: `ORDER_ID`, `ORDER_TIME`, `ORDER_TYPE`, `ORDER_STATE`, `ORDER_STATE_DETAIL`, `MARKET_MAKER_MNEMONIC`, `BUYER_MNEMONIC`, `SELLER_MNEMONIC`, `PRICE`, `ORDER_QUANTITY_TOTAL`, `ORDER_BEGIN`, `ORDER_END`.

Strip date labels (e.g. `30-04Jul`) come from `ORDER_BEGIN` / `ORDER_END`, not from the strip code alone.

### B. Platts assessments (Curves section)

```
SPGICI_MARKETDATA_SHARE.MDV2.PRICEDATA
```

| Symbol | Meaning |
|--------|---------|
| `AMOPM00` | ULSD 10ppmS FOB ARA Barge **MOPL differential** |

Query uses `BATE = 'c'` and assessments **strictly before** the report date so the latest print matches the legacy report (e.g. 25-Jun report → 24-Jun print `2.09`).

Metadata / discovery also lives in `REF_DATA_MARKETDATA` in the same share.

### C. Auth pattern

```python
# snowflake_data.py
SnowFlake(database=SNOWFLAKE_DATABASE, schema=SNOWFLAKE_SCHEMA)
```

Same pattern as other Jain Global notebooks under `dash_irr_database/`.

---

## 4. End-to-end pipeline

```
report_config (what to pull)
        │
        ▼
snowflake_data / chart_data  ──► DataFrames
        │
        ├─► aggregate.build_counterparty_table  ──► strip tables
        │
        └─► report_charts.build_all_charts      ──► Plotly figs
                    │
                    ▼
            charts_to_html_images (PNG + base64)
                    │
                    ▼
            render_html.render_report  ──► .html
```

Notebook cells roughly:

1. Set `REPORT_DATE`  
2. Fetch MOPL / volume / flows / day orders  
3. Build charts + tables  
4. Write HTML under `output/`

---

## 5. Counterparty table logic (`aggregate.py`)

Goal: match the email table columns:

`Bids | Gets hit | Lifts | Bought | Offers | Gets lifted | Hits | Sold | VWAP`

Important rules:

1. **Dedupe on `ORDER_ID`** before counting (one logical order may have many state rows).  
2. **Bids / Offers** = distinct active-side orders by market maker.  
3. **Gets hit / Gets lifted** = consummated bid / offer where that party was the market maker.  
4. **Hits / Lifts** = the other side of the trade (aggressor).  
5. **Bought / Sold** = from `BUYER_MNEMONIC` / `SELLER_MNEMONIC` on consummated rows (not only “lifted offers”).  
6. **VWAP** = quantity-weighted average of consummated prices for that party.

If Mid Window and Back End tables look identical on some days: that can be **real**. Same firms may mirror quotes on both strips with **no trades** — check `ORDER_ID` sets and `ORDER_BEGIN`/`ORDER_END` before assuming a bug.

---

## 6. Charts (`report_charts.py`)

### Curves — `plot_curve`

- Input: daily `AMOPM00` from `fetch_mopl_curve`  
- Annotation box bottom-left: `ULSD FOB ARA Brg (v MOPL): {value} ({date})`  
- Not eWindow VWAP (that was an early approximation)

### Daily volume — `plot_daily_volume`

- Bars = daily consummated volume (kt)  
- Line = 5-day MA  
- Legend top-right (avoids title collision)

### Sankey — `plot_sankey_flows`

- Month-to-date buyer←seller flows (kt)  
- Node labels: `NAME (±volume)` with volume in legacy units (`kt × 1000`, comma-formatted)  
- Width ~850px so middle/right labels breathe  
- Auto `snap` layout (simpler than forced 3-column positioning)

### Intraday strip — `plot_intraday_strip`

This is the “progression of intentions” chart.

**Idea:** one line per `(MARKET_MAKER_MNEMONIC, ORDER_TYPE)`, not per `ORDER_ID`.

Pipeline inside the chart:

1. `_prepare_intraday_rows` — drop duplicate window-close `inactive` rows; prefer explicit `withdrawn`  
2. `_build_party_intention_line` — walk orders in time:
   - `active` → quote points  
   - `consummated` → Hit (bid) / Lifted (offer)  
   - `withdrawn` / `inactive` → Withdrawn  
3. Plot with **`lines+markers`** and **linear** interpolation (not stepwise `hv`)  
4. Y-axis padded ±0.5 so edge markers aren’t clipped  
5. X-axis: 10-minute ticks over the window

---

## 7. HTML rendering (`render_html.py`)

Sections:

1. Curves image  
2. Barges: volume + Sankey  
3. For each strip label: intraday image + HTML table  

Charts are embedded as `data:image/png;base64,...` so the HTML is self-contained (no external `_files` folder needed).

---

## 8. How to run

```bash
# from repo root, with venv that has dash_irr_database deps
cd window_trades_report
# open ara_10ppm_report.ipynb and Run All
# or regenerate via a small script importing the same modules
```

Dependencies: see `requirements.txt` (notably **kaleido** for Plotly PNG export).

---

## 9. How to add another product later

1. Add a new `WindowReportConfig` in `report_config.py` (market / hub / product / strips).  
2. If Curves should use a different Platts symbol, add it beside `MOPL_SYMBOL` (or make it a config field).  
3. Point the notebook at the new config + date.  
4. Spot-check against an archived `.htm` / `.msg` for that product.

Discovery tip: eWindow product strings live in `EWINDOW_TRADEDATA`; Platts symbols in `REF_DATA_MARKETDATA`.

---

## 10. Gotchas learned while building

| Issue | Fix / lesson |
|-------|----------------|
| Import clash with `dash_irr_database/charts.py` | Report charts live in `report_charts.py` |
| Kaleido + pandas Timestamps | Convert to Python `datetime` before export |
| Curves ≠ trade VWAP | Use `AMOPM00` from MarketData share |
| Intraday looked stepwise / broken | Merge by party+side; linear + markers |
| MW ≈ BE visually | Different `ORDER_ID`s; mirrored quotes, zero trades that day |
| Sankey label crowding | Wider chart helps more than forced columns |
| `.msg` filenames | Often use en-dash `–`, not ASCII `-` |

---

## 11. Reference assets

- Legacy HTML: `ARA 10ppm Barges Window  2026-06-25.htm` + `_files/image001.png` … `image006.png`  
- Other product examples in the same folder (e.g. ULSD CIF)  
- Yves ideas / advanced analytics notes: `other/` (mostly outside eWindow scope)

---

## 12. Suggested study order (when you have time)

1. Read `report_config.py` (what “ARA 10ppm” means in Snowflake)  
2. Skim one day of raw rows from `fetch_window_orders`  
3. Walk `aggregate.build_counterparty_table` with FE data  
4. Read `_build_party_intention_line` while looking at an FE chart  
5. Trace the notebook top-to-bottom once without changing anything  

That’s the whole system end-to-end.
