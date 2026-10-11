-- Q8. What does the distribution of resolution times look like?
--
-- Resolution times run from seconds to over a year, so equal-width bins would
-- squash almost everything into the first bar. Instead the bins are equal
-- width on a log10 scale: each bin covers hours from 10^bin_start to
-- 10^(bin_start + 0.25), so every 4 bins = 10x longer.
-- Requests closed in exactly 0 seconds have no log value and are counted
-- in their own row (bin_start = NULL).

SELECT
    CASE WHEN resolution_hours > 0
         THEN FLOOR(LOG(resolution_hours::numeric) / 0.25) * 0.25
    END                                                         AS log10_bin_start,
    COUNT(*)                                                    AS n_requests
FROM v_requests
WHERE resolution_hours IS NOT NULL
GROUP BY 1
ORDER BY 1 NULLS FIRST;
