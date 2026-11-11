"""Pull eWindow trade rows from Snowflake."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

# Reuse dash_irr_database credentials / connector
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "dash_irr_database"))

from _snowflake import SnowFlake  # noqa: E402

from report_config import SNOWFLAKE_DATABASE, SNOWFLAKE_SCHEMA, TABLE, WindowReportConfig  # noqa: E402

ORDER_COLUMNS = [
    "MARKET",
    "PRODUCT",
    "HUB",
    "STRIP",
    "ORDER_DATE",
    "ORDER_TIME",
    "ORDER_BEGIN",
    "ORDER_END",
    "UPDATE_TIME",
    "ORDER_ID",
    "ORDER_TYPE",
    "ORDER_STATE",
    "ORDER_STATE_DETAIL",
    "ORDER_CANCELLED",
    "MARKET_MAKER_MNEMONIC",
    "BUYER_MNEMONIC",
    "SELLER_MNEMONIC",
    "ORDER_QUANTITY_TOTAL",
    "PRICE",
    "PRICE_UNIT",
    "WINDOW_STATE",
    "DEAL_ID",
    "C1_PRICE",
    "C1_PRICE_BASIS",
    "C1_BASIS_PERIOD",
    "C1_BASIS_PERIOD_DETAILS",
]


def get_connection() -> SnowFlake:
    return SnowFlake(database=SNOWFLAKE_DATABASE, schema=SNOWFLAKE_SCHEMA)


def fetch_window_orders(
    config: WindowReportConfig,
    report_date: date,
    strip: str | None = None,
) -> pd.DataFrame:
    """Fetch non-cancelled orders for one report definition and date."""
    cols = ", ".join(ORDER_COLUMNS)
    sql = f"""
        SELECT {cols}
        FROM {TABLE}
        WHERE ORDER_CANCELLED = 'F'
          AND ORDER_DATE::date = %s
          AND MARKET = %s
          AND HUB = %s
          AND PRODUCT = %s
    """
    params: list = [report_date.isoformat(), config.market, config.hub, config.product]
    if strip is not None:
        sql += " AND STRIP = %s"
        params.append(strip)
    sql += " ORDER BY ORDER_TIME, ORDER_ID"

    conn = get_connection()
    cur = conn._conn.cursor()
    cur.execute(sql, params)
    df = cur.fetch_pandas_all()
    conn._conn.close()
    return df


def fetch_report_day(config: WindowReportConfig, report_date: date) -> dict[str, pd.DataFrame]:
    """Return one DataFrame per strip section (FE / MW / BE)."""
    return {
        code: fetch_window_orders(config, report_date, strip=code)
        for code in config.strip_labels
    }


def normalize_orders(df: pd.DataFrame, config: WindowReportConfig) -> pd.DataFrame:
    """
    Apply PVO filters and map C1_PRICE onto PRICE when needed.

    Platts Variable Oil (basis) quotes often store the differential in C1_PRICE
    while PRICE is zero; C1_PRICE_BASIS distinguishes EFP vs cargo differentials.
    """
    out = df.copy()
    if out.empty:
        return out
    if config.c1_price_basis and "C1_PRICE_BASIS" in out.columns:
        out = out[out["C1_PRICE_BASIS"] == config.c1_price_basis].copy()
    if config.price_field == "C1_PRICE" and "C1_PRICE" in out.columns:
        out["PRICE"] = pd.to_numeric(out["C1_PRICE"], errors="coerce")
    return out.reset_index(drop=True)
