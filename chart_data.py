"""Additional Snowflake queries for report charts."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from report_config import (
    ARA_10PPM_PAPER_HUB,
    ASIA_GO_AG_HUB,
    ASIA_GO_PAPER_HUB,
    ASIA_GO_SING_HUB,
    MEC_PARTIALS_HUB,
    MOPL_SYMBOL,
    PRICEDATA_TABLE,
    TABLE,
    WindowReportConfig,
)
from snowflake_data import get_connection


def _run_query(sql: str, params: list | None = None) -> pd.DataFrame:
    conn = get_connection()
    cur = conn._conn.cursor()
    cur.execute(sql, params or [])
    df = cur.fetch_pandas_all()
    conn._conn.close()
    return df


def fetch_mopl_curve(
    report_date: date,
    symbol: str = MOPL_SYMBOL,
    lookback_days: int = 180,
) -> pd.DataFrame:
    """Daily Platts MOPL differential — official Curves section input."""
    start = report_date - timedelta(days=lookback_days)
    sql = f"""
        SELECT
            ASSESSDATE::date AS TRADE_DATE,
            VALUE AS MOPL_DIFF
        FROM {PRICEDATA_TABLE}
        WHERE SYMBOL = %s
          AND BATE = 'c'
          AND ASSESSDATE::date >= %s
          AND ASSESSDATE::date < %s
        ORDER BY 1
    """
    return _run_query(sql, [symbol, start.isoformat(), report_date.isoformat()])


def fetch_daily_volume(
    config: WindowReportConfig,
    report_date: date,
    lookback_days: int = 120,
    *,
    all_products: bool = False,
) -> pd.DataFrame:
    """Daily consummated volume in kt. Optionally aggregate all products on the hub."""
    start = report_date - timedelta(days=lookback_days)
    product_clause = "" if all_products else "AND PRODUCT = %s"
    sql = f"""
        SELECT
            ORDER_DATE::date AS TRADE_DATE,
            SUM(ORDER_QUANTITY_TOTAL) / 1000 AS VOLUME_KT
        FROM {TABLE}
        WHERE ORDER_CANCELLED = 'F'
          AND ORDER_STATE = 'consummated'
          AND MARKET = %s
          AND HUB = %s
          {product_clause}
          AND ORDER_DATE::date BETWEEN %s AND %s
        GROUP BY 1
        ORDER BY 1
    """
    params: list = [config.market, config.hub]
    if not all_products:
        params.append(config.product)
    params.extend([start.isoformat(), report_date.isoformat()])
    return _run_query(sql, params)


def fetch_cycle_flows(
    config: WindowReportConfig,
    report_date: date,
    cycle_start: date | None = None,
    *,
    all_products: bool = False,
    strip: str | None = None,
) -> pd.DataFrame:
    """Buyer/seller flows for Sankey — defaults to calendar month-to-date."""
    start = cycle_start or report_date.replace(day=1)
    product_clause = "" if all_products else "AND PRODUCT = %s"
    strip_clause = "AND STRIP = %s" if strip else ""
    sql = f"""
        SELECT
            SELLER_MNEMONIC,
            BUYER_MNEMONIC,
            SUM(ORDER_QUANTITY_TOTAL) / 1000 AS VOLUME_KT
        FROM {TABLE}
        WHERE ORDER_CANCELLED = 'F'
          AND ORDER_STATE = 'consummated'
          AND MARKET = %s
          AND HUB = %s
          {product_clause}
          {strip_clause}
          AND ORDER_DATE::date BETWEEN %s AND %s
          AND SELLER_MNEMONIC IS NOT NULL
          AND BUYER_MNEMONIC IS NOT NULL
        GROUP BY 1, 2
        HAVING SUM(ORDER_QUANTITY_TOTAL) > 0
        ORDER BY 3 DESC
    """
    params: list = [config.market, config.hub]
    if not all_products:
        params.append(config.product)
    if strip:
        params.append(strip)
    params.extend([start.isoformat(), report_date.isoformat()])
    return _run_query(sql, params)


def laycan_label(begin: object, end: object) -> str | None:
    """Format a cargo laycan window as '18-22Jun' (ORDER_BEGIN / ORDER_END)."""
    b = pd.to_datetime(begin, errors="coerce")
    e = pd.to_datetime(end, errors="coerce")
    if pd.isna(b) or pd.isna(e):
        return None
    return f"{b.day:02d}-{e.day:02d}{e.strftime('%b')}"


def strip_title(raw: pd.DataFrame, strip_code: str) -> str:
    """Build chart subtitle like '30-04Jul' from ORDER_BEGIN / ORDER_END."""
    subset = raw[raw["STRIP"] == strip_code]
    if subset.empty:
        return strip_code
    begin = pd.to_datetime(subset["ORDER_BEGIN"].dropna().min(), errors="coerce")
    end = pd.to_datetime(subset["ORDER_END"].dropna().max(), errors="coerce")
    label = laycan_label(begin, end)
    return label if label else strip_code

def discover_ulsd_cif_activity(report_date: date) -> pd.DataFrame:
    """
    All ULSD CIF-related eWindow instruments active on a report date.

    Returns one row per (MARKET, HUB, PRODUCT, STRIP, C1_PRICE_BASIS).
    """
    sql = f"""
        SELECT
            MARKET,
            HUB,
            PRODUCT,
            STRIP,
            C1_PRICE_BASIS,
            COUNT(*) AS N_ROWS,
            COUNT(DISTINCT ORDER_ID) AS N_ORDERS
        FROM {TABLE}
        WHERE ORDER_CANCELLED = 'F'
          AND ORDER_DATE::date = %s
          AND (
            (
              MARKET = 'EU MidDist Derivative'
              AND UPPER(HUB) LIKE '%%ULSD%%CIF%%'
            )
            OR (
              MARKET = 'EU MidDist PVO'
              AND UPPER(PRODUCT) LIKE '%%ULSD%%'
              AND (
                UPPER(HUB) LIKE 'CIF BASIS%%'
                OR UPPER(HUB) LIKE 'FOB BASIS ARA%%'
              )
            )
          )
        GROUP BY 1, 2, 3, 4, 5
        ORDER BY MARKET, HUB, PRODUCT, STRIP, C1_PRICE_BASIS
    """
    return _run_query(sql, [report_date.isoformat()])


def discover_ara_10ppm_paper(report_date: date) -> pd.DataFrame:
    """
    Day-varying ARA 10ppm Paper strips (Bal Month / month codes).

    Hub: Diesel 10ppm FOB ARA Barges/LS GO 1st Line under EU MidDist Derivative.
    Quiet days return an empty frame (legacy still shows an empty Paper heading).
    """
    sql = f"""
        SELECT
            MARKET,
            HUB,
            PRODUCT,
            STRIP,
            COUNT(*) AS N_ROWS,
            COUNT(DISTINCT ORDER_ID) AS N_ORDERS,
            MIN(ORDER_TIME) AS FIRST_TIME
        FROM {TABLE}
        WHERE ORDER_CANCELLED = 'F'
          AND ORDER_DATE::date = %s
          AND MARKET = 'EU MidDist Derivative'
          AND HUB = %s
        GROUP BY 1, 2, 3, 4
        ORDER BY FIRST_TIME, STRIP
    """
    return _run_query(sql, [report_date.isoformat(), ARA_10PPM_PAPER_HUB])


def discover_asia_go_activity(report_date: date) -> pd.DataFrame:
    """
    Asia Gasoil instruments active on a report date.

    Returns one row per (MARKET, HUB, PRODUCT, STRIP, C1_PRICE_BASIS) for:
    - ASIA MD (PVO) Sing/AG cargoes (excl. Jet)
    - ASIA MidDist Swap Singapore Gasoil Paper strips
    """
    sql = f"""
        SELECT
            MARKET,
            HUB,
            PRODUCT,
            STRIP,
            C1_PRICE_BASIS,
            COUNT(*) AS N_ROWS,
            COUNT(DISTINCT ORDER_ID) AS N_ORDERS,
            MIN(ORDER_TIME) AS FIRST_TIME
        FROM {TABLE}
        WHERE ORDER_CANCELLED = 'F'
          AND ORDER_DATE::date = %s
          AND (
            (
              MARKET = 'ASIA MD (PVO)'
              AND HUB IN (%s, %s)
              AND UPPER(PRODUCT) LIKE 'PLATTS GO%%'
              AND UPPER(PRODUCT) NOT LIKE '%%JET%%'
            )
            OR (
              MARKET = 'ASIA MidDist Swap'
              AND HUB = %s
            )
          )
        GROUP BY 1, 2, 3, 4, 5
        ORDER BY MARKET, HUB, PRODUCT, STRIP, C1_PRICE_BASIS
    """
    return _run_query(
        sql,
        [
            report_date.isoformat(),
            ASIA_GO_SING_HUB,
            ASIA_GO_AG_HUB,
            ASIA_GO_PAPER_HUB,
        ],
    )

def discover_me_crude_partials_strip(report_date: date) -> str | None:
    """Front-month strip with the most MEC Partials activity on the day."""
    sql = f"""
        SELECT STRIP, COUNT(DISTINCT ORDER_ID) AS N_ORDERS
        FROM {TABLE}
        WHERE ORDER_CANCELLED = 'F'
          AND ORDER_DATE::date = %s
          AND MARKET = 'ASIA Crude Partial'
          AND HUB = %s
          AND PRODUCT = 'Platts Dubai'
        GROUP BY 1
        ORDER BY N_ORDERS DESC, STRIP
        LIMIT 1
    """
    df = _run_query(sql, [report_date.isoformat(), MEC_PARTIALS_HUB])
    if df.empty:
        return None
    return str(df.iloc[0]["STRIP"])


def discover_me_crude_cargoes(report_date: date) -> pd.DataFrame:
    """Day-varying ASIA Crude Cargo grades (hub/product/strip/basis)."""
    sql = f"""
        SELECT
            MARKET,
            HUB,
            PRODUCT,
            STRIP,
            C1_PRICE_BASIS,
            COUNT(*) AS N_ROWS,
            COUNT(DISTINCT ORDER_ID) AS N_ORDERS,
            MIN(ORDER_TIME) AS FIRST_TIME
        FROM {TABLE}
        WHERE ORDER_CANCELLED = 'F'
          AND ORDER_DATE::date = %s
          AND MARKET = 'ASIA Crude Cargo'
        GROUP BY 1, 2, 3, 4, 5
        ORDER BY FIRST_TIME, HUB, PRODUCT, STRIP
    """
    return _run_query(sql, [report_date.isoformat()])


def fetch_assessment_value(
    symbol: str,
    as_of: date,
    *,
    lookback_days: int = 10,
) -> float | None:
    """Latest Platts PRICEDATA close (bate=c) on or before as_of."""
    start = as_of - timedelta(days=lookback_days)
    sql = f"""
        SELECT ASSESSDATE::date AS D, VALUE
        FROM {PRICEDATA_TABLE}
        WHERE SYMBOL = %s
          AND BATE = 'c'
          AND ASSESSDATE::date BETWEEN %s AND %s
        ORDER BY ASSESSDATE DESC
        LIMIT 1
    """
    df = _run_query(sql, [symbol, start.isoformat(), as_of.isoformat()])
    if df.empty or pd.isna(df.iloc[0]["VALUE"]):
        return None
    return float(df.iloc[0]["VALUE"])


def fetch_me_crude_convergence_flow(
    report_date: date,
    strip: str,
    *,
    cargo_lot_bbl: int = 500_000,
) -> pd.DataFrame:
    """
    Optional Convergence sankey: one cargo-lot flow when the day has cargo quotes
    and consummated MEC partial volume of at least one cargo lot.

    Seller = cargo market-maker (offer preferred); buyer = largest partial buyer.
    VOLUME_KT is 1.0 so labels can show (+/-1) cargo units.
    """
    cargo_sql = f"""
        SELECT MARKET_MAKER_MNEMONIC, ORDER_TYPE, ORDER_QUANTITY_TOTAL
        FROM {TABLE}
        WHERE ORDER_CANCELLED = 'F'
          AND ORDER_DATE::date = %s
          AND MARKET = 'ASIA Crude Cargo'
        ORDER BY ORDER_TIME
    """
    cargo = _run_query(cargo_sql, [report_date.isoformat()])
    if cargo.empty:
        return pd.DataFrame(columns=["SELLER_MNEMONIC", "BUYER_MNEMONIC", "VOLUME_KT"])

    partial_sql = f"""
        SELECT
            BUYER_MNEMONIC,
            SELLER_MNEMONIC,
            SUM(ORDER_QUANTITY_TOTAL) AS QTY
        FROM {TABLE}
        WHERE ORDER_CANCELLED = 'F'
          AND ORDER_STATE = 'consummated'
          AND ORDER_DATE::date = %s
          AND MARKET = 'ASIA Crude Partial'
          AND HUB = %s
          AND PRODUCT = 'Platts Dubai'
          AND STRIP = %s
          AND BUYER_MNEMONIC IS NOT NULL
          AND SELLER_MNEMONIC IS NOT NULL
        GROUP BY 1, 2
    """
    flows = _run_query(
        partial_sql, [report_date.isoformat(), MEC_PARTIALS_HUB, strip]
    )
    if flows.empty:
        return pd.DataFrame(columns=["SELLER_MNEMONIC", "BUYER_MNEMONIC", "VOLUME_KT"])

    total_qty = float(flows["QTY"].sum())
    if total_qty < cargo_lot_bbl:
        return pd.DataFrame(columns=["SELLER_MNEMONIC", "BUYER_MNEMONIC", "VOLUME_KT"])

    cargo = cargo.copy()
    cargo["ORDER_TYPE"] = cargo["ORDER_TYPE"].str.lower()
    offers = cargo.loc[cargo["ORDER_TYPE"] == "offer", "MARKET_MAKER_MNEMONIC"]
    bids = cargo.loc[cargo["ORDER_TYPE"] == "bid", "MARKET_MAKER_MNEMONIC"]
    cargo_party = (
        str(offers.iloc[0]) if not offers.empty else (str(bids.iloc[0]) if not bids.empty else None)
    )
    if not cargo_party:
        return pd.DataFrame(columns=["SELLER_MNEMONIC", "BUYER_MNEMONIC", "VOLUME_KT"])

    bought = flows.groupby("BUYER_MNEMONIC")["QTY"].sum().sort_values(ascending=False)
    sold = flows.groupby("SELLER_MNEMONIC")["QTY"].sum().sort_values(ascending=False)
    if not offers.empty:
        seller, buyer = cargo_party, str(bought.index[0])
    else:
        buyer, seller = cargo_party, str(sold.index[0])
    if seller == buyer:
        return pd.DataFrame(columns=["SELLER_MNEMONIC", "BUYER_MNEMONIC", "VOLUME_KT"])

    return pd.DataFrame(
        [{"SELLER_MNEMONIC": seller, "BUYER_MNEMONIC": buyer, "VOLUME_KT": 1.0}]
    )
