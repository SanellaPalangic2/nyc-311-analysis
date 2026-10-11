-- Q5. Slowest complaint types with at least 1,000 requests.
--
-- The 1,000 minimum keeps tiny categories, where a few odd cases can swing
-- the median, out of the ranking.
--
-- Two rankings side by side:
--   rank_by_median    slowest typical closing time (closed requests only)
--   rank_by_30d       lowest % closed within 30 days (counts open requests too)
-- When the two disagree, it's usually because many requests are still open,
-- which the median can't see. A type with no closed requests at all gets a
-- NULL median and can only be ranked by the 30-day measure, so the result
-- keeps any type in the top 15 of EITHER ranking.

WITH stats AS (
    SELECT
        complaint_type,
        mode() WITHIN GROUP (ORDER BY agency)                   AS main_agency,
        COUNT(*)                                                AS n_requests,
        COUNT(resolution_hours)                                 AS n_with_time,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY resolution_hours) AS median_hours,
        percentile_cont(0.9) WITHIN GROUP (ORDER BY resolution_hours) AS p90_hours,
        100.0 * AVG((resolution_status = 'no_close_date')::int) AS pct_no_close_date,
        100.0 * SUM(closed_within_30d::int) / NULLIF(SUM(outcome_30d_known::int), 0) AS pct_closed_within_30d
    FROM v_requests
    GROUP BY complaint_type
    HAVING COUNT(*) >= 1000
)
SELECT
    complaint_type,
    main_agency,
    n_requests,
    n_with_time,
    ROUND(median_hours::numeric, 2)                             AS median_hours,
    ROUND((median_hours / 24)::numeric, 2)                      AS median_days,
    ROUND(p90_hours::numeric, 2)                                AS p90_hours,
    ROUND(pct_no_close_date::numeric, 2)                        AS pct_no_close_date,
    ROUND(pct_closed_within_30d::numeric, 2)                    AS pct_closed_within_30d,
    rank_by_median,
    rank_by_30d
FROM (
    SELECT
        *,
        RANK() OVER (ORDER BY median_hours DESC NULLS LAST)     AS rank_by_median,
        RANK() OVER (ORDER BY pct_closed_within_30d ASC)        AS rank_by_30d
    FROM stats
) ranked
WHERE rank_by_median <= 15 OR rank_by_30d <= 15
ORDER BY rank_by_median;
