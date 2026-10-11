-- Q4. Resolution time by season of the date the request was created
-- (column meanings: see 01_by_complaint_type.sql).
-- Winter = Jan, Feb and Dec 2025. The CASE puts seasons in calendar order
-- instead of alphabetical.

SELECT
    season,
    COUNT(*)                                                    AS n_requests,
    COUNT(resolution_hours)                                     AS n_with_time,
    ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY resolution_hours)::numeric, 2) AS median_hours,
    ROUND(percentile_cont(0.9) WITHIN GROUP (ORDER BY resolution_hours)::numeric, 2) AS p90_hours,
    ROUND(AVG(resolution_hours)::numeric, 2)                    AS mean_hours,
    ROUND(100.0 * AVG((resolution_status = 'no_close_date')::int), 2) AS pct_no_close_date,
    ROUND(100.0 * SUM(closed_within_30d::int) / NULLIF(SUM(outcome_30d_known::int), 0), 2) AS pct_closed_within_30d
FROM v_requests
GROUP BY season
ORDER BY CASE season
    WHEN 'Winter' THEN 1 WHEN 'Spring' THEN 2 WHEN 'Summer' THEN 3 ELSE 4
END;
