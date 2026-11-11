"""Plotly charts matching the legacy ARA 10ppm window report."""

from __future__ import annotations

import base64
import math
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go

from report_config import WindowReportConfig

BID_COLORS = ["#1f3a5f", "#4a7fb8", "#8eb4e3", "#2c5282", "#63a4ff"]
OFFER_COLORS = ["#7a2e1a", "#b5651d", "#e8944a", "#c05621", "#f6ad55"]


def _fig_to_png_uri(fig: go.Figure, width: int = 700, height: int = 500) -> str:
    png_bytes = fig.to_image(format="png", width=width, height=height, scale=2)
    encoded = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _as_py_datetime(series: pd.Series) -> list:
    return [ts.to_pydatetime() for ts in pd.to_datetime(series)]


def _color_map(parties: list[str], order_type: str) -> dict[str, str]:
    palette = BID_COLORS if order_type.lower() == "bid" else OFFER_COLORS
    return {party: palette[i % len(palette)] for i, party in enumerate(sorted(set(parties)))}


def _flow_volume_label(name: str, net_kt: float, *, label_scale: float = 1000) -> str:
    """Legacy Sankey labels: STR (-92,100) / TRAFI (+89,000) in kt×1000 units.

    Convergence cargo charts pass label_scale=1 so VOLUME_KT=1.0 renders as (+1).
    """
    units = int(round(abs(net_kt) * label_scale))
    sign = "+" if net_kt >= 0 else "-"
    return f"{name} ({sign}{units:,})"


