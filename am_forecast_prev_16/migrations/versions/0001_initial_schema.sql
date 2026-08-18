-- Account Manager Income Forecasting Platform
-- Initial schema. Generated from app/models by scripts/generate_ddl.py.
-- Do not hand-edit: change the models and regenerate.
--
-- Money is numeric(14,2) throughout. No floats in the financial path.
-- All reported income is GST inclusive.

CREATE TABLE app_user (
	id SERIAL NOT NULL, 
	username VARCHAR(120) NOT NULL, 
	display_name VARCHAR(160) NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	password_hash VARCHAR(255), 
	active BOOLEAN DEFAULT true NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_app_user PRIMARY KEY (id), 
	CONSTRAINT ck_app_user_user_role CHECK (role IN ('viewer', 'manager', 'administrator')), 
	CONSTRAINT uq_app_user_username UNIQUE (username)
);

CREATE TABLE budget_audit (
	id BIGSERIAL NOT NULL, 
	action VARCHAR(60) NOT NULL, 
	scope_description TEXT NOT NULL, 
	canonical_manager VARCHAR(120), 
	financial_year INTEGER, 
	financial_quarter SMALLINT, 
	before_value JSONB, 
	after_value JSONB, 
	reason TEXT NOT NULL, 
	performed_by VARCHAR(120) NOT NULL, 
	performed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_budget_audit PRIMARY KEY (id)
);

CREATE TABLE category_map (
	category VARCHAR(10) NOT NULL, 
	business_classification VARCHAR(60) NOT NULL, 
	description TEXT, 
	active BOOLEAN DEFAULT true NOT NULL, 
	CONSTRAINT pk_category_map PRIMARY KEY (category), 
	CONSTRAINT ck_category_map_category_business_classification CHECK (business_classification IN ('Renewal', 'Transfer Renewal', 'New Business', 'Endorsement', 'Lapse / End-Term Lost Renewal', 'Mid-Term Cancellation', 'New Business Cancellation', 'Adjustment', 'Endorsement Cancellation', 'Policy Reinstatement', 'Unmapped'))
);

CREATE TABLE exclusion_rule (
	id SERIAL NOT NULL, 
	rule_group VARCHAR(60) NOT NULL, 
	rule_name VARCHAR(120) NOT NULL, 
	source_type VARCHAR(20) NOT NULL, 
	target_field VARCHAR(60) NOT NULL, 
	match_type VARCHAR(20) NOT NULL, 
	match_value VARCHAR(120) NOT NULL, 
	active BOOLEAN DEFAULT true NOT NULL, 
	note TEXT, 
	updated_by VARCHAR(120) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_exclusion_rule PRIMARY KEY (id), 
	CONSTRAINT ck_exclusion_rule_exclusion_source_type CHECK (source_type IN ('sales', 'renewals', 'both')), 
	CONSTRAINT ck_exclusion_rule_exclusion_match_type CHECK (match_type IN ('exact', 'contains')), 
	CONSTRAINT uq_exclusion_rule_definition UNIQUE (source_type, target_field, match_type, match_value)
);

CREATE TABLE forecast_baseline (
	forecast_month DATE NOT NULL, 
	financial_year INTEGER NOT NULL, 
	financial_quarter SMALLINT NOT NULL, 
	baseline_status VARCHAR(20) NOT NULL, 
	baseline_source VARCHAR(60), 
	suppress_achievement BOOLEAN DEFAULT false NOT NULL, 
	manager_exceptions JSONB DEFAULT '[]'::jsonb NOT NULL, 
	note TEXT, 
	updated_by VARCHAR(120) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_forecast_baseline PRIMARY KEY (forecast_month), 
	CONSTRAINT ck_forecast_baseline_baseline_status CHECK (baseline_status IN ('complete', 'incomplete', 'unavailable')), 
	CONSTRAINT ck_forecast_baseline_baseline_quarter_range CHECK (financial_quarter BETWEEN 1 AND 4)
);

CREATE INDEX ix_forecast_baseline_financial_year ON forecast_baseline (financial_year);

