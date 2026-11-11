"""Build multi-section ULSD CIF window HTML with day-driven discovery + Paper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from aggregate import build_counterparty_table
from chart_data import (
    discover_ulsd_cif_activity,
    fetch_cycle_flows,
    fetch_daily_volume,
    fetch_mopl_curve,
    laycan_label,
    strip_title,
)
from report_charts import (
    charts_to_html_images,
    plot_curve,
    plot_daily_volume,
    plot_intraday_strip,
    plot_sankey_flows,
)
from report_config import (
    ULSD_CIF_MED_DERIV,
    ULSD_CIF_NWE_DERIV,
    ULSD_FOB_ARA_FLOW,
    WindowReportConfig,
)
from render_html import render_composite_report, render_pack_section, render_strip_section
from snowflake_data import fetch_window_orders, normalize_orders


def _activity_key(config: WindowReportConfig) -> str:
    basis = (config.c1_price_basis or "price").replace(" ", "_")
    strip = next(iter(config.strip_labels), "strip")
    return f"{config.hub}_{config.product}_{strip}_{basis}".replace(" ", "_")


def _pvo_short_title(hub: str, product: str, strip: str) -> str:
    """Human chart prefix from hub/product, matching legacy titles."""
    port = hub.replace("CIF Basis ", "").replace("FOB Basis ", "")
    if "NWE" in product.upper():
        return f"ULSD NWE CIF Basis {port} {strip}"
    if "MED" in product.upper():
        return f"ULSD Med CIF Basis {port} {strip}"
    if "ARA" in product.upper():
        return f"ULSD FOB Basis ARA {strip}"
    return f"{hub} {strip}"


def _deriv_short_title(hub: str) -> str:
    if "MED" in hub.upper():
        return "ULSD CIF Med"
    return "ULSD CIF NWE"


def _pvo_region(product: str, hub: str) -> str | None:
    if hub.upper().startswith("FOB BASIS ARA"):
        return "FOB ARA"
    if "NWE" in product.upper():
        return "CIF NWE"
    if "MED" in product.upper():
        return "CIF Med"
    return None


def _configs_from_discovery(activity: pd.DataFrame) -> dict[str, list[WindowReportConfig]]:
    """
    Build activity configs for CIF NWE / CIF Med / FOB ARA / Paper from one day's rows.

    Paper = derivative strips; other sections = PVO basis instruments.
    """
    sections: dict[str, list[WindowReportConfig]] = {
        "CIF NWE": [],
        "CIF Med": [],
        "FOB ARA": [],
        "Paper": [],
    }
    if activity.empty:
        return sections

    for row in activity.itertuples(index=False):
        market = row.MARKET
        hub = row.HUB
        product = row.PRODUCT
        strip = row.STRIP
        basis = row.C1_PRICE_BASIS if pd.notna(row.C1_PRICE_BASIS) else None

        if market == "EU MidDist Derivative":
            short = _deriv_short_title(hub)
            # Include product hint when balmo / non-standard.
            name_extra = "" if product == "Platts Gasoil Diff" else f" ({product})"
            cfg = WindowReportConfig(
                name=f"{short} {strip}{name_extra}",
                market=market,
                hub=hub,
                product=product,
                strip_labels={strip: strip},
                short_title=f"{short} {strip}",
                price_field="PRICE",
            )
            sections["Paper"].append(cfg)
            continue

        if market == "EU MidDist PVO":
            region = _pvo_region(product, hub)
            if region is None:
                continue
            short = _pvo_short_title(hub, product, strip)
            cfg = WindowReportConfig(
                name=f"{short} vs {basis}" if basis else short,
                market=market,
                hub=hub,
                product=product,
                strip_labels={strip: strip},
                short_title=short,
                price_field="C1_PRICE",
                c1_price_basis=basis,
            )
            sections[region].append(cfg)

    # Stable ordering: more orders first within a section.
    order_map = {
        (r.MARKET, r.HUB, r.PRODUCT, r.STRIP, r.C1_PRICE_BASIS if pd.notna(r.C1_PRICE_BASIS) else None): int(r.N_ORDERS)
        for r in activity.itertuples(index=False)
    }

    def _sort_key(cfg: WindowReportConfig) -> tuple:
        key = (cfg.market, cfg.hub, cfg.product, next(iter(cfg.strip_labels)), cfg.c1_price_basis)
        return (-order_map.get(key, 0), cfg.hub, cfg.c1_price_basis or "", next(iter(cfg.strip_labels)))

    for title in sections:
        sections[title] = sorted(sections[title], key=_sort_key)
    return sections


def _pvo_laycan_groups(subset: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    """
    Split PVO quotes into one chart per cargo laycan (ORDER_BEGIN / ORDER_END).

    Legacy reports plot each loading window separately (e.g. 13-17Jun vs 18-22Jun)
    instead of mixing all laycans onto one path.
    """
    if subset.empty or "ORDER_BEGIN" not in subset.columns or "ORDER_END" not in subset.columns:
        return [("all", subset)]

    work = subset.copy()
    work["_BEGIN"] = pd.to_datetime(work["ORDER_BEGIN"], errors="coerce")
    work["_END"] = pd.to_datetime(work["ORDER_END"], errors="coerce")
    groups: list[tuple[str, pd.DataFrame]] = []
    for (begin, end), grp in work.groupby(["_BEGIN", "_END"], dropna=False):
        label = laycan_label(begin, end)
        clean = grp.drop(columns=["_BEGIN", "_END"])
        groups.append((label or "unknown", clean))

    # Chronological by laycan start, then label for stability.
    groups.sort(key=lambda item: (item[1]["ORDER_BEGIN"].min(), item[0]))
    return groups or [("all", subset)]


def _render_activity_blocks(
    configs: list[WindowReportConfig],
    report_date: date,
) -> list[str]:
    blocks: list[str] = []
    for cfg in configs:
        raw = normalize_orders(fetch_window_orders(cfg, report_date), cfg)
        split_by_laycan = cfg.price_field == "C1_PRICE"
        for code, _label in cfg.strip_labels.items():
            subset = raw[raw["STRIP"] == code] if not raw.empty else raw
            if split_by_laycan:
                chart_groups = _pvo_laycan_groups(subset)
            else:
                date_label = strip_title(raw, code) if not raw.empty else code
                chart_groups = [(date_label, subset)]

            for laycan, grp in chart_groups:
                display = f"{cfg.name}, {laycan}" if split_by_laycan else cfg.name
                fig = plot_intraday_strip(grp, code, laycan, report_date, config=cfg)
                # Unique image key per laycan so parallel charts do not collide.
                key = f"{_activity_key(cfg)}_{laycan}".replace(" ", "_")
                img = charts_to_html_images({key: fig})[key]
                table = build_counterparty_table(grp)
                blocks.append(render_strip_section(display, table, img))
    return blocks


def _flow_header(
    flow: WindowReportConfig,
    report_date: date,
    *,
    show_curve: bool,
    all_products: bool,
) -> tuple[str | None, str | None, str | None]:
    volume_df = fetch_daily_volume(flow, report_date, all_products=all_products)
    flows_df = fetch_cycle_flows(flow, report_date, all_products=all_products)
    figs = {
        "daily_volume": plot_daily_volume(volume_df, flow),
        "sankey": plot_sankey_flows(flows_df, flow, report_date),
    }
    if show_curve and flow.mopl_symbol:
        mopl_df = fetch_mopl_curve(report_date, symbol=flow.mopl_symbol)
        figs["curve"] = plot_curve(mopl_df, flow, report_date)
    images = charts_to_html_images(figs)
    return images.get("curve"), images.get("daily_volume"), images.get("sankey")


def build_ulsd_cif_report(report_date: date) -> str:
    activity = discover_ulsd_cif_activity(report_date)
    discovered = _configs_from_discovery(activity)

    section_specs = [
        ("CIF NWE", ULSD_CIF_NWE_DERIV, True, True),
        ("CIF Med", ULSD_CIF_MED_DERIV, True, True),
        ("FOB ARA", ULSD_FOB_ARA_FLOW, False, True),
        ("Paper", None, False, False),
    ]

    section_blocks: list[str] = []
    toc_titles: list[str] = []

    for title, flow, show_curve, all_products in section_specs:
        toc_titles.append(title)
        curve_img = volume_img = sankey_img = None
        if flow is not None:
            curve_img, volume_img, sankey_img = _flow_header(
                flow,
                report_date,
                show_curve=show_curve,
                all_products=all_products,
            )
        activity_blocks = _render_activity_blocks(discovered.get(title, []), report_date)
        if title == "Paper" and not activity_blocks:
            activity_blocks = [
                "<p><em>No ULSD CIF derivative strips traded this window.</em></p>"
            ]
        section_blocks.append(
            render_pack_section(title, curve_img, volume_img, sankey_img, activity_blocks)
        )

    return render_composite_report(
        "NWE/Med ULSD Window",
        report_date.strftime("%Y-%m-%d"),
        section_blocks,
        toc_titles=toc_titles,
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Build ULSD CIF window HTML report")
    parser.add_argument("--date", default="2026-06-25", help="Report date YYYY-MM-DD")
    args = parser.parse_args()
    report_date = date.fromisoformat(args.date)
    html = build_ulsd_cif_report(report_date)
    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"ulsd_cif_window_{report_date.isoformat()}.html"
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {out} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
