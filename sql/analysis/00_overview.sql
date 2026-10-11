-- Q0. Overview: how many requests, and how skewed are resolution times?
-- The gap between mean and median shows why this project uses medians.

SELECT
    COUNT(*)                                                    AS n_requests,
    COUNT(resolution_hours)                                     AS n_with_resolution_time,
    COUNT(DISTINCT complaint_type)                              AS n_complaint_types,
    COUNT(DISTINCT agency)                                      AS n_agencies,
    MIN(created_at)                                             AS first_created,
    MAX(created_at)                                             AS last_created,
    ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY resolution_hours)::numeric, 2) AS median_hours,
    ROUND(percentile_cont(0.9) WITHIN GROUP (ORDER BY resolution_hours)::numeric, 2) AS p90_hours,
    ROUND(AVG(resolution_hours)::numeric, 2)                    AS mean_hours,
    ROUND(100.0 * AVG((resolution_status = 'no_close_date')::int), 2) AS pct_no_close_date,
    ROUND(100.0 * SUM(closed_within_30d::int) / SUM(outcome_30d_known::int), 2) AS pct_closed_within_30d
FROM v_requests;
