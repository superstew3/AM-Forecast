-- Movement reporting counts by flag, not by primary movement_type.
--
-- A policy that moves manager AND changes amount in the same snapshot has
-- movement_type = 'amount_changed'. Counting transfers by movement_type would
-- miss it entirely and understate manager transfers. The boolean flags are
-- independent, so each attribute is counted wherever it occurred.

-- v_forecast_movement_detail was created with SELECT m.*, which PostgreSQL
-- expands and freezes at creation time. It therefore predates the flag columns
-- added in 0005 and has to be dropped and rebuilt, not replaced.
DROP VIEW IF EXISTS v_forecast_movement_summary;
DROP VIEW IF EXISTS v_manager_transfer_detail;
DROP VIEW IF EXISTS v_forecast_movement_detail;

CREATE VIEW v_forecast_movement_detail AS
SELECT m.*,
       COALESCE(rt.canonical_manager, m.to_manager)   AS canonical_to_manager,
       COALESCE(rf.canonical_manager, m.from_manager) AS canonical_from_manager,
       p.client_code, p.policy_number, p.class_abbrev, p.underwriter_abbrev,
       p.expiry_date
FROM forecast_movement m
LEFT JOIN v_manager_resolution rt ON rt.source_manager = m.to_manager
LEFT JOIN v_manager_resolution rf ON rf.source_manager = m.from_manager
LEFT JOIN LATERAL (
    SELECT client_code, policy_number, class_abbrev, underwriter_abbrev, expiry_date
    FROM forecast_policy fp
    WHERE fp.policy_id = m.policy_id
    ORDER BY fp.snapshot_id DESC LIMIT 1
) p ON true;

CREATE VIEW v_forecast_movement_summary AS
SELECT forecast_month,
       COALESCE(canonical_from_manager, canonical_to_manager) AS canonical_manager,
       SUM(original_income)                                   AS original_expected_income,
       COUNT(*) FILTER (WHERE removed)                        AS policies_removed,
       COALESCE(SUM(previous_income) FILTER (WHERE removed), 0)
                                                              AS expected_income_removed,
       COUNT(*) FILTER (WHERE added)                          AS policies_added,
       COALESCE(SUM(latest_income) FILTER (WHERE added), 0)   AS expected_income_added,
       COALESCE(SUM(movement_amount) FILTER (WHERE amount_changed), 0)
                                                              AS amount_changes,
       COUNT(*) FILTER (WHERE amount_changed)                 AS policies_amount_changed,
       COUNT(*) FILTER (WHERE manager_changed)                AS manager_transfers,
       COUNT(*) FILTER (WHERE detail_changed)                 AS detail_changes,
       -- Policies carrying more than one change at once. Worth seeing on its
       -- own: these are the rows a single-type classification would misreport.
       COUNT(*) FILTER (WHERE cardinality(secondary_changes) > 1)
                                                              AS multi_attribute_changes,
       SUM(latest_income)                                     AS latest_expected_income
FROM v_forecast_movement_detail
GROUP BY 1, 2;

-- Manager transfers in full, with both the amount that moved and where it went.
CREATE VIEW v_manager_transfer_detail AS
SELECT policy_id,
       forecast_month,
       canonical_from_manager,
       canonical_to_manager,
       from_manager AS source_from_manager,
       to_manager   AS source_to_manager,
       previous_income,
       latest_income,
       movement_amount,
       amount_changed,
       detail_changed,
       secondary_changes,
       movement_type AS primary_movement_type,
       client_code, policy_number, class_abbrev, expiry_date
FROM v_forecast_movement_detail
WHERE manager_changed;
