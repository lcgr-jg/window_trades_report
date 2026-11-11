"""Render counterparty tables and chart images as HTML."""

from __future__ import annotations

import pandas as pd

from aggregate import format_activity_table
from report_config import WindowReportConfig


def _img_tag(data_uri: str, alt: str = "", width: int = 700) -> str:
    return f'<img src="{data_uri}" alt="{alt}" width="{width}" style="max-width:100%;margin:12px 0;">'


def render_strip_section(label: str, table: pd.DataFrame, intraday_img: str | None = None) -> str:
    parts = [f"<h3>{label}</h3>"]
    if intraday_img:
        parts.append(_img_tag(intraday_img, alt=f"{label} intraday"))
    display = format_activity_table(table)
    display = display.rename(columns={"MARKET_MAKER_MNEMONIC": ""})
    parts.append(display.to_html(index=False, border=0, classes="window-table"))
    return "\n".join(parts)


def render_report(
    config: WindowReportConfig,
    report_date: str,
    sections: dict[str, pd.DataFrame],
    chart_images: dict[str, str],
    strip_codes: dict[str, str],
    paper_blocks: list[str] | None = None,
) -> str:
    """Build full HTML report with Curves, Barges charts, strip tables, and Paper."""
    curves = _img_tag(chart_images["curve"], alt="curve")
    volume = _img_tag(chart_images["daily_volume"], alt="Daily volume")
    sankey = _img_tag(chart_images["sankey"], alt="Cycle flows", width=850)

    strip_blocks = []
    for label in sections:
        code = strip_codes[label]
        strip_blocks.append(
            render_strip_section(label, sections[label], chart_images.get(f"intraday_{code}"))
        )

    paper = paper_blocks or []
    paper_html = "\n".join(paper) if paper else "<p><em>No Paper activity this window.</em></p>"

    body = f"""
  <h2>Curves</h2>
  {curves}

  <h2>Barges</h2>
  <h4>10ppm</h4>
  {volume}
  {sankey}

  {''.join(strip_blocks)}

  <h2>Paper</h2>
  {paper_html}
"""

    return _wrap_html(config.name, report_date, body)


def render_composite_report(
    title: str,
    report_date: str,
    section_blocks: list[str],
    toc_titles: list[str] | None = None,
) -> str:
    """Assemble a multi-section pack (CIF NWE / Med / FOB ARA / …)."""
    titles = toc_titles or []
    toc_items = "".join(f'<li><a href="#{_slug(t)}">{t}</a></li>' for t in titles)
    toc = f"<h2>Table of Contents</h2>\n<ul>{toc_items}</ul>\n" if titles else ""
    body = toc + "\n".join(section_blocks)
    return _wrap_html(title, report_date, body)


def render_pack_section(
    section_title: str,
    curve_img: str | None,
    volume_img: str | None,
    sankey_img: str | None,
    activity_blocks: list[str],
    *,
    subsection_blocks: list[tuple[str, list[str]]] | None = None,
) -> str:
    parts = [f'<h2 id="{_slug(section_title)}">{section_title}</h2>']
    if curve_img:
        parts.append(_img_tag(curve_img, alt=f"{section_title} curve"))
    if volume_img:
        parts.append(_img_tag(volume_img, alt=f"{section_title} volume"))
    if sankey_img:
        parts.append(_img_tag(sankey_img, alt=f"{section_title} flows", width=850))
    if subsection_blocks:
        for sub_title, blocks in subsection_blocks:
            parts.append(f"<h3>{sub_title}</h3>")
            parts.extend(blocks or ["<p><em>No activity.</em></p>"])
    else:
        parts.extend(activity_blocks)
    return "\n".join(parts)


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in text).strip("-").lower()


def _wrap_html(title: str, report_date: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{title} — {report_date}</title>
  <style>
    body {{ font-family: Calibri, Arial, sans-serif; font-size: 13px; max-width: 920px; }}
    h1 {{ font-size: 18px; }}
    h2 {{ font-size: 16px; margin-top: 28px; }}
    h3 {{ font-size: 14px; margin-top: 24px; }}
    h4 {{ font-size: 13px; margin-top: 16px; }}
    table.window-table {{ border-collapse: collapse; margin-bottom: 16px; }}
    table.window-table th, table.window-table td {{
      border: 1px solid #ccc; padding: 4px 8px; text-align: right;
    }}
    table.window-table th:first-child, table.window-table td:first-child {{ text-align: left; }}
    tr:last-child {{ font-weight: bold; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p>{report_date}</p>
  {body}
</body>
</html>
"""
