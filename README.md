# NYC 311 Service Request Analysis

> **Status:** in progress. Sections marked _TBD_ get filled in as each stage is finished, using only numbers produced by the project's scripts and queries.

## Question

What drives slow resolution times for NYC 311 service requests, broken down by **complaint type, agency, borough, and season**?

## Data

- **Source:** NYC Open Data, *311 Service Requests from 2020 to Present* (Socrata dataset `erm2-nwe9`)
- **API:** https://data.cityofnewyork.us/resource/erm2-nwe9.json
- **Scope:** requests **created** from 2025-01-01 through 2025-12-31 (all four seasons)
- **Row counts:** _TBD (raw, after cleaning)_

## Stack

Python 3 (pandas) → PostgreSQL (storage and SQL analysis) → matplotlib/seaborn (charts) → Power BI (dashboard)

## Project structure

```
.
├── data/
│   ├── raw/            # downloaded API pages (git-ignored)
│   └── processed/      # cleaned outputs (git-ignored)
├── src/                # Python scripts (download, load, clean, findings, export)
│   └── config.py       # paths, date range, and .env settings shared by all scripts
├── sql/                # one analysis question per .sql file
├── tests/              # pytest tests for the cleaning functions
├── outputs/
│   ├── charts/         # PNG charts
│   └── powerbi/        # tidy CSVs for Power BI (git-ignored)
├── docs/               # Power BI build instructions, notes
├── .env.example        # template for secrets (copy to .env)
└── requirements.txt
```

## Methods

_TBD: ingestion, cleaning rules and how many rows each affected, why medians are used, how open requests are handled._

## Key findings

_TBD_

## Limitations

_TBD_

## How to reproduce

1. Install Python 3 and PostgreSQL.
2. Create the virtual environment and install the dependencies:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in `DATABASE_URL` (and optionally `SOCRATA_APP_TOKEN`).
4. _TBD: run order of the scripts._