CREATE TABLE period_coverage (
	id SERIAL NOT NULL, 
	financial_year INTEGER NOT NULL, 
	data_domain VARCHAR(20) NOT NULL, 
	coverage_status VARCHAR(20) NOT NULL, 
	months_present INTEGER NOT NULL, 
	first_month DATE NOT NULL, 
	last_month DATE NOT NULL, 
	label VARCHAR(160), 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_period_coverage PRIMARY KEY (id), 
	CONSTRAINT ck_period_coverage_coverage_status CHECK (coverage_status IN ('complete', 'partial')), 
	CONSTRAINT uq_period_coverage_fy_domain UNIQUE (financial_year, data_domain)
);

CREATE TABLE rebaseline_audit (
	id BIGSERIAL NOT NULL, 
	scope_description TEXT NOT NULL, 
	forecast_month_from DATE NOT NULL, 
	forecast_month_to DATE NOT NULL, 
	reason TEXT NOT NULL, 
	performed_by VARCHAR(120) NOT NULL, 
	performed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	before_total NUMERIC(14, 2) NOT NULL, 
	after_total NUMERIC(14, 2) NOT NULL, 
	before_detail JSONB, 
	after_detail JSONB, 
	CONSTRAINT pk_rebaseline_audit PRIMARY KEY (id)
);

CREATE TABLE reporting_manager (
	id SERIAL NOT NULL, 
	canonical_manager VARCHAR(120) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	include_in_rankings BOOLEAN NOT NULL, 
	include_in_business_totals BOOLEAN NOT NULL, 
	display_order INTEGER, 
	note TEXT, 
	updated_by VARCHAR(120) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_reporting_manager PRIMARY KEY (id), 
	CONSTRAINT ck_reporting_manager_manager_status CHECK (status IN ('active', 'legacy_unmapped', 'inactive')), 
	CONSTRAINT uq_reporting_manager_canonical_manager UNIQUE (canonical_manager)
);

CREATE TABLE reporting_settings (
	id SMALLINT NOT NULL, 
	cut_off_date DATE NOT NULL, 
	cut_off_set_by VARCHAR(120) NOT NULL, 
	cut_off_set_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	match_date_tolerance_days INTEGER NOT NULL, 
	default_growth_pct NUMERIC(14, 2) NOT NULL, 
	gst_note TEXT NOT NULL, 
	CONSTRAINT pk_reporting_settings PRIMARY KEY (id), 
	CONSTRAINT ck_reporting_settings_reporting_settings_singleton CHECK (id = 1), 
	CONSTRAINT ck_reporting_settings_match_tolerance_range CHECK (match_date_tolerance_days BETWEEN 0 AND 365)
);

CREATE TABLE upload_batch (
	id BIGSERIAL NOT NULL, 
	file_name VARCHAR(260) NOT NULL, 
	file_type VARCHAR(20) NOT NULL, 
	file_sha256 VARCHAR(64) NOT NULL, 
	file_size_bytes BIGINT, 
	uploaded_by VARCHAR(120) NOT NULL, 
	uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	accepted_by VARCHAR(120), 
	accepted_at TIMESTAMP WITH TIME ZONE, 
	status VARCHAR(20) NOT NULL, 
	source_row_count INTEGER, 
	accepted_row_count INTEGER, 
	duplicate_row_count INTEGER, 
	excluded_row_count INTEGER, 
	rejected_row_count INTEGER, 
	coverage_start DATE, 
	coverage_end DATE, 
	positive_income NUMERIC(14, 2), 
	return_income NUMERIC(14, 2), 
	net_income NUMERIC(14, 2), 
	expected_forecast_income NUMERIC(14, 2), 
	exception_count INTEGER, 
	rolled_back_by VARCHAR(120), 
	rolled_back_at TIMESTAMP WITH TIME ZONE, 
	rollback_reason TEXT, 
	validation_messages JSONB DEFAULT '[]'::jsonb NOT NULL, 
	column_mapping JSONB DEFAULT '{}'::jsonb NOT NULL, 
	CONSTRAINT pk_upload_batch PRIMARY KEY (id), 
	CONSTRAINT ck_upload_batch_batch_file_type CHECK (file_type IN ('sales', 'renewals', 'legacy_forecast')), 
	CONSTRAINT ck_upload_batch_batch_status CHECK (status IN ('pending', 'accepted', 'rejected', 'rolled_back'))
);

