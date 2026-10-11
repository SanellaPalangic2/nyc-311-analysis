-- Q6. Interaction: does the same complaint type close faster in some boroughs?
--
-- Comparing boroughs directly is misleading because each borough has a
-- different mix of complaint types. Comparing within one complaint type
-- removes that mix effect.
--
-- Scope: the 10 highest-volume complaint types, known boroughs only.
-- ratio_to_citywide = borough median / citywide median for that type.
--   1.00 = same as citywide, 2.00 = twice as slow, 0.50 = twice as fast.

WITH top_types AS (
    SELECT complaint_type
    FROM v_requests
    GROUP BY complaint_type
    ORDER BY COUNT(*) DESC
    LIMIT 10
),
citywide AS (
    SELECT
        complaint_type,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY resolution_hours) AS citywide_median_hours
    FROM v_requests
    WHERE complaint_type IN (SELECT complaint_type FROM top_types)
      AND is_borough_known
    GROUP BY complaint_type
),
by_borough AS (
    SELECT
        complaint_type,
        borough,
        COUNT(*)                                                AS n_requests,
        COUNT(resolution_hours)                                 AS n_with_time,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY resolution_hours) AS median_hours,
        percentile_cont(0.9) WITHIN GROUP (ORDER BY resolution_hours) AS p90_hours,
        100.0 * SUM(closed_within_30d::int) / NULLIF(SUM(outcome_30d_known::int), 0) AS pct_closed_within_30d
    FROM v_requests
    WHERE complaint_type IN (SELECT complaint_type FROM top_types)
      AND is_borough_known
    GROUP BY complaint_type, borough
)
SELECT
    b.complaint_type,
    b.borough,
    b.n_requests,
    b.n_with_time,
    ROUND(b.median_hours::numeric, 2)                           AS median_hours,
    ROUND(c.citywide_median_hours::numeric, 2)                  AS citywide_median_hours,
    ROUND((b.median_hours / NULLIF(c.citywide_median_hours, 0))::numeric, 2) AS ratio_to_citywide,
    ROUND(b.p90_hours::numeric, 2)                              AS p90_hours,
    ROUND(b.pct_closed_within_30d::numeric, 2)                  AS pct_closed_within_30d
FROM by_borough b
JOIN citywide c USING (complaint_type)
ORDER BY b.complaint_type, b.median_hours DESC;
