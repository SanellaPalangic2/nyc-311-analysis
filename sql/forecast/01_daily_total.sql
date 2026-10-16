-- F1. Daily request volume: one row per calendar day of 2025, total requests.
--
-- The calendar comes from generate_series and the counts are LEFT JOINed onto
-- it, so a day with no requests appears as 0 instead of silently missing.
-- hours_with_data and first/last_created show whether a day is complete (a
-- partial day, e.g. a download that stopped mid-day, has fewer hours and an
-- early last_created). created_at is NYC local time, so the spring DST day
-- (2025-03-09) has only 23 clock hours and 2025-11-02 has 25.

WITH calendar AS (
    SELECT d::date AS day
    FROM generate_series(DATE '2025-01-01', DATE '2025-12-31', INTERVAL '1 day') AS d
),
daily AS (
    SELECT
        created_at::date                               AS day,
        COUNT(*)                                       AS requests,
        COUNT(DISTINCT date_trunc('hour', created_at)) AS hours_with_data,
        MIN(created_at)                                AS first_created,
        MAX(created_at)                                AS last_created
    FROM clean_311
    GROUP BY 1
)
SELECT
    c.day,
    COALESCE(d.requests, 0)        AS requests,
    COALESCE(d.hours_with_data, 0) AS hours_with_data,
    d.first_created,
    d.last_created
FROM calendar c
LEFT JOIN daily d USING (day)
ORDER BY c.day;
