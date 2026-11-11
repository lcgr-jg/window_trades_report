"""Build Middle East Crude Window HTML — Partials + Cargoes (Phase 1 + 2).

Includes:
  - MEC Partials: volume, flat intraday, normalised vs Dubai M2 + Reference Price,
    counterparty table, cycle sankey, optional Convergence sankey
  - Other PG Partials: empty stub heading
  - Cargoes: day-discovered ASIA Crude Cargo charts (laycan x party x side)
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from aggregate import build_counterparty_table
from chart_data import (
    discover_me_crude_cargoes,
    discover_me_crude_partials_strip,
    fetch_assessment_value,
    fetch_cycle_flows,
    fetch_daily_volume,
    fetch_me_crude_convergence_flow,
    laycan_label,
)
from report_charts import charts_to_html_images, plot_daily_volume, plot_intraday_strip, plot_sankey_flows
from report_config import (
    MEC_CARGO_LOT_BBL,
    MEC_CASH_DUBAI_DIFF_SYMBOL,
    MEC_DUBAI_NORM_SYMBOL,
    MEC_PARTIALS,
    WindowReportConfig,
)
from render_html import render_composite_report, render_pack_section, render_strip_section
from snowflake_data import fetch_window_orders, normalize_orders


def _with_strip(base: WindowReportConfig, strip: str) -> WindowReportConfig:
    return WindowReportConfig(
        name=base.name,
        market=base.market,
        hub=base.hub,
        product=base.product,
        strip_labels={strip: strip},
        short_title=base.short_title,
        price_field=base.price_field,
        c1_price_basis=base.c1_price_basis,
        mopl_symbol=base.mopl_symbol,
        mopl_label=base.mopl_label,
        curve_title=base.curve_title,
    )


def _cargo_short_title(hub: str, product: str, strip: str) -> str:
    """Legacy: 'Upper Zakum FOB Zirku Island Jul26'."""
    grade = product.replace("Platts ", "").strip()
    return f"{grade} {hub} {strip}"


def _cargo_configs(report_date: date) -> list[WindowReportConfig]:
    activity = discover_me_crude_cargoes(report_date)
    configs: list[WindowReportConfig] = []
    for row in activity.itertuples(index=False):
        basis = row.C1_PRICE_BASIS if pd.notna(row.C1_PRICE_BASIS) else None
        short = _cargo_short_title(row.HUB, row.PRODUCT, row.STRIP)
        configs.append(
            WindowReportConfig(
                name=f"{short} vs {basis}" if basis else short,
                market=row.MARKET,
                hub=row.HUB,
                product=row.PRODUCT,
                strip_labels={row.STRIP: row.STRIP},
                short_title=short,
                price_field="C1_PRICE",
                c1_price_basis=basis,
            )
        )
    return configs


def _party_laycan_groups(subset: pd.DataFrame) -> list[tuple[str, str, str, pd.DataFrame]]:
    """One chart per (laycan x party x side) for crude cargoes."""
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


def _render_cargo_blocks(configs: list[WindowReportConfig], report_date: date) -> list[str]:
    blocks: list[str] = []
    for cfg in configs:
        raw = normalize_orders(fetch_window_orders(cfg, report_date), cfg)
        for code in cfg.strip_labels:
            subset = raw[raw["STRIP"] == code] if not raw.empty else raw
            for laycan, party, side, grp in _party_laycan_groups(subset):
                fig = plot_intraday_strip(grp, code, laycan, report_date, config=cfg)
                key = (
                    f"cargo_{cfg.hub}_{cfg.product}_{code}_{laycan}_{party}_{side}".replace(
                        " ", "_"
                    )
                )
                img = charts_to_html_images({key: fig})[key]
                table = build_counterparty_table(grp)
                display = f"{cfg.short_title}, {laycan} - {party} ({side.title()})"
                blocks.append(render_strip_section(display, table, img))
    return blocks


def _img_tag(src: str | None, alt: str, width: int = 700) -> str | None:
    if not src:
        return None
    return f'<img src="{src}" alt="{alt}" width="{width}" style="max-width:100%;margin:12px 0;">'


def _render_mec_partials(report_date: date, strip: str) -> list[str]:
    """Volume + flat + normalised (+ Reference Price) + table + sankey + optional Convergence."""
    cfg = _with_strip(MEC_PARTIALS, strip)
    day_orders = fetch_window_orders(cfg, report_date, strip=strip)

    vol_cfg = WindowReportConfig(
        name=cfg.name,
        market=cfg.market,
        hub=cfg.hub,
        product=cfg.product,
        strip_labels=cfg.strip_labels,
        short_title="MEC Partials",
        price_field=cfg.price_field,
    )
    volume_df = fetch_daily_volume(cfg, report_date)
    flows_df = fetch_cycle_flows(cfg, report_date, strip=strip)

    sankey_cfg = WindowReportConfig(
        name=cfg.name,
        market=cfg.market,
        hub=cfg.hub,
        product=cfg.product,
        strip_labels=cfg.strip_labels,
        short_title=f"MEC Partials {strip}",
        price_field=cfg.price_field,
    )

    # Same-day Platts Dubai Mo03; prior-assessment Cash Dubai Diff for the dotted line.
    dubai_m2 = fetch_assessment_value(MEC_DUBAI_NORM_SYMBOL, report_date)
    reference = fetch_assessment_value(
        MEC_CASH_DUBAI_DIFF_SYMBOL, report_date - timedelta(days=1)
    )

    norm_cfg = WindowReportConfig(
        name=cfg.name,
        market=cfg.market,
        hub=cfg.hub,
        product=cfg.product,
        strip_labels=cfg.strip_labels,
        short_title="MEC Partials (Normalised vs Dubai M2) (25kb)",
        price_field=cfg.price_field,
    )

    figs: dict = {
        "daily_volume": plot_daily_volume(volume_df, vol_cfg, volume_unit="25kb"),
        "intraday": plot_intraday_strip(day_orders, strip, strip, report_date, config=cfg),
    }
    if dubai_m2 is not None and not day_orders.empty:
        figs["normalised"] = plot_intraday_strip(
            day_orders,
            strip,
            strip,
            report_date,
            config=norm_cfg,
            price_offset=dubai_m2,
            reference_price=reference,
        )
    figs["sankey"] = plot_sankey_flows(flows_df, sankey_cfg, report_date, volume_unit="kb")

    conv_df = fetch_me_crude_convergence_flow(
        report_date, strip, cargo_lot_bbl=MEC_CARGO_LOT_BBL
    )
    if not conv_df.empty:
        figs["convergence"] = plot_sankey_flows(
            conv_df,
            sankey_cfg,
            report_date,
            volume_unit="500kb",
            label_scale=1,
            chart_title=(
                f"MEC Partials Convergence (500kb) {strip} | Cycle-to-Date | "
                f"{report_date:%d-%b-%Y}"
            ),
        )

    images = charts_to_html_images(figs)
    table = build_counterparty_table(day_orders)

    parts = [
        _img_tag(images.get("daily_volume"), "MEC volume"),
        _img_tag(images.get("intraday"), "MEC intraday"),
        _img_tag(images.get("normalised"), "MEC normalised vs Dubai M2"),
        render_strip_section(f"MEC Partials {strip}", table, None),
        _img_tag(images.get("sankey"), "MEC flows", width=850),
        _img_tag(images.get("convergence"), "MEC convergence", width=850),
    ]
    return [p for p in parts if p]


def build_me_crude_report(report_date: date) -> str:
    strip = discover_me_crude_partials_strip(report_date)

    # --- Partials ---
    partials_subsections: list[tuple[str, list[str]]] = []
    if strip:
        partials_subsections.append(("MEC Partials", _render_mec_partials(report_date, strip)))
    else:
        partials_subsections.append(
            ("MEC Partials", ["<p><em>No MEC Partials activity this window.</em></p>"])
        )
    # Always emit empty Other PG stub (none in sample pack).
    partials_subsections.append(
        ("Other PG Partials", ["<p><em>No Other PG Partials activity this window.</em></p>"])
    )

    partials_html = render_pack_section(
        "Partials",
        None,
        None,
        None,
        [],
        subsection_blocks=partials_subsections,
    )

    # --- Cargoes ---
    cargo_blocks = _render_cargo_blocks(_cargo_configs(report_date), report_date)
    if not cargo_blocks:
        cargo_blocks = ["<p><em>No Cargoes activity this window.</em></p>"]
    cargoes_html = render_pack_section("Cargoes", None, None, None, cargo_blocks)

    return render_composite_report(
        "Middle East Crude Window",
        report_date.strftime("%Y-%m-%d"),
        [partials_html, cargoes_html],
        toc_titles=["Partials", "Cargoes"],
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Build Middle East Crude Window HTML")
    parser.add_argument("--date", default="2026-05-29", help="Report date YYYY-MM-DD")
    args = parser.parse_args()
    report_date = date.fromisoformat(args.date)
    html = build_me_crude_report(report_date)
    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"me_crude_window_{report_date.isoformat()}.html"
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {out} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
