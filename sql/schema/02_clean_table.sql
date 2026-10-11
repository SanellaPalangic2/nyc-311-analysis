-- Clean table: one row per request, with real data types and cleaning flags.
-- Built by src/clean.py. Rows are FLAGGED rather than deleted wherever
-- possible, so every cleaning decision can be counted and undone in a query.

-- CASCADE also drops the v_requests view built on this table; run_analysis.py recreates it.
DROP TABLE IF EXISTS clean_311 CASCADE;

CREATE TABLE clean_311 (
    unique_key              BIGINT PRIMARY KEY,  -- duplicates removed, so this is enforceable
    created_at              TIMESTAMP NOT NULL,  -- NYC local time, as published
    closed_at               TIMESTAMP,
    agency                  TEXT,
    agency_name             TEXT,
    complaint_type          TEXT,
    descriptor              TEXT,
    borough                 TEXT,                -- 'UNSPECIFIED' when missing
    incident_zip            TEXT,                -- text, not a number: ZIPs can start with 0
    status                  TEXT,
    open_data_channel_type  TEXT,
    latitude                DOUBLE PRECISION,
    longitude               DOUBLE PRECISION,
    resolution_status       TEXT NOT NULL,       -- closed / no_close_date / closed_before_created / placeholder_date
    resolution_hours        DOUBLE PRECISION,    -- only filled when resolution_status = 'closed'
    is_zero_duration        BOOLEAN NOT NULL,    -- closed the same second it was created
    is_extreme              BOOLEAN NOT NULL,    -- above Q3 + 3*IQR for its complaint type
    is_borough_known        BOOLEAN NOT NULL,
    season                  TEXT NOT NULL,
    created_month           SMALLINT NOT NULL
);

-- One row per cleaning rule with the number of rows it affected, so the
-- README numbers come from a table you can query, not from console output.
DROP TABLE IF EXISTS cleaning_report;

CREATE TABLE cleaning_report (
    step           SMALLINT,
    rule           TEXT,
    rows_affected  BIGINT,
    action         TEXT
);