def _floor_10min(ts: pd.Timestamp) -> datetime:
    ts = pd.Timestamp(ts)
    minute = (ts.minute // 10) * 10
    return ts.replace(minute=minute, second=0, microsecond=0).to_pydatetime()


def _ceil_10min(ts: pd.Timestamp) -> datetime:
    ts = pd.Timestamp(ts)
    floored = ts.replace(minute=(ts.minute // 10) * 10, second=0, microsecond=0)
    if ts > floored:
        floored += timedelta(minutes=10)
    return floored.to_pydatetime()


def _is_open_window(window_state: object) -> bool:
    """True when WINDOW_STATE is the pre-MOC 'Open' phase."""
    if window_state is None or pd.isna(window_state):
        return False
    return str(window_state).strip().lower() == "open"


def _prepare_intraday_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Drop duplicate window-close rows and prefer explicit withdraw over inactive."""
    out = df.copy()
    out["ORDER_TIME"] = pd.to_datetime(out["ORDER_TIME"])
    out["UPDATE_TIME"] = pd.to_datetime(out["UPDATE_TIME"])
    out["ORDER_TYPE"] = out["ORDER_TYPE"].str.lower()
    out["ORDER_STATE"] = out["ORDER_STATE"].str.lower()
    out["PRICE"] = out["PRICE"].astype(float)

    keep_idx: list[int] = []
    for _, grp in out.groupby("ORDER_ID"):
        grp = grp.sort_values(["ORDER_TIME", "UPDATE_TIME"])
        has_withdrawn = (grp["ORDER_STATE"] == "withdrawn").any()
        seen_inactive = False
        for idx, row in grp.iterrows():
            state = row["ORDER_STATE"]
            if state == "inactive":
                if has_withdrawn:
                    continue
                if seen_inactive:
                    continue
                if row["UPDATE_TIME"].date() != row["ORDER_TIME"].date():
                    continue
                seen_inactive = True
            keep_idx.append(idx)
    return out.loc[sorted(keep_idx)].reset_index(drop=True)


def _order_lifecycle_rows(order_rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split one order into active price steps vs terminal markers."""
    order_rows = order_rows.sort_values(["ORDER_TIME", "UPDATE_TIME"])
    active = order_rows[order_rows["ORDER_STATE"] == "active"].copy()
    terminals = order_rows[order_rows["ORDER_STATE"].isin(["consummated", "withdrawn", "inactive"])].copy()
    return active, terminals


def _append_quote_point(
    times: list[datetime],
    prices: list[float],
    conditional: list[bool],
    t: datetime,
    p: float,
    is_conditional: bool,
) -> None:
    """Append a quote only when time/price actually changes (avoids duplicate dots)."""
    if times and times[-1] == t and prices[-1] == p:
        # Prefer keeping a conditional flag if either copy is conditional.
        if is_conditional and conditional:
            conditional[-1] = True
        return
    times.append(t)
    prices.append(p)
    conditional.append(is_conditional)


def _append_conditional_stub(
    times: list[datetime],
    prices: list[float],
    conditional: list[bool],
    first_t: datetime,
    first_p: float,
) -> None:
    """
    Legacy PVO treatment: 2–3 hollow markers in a small yellow box at quote start.

    Snowflake often has a single Open print; Yves still drew a short hollow stub.
    """
    stub_times = [
        first_t - timedelta(minutes=2),
        first_t - timedelta(minutes=1),
        first_t,
    ]
    for t in stub_times:
        _append_quote_point(times, prices, conditional, t, first_p, True)


def _build_party_intention_line(
    party_rows: pd.DataFrame,
    *,
    enable_conditional: bool = False,
) -> tuple[list[datetime], list[float], list[bool], list[dict]]:
    """
    One interpolated quote path per market-maker / side.

    Active rows are quote markers; terminals become Hit/Lifted/Withdrawn and
    extend the line to the event time so the path stays continuous.

    When enable_conditional (PVO/CIF only), an order that *starts* in WINDOW_STATE
    Open gets a short hollow stub + yellow box — not hollow markers for the whole
    Open lifetime (legacy barge charts never use this).
    """
    party_rows = _prepare_intraday_rows(party_rows)
    if party_rows.empty:
        return [], [], [], []

    quote_times: list[datetime] = []
    quote_prices: list[float] = []
    quote_conditional: list[bool] = []
    markers: list[dict] = []

    order_groups = list(party_rows.groupby("ORDER_ID"))
    order_groups.sort(key=lambda item: item[1]["ORDER_TIME"].min())

    for _, order_rows in order_groups:
        active, terminals = _order_lifecycle_rows(order_rows)
        side = order_rows["ORDER_TYPE"].iloc[0]

        stub_applied = False
        for _, row in active.iterrows():
            t = row["ORDER_TIME"].to_pydatetime()
            p = float(row["PRICE"])
            starts_open = _is_open_window(row.get("WINDOW_STATE"))
            # Only the first print of an Open-started order is the conditional stub.
            if enable_conditional and starts_open and not stub_applied:
                _append_conditional_stub(
                    quote_times, quote_prices, quote_conditional, t, p
                )
                stub_applied = True
                continue
            # Any active print locks out a later mid-path stub.
            stub_applied = True
            _append_quote_point(
                quote_times,
                quote_prices,
                quote_conditional,
                t,
                p,
                False,
            )

        for _, row in terminals.iterrows():
            t = row["ORDER_TIME"].to_pydatetime()
            p = float(row["PRICE"])
            state = row["ORDER_STATE"]
            if state == "consummated":
                kind = "hit" if side == "bid" else "lifted"
                markers.append({"time": t, "price": p, "kind": kind})
            elif state == "withdrawn":
                # Explicit withdraw only — kerb/window-close inactive is not an X.
                markers.append({"time": t, "price": p, "kind": "withdrawn"})
            # inactive (kerb): extend the path for continuity, no withdrawn marker.
            _append_quote_point(
                quote_times,
                quote_prices,
                quote_conditional,
                t,
                p,
                False,
            )

    return quote_times, quote_prices, quote_conditional, markers


def _add_conditional_quote_boxes(
    fig: go.Figure,
    quote_times: list[datetime],
    quote_prices: list[float],
    quote_conditional: list[bool],
) -> None:
    """Compact yellow highlight around the initial conditional stub only."""
    cond_times = [t for t, c in zip(quote_times, quote_conditional) if c]
    cond_prices = [p for p, c in zip(quote_prices, quote_conditional) if c]
    if not cond_times:
        return
    # Tight pad matches legacy ~few-minute box around the hollow stub.
    pad_t = timedelta(seconds=45)
    pad_p = 0.15
    fig.add_shape(
        type="rect",
        x0=min(cond_times) - pad_t,
        x1=max(cond_times) + pad_t,
        y0=min(cond_prices) - pad_p,
        y1=max(cond_prices) + pad_p,
        fillcolor="rgba(255, 235, 59, 0.45)",
        line=dict(color="#744210", width=1),
        layer="below",
    )


def plot_curve(mopl_df: pd.DataFrame, config: WindowReportConfig, report_date: date) -> go.Figure:
    df = mopl_df.copy()
    df["TRADE_DATE"] = pd.to_datetime(df["TRADE_DATE"])
    x = _as_py_datetime(df["TRADE_DATE"])
    y = df["MOPL_DIFF"].astype(float)
    label = config.mopl_label or "MOPL"
    curve_title = config.curve_title or f"{config.short_title or config.name} (v MOPL)"
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=x,
            y=y,
            mode="lines",
            line=dict(color="#4a5568", width=2),
            name="MOPL",
        )
    )
    if not df.empty:
        last = df.iloc[-1]
        print_date = last["TRADE_DATE"]
        print_value = float(last["MOPL_DIFF"])
        # Match legacy layout: latest-print callout anchored bottom-left inside plot.
        fig.add_annotation(
            xref="paper",
            yref="paper",
            x=0.01,
            y=0.04,
            xanchor="left",
            yanchor="bottom",
            text=(
                f"<b>{label}:</b> {print_value:.2f} "
                f"({print_date:%d-%b-%y})"
            ),
            showarrow=False,
            align="left",
            bgcolor="rgba(255,255,255,0.85)",
            bordercolor="#cbd5e0",
            borderwidth=1,
            font=dict(size=11, color="#2d3748"),
        )
    fig.update_layout(
        title=dict(
            text=f"{curve_title} | {report_date:%d-%b-%Y}",
            x=0,
            xanchor="left",
        ),
        xaxis_title="Date",
        yaxis=dict(title="", dtick=20, gridcolor="#e2e8f0"),
        template="plotly_white",
        height=500,
        width=700,
        margin=dict(l=50, r=30, t=60, b=50),
        showlegend=False,
    )
    return fig


def plot_daily_volume(
    volume_df: pd.DataFrame,
    config: WindowReportConfig,
    *,
    volume_unit: str = "kt",
) -> go.Figure:
    df = volume_df.copy()
    df["TRADE_DATE"] = pd.to_datetime(df["TRADE_DATE"])
    df["VOLUME_KT"] = df["VOLUME_KT"].astype(float)
    df["MA5"] = df["VOLUME_KT"].rolling(5, min_periods=1).mean()
    x = _as_py_datetime(df["TRADE_DATE"])

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=x,
            y=df["VOLUME_KT"],
            name="Daily Volume",
            marker_color="#4a5568",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=df["MA5"],
            name="5-Day MA",
            mode="lines",
            line=dict(color="#dd6b20", width=2),
        )
    )
    if not df.empty:
        last = df.iloc[-1]
        fig.add_trace(
            go.Scatter(
                x=[last["TRADE_DATE"].to_pydatetime()],
                y=[last["VOLUME_KT"]],
                mode="markers",
                marker=dict(color="#9b2c2c", size=10),
                name="Last",
            )
        )
    fig.update_layout(
        title=f"{config.short_title or config.name} | Daily Volume ({volume_unit})",
        xaxis_title="Date",
        yaxis_title="Volume",
        template="plotly_white",
        height=500,
        width=700,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=1, xanchor="right"),
        margin=dict(l=50, r=30, t=60, b=50),
    )
    return fig


