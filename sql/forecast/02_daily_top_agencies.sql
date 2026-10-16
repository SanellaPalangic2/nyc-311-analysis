-- F2. Daily request volume for the 5 agencies with the most requests in 2025.
--
-- Long format: one row per (day, agency). The calendar x agency CROSS JOIN
-- plus LEFT JOIN gives every agency a row for every day, so a day on which an
-- agency received nothing shows up as 0 rather than a gap.

WITH top_agencies AS (
    SELECT agency, COUNT(*) AS total
    FROM clean_311
    GROUP BY agency
    ORDER BY total DESC
    LIMIT 5
),
calendar AS (
    SELECT d::date AS day
    FROM generate_series(DATE '2025-01-01', DATE '2025-12-31', INTERVAL '1 day') AS d
),
daily AS (
    SELECT created_at::date AS day, agency, COUNT(*) AS requests
    FROM clean_311
    WHERE agency IN (SELECT agency FROM top_agencies)
    GROUP BY 1, 2
)
SELECT c.day, t.agency, COALESCE(d.requests, 0) AS requests
FROM calendar c
CROSS JOIN top_agencies t
LEFT JOIN daily d ON d.day = c.day AND d.agency = t.agency
ORDER BY t.total DESC, c.day;
