-- Q2. Resolution time by agency (column meanings: see 01_by_complaint_type.sql).
-- mode() picks the most common agency_name for each agency code, so the
-- result shows a readable name without splitting the group.

SELECT
    agency,
    mode() WITHIN GROUP (ORDER BY agency_name)                  AS agency_name,
    COUNT(*)                                                    AS n_requests,
    COUNT(resolution_hours)                                     AS n_with_time,
    COUNT(DISTINCT complaint_type)                              AS n_complaint_types,
    ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY resolution_hours)::numeric, 2) AS median_hours,
    ROUND(percentile_cont(0.9) WITHIN GROUP (ORDER BY resolution_hours)::numeric, 2) AS p90_hours,
    ROUND(AVG(resolution_hours)::numeric, 2)                    AS mean_hours,
    ROUND(100.0 * AVG((resolution_status = 'no_close_date')::int), 2) AS pct_no_close_date,
    ROUND(100.0 * SUM(closed_within_30d::int) / NULLIF(SUM(outcome_30d_known::int), 0), 2) AS pct_closed_within_30d
FROM v_requests
GROUP BY agency
ORDER BY median_hours DESC NULLS LAST;
