-- F5. Profile of the busiest hotspot location (see 04_daily_hotspots.sql):
-- its requests on hotspot days vs normal days, the channel they came in by,
-- and how long they took to close compared with the same complaint type elsewhere.
-- Answers "are these real work or instant duplicates?" with numbers.

WITH location_days AS (
    SELECT created_at::date AS day, round(latitude::numeric, 4) AS lat,
           round(longitude::numeric, 4) AS lon, complaint_type, COUNT(*) AS requests
    FROM clean_311
    WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    GROUP BY 1, 2, 3, 4
),
top_location AS (  -- the location + type with the most requests on hotspot days
    SELECT lat, lon, complaint_type
    FROM location_days
    WHERE requests > 100
    GROUP BY 1, 2, 3
    ORDER BY SUM(requests) DESC
    LIMIT 1
),
daily AS (  -- that location's count on every day of the year (0 when it filed nothing)
    SELECT c.day, COALESCE(l.requests, 0) AS requests
    FROM generate_series(DATE '2025-01-01', DATE '2025-12-31', INTERVAL '1 day') AS c(day)
    CROSS JOIN top_location t
    LEFT JOIN location_days l ON l.day = c.day AND l.lat = t.lat AND l.lon = t.lon
                             AND l.complaint_type = t.complaint_type
),
rows AS (  -- requests of that complaint type, marked by whether they came from a hotspot location-day
    SELECT r.open_data_channel_type, r.incident_zip, r.resolution_hours, (h.day IS NOT NULL) AS is_hotspot
    FROM clean_311 r
    JOIN top_location t ON r.complaint_type = t.complaint_type
    LEFT JOIN location_days h ON h.requests > 100 AND h.day = r.created_at::date
         AND h.lat = round(r.latitude::numeric, 4) AND h.lon = round(r.longitude::numeric, 4)
         AND h.complaint_type = r.complaint_type
)
SELECT
    (SELECT complaint_type FROM top_location)                                     AS complaint_type,
    (SELECT incident_zip FROM rows WHERE is_hotspot GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1) AS zip,
    (SELECT SUM(requests) FROM daily)                                             AS location_requests_year,
    (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY requests) FROM daily)     AS location_median_per_day,
    (SELECT MAX(requests) FROM daily)                                             AS location_max_per_day,
    (SELECT COUNT(*) FROM daily WHERE requests > 100)                             AS location_hotspot_days,
    (SELECT 100.0 * AVG((open_data_channel_type = 'MOBILE')::int) FROM rows WHERE is_hotspot)     AS hotspot_pct_mobile,
    (SELECT 100.0 * AVG((open_data_channel_type = 'MOBILE')::int) FROM rows WHERE NOT is_hotspot) AS other_pct_mobile,
    (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY resolution_hours) FROM rows WHERE is_hotspot)     AS hotspot_median_hours_to_close,
    (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY resolution_hours) FROM rows WHERE NOT is_hotspot) AS other_median_hours_to_close;
