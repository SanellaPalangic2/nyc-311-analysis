"""Generate the Power BI dashboard as a Power BI Project (.pbip).

Run:  python src/build_powerbi_project.py
      (run src/export_powerbi.py first; this reads the CSV headers)

Then open  powerbi/NYC311.pbip  in Power BI Desktop and click Refresh.

A .pbip is a Power BI report saved as plain text files instead of one binary
.pbix file:
  powerbi/NYC311.SemanticModel/   the data model, in TMDL (tables, Power Query,
                                  relationships, DAX measures)
  powerbi/NYC311.Report/          the report pages and visuals, in JSON (PBIR)
Text files can be reviewed in git, and they hold no data: Power BI loads the
CSVs from outputs/powerbi/ when you refresh. So the project stays small enough
to commit, unlike a .pbix with 3.6M rows inside.

The model and measures match docs/powerbi_guide.md.
"""
import csv
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import config

PROJECT_DIR = config.PROJECT_ROOT / "powerbi"
NAME = "NYC311"
MODEL_DIR = PROJECT_DIR / f"{NAME}.SemanticModel"
REPORT_DIR = PROJECT_DIR / f"{NAME}.Report"
THEME_FILE = config.PROJECT_ROOT / "docs" / "powerbi_theme.json"

SCHEMA = "https://developer.microsoft.com/json-schemas/fabric"
VISUAL_SCHEMA = f"{SCHEMA}/item/report/definition/visualContainer/2.7.0/schema.json"
PAGE_SCHEMA = f"{SCHEMA}/item/report/definition/page/2.1.0/schema.json"
VERSIONS = {"visual": "2.7.0", "page": "2.1.0", "report": "3.2.0"}

# Power BI's built-in base theme. Every saved report ships a copy of it; we
# copy it from the local Power BI Desktop install (see find_base_theme).
BASE_THEME = "CY26SU09"


