"""Build ARA 10ppm Barges window HTML (Curves / Barges / Paper)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from aggregate import build_counterparty_table
from chart_data import (
    discover_ara_10ppm_paper,
    fetch_cycle_flows,
    fetch_daily_volume,
    fetch_mopl_curve,
    strip_title,
)
from report_charts import build_all_charts, charts_to_html_images, plot_intraday_strip
from report_config import ARA_10PPM_BARGES, WindowReportConfig
from render_html import render_report, render_strip_section
from snowflake_data import fetch_window_orders


def _paper_configs(report_date: date) -> list[WindowReportConfig]:
    activity = discover_ara_10ppm_paper(report_date)
    configs: list[WindowReportConfig] = []
    for row in activity.itertuples(index=False):
        strip = row.STRIP
        configs.append(
            WindowReportConfig(
                name=f"10ppm ARA {strip}",
                market=row.MARKET,
                hub=row.HUB,
                product=row.PRODUCT,
                strip_labels={strip: strip},
                short_title=f"10ppm ARA {strip}",
                price_field="PRICE",
            )
        )
    return configs


def build_ara_10ppm_report(report_date: date) -> str:
    config = ARA_10PPM_BARGES
    mopl_df = fetch_mopl_curve(report_date, symbol=config.mopl_symbol or "AMOPM00")
    volume_df = fetch_daily_volume(config, report_date)
    flows_df = fetch_cycle_flows(config, report_date)
    day_orders = fetch_window_orders(config, report_date)

    strip_titles = {
        code: strip_title(day_orders, code) for code in config.strip_labels
    }
    charts = build_all_charts(
        config,
        report_date,
        mopl_df,
        volume_df,
        flows_df,
        day_orders,
        strip_titles,
    )

    sections: dict[str, object] = {}
    strip_codes: dict[str, str] = {}
    for code, label in config.strip_labels.items():
        raw = day_orders[day_orders["STRIP"] == code] if not day_orders.empty else day_orders
        date_label = strip_titles.get(code, code)
        section_label = f"{label} ({date_label})"
        sections[section_label] = build_counterparty_table(raw)
        strip_codes[section_label] = code

    # Paper: day-driven derivative strips (may be empty).
    paper_blocks: list[str] = []
    for paper_cfg in _paper_configs(report_date):
        code = next(iter(paper_cfg.strip_labels))
        raw = fetch_window_orders(paper_cfg, report_date, strip=code)
        fig = plot_intraday_strip(raw, code, code, report_date, config=paper_cfg)
        key = f"paper_{code}".replace(" ", "_")
        img = charts_to_html_images({key: fig})[key]
        table = build_counterparty_table(raw)
        paper_blocks.append(render_strip_section(paper_cfg.name, table, img))

    chart_images = charts_to_html_images(charts)
    return render_report(
        config,
        report_date.strftime("%Y-%m-%d"),
        sections,
        chart_images,
        strip_codes,
        paper_blocks=paper_blocks,
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Build ARA 10ppm Barges window HTML")
    parser.add_argument("--date", default="2026-06-09", help="Report date YYYY-MM-DD")
    args = parser.parse_args()
    report_date = date.fromisoformat(args.date)
    html = build_ara_10ppm_report(report_date)
    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"ara_10ppm_barges_{report_date.isoformat()}.html"
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {out} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