CREATE INDEX ix_upload_batch_file_sha256 ON upload_batch (file_sha256);

CREATE TABLE forecast_snapshot (
	id BIGSERIAL NOT NULL, 
	batch_id BIGINT NOT NULL, 
	as_of_date DATE NOT NULL, 
	coverage_start DATE NOT NULL, 
	coverage_end DATE NOT NULL, 
	source_row_count INTEGER NOT NULL, 
	included_row_count INTEGER NOT NULL, 
	excluded_row_count INTEGER NOT NULL, 
	negative_row_count INTEGER DEFAULT 0 NOT NULL, 
	zero_row_count INTEGER DEFAULT 0 NOT NULL, 
	overdue_row_count INTEGER DEFAULT 0 NOT NULL, 
	raw_expected_income NUMERIC(14, 2) NOT NULL, 
	forecast_contribution NUMERIC(14, 2) NOT NULL, 
	is_superseded BOOLEAN DEFAULT false NOT NULL, 
	validation_messages JSONB DEFAULT '[]'::jsonb NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_forecast_snapshot PRIMARY KEY (id), 
	CONSTRAINT fk_forecast_snapshot_batch_id_upload_batch FOREIGN KEY(batch_id) REFERENCES upload_batch (id)
);

CREATE TABLE growth_rate (
	id SERIAL NOT NULL, 
	scope VARCHAR(20) NOT NULL, 
	canonical_manager VARCHAR(120), 
	financial_year INTEGER, 
	financial_quarter SMALLINT, 
	growth_pct NUMERIC(6, 4), 
	dollar_override NUMERIC(14, 2), 
	note TEXT, 
	active BOOLEAN DEFAULT true NOT NULL, 
	created_by VARCHAR(120) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	superseded_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_growth_rate PRIMARY KEY (id), 
	CONSTRAINT ck_growth_rate_growth_scope CHECK (scope IN ('global', 'manager', 'manager_quarter')), 
	CONSTRAINT ck_growth_rate_growth_value_present CHECK (growth_pct IS NOT NULL OR dollar_override IS NOT NULL), 
	CONSTRAINT ck_growth_rate_growth_scope_consistency CHECK ((scope = 'global' AND canonical_manager IS NULL AND financial_quarter IS NULL) OR (scope = 'manager' AND canonical_manager IS NOT NULL AND financial_quarter IS NULL) OR (scope = 'manager_quarter' AND canonical_manager IS NOT NULL  AND financial_year IS NOT NULL AND financial_quarter IS NOT NULL)), 
	CONSTRAINT ck_growth_rate_growth_quarter_range CHECK (financial_quarter IS NULL OR financial_quarter BETWEEN 1 AND 4), 
	CONSTRAINT fk_growth_rate_canonical_manager_reporting_manager FOREIGN KEY(canonical_manager) REFERENCES reporting_manager (canonical_manager) ON UPDATE CASCADE
);

CREATE UNIQUE INDEX uq_growth_global ON growth_rate (scope) WHERE scope = 'global' AND active;

CREATE UNIQUE INDEX uq_growth_manager_quarter ON growth_rate (canonical_manager, financial_year, financial_quarter) WHERE scope = 'manager_quarter' AND active;

CREATE UNIQUE INDEX uq_growth_manager ON growth_rate (canonical_manager, financial_year) WHERE scope = 'manager' AND active;

CREATE TABLE ingest_exception (
	id BIGSERIAL NOT NULL, 
	batch_id BIGINT NOT NULL, 
	exception_type VARCHAR(60) NOT NULL, 
	severity VARCHAR(20) NOT NULL, 
	source_row_number INTEGER, 
	field_name VARCHAR(60), 
	field_value TEXT, 
	message TEXT NOT NULL, 
	payload JSONB, 
	detected_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	resolved_by VARCHAR(120), 
	resolved_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_ingest_exception PRIMARY KEY (id), 
	CONSTRAINT ck_ingest_exception_exception_severity CHECK (severity IN ('info', 'warning', 'error')), 
	CONSTRAINT fk_ingest_exception_batch_id_upload_batch FOREIGN KEY(batch_id) REFERENCES upload_batch (id)
);

