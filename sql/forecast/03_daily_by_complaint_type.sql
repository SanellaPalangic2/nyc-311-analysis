-- F3. Daily volume per complaint type. Used only to explain outlier days
-- (which complaint type drove a spike or a dip), not as a model input.
-- Days on which a type had no requests are simply absent; the Python side
-- fills them with 0 when it pivots to a day x type table.

SELECT created_at::date AS day, complaint_type, COUNT(*) AS requests
FROM clean_311
GROUP BY 1, 2
ORDER BY 1, 2;
