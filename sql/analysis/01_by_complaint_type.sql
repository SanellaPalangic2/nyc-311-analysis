-- Q1. Resolution time by complaint type.
--
-- How to read the columns (same in every analysis file):
--   n_requests             all requests in the group
--   n_with_time            requests with a valid resolution time (median/p90 use only these)
--   median_hours           typical request: half closed faster, half slower
--   p90_hours              slow tail: 90% closed within this time
--   mean_hours             shown for comparison; pulled up by very slow requests
--   pct_no_close_date      share with no close date; high values = median is too optimistic
--   pct_closed_within_30d  includes still-open requests as "no", so it isn't biased by them
--
-- percentile_cont(0.5) WITHIN GROUP (ORDER BY x) is Postgres' median. It
-- ignores NULLs, so rows without a resolution time drop out automatically.

SELECT
    complaint_type,
    COUNT(*)                                                    AS n_requests,
    COUNT(resolution_hours)                                     AS n_with_time,
    ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY resolution_hours)::numeric, 2) AS median_hours,
    ROUND(percentile_cont(0.9) WITHIN GROUP (ORDER BY resolution_hours)::numeric, 2) AS p90_hours,
    ROUND(AVG(resolution_hours)::numeric, 2)                    AS mean_hours,
    ROUND(100.0 * AVG((resolution_status = 'no_close_date')::int), 2) AS pct_no_close_date,
    ROUND(100.0 * SUM(closed_within_30d::int) / NULLIF(SUM(outcome_30d_known::int), 0), 2) AS pct_closed_within_30d
FROM v_requests
GROUP BY complaint_type
ORDER BY median_hours DESC NULLS LAST;