CREATE INDEX ix_ingest_exception_exception_type ON ingest_exception (exception_type);

CREATE TABLE legacy_forecast_reference (
	id BIGSERIAL NOT NULL, 
	batch_id BIGINT, 
	forecast_month DATE NOT NULL, 
	financial_year INTEGER NOT NULL, 
	financial_quarter SMALLINT NOT NULL, 
	source_manager VARCHAR(120) NOT NULL, 
	forecast_amount NUMERIC(14, 2) NOT NULL, 
	promoted_to_original BOOLEAN DEFAULT false NOT NULL, 
	is_verified_exclusion_clean BOOLEAN DEFAULT true NOT NULL, 
	note TEXT, 
	loaded_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_legacy_forecast_reference PRIMARY KEY (id), 
	CONSTRAINT uq_legacy_forecast_month_manager UNIQUE (forecast_month, source_manager), 
	CONSTRAINT fk_legacy_forecast_reference_batch_id_upload_batch FOREIGN KEY(batch_id) REFERENCES upload_batch (id)
);

CREATE INDEX ix_legacy_forecast_reference_forecast_month ON legacy_forecast_reference (forecast_month);

CREATE TABLE manager_alias (
	id SERIAL NOT NULL, 
	source_manager VARCHAR(120) NOT NULL, 
	source_manager_norm VARCHAR(120) NOT NULL, 
	canonical_manager VARCHAR(120) NOT NULL, 
	active BOOLEAN DEFAULT true NOT NULL, 
	note TEXT, 
	updated_by VARCHAR(120) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_manager_alias PRIMARY KEY (id), 
	CONSTRAINT uq_manager_alias_source_manager UNIQUE (source_manager), 
	CONSTRAINT fk_manager_alias_canonical_manager_reporting_manager FOREIGN KEY(canonical_manager) REFERENCES reporting_manager (canonical_manager) ON UPDATE CASCADE
);

CREATE INDEX ix_manager_alias_source_manager_norm ON manager_alias (source_manager_norm);

CREATE TABLE monthly_target_override (
	id SERIAL NOT NULL, 
	canonical_manager VARCHAR(120) NOT NULL, 
	target_month DATE NOT NULL, 
	override_amount NUMERIC(14, 2) NOT NULL, 
	reason TEXT NOT NULL, 
	created_by VARCHAR(120) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	active BOOLEAN DEFAULT true NOT NULL, 
	CONSTRAINT pk_monthly_target_override PRIMARY KEY (id), 
	CONSTRAINT fk_monthly_target_override_canonical_manager_reporting_manager FOREIGN KEY(canonical_manager) REFERENCES reporting_manager (canonical_manager) ON UPDATE CASCADE
);

CREATE UNIQUE INDEX uq_monthly_override ON monthly_target_override (canonical_manager, target_month) WHERE active;