def plot_sankey_flows(
    flows_df: pd.DataFrame,
    config: WindowReportConfig,
    report_date: date,
    *,
    volume_unit: str = "kt",
    label_scale: float = 1000,
    chart_title: str | None = None,
) -> go.Figure:
    df = flows_df.copy()
    default_title = (
        f"{config.short_title or config.name} Flows ({volume_unit}) | Cycle-to-Date | {report_date:%d-%b-%Y}"
    )
    if df.empty:
        fig = go.Figure()
        fig.update_layout(
            title=chart_title
            or f"{config.short_title or config.name} Flows ({volume_unit}) | No trades in cycle"
        )
        return fig

    sold = df.groupby("SELLER_MNEMONIC")["VOLUME_KT"].sum()
    bought = df.groupby("BUYER_MNEMONIC")["VOLUME_KT"].sum()
    parties = sorted(set(sold.index) | set(bought.index))
    net = {p: float(bought.get(p, 0) - sold.get(p, 0)) for p in parties}
    labels = [_flow_volume_label(p, net[p], label_scale=label_scale) for p in parties]
    label_idx = {name: i for i, name in enumerate(parties)}

    source, target, value = [], [], []
    for row in df.itertuples():
        source.append(label_idx[row.SELLER_MNEMONIC])
        target.append(label_idx[row.BUYER_MNEMONIC])
        value.append(float(row.VOLUME_KT))

    fig = go.Figure(
        go.Sankey(
            arrangement="snap",
            node=dict(
                label=labels,
                pad=18,
                thickness=18,
            ),
            link=dict(source=source, target=target, value=value, color="rgba(160,174,192,0.45)"),
        )
    )
    fig.update_layout(
        title=chart_title or default_title,
        height=520,
        width=850,
        margin=dict(l=20, r=100, t=60, b=20),
    )
    return fig


