-- Raw landing table: one row per line of the downloaded CSV pages.
--
-- Every column is TEXT on purpose. The raw layer stores exactly what the API
-- sent, so a single malformed date or number can't make the bulk load fail.
-- Converting to real types (timestamps, numbers) happens in the cleaning step,
-- where bad values are counted and reported instead of crashing the load.
--
-- There is no PRIMARY KEY on unique_key: if the source has duplicates we want
-- to load them and count them, not have COPY abort on the first one.

DROP TABLE IF EXISTS raw_311;

CREATE TABLE raw_311 (
    unique_key                      TEXT,
    created_date                    TEXT,
    closed_date                     TEXT,
    agency                          TEXT,
    agency_name                     TEXT,
    complaint_type                  TEXT,
    descriptor                      TEXT,
    location_type                   TEXT,
    incident_zip                    TEXT,
    city                            TEXT,
    borough                         TEXT,
    status                          TEXT,
    resolution_action_updated_date  TEXT,
    community_board                 TEXT,
    open_data_channel_type          TEXT,
    latitude                        TEXT,
    longitude                       TEXT
);