CREATE TABLE sales_transaction (
	id BIGSERIAL NOT NULL, 
	fingerprint VARCHAR(64) NOT NULL, 
	first_seen_batch_id BIGINT NOT NULL, 
	first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	last_seen_batch_id BIGINT NOT NULL, 
	last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	seen_count INTEGER DEFAULT 1 NOT NULL, 
	transaction_date TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	period_month DATE NOT NULL, 
	financial_year INTEGER NOT NULL, 
	financial_quarter SMALLINT NOT NULL, 
	source_manager VARCHAR(120) NOT NULL, 
	group1_id INTEGER, 
	group2_description VARCHAR(120), 
	client_id BIGINT, 
	client_code VARCHAR(60), 
	client_code_norm VARCHAR(60), 
	policy_number VARCHAR(120), 
	policy_number_norm VARCHAR(120), 
	invoice_number BIGINT, 
	username VARCHAR(120), 
	category VARCHAR(10) NOT NULL, 
	business_classification VARCHAR(60) NOT NULL, 
	derived_classification VARCHAR(60) NOT NULL, 
	policy_class VARCHAR(60), 
	uw_code VARCHAR(60), 
	reason TEXT, 
	premium NUMERIC(14, 2), 
	nett NUMERIC(14, 2), 
	commission NUMERIC(14, 2) NOT NULL, 
	fees NUMERIC(14, 2) NOT NULL, 
	sub_comm NUMERIC(14, 2), 
	actual_income NUMERIC(14, 2) GENERATED ALWAYS AS (commission + fees) STORED NOT NULL, 
	positive_income NUMERIC(14, 2) GENERATED ALWAYS AS (GREATEST(commission + fees, 0)) STORED NOT NULL, 
	signed_return_income NUMERIC(14, 2) GENERATED ALWAYS AS (LEAST(commission + fees, 0)) STORED NOT NULL, 
	absolute_return_income NUMERIC(14, 2) GENERATED ALWAYS AS (ABS(LEAST(commission + fees, 0))) STORED NOT NULL, 
	financial_direction VARCHAR(10) NOT NULL, 
	primary_assoc_code VARCHAR(60), 
	primary_assoc_amount NUMERIC(14, 2), 
	secondary_assoc_code VARCHAR(60), 
	secondary_assoc_amount NUMERIC(14, 2), 
	is_excluded BOOLEAN DEFAULT false NOT NULL, 
	exclusion_rule_id INTEGER, 
	exclusion_field VARCHAR(60), 
	exclusion_value VARCHAR(120), 
	source_row JSONB NOT NULL, 
	CONSTRAINT pk_sales_transaction PRIMARY KEY (id), 
	CONSTRAINT ck_sales_transaction_txn_financial_direction CHECK (financial_direction IN ('positive', 'negative', 'nil')), 
	CONSTRAINT ck_sales_transaction_txn_business_classification CHECK (business_classification IN ('Renewal', 'Transfer Renewal', 'New Business', 'Endorsement', 'Lapse / End-Term Lost Renewal', 'Mid-Term Cancellation', 'New Business Cancellation', 'Adjustment', 'Endorsement Cancellation', 'Policy Reinstatement', 'Unmapped')), 
	CONSTRAINT ck_sales_transaction_txn_derived_classification CHECK (derived_classification IN ('Positive Renewal', 'Renewal Return or Correction', 'Positive Transfer Renewal', 'Transfer Renewal Return or Correction', 'Positive New Business', 'Negative New Business Correction', 'New Business Cancellation', 'Positive Endorsement', 'Negative Endorsement', 'Endorsement Cancellation', 'Lapse / Lost Renewal', 'Mid-Term Cancellation', 'Positive Adjustment', 'Negative Adjustment', 'Policy Reinstatement', 'Unmapped')), 
	CONSTRAINT ck_sales_transaction_txn_quarter_range CHECK (financial_quarter BETWEEN 1 AND 4), 
	CONSTRAINT ck_sales_transaction_txn_exclusion_consistency CHECK ((is_excluded = false AND exclusion_rule_id IS NULL) OR (is_excluded = true AND exclusion_rule_id IS NOT NULL)), 
	CONSTRAINT uq_sales_transaction_fingerprint UNIQUE (fingerprint), 
	CONSTRAINT fk_sales_transaction_first_seen_batch_id_upload_batch FOREIGN KEY(first_seen_batch_id) REFERENCES upload_batch (id), 
	CONSTRAINT fk_sales_transaction_last_seen_batch_id_upload_batch FOREIGN KEY(last_seen_batch_id) REFERENCES upload_batch (id), 
	CONSTRAINT fk_sales_transaction_exclusion_rule_id_exclusion_rule FOREIGN KEY(exclusion_rule_id) REFERENCES exclusion_rule (id)
);

CREATE INDEX ix_txn_reporting ON sales_transaction (period_month, source_manager) WHERE NOT is_excluded;

