-- Q7. Is the season difference real, or caused by a different mix of requests?
--
-- NYPD requests (parking, noise) close in about an hour and make up close to
-- half of all requests. If one season has more of them, its overall median
-- drops even if no agency got faster. This query splits each season into
-- NYPD vs. all other agencies so the two effects can be told apart.

SELECT
    season,
    COUNT(*)                                                    AS n_requests,
    ROUND(100.0 * AVG((agency = 'NYPD')::int), 2)               AS pct_nypd,
    ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY resolution_hours)::numeric, 2) AS median_hours_all,
    ROUND((percentile_cont(0.5) WITHIN GROUP (ORDER BY resolution_hours)
           FILTER (WHERE agency = 'NYPD'))::numeric, 2)          AS median_hours_nypd,
    ROUND((percentile_cont(0.5) WITHIN GROUP (ORDER BY resolution_hours)
           FILTER (WHERE agency <> 'NYPD'))::numeric, 2)         AS median_hours_other_agencies,
    ROUND(100.0 * SUM(closed_within_30d::int) FILTER (WHERE agency <> 'NYPD')
          / SUM(outcome_30d_known::int) FILTER (WHERE agency <> 'NYPD'), 2) AS pct_30d_other_agencies
FROM v_requests
GROUP BY season
ORDER BY CASE season
    WHEN 'Winter' THEN 1 WHEN 'Spring' THEN 2 WHEN 'Summer' THEN 3 ELSE 4
END;
