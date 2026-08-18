-- Future months follow the newest snapshot; closed months stay put.
--
-- Until now the Original Forecast for a month was written once and never again:
-- the first snapshot to cover a month owned it permanently. That protected
-- history, but it also meant a fresher Renewals Pending file could not improve
-- the forecast for months still ahead, which is the whole reason for loading
-- one.
--
-- The rule is now stated by time rather than by arrival order:
--
--   * A month at or before the reporting cut-off is CLOSED. Its forecast is
--     what it was measured against and is never rewritten.
--   * A month after the cut-off is OPEN. A newer accepted snapshot replaces its
--     forecast, because later information is better information.
--   * A month may also be locked explicitly, which pins it even while open.
--
-- Budgets are unaffected either way: a locked budget month keeps its figure
-- regardless of what the forecast beneath it does. The two locks answer
-- different questions and are deliberately separate.

CREATE TABLE IF NOT EXISTS forecast_month_lock (
    forecast_month date PRIMARY KEY,
    locked_at      timestamptz NOT NULL DEFAULT now(),
    locked_by      varchar(120) NOT NULL,
    reason         text NOT NULL,
    -- Where the pinned figure came from, so it stays explainable later.
    source_description text,
    forecast_total numeric(14,2),
    active         boolean NOT NULL DEFAULT true,
    released_at    timestamptz,
    released_by    varchar(120),
    release_reason text
);

CREATE INDEX IF NOT EXISTS ix_forecast_month_lock_active
    ON forecast_month_lock (forecast_month) WHERE active;

COMMENT ON TABLE forecast_month_lock IS
    'Months whose Original Forecast is pinned regardless of later snapshots. '
    'Closed months are protected by the cut-off already; this is for pinning a '
    'month that is still open, or one established from a source other than the '
    'current snapshot.';

-- Whether a month accepts a new forecast, and why.
CREATE OR REPLACE VIEW v_forecast_month_writable AS
SELECT m.forecast_month,
       cut.cut_month,
       m.forecast_month > cut.cut_month              AS is_open,
       l.forecast_month IS NOT NULL                  AS is_pinned,
       (m.forecast_month > cut.cut_month
        AND l.forecast_month IS NULL)                AS is_writable,
       CASE
         WHEN m.forecast_month <= cut.cut_month
           THEN 'closed: at or before the reporting cut-off'
         WHEN l.forecast_month IS NOT NULL
           THEN 'pinned: ' || COALESCE(l.reason, 'locked')
         ELSE 'open: a newer snapshot will replace this month'
       END                                           AS status
FROM (SELECT DISTINCT forecast_month FROM original_forecast
      UNION
      SELECT DISTINCT forecast_month FROM forecast_policy WHERE NOT is_excluded) m
CROSS JOIN (SELECT date_trunc('month', cut_off_date)::date AS cut_month
            FROM reporting_settings WHERE id = 1) cut
LEFT JOIN forecast_month_lock l
       ON l.forecast_month = m.forecast_month AND l.active;