CREATE INDEX ix_sales_transaction_policy_class ON sales_transaction (policy_class);

CREATE INDEX ix_sales_transaction_invoice_number ON sales_transaction (invoice_number);

CREATE INDEX ix_txn_match_keys ON sales_transaction (client_code_norm, policy_number_norm);

CREATE INDEX ix_txn_fy_quarter ON sales_transaction (financial_year, financial_quarter) WHERE NOT is_excluded;

CREATE INDEX ix_sales_transaction_uw_code ON sales_transaction (uw_code);

CREATE INDEX ix_sales_transaction_category ON sales_transaction (category);

CREATE INDEX ix_sales_transaction_source_manager ON sales_transaction (source_manager);

CREATE TABLE forecast_actual_match (
	id BIGSERIAL NOT NULL, 
	policy_id BIGINT, 
	forecast_month DATE, 
	transaction_id BIGINT, 
	match_status VARCHAR(40) NOT NULL, 
	match_method VARCHAR(10) NOT NULL, 
	match_tier SMALLINT, 
	confidence NUMERIC(4, 3), 
	matched_income NUMERIC(14, 2), 
	candidate_count INTEGER DEFAULT 1 NOT NULL, 
	requires_review BOOLEAN DEFAULT false NOT NULL, 
	reviewed_by VARCHAR(120), 
	reviewed_at TIMESTAMP WITH TIME ZONE, 
	review_note TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_forecast_actual_match PRIMARY KEY (id), 
	CONSTRAINT ck_forecast_actual_match_match_status CHECK (match_status IN ('matched_renewal', 'matched_transfer_renewal', 'matched_lapse', 'pending', 'removed_from_latest', 'added_after_original', 'multiple_candidate_matches', 'unmatched_actual_renewal', 'unmatched_forecast_policy', 'manual_match', 'match_rejected')), 
	CONSTRAINT ck_forecast_actual_match_match_method CHECK (match_method IN ('auto', 'manual')), 
	CONSTRAINT ck_forecast_actual_match_match_confidence_range CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1), 
	CONSTRAINT fk_forecast_actual_match_transaction_id_sales_transaction FOREIGN KEY(transaction_id) REFERENCES sales_transaction (id)
);

CREATE INDEX ix_forecast_actual_match_match_status ON forecast_actual_match (match_status);

CREATE INDEX ix_forecast_actual_match_forecast_month ON forecast_actual_match (forecast_month);

CREATE INDEX ix_forecast_actual_match_policy_id ON forecast_actual_match (policy_id);

CREATE TABLE forecast_month_coverage (
	forecast_month DATE NOT NULL, 
	original_snapshot_id BIGINT, 
	latest_snapshot_id BIGINT, 
	original_grain VARCHAR(20) DEFAULT 'policy' NOT NULL, 
	established_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_forecast_month_coverage PRIMARY KEY (forecast_month), 
	CONSTRAINT ck_forecast_month_coverage_coverage_original_grain CHECK (original_grain IN ('policy', 'manager_month')), 
	CONSTRAINT fk_forecast_month_coverage_original_snapshot_id_forecas_549e FOREIGN KEY(original_snapshot_id) REFERENCES forecast_snapshot (id), 
	CONSTRAINT fk_forecast_month_coverage_latest_snapshot_id_forecast_snapshot FOREIGN KEY(latest_snapshot_id) REFERENCES forecast_snapshot (id)
);