def lineage(*parts):
    """Stable ID for a model object, so regenerating doesn't create a diff."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "nyc311/" + "/".join(parts)))


# =============================================================================
# Semantic model (TMDL)
# =============================================================================
# TMDL is indentation-based, like Python, but uses TABS. Each table is one
# file with its columns, measures and a "partition" holding the Power Query (M)
# code that loads it.

# Column types for the tables whose types matter. (tmdl dataType, M type)
TYPES = {
    "int": ("int64", "Int64.Type"),
    "num": ("double", "type number"),
    "date": ("dateTime", "type date"),
    "text": ("string", "type text"),
}

TABLES = {
    "fact_requests": {
        "columns": {
            "unique_key": "int", "created_date": "date", "created_month": "int",
            "season": "text", "agency": "text", "agency_group": "text",
            "complaint_type": "text", "borough": "text", "resolution_status": "text",
            "resolution_hours": "num", "has_no_close_date": "int",
            "outcome_30d_known": "int", "closed_within_30d": "int",
            "is_extreme": "int", "is_zero_duration": "int",
        },
        # Hidden so visuals use the dimension tables, keeping filters flowing
        # through the relationships.
        "hidden": {"agency", "complaint_type", "borough", "season"},
    },
    "dim_agency": {"columns": {"agency": "text", "agency_name": "text"}},
    "dim_complaint_type": {
        "columns": {"complaint_type": "text", "main_agency": "text", "n_requests": "int",
                    "has_1000_plus": "int", "is_top10_volume": "int"},
    },
    "dim_borough": {
        "columns": {"borough": "text", "borough_order": "int", "is_known": "int"},
        "hidden": {"borough_order"},
        "sort_by": {"borough": "borough_order"},
    },
    "dim_season": {
        "columns": {"season": "text", "season_order": "int", "months": "text"},
        "hidden": {"season_order"},
        "sort_by": {"season": "season_order"},
    },
    "cleaning_report": {
        "columns": {"step": "int", "rule": "text", "rows_affected": "int", "action": "text"},
    },
}

# Answer-key tables from the SQL: types are read from the CSV (numbers vs text).
SUMMARY_TABLES = ["summary_overview", "summary_by_agency", "summary_by_borough",
                  "summary_by_season", "summary_slowest_types",
                  "summary_type_by_borough", "summary_season_mix"]

RELATIONSHIPS = [
    ("fact_requests", "agency", "dim_agency", "agency"),
    ("fact_requests", "complaint_type", "dim_complaint_type", "complaint_type"),
    ("fact_requests", "borough", "dim_borough", "borough"),
    ("fact_requests", "season", "dim_season", "season"),
]

# (name, DAX, format string). Same measures as docs/powerbi_guide.md.
MEASURES = [
    ("Requests", "COUNTROWS ( fact_requests )", "#,0"),
    ("Median Hours",
     "CALCULATE (\n    MEDIAN ( fact_requests[resolution_hours] ),\n"
     "    fact_requests[resolution_status] = \"closed\"\n)", "#,0.00"),
    ("P90 Hours",
     "CALCULATE (\n    PERCENTILE.INC ( fact_requests[resolution_hours], 0.9 ),\n"
     "    fact_requests[resolution_status] = \"closed\"\n)", "#,0.00"),
    ("Mean Hours",
     "CALCULATE (\n    AVERAGE ( fact_requests[resolution_hours] ),\n"
     "    fact_requests[resolution_status] = \"closed\"\n)", "#,0.00"),
    ("Median Days", "DIVIDE ( [Median Hours], 24 )", "#,0.00"),
    ("% Closed Within 30 Days",
     "DIVIDE (\n    SUM ( fact_requests[closed_within_30d] ),\n"
     "    SUM ( fact_requests[outcome_30d_known] )\n)", "0.00%"),
    ("% No Close Date",
     "DIVIDE ( SUM ( fact_requests[has_no_close_date] ), [Requests] )", "0.00%"),
    ("% NYPD",
     "DIVIDE (\n    CALCULATE ( [Requests], fact_requests[agency_group] = \"NYPD\" ),\n"
     "    [Requests]\n)", "0.00%"),
    ("Citywide Median Hours",
     "CALCULATE (\n    [Median Hours],\n    REMOVEFILTERS ( dim_borough ),\n"
     "    dim_borough[is_known] = 1\n)", "#,0.00"),
    ("Ratio to Citywide", "DIVIDE ( [Median Hours], [Citywide Median Hours] )", '0.00"x"'),
    # Used for conditional formatting: orange when 20%+ of requests have no
    # close date (the median is too optimistic there), otherwise no color.
    ("No Close Date Color",
     "IF ( [% No Close Date] >= 0.2, \"#eb6834\", BLANK () )", None),
]


def tmdl_name(name):
    """Quote names containing spaces or symbols, as TMDL requires."""
    return name if name.replace("_", "").isalnum() else "'" + name.replace("'", "''") + "'"


def indent(text, tabs):
    return "\n".join("\t" * tabs + line for line in text.splitlines())


def summary_columns(table):
    """Read a summary CSV and type each column: numeric if every value parses."""
    with open(config.POWERBI_DIR / f"{table}.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header, data = rows[0], rows[1:]
    columns = {}
    for i, col in enumerate(header):
        values = [r[i] for r in data if r[i] != ""]
        try:
            [float(v) for v in values]
            columns[col] = "num"
        except ValueError:
            columns[col] = "text"
    return columns


def power_query(table, columns):
    """M code: read the CSV from the DataFolder parameter and set types.

    The "en-US" culture on the type step makes numbers like 6.6103 load the
    same way on any Windows region setting (some regions use a comma as the
    decimal separator).
    """
    types = ", ".join(f'{{"{c}", {TYPES[t][1]}}}' for c, t in columns.items())
    return (
        "let\n"
        f'    Source = Csv.Document(File.Contents(DataFolder & "{table}.csv"), '
        f'[Delimiter = ",", Columns = {len(columns)}, Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),\n'
        "    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),\n"
        f'    Typed = Table.TransformColumnTypes(Promoted, {{{types}}}, "en-US")\n'
        "in\n"
        "    Typed"
    )


def table_tmdl(table, spec, hide_table=False):
    lines = [f"table {tmdl_name(table)}"]
    if hide_table:
        lines.append("\tisHidden")
    lines.append(f"\tlineageTag: {lineage(table)}")
    lines.append("")

    if table == "fact_requests":
        for name, dax, fmt in MEASURES:
            if "\n" in dax:
                lines.append(f"\tmeasure {tmdl_name(name)} =")
                lines.append(indent(dax, 3))
            else:
                lines.append(f"\tmeasure {tmdl_name(name)} = {dax}")
            if fmt:
                lines.append(f"\t\tformatString: {fmt}")
            lines.append(f"\t\tlineageTag: {lineage(table, 'measure', name)}")
            lines.append("")

    for col, kind in spec["columns"].items():
        data_type = TYPES[kind][0]
        lines.append(f"\tcolumn {tmdl_name(col)}")
        lines.append(f"\t\tdataType: {data_type}")
        if col in spec.get("hidden", set()):
            lines.append("\t\tisHidden")
        if kind == "date":
            lines.append("\t\tformatString: yyyy-mm-dd")
        elif kind == "int":
            # Counts get a thousands separator (3,655,042); IDs, flags,
            # months and step numbers don't.
            fmt = "#,0" if col in ("rows_affected", "n_requests") else "0"
            lines.append(f"\t\tformatString: {fmt}")
        # Numbers in these tables are IDs, flags or pre-computed values, so
        # Power BI shouldn't add them up by default; the measures do the math.
        lines.append(f"\t\tsummarizeBy: none")
        lines.append(f"\t\tsourceColumn: {col}")
        if col in spec.get("sort_by", {}):
            lines.append(f"\t\tsortByColumn: {spec['sort_by'][col]}")
        lines.append(f"\t\tlineageTag: {lineage(table, col)}")
        lines.append("")

    lines.append(f"\tpartition {tmdl_name(table)} = m")
    lines.append("\t\tmode: import")
    lines.append("\t\tsource =")
    lines.append(indent(power_query(table, spec["columns"]), 4))
    lines.append("")
    return "\n".join(lines)


def write_model():
    definition = MODEL_DIR / "definition"
    (definition / "tables").mkdir(parents=True)

    write_json(MODEL_DIR / "definition.pbism", {
        "$schema": f"{SCHEMA}/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.0",
        "settings": {},
    })

    (definition / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1600\n", encoding="utf-8")

    all_tables = list(TABLES) + SUMMARY_TABLES
    query_order = json.dumps(["DataFolder"] + all_tables)
    model = [
        "model Model",
        "\tculture: en-US",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tsourceQueryCulture: en-US",
        "\tdataAccessOptions",
        "\t\tlegacyRedirects",
        "\t\treturnErrorValuesAsNull",
        "",
        # Turn off auto date tables; created_month is used for trends instead.
        "annotation __PBI_TimeIntelligenceEnabled = 0",
        "",
        f"annotation PBI_QueryOrder = {query_order}",
        "",
    ] + [f"ref table {tmdl_name(t)}" for t in all_tables]
    (definition / "model.tmdl").write_text("\n".join(model) + "\n", encoding="utf-8")

    # The folder with the CSVs, as a Power Query parameter. To use the project
    # on another computer: Transform data -> Edit parameters -> DataFolder.
    data_folder = str(config.POWERBI_DIR) + "\\"
    (definition / "expressions.tmdl").write_text(
        f'expression DataFolder = "{data_folder}" meta [IsParameterQuery = true, '
        f'Type = "Text", IsParameterQueryRequired = true]\n'
        f"\tlineageTag: {lineage('DataFolder')}\n\n"
        "\tannotation PBI_ResultType = Text\n",
        encoding="utf-8",
    )

    relationships = []
    for from_table, from_col, to_table, to_col in RELATIONSHIPS:
        relationships += [
            f"relationship {lineage('rel', from_table, from_col)}",
            f"\tfromColumn: {from_table}.{from_col}",
            f"\ttoColumn: {to_table}.{to_col}",
            "",
        ]
    (definition / "relationships.tmdl").write_text("\n".join(relationships), encoding="utf-8")

    for table, spec in TABLES.items():
        (definition / "tables" / f"{table}.tmdl").write_text(table_tmdl(table, spec), encoding="utf-8")
    for table in SUMMARY_TABLES:
        spec = {"columns": summary_columns(table)}
        (definition / "tables" / f"{table}.tmdl").write_text(
            table_tmdl(table, spec, hide_table=True), encoding="utf-8")


# =============================================================================
# Report (PBIR JSON)
# =============================================================================
# Helpers that build the JSON Power BI uses to reference fields and values.

def col(table, column):
    return {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": column}}


def measure(name):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": "fact_requests"}}, "Property": name}}


def ref(field):
    """queryRef: Power BI's per-visual name for a field, e.g. 'dim_agency.agency'."""
    kind = "Column" if "Column" in field else "Measure"
    return f"{field[kind]['Expression']['SourceRef']['Entity']}.{field[kind]['Property']}"


