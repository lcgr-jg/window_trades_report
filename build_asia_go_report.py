"""Build Asia Gasoil Window HTML with day-driven Sing / AG / Paper discovery."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from aggregate import build_counterparty_table
from chart_data import (
    discover_asia_go_activity,
    fetch_cycle_flows,
    fetch_daily_volume,
    laycan_label,
)
from report_charts import charts_to_html_images, plot_daily_volume, plot_intraday_strip, plot_sankey_flows
from report_config import (
    ASIA_GO_AG_HUB,
    ASIA_GO_FLOW,
    ASIA_GO_PAPER_HUB,
    ASIA_GO_SING_HUB,
    WindowReportConfig,
)
from render_html import render_composite_report, render_pack_section, render_strip_section
from snowflake_data import fetch_window_orders, normalize_orders


def _is_sing_10ppm(product: str) -> bool:
    return product == "Platts GO 10ppm"


def _is_ag_10ppm(product: str) -> bool:
    return product == "Platts GO 10ppm - AG"


def _pvo_short_title(hub: str, product: str, strip: str) -> str:
    """Legacy titles like '10ppm FOB Straits 15-30' / '500ppm FOB Straits 15-30'."""
    grade = product.replace("Platts GO ", "").replace(" - AG", "").strip()
    loc = "FOB Straits" if hub == ASIA_GO_SING_HUB else "FOB ArabGulf"
    return f"{grade} {loc} {strip}"


def _configs_from_discovery(activity: pd.DataFrame) -> dict[str, dict[str, list[WindowReportConfig]]]:
    """
    Nested section map:
      Sing Cargoes -> {10ppm, Others}
      AG Cargoes   -> {10ppm, Others}
      Paper        -> {Paper: [...]}
    """
    sections: dict[str, dict[str, list[WindowReportConfig]]] = {
        "Sing Cargoes": {"10ppm": [], "Others": []},
        "AG Cargoes": {"10ppm": [], "Others": []},
        "Paper": {"Paper": []},
    }
    if activity.empty:
        return sections

    for row in activity.itertuples(index=False):
        market = row.MARKET
        hub = row.HUB
        product = row.PRODUCT
        strip = row.STRIP
        basis = row.C1_PRICE_BASIS if pd.notna(row.C1_PRICE_BASIS) else None

        if market == "ASIA MidDist Swap" and hub == ASIA_GO_PAPER_HUB:
            sections["Paper"]["Paper"].append(
                WindowReportConfig(
                    name=f"Sing GO {strip}",
                    market=market,
                    hub=hub,
                    product=product,
                    strip_labels={strip: strip},
                    # Prefix only — plot_intraday_strip appends the strip label.
                    short_title="Sing GO",
                    price_field="PRICE",
                )
            )
            continue

        if market != "ASIA MD (PVO)":
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

        if hub == ASIA_GO_SING_HUB:
            bucket = "10ppm" if _is_sing_10ppm(product) else "Others"
            sections["Sing Cargoes"][bucket].append(cfg)
        elif hub == ASIA_GO_AG_HUB:
            bucket = "10ppm" if _is_ag_10ppm(product) else "Others"
            sections["AG Cargoes"][bucket].append(cfg)

    # Stable order: more orders first, then product/strip.
    order_map = {
        (
            r.MARKET,
            r.HUB,
            r.PRODUCT,
            r.STRIP,
            r.C1_PRICE_BASIS if pd.notna(r.C1_PRICE_BASIS) else None,
        ): int(r.N_ORDERS)
        for r in activity.itertuples(index=False)
    }

    def _sort_key(cfg: WindowReportConfig) -> tuple:
        key = (
            cfg.market,
            cfg.hub,
            cfg.product,
            next(iter(cfg.strip_labels)),
            cfg.c1_price_basis,
        )
        return (-order_map.get(key, 0), cfg.product, next(iter(cfg.strip_labels)))

    for section in sections.values():
        for bucket in section:
            section[bucket] = sorted(section[bucket], key=_sort_key)
    return sections


def _party_laycan_groups(subset: pd.DataFrame) -> list[tuple[str, str, str, pd.DataFrame]]:
    """
    One chart per (laycan × party × side) — matches legacy Asia GO emails.

    Returns list of (laycan_label, party, side, group_df).
    """
    if subset.empty:
        return []

    work = subset.copy()
    work["_BEGIN"] = pd.to_datetime(work["ORDER_BEGIN"], errors="coerce")
    work["_END"] = pd.to_datetime(work["ORDER_END"], errors="coerce")
    work["ORDER_TYPE"] = work["ORDER_TYPE"].str.lower()

    groups: list[tuple[str, str, str, pd.DataFrame]] = []
    keys = ["_BEGIN", "_END", "MARKET_MAKER_MNEMONIC", "ORDER_TYPE"]
    for (begin, end, party, side), grp in work.groupby(keys, dropna=False):
        label = laycan_label(begin, end) or "unknown"
        clean = grp.drop(columns=["_BEGIN", "_END"])
        groups.append((label, str(party), str(side), clean))

    groups.sort(key=lambda item: (item[3]["ORDER_BEGIN"].min(), item[1], item[2]))
    return groups


def _render_pvo_blocks(
    configs: list[WindowReportConfig],
    report_date: date,
) -> list[str]:
    """PVO Sing/AG: one chart + table per laycan×party×side."""
    blocks: list[str] = []
    for cfg in configs:
        raw = normalize_orders(fetch_window_orders(cfg, report_date), cfg)
        for code in cfg.strip_labels:
            subset = raw[raw["STRIP"] == code] if not raw.empty else raw
            for laycan, party, side, grp in _party_laycan_groups(subset):
                fig = plot_intraday_strip(grp, code, laycan, report_date, config=cfg)
                key = (
                    f"{cfg.hub}_{cfg.product}_{code}_{laycan}_{party}_{side}".replace(" ", "_")
                )
                img = charts_to_html_images({key: fig})[key]
                table = build_counterparty_table(grp)
                display = f"{cfg.short_title}, {laycan} - {party} ({side.title()})"
                blocks.append(render_strip_section(display, table, img))
    return blocks


def _render_paper_blocks(
    configs: list[WindowReportConfig],
    report_date: date,
) -> list[str]:
    """Paper strips: one combined chart per strip (all parties)."""
    blocks: list[str] = []
    for cfg in configs:
        code = next(iter(cfg.strip_labels))
        raw = fetch_window_orders(cfg, report_date, strip=code)
        fig = plot_intraday_strip(raw, code, code, report_date, config=cfg)
        key = f"paper_{code}".replace(" ", "_").replace("/", "_")
        img = charts_to_html_images({key: fig})[key]
        table = build_counterparty_table(raw)
        blocks.append(render_strip_section(cfg.name, table, img))
    return blocks


def build_asia_go_report(report_date: date) -> str:
    activity = discover_asia_go_activity(report_date)
    discovered = _configs_from_discovery(activity)

    # Curves: Sing GO 10ppm volume + sankey only (no MOPS curve in legacy).
    flow = ASIA_GO_FLOW
    volume_df = fetch_daily_volume(flow, report_date)
    flows_df = fetch_cycle_flows(flow, report_date)
    # Display name on Curves charts matches legacy "Sing GO 10ppm".
    flow_display = WindowReportConfig(
        name=flow.name,
        market=flow.market,
        hub=flow.hub,
        product=flow.product,
        strip_labels=flow.strip_labels,
        short_title="Sing GO 10ppm",
        price_field=flow.price_field,
        c1_price_basis=flow.c1_price_basis,
    )
    figs = {
        "daily_volume": plot_daily_volume(volume_df, flow_display, volume_unit="kb"),
        "sankey": plot_sankey_flows(flows_df, flow_display, report_date, volume_unit="kb"),
    }
    images = charts_to_html_images(figs)

    section_blocks: list[str] = []
    toc_titles = ["Curves", "Sing Cargoes", "AG Cargoes", "Paper"]

    # Curves header section.
    section_blocks.append(
        render_pack_section(
            "Curves",
            None,
            images["daily_volume"],
            images["sankey"],
            [],
        )
    )

    # Sing / AG with nested 10ppm / Others.
    for title in ("Sing Cargoes", "AG Cargoes"):
        buckets = discovered.get(title, {"10ppm": [], "Others": []})
        subsections: list[tuple[str, list[str]]] = []
        for bucket in ("10ppm", "Others"):
            blocks = _render_pvo_blocks(buckets.get(bucket, []), report_date)
            subsections.append((bucket, blocks))
        section_blocks.append(
            render_pack_section(title, None, None, None, [], subsection_blocks=subsections)
        )

    # Paper.
    paper_cfgs = discovered.get("Paper", {}).get("Paper", [])
    paper_blocks = _render_paper_blocks(paper_cfgs, report_date)
    if not paper_blocks:
        paper_blocks = ["<p><em>No Paper activity this window.</em></p>"]
    section_blocks.append(render_pack_section("Paper", None, None, None, paper_blocks))

    return render_composite_report(
        "Asia Gasoil Window",
        report_date.strftime("%Y-%m-%d"),
        section_blocks,
        toc_titles=toc_titles,
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Build Asia Gasoil Window HTML report")
    parser.add_argument("--date", default="2026-06-23", help="Report date YYYY-MM-DD")
    args = parser.parse_args()
    report_date = date.fromisoformat(args.date)
    html = build_asia_go_report(report_date)
    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"asia_go_window_{report_date.isoformat()}.html"
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {out} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