CREATE TABLE forecast_movement (
	id BIGSERIAL NOT NULL, 
	from_snapshot_id BIGINT, 
	to_snapshot_id BIGINT NOT NULL, 
	policy_id BIGINT NOT NULL, 
	forecast_month DATE NOT NULL, 
	movement_type VARCHAR(30) NOT NULL, 
	original_income NUMERIC(14, 2) DEFAULT 0 NOT NULL, 
	previous_income NUMERIC(14, 2) DEFAULT 0 NOT NULL, 
	latest_income NUMERIC(14, 2) DEFAULT 0 NOT NULL, 
	movement_amount NUMERIC(14, 2) DEFAULT 0 NOT NULL, 
	from_manager VARCHAR(120), 
	to_manager VARCHAR(120), 
	detail_changes JSONB, 
	detected_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_forecast_movement PRIMARY KEY (id), 
	CONSTRAINT ck_forecast_movement_movement_type CHECK (movement_type IN ('removed_from_latest', 'added_after_original', 'amount_changed', 'manager_changed', 'detail_changed', 'unchanged')), 
	CONSTRAINT ck_forecast_movement_movement_latest_non_negative CHECK (latest_income >= 0), 
	CONSTRAINT fk_forecast_movement_from_snapshot_id_forecast_snapshot FOREIGN KEY(from_snapshot_id) REFERENCES forecast_snapshot (id), 
	CONSTRAINT fk_forecast_movement_to_snapshot_id_forecast_snapshot FOREIGN KEY(to_snapshot_id) REFERENCES forecast_snapshot (id)
);

CREATE INDEX ix_forecast_movement_forecast_month ON forecast_movement (forecast_month);

CREATE INDEX ix_forecast_movement_movement_type ON forecast_movement (movement_type);

CREATE INDEX ix_forecast_movement_policy_id ON forecast_movement (policy_id);

CREATE TABLE forecast_policy (
	id BIGSERIAL NOT NULL, 
	snapshot_id BIGINT NOT NULL, 
	policy_id BIGINT NOT NULL, 
	client_id BIGINT, 
	client_code VARCHAR(60), 
	client_code_norm VARCHAR(60), 
	policy_number VARCHAR(120), 
	policy_number_norm VARCHAR(120), 
	class_abbrev VARCHAR(60), 
	class_code VARCHAR(60), 
	class_description VARCHAR(160), 
	underwriter_abbrev VARCHAR(60), 
	inception_date DATE, 
	expiry_date DATE NOT NULL, 
	next_expiry_date DATE, 
	renewal_months INTEGER, 
	forecast_month DATE NOT NULL, 
	financial_year INTEGER NOT NULL, 
	financial_quarter SMALLINT NOT NULL, 
	source_manager VARCHAR(120) NOT NULL, 
	comm NUMERIC(14, 2) NOT NULL, 
	comm_tax NUMERIC(14, 2) NOT NULL, 
	fee NUMERIC(14, 2) NOT NULL, 
	fee_tax NUMERIC(14, 2) NOT NULL, 
	premium NUMERIC(14, 2), 
	total_premium NUMERIC(14, 2), 
	raw_expected_income NUMERIC(14, 2) GENERATED ALWAYS AS (comm + comm_tax + fee + fee_tax) STORED NOT NULL, 
	forecast_contribution NUMERIC(14, 2) GENERATED ALWAYS AS (GREATEST(comm + comm_tax + fee + fee_tax, 0)) STORED NOT NULL, 
	exception_flags VARCHAR(30)[] DEFAULT '{}'::varchar[] NOT NULL, 
	is_excluded BOOLEAN DEFAULT false NOT NULL, 
	exclusion_rule_id INTEGER, 
	exclusion_field VARCHAR(60), 
	exclusion_value VARCHAR(120), 
	source_row JSONB NOT NULL, 
	CONSTRAINT pk_forecast_policy PRIMARY KEY (id), 
	CONSTRAINT uq_forecast_policy_snapshot_policy UNIQUE (snapshot_id, policy_id), 
	CONSTRAINT ck_forecast_policy_forecast_exception_flags CHECK (exception_flags <@ ARRAY['negative_expected', 'zero_expected', 'overdue_pending', 'residual_pending']::varchar[]), 
	CONSTRAINT ck_forecast_policy_fcst_quarter_range CHECK (financial_quarter BETWEEN 1 AND 4), 
	CONSTRAINT fk_forecast_policy_snapshot_id_forecast_snapshot FOREIGN KEY(snapshot_id) REFERENCES forecast_snapshot (id), 
	CONSTRAINT fk_forecast_policy_exclusion_rule_id_exclusion_rule FOREIGN KEY(exclusion_rule_id) REFERENCES exclusion_rule (id)
);