# Readable names shown on slicers, axes and table headers instead of the raw
# column names (e.g. "Month created" rather than "created_month").
DISPLAY_NAMES = {
    "borough": "Borough", "season": "Season", "agency": "Agency code",
    "agency_name": "Agency", "complaint_type": "Complaint type", "main_agency": "Agency",
    "created_month": "Month created", "step": "Step", "rule": "Rule",
    "rows_affected": "Rows affected", "action": "Action",
}


def projection(field, display=None):
    """A field placed in a visual. `display` overrides the name shown in the
    visual (used to keep table headers short); otherwise columns get their
    readable name from DISPLAY_NAMES and measures keep their own name."""
    p = {"field": field, "queryRef": ref(field), "nativeQueryRef": ref(field).split(".", 1)[1]}
    name = display or (DISPLAY_NAMES.get(field["Column"]["Property"]) if "Column" in field else None)
    if name:
        p["displayName"] = name
    return p


def literal(value):
    """Formatting values are written as query literals: strings in single quotes."""
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (int, float)):
        text = f"{value}D"
    else:
        text = "'" + value.replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": text}}}


def in_filter(table, column, values, name):
    """Visual-level filter: keep rows where table[column] is one of values."""
    def lit(v):
        return {"Literal": {"Value": f"{v}L" if isinstance(v, int) else "'" + v + "'"}}
    return {
        "name": name,
        "field": col(table, column),
        "type": "Categorical",
        "filter": {
            "Version": 2,
            "From": [{"Name": "t", "Entity": table, "Type": 0}],
            "Where": [{"Condition": {"In": {
                "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "t"}},
                                            "Property": column}}],
                "Values": [[lit(v)] for v in values],
            }}}],
        },
    }


