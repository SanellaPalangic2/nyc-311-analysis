-- Q3. Resolution time by borough (column meanings: see 01_by_complaint_type.sql).
-- The 2,607 requests with an unknown borough are left out here only.

SELECT
    borough,
    COUNT(*)                                                    AS n_requests,
    COUNT(resolution_hours)                                     AS n_with_time,
    ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY resolution_hours)::numeric, 2) AS median_hours,
    ROUND(percentile_cont(0.9) WITHIN GROUP (ORDER BY resolution_hours)::numeric, 2) AS p90_hours,
    ROUND(AVG(resolution_hours)::numeric, 2)                    AS mean_hours,
    ROUND(100.0 * AVG((resolution_status = 'no_close_date')::int), 2) AS pct_no_close_date,
    ROUND(100.0 * SUM(closed_within_30d::int) / NULLIF(SUM(outcome_30d_known::int), 0), 2) AS pct_closed_within_30d
FROM v_requests
WHERE is_borough_known
GROUP BY borough
ORDER BY median_hours DESC;