def plot_intraday_strip(
    raw: pd.DataFrame,
    strip_code: str,
    strip_label: str,
    report_date: date,
    config: WindowReportConfig | None = None,
    *,
    price_offset: float | None = None,
    reference_price: float | None = None,
) -> go.Figure:
    df = raw[raw["STRIP"] == strip_code].copy() if "STRIP" in raw.columns else raw.copy()
    fig = go.Figure()
    title_prefix = (config.short_title if config and config.short_title else "Window")
    basis_bit = ""
    if config and config.c1_price_basis:
        # Legacy titles look like: "... (vs EFP ICE LS GO, Month Jul), 13-17Jul"
        period = ""
        if "C1_BASIS_PERIOD" in df.columns and df["C1_BASIS_PERIOD"].notna().any():
            period = str(df["C1_BASIS_PERIOD"].dropna().iloc[0])
        details = ""
        if "C1_BASIS_PERIOD_DETAILS" in df.columns and df["C1_BASIS_PERIOD_DETAILS"].notna().any():
            details = str(df["C1_BASIS_PERIOD_DETAILS"].dropna().iloc[0])
        extra = ", ".join(x for x in (period, details) if x)
        basis_bit = f" (vs {config.c1_price_basis}" + (f", {extra}" if extra else "") + ")"
    chart_title = (
        f"{title_prefix}{basis_bit}, {strip_label} | {report_date:%d-%b-%Y}"
        if basis_bit
        else f"{title_prefix} {strip_label} | {report_date:%d-%b-%Y}"
    )

    if df.empty:
        fig.update_layout(title=f"{chart_title} — no data")
        return fig

    # Normalised charts: PRICE − Dubai Mo03 (legacy title still says "Dubai M2").
    if price_offset is not None:
        df = df.copy()
        df["PRICE"] = pd.to_numeric(df["PRICE"], errors="coerce") - float(price_offset)

    df = _prepare_intraday_rows(df)
    bid_parties = df.loc[df["ORDER_TYPE"] == "bid", "MARKET_MAKER_MNEMONIC"].dropna().unique().tolist()
    offer_parties = df.loc[df["ORDER_TYPE"] == "offer", "MARKET_MAKER_MNEMONIC"].dropna().unique().tolist()
    bid_colors = _color_map(bid_parties, "bid")
    offer_colors = _color_map(offer_parties, "offer")
    seen_legend: set[str] = set()

    marker_styles = {
        "hit": dict(symbol="triangle-down", color="#e53e3e", size=11),
        "lifted": dict(symbol="triangle-up", color="#38a169", size=11),
        "withdrawn": dict(symbol="x", color="#718096", size=11, line=dict(width=2, color="#1a202c")),
    }
    marker_points: dict[str, list[tuple[datetime, float]]] = {k: [] for k in marker_styles}

    # Legacy yellow/hollow stub is PVO/CIF only (C1_PRICE), never ARA barge charts.
    enable_conditional = bool(config and config.price_field == "C1_PRICE")

    for (party, side), party_rows in df.groupby(["MARKET_MAKER_MNEMONIC", "ORDER_TYPE"]):
        quote_times, quote_prices, quote_conditional, markers = _build_party_intention_line(
            party_rows,
            enable_conditional=enable_conditional,
        )
        if not quote_times:
            continue

        color = bid_colors.get(party, "#4a5568") if side == "bid" else offer_colors.get(party, "#b5651d")
        legend_key = f"{party} ({side.title()})"
        show = legend_key not in seen_legend
        seen_legend.add(legend_key)
        if enable_conditional:
            _add_conditional_quote_boxes(fig, quote_times, quote_prices, quote_conditional)
        marker_symbols = [
            "circle-open" if is_cond else "circle" for is_cond in quote_conditional
        ]
        fig.add_trace(
            go.Scatter(
                x=quote_times,
                y=quote_prices,
                mode="lines+markers",
                line=dict(color=color, width=2, shape="linear"),
                marker=dict(
                    color=color,
                    size=7,
                    symbol=marker_symbols,
                    line=dict(width=1.5, color=color),
                ),
                name=legend_key,
                legendgroup=legend_key,
                showlegend=show,
            )
        )
        for m in markers:
            marker_points[m["kind"]].append((m["time"], m["price"]))

    for kind, style in marker_styles.items():
        pts = marker_points[kind]
        if not pts:
            continue
        fig.add_trace(
            go.Scatter(
                x=[p[0] for p in pts],
                y=[p[1] for p in pts],
                mode="markers",
                marker=style,
                name=kind.title() if kind != "hit" else "Hit",
                legendgroup=kind,
                showlegend=True,
            )
        )

    x_min = _floor_10min(df["ORDER_TIME"].min())
    # Pad past the last print so 15:30 Hit/Lift/Withdrawn markers are not clipped.
    x_max = _ceil_10min(df["ORDER_TIME"].max()) + timedelta(minutes=2)
    if enable_conditional:
        # Conditional stub inserts points ~2 minutes before the first Open print.
        x_min = min(x_min, _floor_10min(df["ORDER_TIME"].min() - timedelta(minutes=3)))

    y_min = float(df["PRICE"].min())
    y_max = float(df["PRICE"].max())
    if reference_price is not None:
        y_min = min(y_min, float(reference_price))
        y_max = max(y_max, float(reference_price))
        # Dotted horizontal line — legacy "Reference Price" (prior Cash Dubai Diff).
        fig.add_trace(
            go.Scatter(
                x=[x_min, x_max],
                y=[float(reference_price), float(reference_price)],
                mode="lines",
                line=dict(color="#1a202c", width=2, dash="dot"),
                name="Reference Price",
                legendgroup="reference_price",
                showlegend=True,
            )
        )

    fig.update_layout(
        title=chart_title,
        xaxis=dict(
            title="Time",
            tickformat="%H:%M",
            dtick=600_000,  # 10-minute ticks during the window
            range=[x_min, x_max],
            gridcolor="#e2e8f0",
        ),
        yaxis=dict(
            title="Price",
            dtick=1,
            # Pad past min/max so edge markers (at -1 / 4 etc.) are not clipped.
            range=[
                math.floor(y_min) - 0.5,
                math.ceil(y_max) + 0.5,
            ],
            gridcolor="#e2e8f0",
        ),
        template="plotly_white",
        height=500,
        width=700,
        legend=dict(font=dict(size=10)),
        margin=dict(l=50, r=140, t=60, b=50),
    )
    return fig


def build_all_charts(
    config: WindowReportConfig,
    report_date: date,
    mopl_df: pd.DataFrame,
    volume_df: pd.DataFrame,
    flows_df: pd.DataFrame,
    day_orders: pd.DataFrame,
    strip_titles: dict[str, str],
) -> dict[str, go.Figure]:
    charts = {
        "curve": plot_curve(mopl_df, config, report_date),
        "daily_volume": plot_daily_volume(volume_df, config),
        "sankey": plot_sankey_flows(flows_df, config, report_date),
    }
    for code in config.strip_labels:
        charts[f"intraday_{code}"] = plot_intraday_strip(
            day_orders,
            code,
            strip_titles.get(code, code),
            report_date,
            config=config,
        )
    return charts


def charts_to_html_images(charts: dict[str, go.Figure]) -> dict[str, str]:
    # Sankey is wider so middle/right labels have more breathing room.
    sizes = {"sankey": (850, 520)}
    out = {}
    for name, fig in charts.items():
        w, h = sizes.get(name, (700, 500))
        out[name] = _fig_to_png_uri(fig, width=w, height=h)
    return out