class Page:
    def __init__(self, name, display_name):
        self.name = name
        self.display_name = display_name
        self.visuals = []

    def add(self, visual_type, x, y, w, h, roles=None, title=None, sort=None,
            filters=None, objects=None, sync_group=None):
        n = len(self.visuals) + 1
        visual = {"visualType": visual_type}
        if roles:
            # A field can be given as (field, "Short name") to rename it here only.
            visual["query"] = {"queryState": {
                role: {"projections": [projection(*f) if isinstance(f, tuple) else projection(f)
                                       for f in fields]}
                for role, fields in roles.items()
            }}
            if sort:
                field, direction = sort
                visual["query"]["sortDefinition"] = {
                    "sort": [{"field": field, "direction": direction}],
                    "isDefaultSort": False,
                }
        if objects:
            visual["objects"] = objects
        if title:
            visual["visualContainerObjects"] = {
                "title": [{"properties": {"show": literal(True), "text": literal(title)}}]
            }
        if sync_group:
            visual["syncGroup"] = {"groupName": sync_group, "fieldChanges": True,
                                   "filterChanges": True}
        container = {
            "$schema": VISUAL_SCHEMA,
            "name": f"{self.name}_v{n:02d}",
            "position": {"x": x, "y": y, "z": n * 1000, "width": w, "height": h,
                         "tabOrder": n * 1000},
            "visual": visual,
        }
        if filters:
            container["filterConfig"] = {"filters": filters}
        self.visuals.append(container)

    def text(self, x, y, w, h, paragraphs):
        """A text box. paragraphs: list of (text, size_pt, bold)."""
        runs = [{"textRuns": [{"value": text, "textStyle": {
                    "fontSize": f"{size}pt", **({"fontWeight": "bold"} if bold else {})}}]}
                for text, size, bold in paragraphs]
        self.add("textbox", x, y, w, h,
                 objects={"general": [{"properties": {"paragraphs": runs}}]})


def slicers(page):
    """The same three dropdown slicers on pages 1-3, kept in sync by syncGroup."""
    known_only = [in_filter("dim_borough", "is_known", [1], "known_boroughs")]
    dropdown = {"data": [{"properties": {"mode": literal("Dropdown")}}]}
    page.add("slicer", 20, 70, 300, 60, {"Values": [col("dim_borough", "borough")]},
             filters=known_only, objects=dropdown, sync_group="borough")
    page.add("slicer", 340, 70, 300, 60, {"Values": [col("dim_season", "season")]},
             objects=dropdown, sync_group="season")
    page.add("slicer", 660, 70, 400, 60, {"Values": [col("dim_agency", "agency_name")]},
             objects=dropdown, sync_group="agency")


