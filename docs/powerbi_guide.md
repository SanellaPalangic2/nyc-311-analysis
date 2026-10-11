# Building the Power BI dashboard

Step-by-step instructions for building the dashboard yourself in Power BI Desktop.
Plan on 1–2 hours the first time.

## Fast path: open the generated project

The finished dashboard is already in the repo as a Power BI Project:

```powershell
python src/export_powerbi.py          # CSVs (if you haven't already)
python src/build_powerbi_project.py   # regenerates powerbi/ (optional)
```

1. Double-click `powerbi/NYC311.pbip` (or **File → Open** in Power BI Desktop).
2. If Power BI says the data needs refreshing, click **Refresh**. Loading the
   3.66M rows takes a few minutes.
3. On a different computer, the CSV folder will be in a different place:
   **Home → Transform data → Edit parameters → DataFolder**, paste the full path
   to your `outputs/powerbi\` folder (ending with `\`), then **Refresh**.
4. Run the **answer-key check** in section 3 below to confirm the numbers.
5. **File → Save** keeps it as a .pbip. Power BI may rewrite some files in a newer
   format; that's normal.

The project is the same model, measures and pages described below. The rest of
this guide explains each piece and how to build it by hand.

> Report-level conditional formatting (the orange % No Close Date cells and the
> heatmap colors on page 4) is the part most likely to need a manual touch-up.
> If a color doesn't show, set it as described in section 5.

---

## 0. Before you start

1. Make the CSVs (about 2.5 minutes):
   ```powershell
   .\.venv\Scripts\Activate.ps1
   python src/export_powerbi.py
   ```
   Files land in `outputs/powerbi/`.
2. Install **Power BI Desktop** (free, from the Microsoft Store).

### What's in the folder

| File | Rows | What it's for |
|---|---:|---|
| `fact_requests.csv` | 3,655,042 | One row per request. Every measure is calculated from this. |
| `dim_agency.csv` | 15 | Agency code → full name |
| `dim_complaint_type.csv` | 193 | Main agency, total requests, `has_1000_plus` flag |
| `dim_borough.csv` | 6 | Sort order, `is_known` flag (0 = UNSPECIFIED) |
| `dim_season.csv` | 4 | Calendar sort order |
| `summary_*.csv` | small | **Answer key**: the SQL results. Your measures must match these. |
| `cleaning_report.csv` | 14 | Rows affected by each cleaning rule |

**Why one row per request (interview answer):** medians can't be added up or averaged.
The median of the five borough medians is not the citywide median. If Power BI only
had pre-computed medians, a filter like "Bronx + Summer" would show a wrong number.
With request-level rows, Power BI computes the true median for any filter combination.

---

## 1. Load the data

1. **Home → Get data → Text/CSV** → choose `fact_requests.csv` → click **Transform Data**
   (not Load) so you can check the column types in Power Query.
2. Check these types (click the icon left of each column name to change one):

   | Column | Type |
   |---|---|
   | `unique_key` | Whole Number |
   | `created_date` | Date |
   | `created_month` | Whole Number |
   | `resolution_hours` | Decimal Number |
   | `has_no_close_date`, `outcome_30d_known`, `closed_within_30d`, `is_extreme`, `is_zero_duration` | Whole Number |
   | everything else | Text |

   > If your Windows region uses a comma as the decimal separator, `resolution_hours`
   > may load wrong (e.g. 6.6103 read as 66103). Fix: right-click the column →
   > **Change Type → Using Locale…** → Decimal Number, **English (United States)**.

3. **Close & Apply**. The import takes a few minutes. Power BI compresses the 342 MB
   CSV to a fraction of that.
4. Repeat **Get data → Text/CSV** for each `dim_*.csv`, each `summary_*.csv`, and
   `cleaning_report.csv`. These are small, so **Load** directly is fine.

---

## 2. Build the model (Model view, the third icon on the left)

1. **Delete any relationships Power BI created by itself** (right-click a line → Delete).
   Auto-detect sometimes links the summary tables, and they must stay unlinked.
2. Create these four relationships. Drag the column from `fact_requests` onto the
   matching column in the dimension table:

   | From (many side) | To (one side) |
   |---|---|
   | `fact_requests[agency]` | `dim_agency[agency]` |
   | `fact_requests[complaint_type]` | `dim_complaint_type[complaint_type]` |
   | `fact_requests[borough]` | `dim_borough[borough]` |
   | `fact_requests[season]` | `dim_season[season]` |

   Each one should show **Many to one (\*:1)** and **Single** cross-filter direction.
   This shape (one fact table in the middle, dimensions around it) is a **star schema**.
3. **Sort seasons and boroughs properly.** In Data view, select `dim_season[season]`
   → **Column tools → Sort by column → season_order**. Do the same for
   `dim_borough[borough]` → `borough_order`. Without this, seasons sort alphabetically
   (Fall, Spring, Summer, Winter).
4. **Hide the duplicate columns on the fact table:** in the Data pane, right-click
   `fact_requests[agency]`, `[complaint_type]`, `[borough]`, `[season]` → **Hide in
   report view**. Then every visual uses the dimension columns, so filters always
   flow through the relationships.
5. Hide the summary tables from report view too (right-click → Hide), except when
   you're checking numbers.

---

## 3. Create the measures

Select `fact_requests` in the Data pane → **Home → New measure**, paste one measure,
press Enter, and repeat. Set the format in **Measure tools** (shown after each).

```DAX
Requests = COUNTROWS ( fact_requests )
```
Format: Whole number, thousands separator.

```DAX
Median Hours =
CALCULATE (
    MEDIAN ( fact_requests[resolution_hours] ),
    fact_requests[resolution_status] = "closed"
)
```
Format: Decimal, 2 places. Only requests with a valid resolution time count, which
matches the SQL.

```DAX
P90 Hours =
CALCULATE (
    PERCENTILE.INC ( fact_requests[resolution_hours], 0.9 ),
    fact_requests[resolution_status] = "closed"
)
```
Format: Decimal, 2 places. `PERCENTILE.INC` interpolates between ranks the same way
as Postgres `percentile_cont`, so the numbers match.

```DAX
Mean Hours =
CALCULATE (
    AVERAGE ( fact_requests[resolution_hours] ),
    fact_requests[resolution_status] = "closed"
)
```

```DAX
Median Days = DIVIDE ( [Median Hours], 24 )
```

```DAX
% Closed Within 30 Days =
DIVIDE (
    SUM ( fact_requests[closed_within_30d] ),
    SUM ( fact_requests[outcome_30d_known] )
)
```
Format: Percentage, 2 places. Still-open requests count as "not closed", so this
measure isn't biased by them.

```DAX
% No Close Date =
DIVIDE ( SUM ( fact_requests[has_no_close_date] ), [Requests] )
```
Format: Percentage, 2 places.

```DAX
% NYPD =
DIVIDE (
    CALCULATE ( [Requests], fact_requests[agency_group] = "NYPD" ),
    [Requests]
)
```
Format: Percentage, 2 places.

```DAX
Citywide Median Hours =
CALCULATE (
    [Median Hours],
    REMOVEFILTERS ( dim_borough ),
    dim_borough[is_known] = 1
)
```
`REMOVEFILTERS` throws away the borough filter coming from the matrix column or a
slicer, so this is the median across all known boroughs. Other filters, such as
complaint type and season, stay in place.

```DAX
Ratio to Citywide = DIVIDE ( [Median Hours], [Citywide Median Hours] )
```
Format: Custom format string `0.00"x"`.

### Check your measures against the answer key (do not skip)

1. Add a **Table** visual with `dim_agency[agency]`, `Requests`, `Median Hours`,
   `P90 Hours`, `% No Close Date`, `% Closed Within 30 Days`.
2. Compare with `summary_by_agency` (in Data view). Every value must match.
   Example: NYPD = 1,717,594 requests, Median Hours 1.26, P90 6.76.
3. Do the same for borough (filter `dim_borough[is_known]` = 1) against
   `summary_by_borough`, and season against `summary_by_season`.
4. Delete the check table when you're done.

> A value ending in exactly 5 at the third decimal can display 0.01 differently
> (e.g. OOS p90 is exactly 1142.635: Postgres shows 1142.64). That's rounding,
> not an error. Anything bigger than 0.01 means a measure or relationship is wrong.

---

## 4. Apply the theme

**View → Themes → Browse for themes** → `docs/powerbi_theme.json`. This sets the same
blue/orange the charts use (checked for colorblind safety) and the off-white background.

---

## 5. Build the pages

General layout rule for every page: **slicers in one row across the top**, title text
box top-left, visuals below. Add a title to every visual that says what it shows.

### Page 1 — Overview

| Visual | Settings |
|---|---|
| Text box | "NYC 311 resolution times, 2025" + one line: "3,655,042 requests created Jan 1–Dec 31, 2025" |
| 3 slicers (top row) | `dim_borough[borough]`, `dim_season[season]`, `dim_agency[agency_name]`. Style: **Dropdown**. On the borough slicer, add the visual filter `is_known` = 1. |
| 5 Cards | `Requests`, `Median Hours`, `P90 Hours`, `% Closed Within 30 Days`, `% No Close Date` |
| Line chart | X: `fact_requests[created_month]`, Y: `Median Hours`. In Format → X-axis, set **Type: Categorical** so all 12 months show. Title: "Median hours to close, by month created" |
| Line chart | X: `created_month` (Categorical), Y: `Requests`. Title: "Requests by month created" |

Use **two line charts, not one chart with two y-axes**. Two axes on different scales
make lines look related when they may not be.

### Page 2 — Agencies

| Visual | Settings |
|---|---|
| Clustered bar chart | Y: `dim_agency[agency_name]`, X: `Median Hours`. Sort by Median Hours, descending. Tooltips: add `P90 Hours`, `Requests`, `% No Close Date`. |
| Table | `agency_name`, `Requests`, `Median Hours`, `P90 Hours`, `% Closed Within 30 Days`, `% No Close Date`. Conditional formatting → **Data bars** on `Median Hours`. |

Talking point: NYPD's median is 1.26 h on 46.99% of all requests. EDC's 7,845.19 h
median comes with 47.06% of requests still lacking a close date.

### Page 3 — Complaint types

| Visual | Settings |
|---|---|
| Table | `dim_complaint_type[complaint_type]`, `main_agency`, `Requests`, `Median Days`, `P90 Hours`, `% Closed Within 30 Days`, `% No Close Date`. Visual filter: `has_1000_plus` = 1. Sort by `Median Days` descending. |
| Conditional formatting on that table | `% No Close Date` → **Background color → Rules**: if value ≥ 0.2 → `#eb6834` (orange). This flags types where the median is too optimistic. |
| Clustered bar chart | Y: `complaint_type`, X: `% Closed Within 30 Days`. Visual filters: `has_1000_plus` = 1, and `complaint_type` **Top N = 15, Bottom** by `% Closed Within 30 Days`. Title: "Least likely to close within 30 days" |

