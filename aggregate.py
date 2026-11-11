"""
Build counterparty activity tables from raw eWindow order rows.

Each order can appear multiple times (price/state updates). We dedupe on
ORDER_ID before counting bids/offers, then derive trade stats from
consummated rows only — matching the legacy ARA 10ppm email layout.
"""

from __future__ import annotations

import pandas as pd

COUNTERPARTY_COL = "MARKET_MAKER_MNEMONIC"
METRIC_COLS = [
    "Bids",
    "Gets hit",
    "Lifts",
    "Bought",
    "Offers",
    "Gets lifted",
    "Hits",
    "Sold",
    "VWAP",
]


def _dedupe_orders(df: pd.DataFrame) -> pd.DataFrame:
    """One row per ORDER_ID — keep the latest state by UPDATE_TIME when present."""
    if df.empty:
        return df.copy()
    sort_cols = [c for c in ("UPDATE_TIME", "ORDER_TIME") if c in df.columns]
    ordered = df.sort_values(sort_cols) if sort_cols else df
    return ordered.drop_duplicates(subset=["ORDER_ID"], keep="last")


def _vwap(prices: pd.Series, qtys: pd.Series) -> float:
    qty = qtys.fillna(0).astype(float)
    price = prices.astype(float)
    total_qty = qty.sum()
    if total_qty <= 0:
        return 0.0
    return float((price * qty).sum() / total_qty)


def build_counterparty_table(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate order activity by market-maker mnemonic.

    Semantics (verified against 2026-06-25 ARA 10ppm Front End email):
      - Bids / Offers: distinct live orders posted (any final state except cancelled)
      - Gets hit: consummated bid where this party was the buyer
      - Hits: consummated bid where this party was the seller (hit someone's bid)
      - Lifts: consummated offer where this party was the buyer
      - Gets lifted: consummated offer where this party was the seller
      - Bought / Sold: deal quantity on consummated rows
      - VWAP: volume-weighted average price on consummated buy + sell flow
    """
    if df.empty:
        return pd.DataFrame(columns=[COUNTERPARTY_COL, *METRIC_COLS])

    df = df.copy()
    for col in (COUNTERPARTY_COL, "ORDER_TYPE", "ORDER_STATE", "BUYER_MNEMONIC", "SELLER_MNEMONIC"):
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str).str.strip()

    deduped = _dedupe_orders(df)
    consummated = df[df["ORDER_STATE"].str.lower() == "consummated"].copy()

    parties = sorted(
        p
        for p in set(deduped[COUNTERPARTY_COL]) | set(consummated["BUYER_MNEMONIC"]) | set(consummated["SELLER_MNEMONIC"])
        if p and p not in ("", "None", "nan")
    )

    rows: list[dict] = []
    for party in parties:
        bids = deduped[
            (deduped[COUNTERPARTY_COL] == party) & (deduped["ORDER_TYPE"].str.lower() == "bid")
        ]["ORDER_ID"].nunique()

        offers = deduped[
            (deduped[COUNTERPARTY_COL] == party) & (deduped["ORDER_TYPE"].str.lower() == "offer")
        ]["ORDER_ID"].nunique()

        cons_bids = consummated[
            (consummated["ORDER_TYPE"].str.lower() == "bid") & (consummated["BUYER_MNEMONIC"] == party)
        ]
        cons_offers_buy = consummated[
            (consummated["ORDER_TYPE"].str.lower() == "offer") & (consummated["BUYER_MNEMONIC"] == party)
        ]
        cons_bids_hit = consummated[
            (consummated["ORDER_TYPE"].str.lower() == "bid") & (consummated["SELLER_MNEMONIC"] == party)
        ]
        cons_offers_lifted = consummated[
            (consummated["ORDER_TYPE"].str.lower() == "offer") & (consummated["SELLER_MNEMONIC"] == party)
        ]

        party_trades = consummated[
            (consummated["BUYER_MNEMONIC"] == party) | (consummated["SELLER_MNEMONIC"] == party)
        ]
        bought = party_trades[party_trades["BUYER_MNEMONIC"] == party]["ORDER_QUANTITY_TOTAL"].astype(float).sum()
        sold = party_trades[party_trades["SELLER_MNEMONIC"] == party]["ORDER_QUANTITY_TOTAL"].astype(float).sum()

        rows.append(
            {
                COUNTERPARTY_COL: party,
                "Bids": int(bids),
                "Gets hit": int(len(cons_bids)),
                "Lifts": int(len(cons_offers_buy)),
                "Bought": bought,
                "Offers": int(offers),
                "Gets lifted": int(len(cons_offers_lifted)),
                "Hits": int(len(cons_bids_hit)),
                "Sold": sold,
                "VWAP": _vwap(party_trades["PRICE"], party_trades["ORDER_QUANTITY_TOTAL"]),
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    out = out[out[COUNTERPARTY_COL] != "TOTAL ROW"]
    out = out[
        (out[["Bids", "Gets hit", "Lifts", "Offers", "Gets lifted", "Hits"]] > 0).any(axis=1)
        | (out[["Bought", "Sold"]] > 0).any(axis=1)
    ]
    out = out.sort_values(COUNTERPARTY_COL).reset_index(drop=True)

    totals = {
        COUNTERPARTY_COL: "TOTAL ROW",
        "Bids": int(out["Bids"].sum()),
        "Gets hit": int(out["Gets hit"].sum()),
        "Lifts": int(out["Lifts"].sum()),
        "Bought": float(out["Bought"].sum()),
        "Offers": int(out["Offers"].sum()),
        "Gets lifted": int(out["Gets lifted"].sum()),
        "Hits": int(out["Hits"].sum()),
        "Sold": float(out["Sold"].sum()),
        "VWAP": _vwap(
            consummated["PRICE"],
            consummated["ORDER_QUANTITY_TOTAL"],
        ),
    }
    out = pd.concat([out, pd.DataFrame([totals])], ignore_index=True)
    return out


def format_activity_table(df: pd.DataFrame) -> pd.DataFrame:
    """Pretty-print numeric columns like the email report."""
    display = df.copy()
    for col in ("Bought", "Sold"):
        if col in display.columns:
            display[col] = display[col].apply(lambda x: f"{x:,.0f}" if x else "0")
    if "VWAP" in display.columns:
        def _fmt_vwap(x):
            if pd.isna(x) or x == 0:
                return "0.00"
            return f"{x:.2f}"

        display["VWAP"] = display["VWAP"].apply(_fmt_vwap)
        # total row with no consummated flow reads as nan in the legacy report
        if not display.empty and display.iloc[-1]["VWAP"] == "0.00":
            trade_cols = ["Gets hit", "Lifts", "Hits", "Gets lifted"]
            if display.iloc[-1][trade_cols].astype(str).eq("0").all():
                display.iloc[-1, display.columns.get_loc("VWAP")] = "nan"
    return display