def read_summary(table, key=None):
    """Rows of a summary CSV (the SQL results), keyed by `key` if given."""
    with open(config.POWERBI_DIR / f"{table}.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {r[key]: r for r in rows} if key else rows


def text_facts():
    """Numbers quoted in the report's text boxes, read from the SQL results
    so they're never typed in by hand (same rule as outputs/findings.md)."""
    overview = read_summary("summary_overview")[0]
    agency = read_summary("summary_by_agency", "agency")
    mix = read_summary("summary_season_mix", "season")
    slow = read_summary("summary_slowest_types", "complaint_type")

    def by(col, pick):
        return pick(mix, key=lambda s: float(mix[s][col]))

    all_slow, all_fast = by("median_hours_all", max), by("median_hours_all", min)
    other_slow, other_fast = by("median_hours_other_agencies", max), by("median_hours_other_agencies", min)
    n_total = int(overview["n_requests"])
    nypd = agency["NYPD"]
    return {
        "n_total": f"{n_total:,}",
        "pct_no_close": overview["pct_no_close_date"],
        "nypd_share": f"{100 * int(nypd['n_requests']) / n_total:.2f}",
        "nypd_median": nypd["median_hours"],
        "all_slow": all_slow, "all_fast": all_fast,
        "other_slow": other_slow, "other_fast": other_fast,
        "pct_nypd_slow": mix[all_slow]["pct_nypd"], "pct_nypd_fast": mix[all_fast]["pct_nypd"],
        "nypd_med_slow": mix[all_slow]["median_hours_nypd"],
        "nypd_med_fast": mix[all_fast]["median_hours_nypd"],
        "other_med_slow": mix[other_slow]["median_hours_other_agencies"],
        "other_med_fast": mix[other_fast]["median_hours_other_agencies"],
        "heli_open": slow["Noise - Helicopter"]["pct_no_close_date"],
        # Types whose median is between 1,440 and 1,441 hours (60 days)
        "n_sixty": sum(1 for r in slow.values()
                       if r["median_hours"] and 1440 <= float(r["median_hours"]) < 1441),
    }


def build_pages():
    pages = []
    f = text_facts()
    has_1000 = in_filter("dim_complaint_type", "has_1000_plus", [1], "types_1000_plus")

    # --- 1. Overview ---------------------------------------------------------
    p = Page("overview", "1. Overview")
    p.text(20, 5, 1240, 60, [("NYC 311 resolution times, 2025", 20, True),
                             (f"{f['n_total']} requests created Jan 1 - Dec 31, 2025. "
                              "Medians use requests with a valid resolution time.", 10, False)])
    slicers(p)
    cards = [("Requests", "Requests"), ("Median Hours", "Median hours to close"),
             ("P90 Hours", "90th percentile hours"),
             ("% Closed Within 30 Days", "% closed within 30 days"),
             ("% No Close Date", "% with no close date")]
    # Display units "None" (1): show 3,655,042, not Power BI's default "4M".
    # The card's own category label is hidden because the title already says it.
    card_format = {"labels": [{"properties": {"labelDisplayUnits": literal(1)}}],
                   "categoryLabels": [{"properties": {"show": literal(False)}}]}
    for i, (m, title) in enumerate(cards):
        p.add("card", 20 + i * 250, 145, 236, 110, {"Values": [measure(m)]}, title=title,
              objects=card_format)
    month = col("fact_requests", "created_month")
    categorical_axis = {"categoryAxis": [{"properties": {"axisType": literal("Categorical")}}]}
    p.add("lineChart", 20, 270, 610, 430, {"Category": [month], "Y": [measure("Median Hours")]},
          title="Median hours to close, by month created", objects=categorical_axis,
          sort=(month, "Ascending"))
    p.add("lineChart", 650, 270, 610, 430, {"Category": [month], "Y": [measure("Requests")]},
          title="Requests by month created", objects=categorical_axis,
          sort=(month, "Ascending"))
    pages.append(p)

    # --- 2. Agencies ---------------------------------------------------------
    p = Page("agencies", "2. Agencies")
    p.text(20, 5, 1240, 60, [("The agency handling a request makes the biggest difference", 18, True),
                             (f"NYPD handles {f['nypd_share']}% of all requests with a "
                              f"{f['nypd_median']} h median.", 10, False)])
    slicers(p)
    # Bar chart uses the short agency code so labels aren't cut off; the table
    # next to it has the full names. Table headers are kept short so every
    # column fits without a scrollbar.
    p.add("clusteredBarChart", 20, 145, 400, 560,
          {"Category": [col("dim_agency", "agency")], "Y": [measure("Median Hours")],
           "Tooltips": [measure("P90 Hours"), measure("Requests"), measure("% No Close Date")]},
          title="Median hours to close, by agency", sort=(measure("Median Hours"), "Descending"))
    p.add("tableEx", 440, 145, 820, 560,
          {"Values": [col("dim_agency", "agency_name"), measure("Requests"),
                      (measure("Median Hours"), "Median h"), (measure("P90 Hours"), "P90 h"),
                      (measure("% Closed Within 30 Days"), "% within 30 days"),
                      (measure("% No Close Date"), "% no close date")]},
          title="Agency summary", sort=(measure("Median Hours"), "Descending"))
    pages.append(p)

    # --- 3. Complaint types --------------------------------------------------
    p = Page("complaint_types", "3. Complaint types")
    p.text(20, 5, 1240, 60, [("Complaint types with 1,000+ requests", 18, True),
                             ("Orange = 20% or more have no close date, so the median is too "
                              "optimistic. Compare those with % closed within 30 days.", 10, False)])
    slicers(p)
    ctype = col("dim_complaint_type", "complaint_type")
    no_close_color = {"values": [{
        "properties": {"backColor": {"solid": {"color": {"expr": measure("No Close Date Color")}}}},
        # Both parts are needed: "data" = apply to every data cell (not just
        # totals), "metadata" = only in the % no close date column.
        "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}],
                     "metadata": ref(measure("% No Close Date"))},
    }]}
    # P90 left out and headers kept short so the orange "% no close date"
    # column (the point of this page) fits without scrolling.
    p.add("tableEx", 20, 145, 780, 560,
          {"Values": [ctype, col("dim_complaint_type", "main_agency"), measure("Requests"),
                      (measure("Median Days"), "Median days"),
                      (measure("% Closed Within 30 Days"), "% within 30 days"),
                      (measure("% No Close Date"), "% no close date")]},
          title="Slowest complaint types (median days, closed requests only)",
          sort=(measure("Median Days"), "Descending"), filters=[has_1000], objects=no_close_color)
    p.add("clusteredBarChart", 820, 145, 440, 560,
          {"Category": [ctype], "Y": [measure("% Closed Within 30 Days")]},
          title="Least likely to close within 30 days",
          sort=(measure("% Closed Within 30 Days"), "Ascending"), filters=[has_1000])
    pages.append(p)

    # --- 4. Borough x complaint type -----------------------------------------
    p = Page("borough_by_type", "4. Borough x type")
    p.text(20, 5, 1240, 60, [("The same complaint type closes at different speeds by borough", 18, True),
                             ("Borough median / citywide median for that type. Red = slower, "
                              "blue = faster. Top 10 complaint types by volume.", 10, False)])
    matrix_filters = [in_filter("dim_borough", "is_known", [1], "known_boroughs"),
                      in_filter("dim_complaint_type", "is_top10_volume", [1], "top10_types")]
    rows = {"Rows": [ctype], "Columns": [col("dim_borough", "borough")]}
    ratio = measure("Ratio to Citywide")
    gradient = {"values": [{
        "properties": {"backColor": {"solid": {"color": {"expr": {"FillRule": {
            "Input": ratio,
            "FillRule": {"linearGradient3": {
                "min": {"color": {"Literal": {"Value": "'#256abf'"}}, "value": {"Literal": {"Value": "0.2D"}}},
                "mid": {"color": {"Literal": {"Value": "'#f0efec'"}}, "value": {"Literal": {"Value": "1D"}}},
                "max": {"color": {"Literal": {"Value": "'#e34948'"}}, "value": {"Literal": {"Value": "3D"}}},
                "nullColoringStrategy": {"strategy": {"Literal": {"Value": "'asZero'"}}},
            }},
        }}}}}},
        "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": ref(ratio)},
    }]}
    # No totals on the ratio matrix: a "Total" ratio (e.g. Bronx across all
    # ten types) mixes types with very different speeds and reads as nonsense.
    no_totals = {"subTotals": [{"properties": {"rowSubtotals": literal(False),
                                               "columnSubtotals": literal(False)}}]}
    p.add("pivotTable", 20, 75, 1240, 315, {**rows, "Values": [ratio]},
          title="Ratio to citywide median", filters=matrix_filters,
          objects={**gradient, **no_totals})
    p.add("pivotTable", 20, 400, 1240, 305, {**rows, "Values": [measure("Median Hours")]},
          title="Median hours to close", filters=matrix_filters)
    pages.append(p)

    # --- 5. Season mix --------------------------------------------------------
    p = Page("season_mix", "5. Season mix")
    p.text(20, 5, 1240, 60, [(f"{f['all_slow']} looks slowest overall. Without NYPD, "
                              f"{f['other_slow']} is slowest.", 18, True),
                             ("Each season has a different share of fast NYPD requests, which moves "
                              "the overall median (a mix effect).", 10, False)])
    season = col("dim_season", "season")
    labels = {"labels": [{"properties": {"show": literal(True)}}]}
    for i, (group, title) in enumerate([(None, "All requests"), ("NYPD", "NYPD only"),
                                        ("Other agencies", "All other agencies")]):
        filters = [in_filter("fact_requests", "agency_group", [group], "agency_group")] if group else None
        p.add("clusteredColumnChart", 20 + i * 420, 75, 400, 400,
              {"Category": [season], "Y": [measure("Median Hours")]},
              title=f"{title}: median hours", objects=labels, filters=filters,
              sort=(season, "Ascending"))
    p.add("tableEx", 20, 490, 600, 215,
          {"Values": [season, measure("% NYPD"), measure("Median Hours"), measure("Requests")]},
          title="NYPD share by season", sort=(season, "Ascending"))
    p.text(640, 490, 620, 215, [
        (f"Why {f['all_slow']} looks slowest", 12, True),
        (f"NYPD (median {f['nypd_median']} h) is a smaller share in {f['all_slow']} "
         f"({f['pct_nypd_slow']}%) than {f['all_fast']} ({f['pct_nypd_fast']}%), and NYPD itself "
         f"is slower in {f['all_slow']} ({f['nypd_med_slow']} h vs {f['nypd_med_fast']} h).", 10, False),
        (f"For all other agencies combined, {f['other_slow']} is slowest ({f['other_med_slow']} h) "
         f"and {f['other_fast']} fastest ({f['other_med_fast']} h).", 10, False),
    ])
    pages.append(p)

    # --- 6. Data notes --------------------------------------------------------
    p = Page("data_notes", "6. Data notes")
    p.text(20, 5, 1240, 50, [("Data and method notes", 18, True)])
    p.add("tableEx", 20, 65, 780, 640,
          {"Values": [col("cleaning_report", "step"), col("cleaning_report", "rule"),
                      col("cleaning_report", "rows_affected"), col("cleaning_report", "action")]},
          title="Cleaning rules and rows affected",
          sort=(col("cleaning_report", "step"), "Ascending"))
    p.text(820, 65, 440, 640, [
        ("Source", 12, True),
        ("NYC Open Data, 311 Service Requests from 2020 to Present (erm2-nwe9). "
         "Requests created 2025-01-01 to 2025-12-31; snapshot 2026-10-09.", 10, False),
        ("Caveats", 12, True),
        (f"Open requests: {f['pct_no_close']}% have no close date, concentrated in a few types "
         f"(Noise - Helicopter {f['heli_open']}%). Their medians are too optimistic.", 10, False),
        (f"{f['n_sixty']} complaint types have a median of almost exactly 60 days, which suggests "
         "an automatic closing rule rather than real work time.", 10, False),
        ("Descriptive, not causal. Winter = Jan-Feb 2025 + Dec 2025.", 10, False),
    ])
    pages.append(p)
    return pages