### Page 4 — Borough × complaint type

| Visual | Settings |
|---|---|
| Matrix | Rows: `dim_complaint_type[complaint_type]`, Columns: `dim_borough[borough]`, Values: `Ratio to Citywide` |
| Matrix filters | `dim_borough[is_known]` = 1; `complaint_type` **Top N = 10** by `Requests` |
| Conditional formatting | Values → **Background color → fx → Format style: Gradient**, tick **Add a middle color**. Minimum: Number 0.2, `#256abf`. Center: Number 1, `#f0efec`. Maximum: Number 3, `#e34948`. |
| Second matrix (optional) | Same rows/columns with `Median Hours`, to show the raw times |

Check: Illegal Parking × Bronx = 1.52x, Water System × Staten Island = 2.84x
(`summary_type_by_borough`). Don't put a borough slicer on this page; boroughs are
already the columns.

### Page 5 — Season mix effect

Build three **Clustered column charts** side by side, each with X: `dim_season[season]`,
Y: `Median Hours`, and data labels on:

| Chart | Visual filter | Title |
|---|---|---|
| 1 | none | "All requests" |
| 2 | `fact_requests[agency_group]` = NYPD | "NYPD only" |
| 3 | `agency_group` = Other agencies | "All other agencies" |

Add a **Table** below with `season`, `% NYPD`, `Median Hours`. Use three separate charts
rather than small multiples because the scales are very different (about 1 h for NYPD vs
about 60 h for the others), and separate charts let each one size its own y-axis.

