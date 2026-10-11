-- Analysis view: clean_311 plus two columns for the "% closed within 30 days"
-- metric, so every analysis query can use them without repeating the logic.
--
-- Why this metric exists: medians can only use requests that have closed, and
-- the requests still open are the slowest ones, so medians lean optimistic.
-- "% closed within 30 days" also counts the still-open requests (as "no"),
-- which removes that bias. Every 2025 request is at least 9 months old at the
-- data snapshot (2026-10-09), so we know the 30-day outcome for all of them.
--
-- outcome_30d_known is FALSE when we can't tell when (or whether) a request
-- closed: closed_date before created_date, or status 'Closed' with no date.
-- Those rows are left out of the denominator.

CREATE OR REPLACE VIEW v_requests AS
SELECT
    *,
    (
        resolution_status = 'closed'
        OR (resolution_status = 'no_close_date' AND status IS DISTINCT FROM 'Closed')
    ) AS outcome_30d_known,
    (resolution_status = 'closed' AND resolution_hours <= 30 * 24) AS closed_within_30d
FROM clean_311;