def write_report():
    definition = REPORT_DIR / "definition"
    definition.mkdir(parents=True)

    write_json(REPORT_DIR / "definition.pbir", {
        "$schema": f"{SCHEMA}/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0",
        "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}},
    })
    write_json(definition / "version.json", {
        "$schema": f"{SCHEMA}/item/report/definition/versionMetadata/1.0.0/schema.json",
        "version": "2.0.0",
    })

    # Themes: the built-in base theme plus our custom colors on top of it.
    shared = REPORT_DIR / "StaticResources" / "SharedResources" / "BaseThemes"
    registered = REPORT_DIR / "StaticResources" / "RegisteredResources"
    shared.mkdir(parents=True)
    registered.mkdir(parents=True)
    shutil.copy(find_base_theme(), shared / f"{BASE_THEME}.json")
    shutil.copy(THEME_FILE, registered / "NYC311Theme.json")

    write_json(definition / "report.json", {
        "$schema": f"{SCHEMA}/item/report/definition/report/3.2.0/schema.json",
        "themeCollection": {
            "baseTheme": {"name": BASE_THEME, "reportVersionAtImport": VERSIONS,
                          "type": "SharedResources"},
            "customTheme": {"name": "NYC311Theme.json", "reportVersionAtImport": VERSIONS,
                            "type": "RegisteredResources"},
        },
        "resourcePackages": [
            {"name": "SharedResources", "type": "SharedResources",
             "items": [{"name": BASE_THEME, "path": f"BaseThemes/{BASE_THEME}.json",
                        "type": "BaseTheme"}]},
            {"name": "RegisteredResources", "type": "RegisteredResources",
             "items": [{"name": "NYC311Theme.json", "path": "NYC311Theme.json",
                        "type": "CustomTheme"}]},
        ],
    })

    pages = build_pages()
    write_json(definition / "pages" / "pages.json", {
        "$schema": f"{SCHEMA}/item/report/definition/pagesMetadata/1.0.0/schema.json",
        "pageOrder": [p.name for p in pages],
        "activePageName": pages[0].name,
    })
    for page in pages:
        page_dir = definition / "pages" / page.name
        write_json(page_dir / "page.json", {
            "$schema": PAGE_SCHEMA,
            "name": page.name,
            "displayName": page.display_name,
            "displayOption": "FitToPage",
            "height": 720,
            "width": 1280,
        })
        for visual in page.visuals:
            write_json(page_dir / "visuals" / visual["name"] / "visual.json", visual)