Check: Winter 14.00 / Summer 4.01 (all), Summer 73.38 / Winter 56.55 (other agencies).
Add a text box: "Winter looks slowest overall only because a smaller share of its
requests go to NYPD (42.77% vs 48.82% in Summer). Excluding NYPD, Summer is slowest."

### Page 6 — Data and method notes

| Visual | Settings |
|---|---|
| Table | `cleaning_report`: `rule`, `rows_affected`, `action` (sort by `step`) |
| Text box | Data source + date range, the caveats from `outputs/findings.md` (open requests, possible 60-day automatic closures, descriptive not causal), and "Winter = Jan–Feb 2025 + Dec 2025" |

---

## 6. Make the slicers work across pages

**View → Sync slicers.** Select each slicer on page 1 and tick **Sync** and **Visible**
for pages 1, 2 and 3. Leave pages 4 and 5 unticked: page 4 already splits by borough,
and page 5 compares seasons, so a season slicer there would break the comparison.

Optional: on pages 1–3, add the **Filters pane** filter `fact_requests[is_extreme]` = 0
to show results without the flagged outliers. Medians barely move; means do. This is a
good way to show *why* medians were the right choice.

---

## 7. Save and share

1. Save as `nyc311_dashboard.pbix` in `outputs/`. **Don't commit it.** A .pbix contains
   the imported data (`.gitignore` already excludes `*.pbix`).
2. For the README, export screenshots: **File → Export → Export to PDF**, or take a
   screenshot of each page and save it to `outputs/dashboard/` as PNG.
3. To share a live link, **Publish** to the Power BI Service (needs a work/school
   account), or put the screenshots in the README and on your portfolio.
