"""Report definitions mapping email titles to Snowflake filters."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WindowReportConfig:
    name: str
    market: str
    hub: str
    product: str
    strip_labels: dict[str, str]  # snowflake strip code -> display label
    short_title: str = ""  # chart title prefix, e.g. "ULSD CIF NWE"
    mopl_symbol: str | None = None
    mopl_label: str | None = None
    curve_title: str | None = None
    # PVO orders store the quoted differential in C1_PRICE; PRICE is often 0.
    price_field: str = "PRICE"
    c1_price_basis: str | None = None  # e.g. "EFP ICE LS GO" / "ULSD CIFNWE Crg"


@dataclass(frozen=True)
class ReportSection:
    """One TOC section of a multi-market pack (e.g. CIF NWE)."""

    title: str
    flow_config: WindowReportConfig | None = None  # volume + sankey
    activity_configs: tuple[WindowReportConfig, ...] = field(default_factory=tuple)
    show_curve: bool = False


ARA_10PPM_BARGES = WindowReportConfig(
    name="ARA 10ppm Barges Window",
    market="EU MidDist Barge",
    hub="ARA Smr",
    product="Platts ULSD Brg 1-3kt ICE LSGO M1",
    strip_labels={"FE": "Front End", "MW": "Mid Window", "BE": "Back End"},
    short_title="10ppm ARA Smr",
    mopl_symbol="AMOPM00",
    mopl_label="ULSD FOB ARA Brg (v MOPL)",
    curve_title="ARA 10ppm (v MOPL)",
)

# Stable hubs / MOPL symbols for the ULSD CIF pack (activities discovered per day).
ULSD_CIF_NWE_HUB = "ULSD 10ppm CIF NWE Cargoes vs Low Sulphur Gasoil 1st Line"
ULSD_CIF_MED_HUB = "ULSD 10ppm CIF Med Cargoes vs Low Sulphur Gasoil 1st Line"

ULSD_CIF_NWE_DERIV = WindowReportConfig(
    name="ULSD CIF NWE Derivative",
    market="EU MidDist Derivative",
    hub=ULSD_CIF_NWE_HUB,
    product="Platts Gasoil Diff",  # volume/sankey use all_products=True
    strip_labels={"Jul26": "Jul26"},
    short_title="ULSD CIF NWE",
    mopl_symbol="AMOPL00",
    mopl_label="ULSD CIF NWE Crg (v MOPL)",
    curve_title="ULSD CIF NWE (v MOPL)",
)

ULSD_CIF_MED_DERIV = WindowReportConfig(
    name="ULSD CIF Med Derivative",
    market="EU MidDist Derivative",
    hub=ULSD_CIF_MED_HUB,
    product="Platts Gasoil Diff",
    strip_labels={"Jul26": "Jul26"},
    short_title="ULSD CIF Med",
    mopl_symbol="AMOPN00",
    mopl_label="ULSD CIF Med Crg (v MOPL)",
    curve_title="ULSD CIF Med (v MOPL)",
)

ULSD_FOB_ARA_FLOW = WindowReportConfig(
    name="ULSD FOB Basis ARA",
    market="EU MidDist PVO",
    hub="FOB Basis ARA",
    product="Platts ARA ULSD Crg",
    strip_labels={"7 - 20": "7 - 20"},
    short_title="ULSD FOB ARA",
    price_field="C1_PRICE",
)

# ARA 10ppm Paper (day-varying derivative strips under the barge MOPL hub).
ARA_10PPM_PAPER_HUB = "Diesel 10ppm FOB ARA Barges/LS GO 1st Line"

# Asia Gasoil Window
ASIA_GO_SING_HUB = "FOB Straits"
ASIA_GO_AG_HUB = "FOB ArabGulf"
ASIA_GO_PAPER_HUB = "Singapore Gasoil 10ppm (Platts)"

ASIA_GO_SING_10PPM = WindowReportConfig(
    name="Sing GO 10ppm",
    market="ASIA MD (PVO)",
    hub=ASIA_GO_SING_HUB,
    product="Platts GO 10ppm",
    strip_labels={"15-30": "15-30"},
    short_title="10ppm FOB Straits 15-30",
    price_field="C1_PRICE",
    c1_price_basis="MOPS Gasoil",
)

ASIA_GO_AG_10PPM = WindowReportConfig(
    name="AG GO 10ppm",
    market="ASIA MD (PVO)",
    hub=ASIA_GO_AG_HUB,
    product="Platts GO 10ppm - AG",
    strip_labels={"20-40": "20-40"},
    short_title="10ppm FOB ArabGulf 20-40",
    price_field="C1_PRICE",
    c1_price_basis="MOPAG Gasoil",
)

ASIA_GO_FLOW = ASIA_GO_SING_10PPM  # Curves volume/sankey use Sing 10ppm

# Middle East Crude Window
MEC_PARTIALS_HUB = "Platts Cash Dubai Partials (Fateh)"
# Legacy title says "Dubai M2"; chart levels match Platts Dubai Mo03 (PCAAV00) on report date.
MEC_DUBAI_NORM_SYMBOL = "PCAAV00"
MEC_CASH_DUBAI_DIFF_SYMBOL = "DBDDC00"
MEC_CARGO_LOT_BBL = 500_000  # 500kb convergence threshold / chart unit

MEC_PARTIALS = WindowReportConfig(
    name="MEC Partials",
    market="ASIA Crude Partial",
    hub=MEC_PARTIALS_HUB,
    product="Platts Dubai",
    strip_labels={"Jul26": "Jul26"},  # day builder overwrites with active strip
    short_title="MEC Partials (25kb)",
    price_field="PRICE",
)

TABLE = "SPGICI_EWINDOW_MARKETDATA.EWMD.EWINDOW_TRADEDATA"
SNOWFLAKE_DATABASE = "SPGICI_EWINDOW_MARKETDATA"
SNOWFLAKE_SCHEMA = "EWMD"

PRICEDATA_TABLE = "SPGICI_MARKETDATA_SHARE.MDV2.PRICEDATA"
MOPL_SYMBOL = "AMOPM00"
MOPL_LABEL = "ULSD FOB ARA Brg (v MOPL)"