def find_base_theme():
    """Locate Power BI's built-in base theme file in the Desktop install.

    Checks the regular installer location, then asks Windows where the
    Microsoft Store version is installed (that folder can't be searched
    directly without admin rights).
    """
    roots = [Path(r"C:\Program Files\Microsoft Power BI Desktop")]
    store = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-AppxPackage -Name Microsoft.MicrosoftPowerBIDesktop).InstallLocation"],
        capture_output=True, text=True)
    if store.stdout.strip():
        roots.append(Path(store.stdout.strip()))
    theme = Path("bin/WebView2Resources/minerva/sharedresources/BaseThemes") / f"{BASE_THEME}.json"
    for root in roots:
        if (root / theme).exists():
            return root / theme
    raise FileNotFoundError(f"Power BI base theme {BASE_THEME}.json not found. "
                            "Is Power BI Desktop installed?")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main():
    if not (config.POWERBI_DIR / "fact_requests.csv").exists():
        raise SystemExit("Run src/export_powerbi.py first.")

    # Rebuild only the generated files. Each folder's .pbi subfolder (Power BI's
    # local data cache and settings, git-ignored) is kept, so reopening after a
    # rebuild doesn't force a full data refresh.
    for folder in (MODEL_DIR, REPORT_DIR):
        if folder.exists():
            for item in folder.iterdir():
                if item.name == ".pbi":
                    continue
                shutil.rmtree(item) if item.is_dir() else item.unlink()

    write_model()
    write_report()
    write_json(PROJECT_DIR / f"{NAME}.pbip", {
        "$schema": f"{SCHEMA}/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0",
        "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
        "settings": {"enableAutoRecovery": True},
    })

    n_visuals = sum(1 for _ in REPORT_DIR.rglob("visual.json"))
    print(f"Built {PROJECT_DIR.relative_to(config.PROJECT_ROOT)}\\{NAME}.pbip "
          f"({len(TABLES) + len(SUMMARY_TABLES)} tables, {len(MEASURES)} measures, "
          f"{n_visuals} visuals)")


if __name__ == "__main__":
    main()
