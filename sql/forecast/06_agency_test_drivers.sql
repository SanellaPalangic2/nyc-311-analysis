-- F6. What each top-5 agency's demand was made of in the test period
-- (2025-10-23 to 2025-12-31): its largest complaint type, and the largest
-- complaint type on its single busiest day. Used to explain why some agencies
-- are harder to forecast; never a model input.

WITH top_agencies AS (
    SELECT agency FROM clean_311 GROUP BY agency ORDER BY COUNT(*) DESC LIMIT 5
),
test AS (
    SELECT agency, created_at::date AS day, complaint_type
    FROM clean_311
    WHERE created_at >= '2025-10-23' AND created_at < '2026-01-01'
      AND agency IN (SELECT agency FROM top_agencies)
),
type_share AS (
    SELECT agency, complaint_type, COUNT(*) AS n,
           100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY agency) AS pct,
           ROW_NUMBER() OVER (PARTITION BY agency ORDER BY COUNT(*) DESC) AS rk
    FROM test GROUP BY agency, complaint_type
),
busiest AS (
    SELECT agency, day, COUNT(*) AS n,
           ROW_NUMBER() OVER (PARTITION BY agency ORDER BY COUNT(*) DESC) AS rk
    FROM test GROUP BY agency, day
),
busiest_type AS (
    SELECT t.agency, t.complaint_type, COUNT(*) AS n,
           ROW_NUMBER() OVER (PARTITION BY t.agency ORDER BY COUNT(*) DESC) AS rk
    FROM test t JOIN busiest b ON b.agency = t.agency AND b.day = t.day AND b.rk = 1
    GROUP BY t.agency, t.complaint_type
)
SELECT s.agency,
       s.complaint_type AS top_type, round(s.pct::numeric, 1) AS top_type_pct,
       b.day AS busiest_day, b.n AS busiest_day_requests,
       bt.complaint_type AS busiest_day_top_type, bt.n AS busiest_day_top_type_requests
FROM type_share s
JOIN busiest b ON b.agency = s.agency AND b.rk = 1
JOIN busiest_type bt ON bt.agency = s.agency AND bt.rk = 1
WHERE s.rk = 1
ORDER BY s.agency;