CREATE INDEX ix_forecast_policy_source_manager ON forecast_policy (source_manager);

CREATE INDEX ix_forecast_policy_policy_id ON forecast_policy (policy_id);

CREATE INDEX ix_forecast_policy_class_abbrev ON forecast_policy (class_abbrev);

CREATE INDEX ix_forecast_policy_forecast_month ON forecast_policy (forecast_month);

CREATE INDEX ix_fcst_reporting ON forecast_policy (snapshot_id, forecast_month, source_manager) WHERE NOT is_excluded;

CREATE INDEX ix_fcst_match_keys ON forecast_policy (client_code_norm, policy_number_norm);

CREATE INDEX ix_forecast_policy_underwriter_abbrev ON forecast_policy (underwriter_abbrev);

CREATE TABLE original_forecast (
	id BIGSERIAL NOT NULL, 
	grain VARCHAR(20) DEFAULT 'policy' NOT NULL, 
	policy_id BIGINT, 
	forecast_month DATE NOT NULL, 
	financial_year INTEGER NOT NULL, 
	financial_quarter SMALLINT NOT NULL, 
	origin VARCHAR(30) DEFAULT 'snapshot' NOT NULL, 
	established_snapshot_id BIGINT, 
	established_batch_id BIGINT, 
	established_by VARCHAR(120) NOT NULL, 
	established_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	source_manager VARCHAR(120) NOT NULL, 
	client_code VARCHAR(60), 
	policy_number VARCHAR(120), 
	class_abbrev VARCHAR(60), 
	expected_income NUMERIC(14, 2) NOT NULL, 
	forecast_contribution NUMERIC(14, 2) NOT NULL, 
	note TEXT, 
	CONSTRAINT pk_original_forecast PRIMARY KEY (id), 
	CONSTRAINT ck_original_forecast_orig_grain CHECK (grain IN ('policy', 'manager_month')), 
	CONSTRAINT ck_original_forecast_orig_origin CHECK (origin IN ('snapshot', 'legacy_dashboard', 'rebaseline', 'derived_from_actuals')), 
	CONSTRAINT ck_original_forecast_orig_grain_policy_consistency CHECK ((grain = 'policy' AND policy_id IS NOT NULL) OR (grain = 'manager_month' AND policy_id IS NULL)), 
	CONSTRAINT ck_original_forecast_orig_contribution_non_negative CHECK (forecast_contribution >= 0), 
	CONSTRAINT fk_original_forecast_established_snapshot_id_forecast_snapshot FOREIGN KEY(established_snapshot_id) REFERENCES forecast_snapshot (id), 
	CONSTRAINT fk_original_forecast_established_batch_id_upload_batch FOREIGN KEY(established_batch_id) REFERENCES upload_batch (id)
);

CREATE INDEX ix_original_forecast_forecast_month ON original_forecast (forecast_month);

CREATE UNIQUE INDEX uq_orig_policy ON original_forecast (policy_id, forecast_month) WHERE grain = 'policy';

CREATE UNIQUE INDEX uq_orig_manager_month ON original_forecast (source_manager, forecast_month) WHERE grain = 'manager_month';

CREATE INDEX ix_original_forecast_source_manager ON original_forecast (source_manager);

CREATE INDEX ix_original_forecast_policy_id ON original_forecast (policy_id);

CREATE TABLE restated_transaction (
	id BIGSERIAL NOT NULL, 
	transaction_id BIGINT NOT NULL, 
	batch_id BIGINT NOT NULL, 
	changed_fields JSONB NOT NULL, 
	detected_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	resolved_by VARCHAR(120), 
	resolved_at TIMESTAMP WITH TIME ZONE, 
	resolution VARCHAR(30), 
	note TEXT, 
	CONSTRAINT pk_restated_transaction PRIMARY KEY (id), 
	CONSTRAINT fk_restated_transaction_transaction_id_sales_transaction FOREIGN KEY(transaction_id) REFERENCES sales_transaction (id), 
	CONSTRAINT fk_restated_transaction_batch_id_upload_batch FOREIGN KEY(batch_id) REFERENCES upload_batch (id)
);
