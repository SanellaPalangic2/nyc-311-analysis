-- F4. Daily "hotspot" requests: requests from one location (coordinates
-- rounded to 4 decimals, about 10 m) of one complaint type, on a day when that
-- location filed more than 100 of that type.
--
-- Why: in 2025 one Bronx location (ZIP 10466) filed about 123,000 Noise -
-- Residential complaints, in bursts of up to several thousand a day, mostly
-- from the mobile app. Those bursts are real records, but no calendar or
-- history can predict them. This query measures them so the forecasts can
-- report how much of their error the bursts cause.
--
-- The rule only looks at requests from the same day, so it could be applied at
-- the end of each day in real time: it uses no future information.
-- Rows with no coordinates can't be grouped by location and never count.

WITH location_days AS (
    SELECT
        created_at::date               AS day,
        round(latitude::numeric, 4)    AS lat,
        round(longitude::numeric, 4)   AS lon,
        complaint_type,
        agency,
        COUNT(*)                       AS requests
    FROM clean_311
    WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    GROUP BY 1, 2, 3, 4, 5
    HAVING COUNT(*) > 100
)
SELECT day, agency, SUM(requests) AS hotspot_requests
FROM location_days
GROUP BY day, agency
ORDER BY day, agency;
