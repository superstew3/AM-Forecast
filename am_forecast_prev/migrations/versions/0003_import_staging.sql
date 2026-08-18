-- Stage 2: import staging, sighting ledger, column mapping profiles, rollback audit.

CREATE TABLE column_mapping_profile (
	id SERIAL NOT NULL, 
	profile_name VARCHAR(120) NOT NULL, 
	file_type VARCHAR(20) NOT NULL, 
	mapping JSONB NOT NULL, 
	is_default BOOLEAN DEFAULT false NOT NULL, 
	note TEXT, 
	created_by VARCHAR(120) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_column_mapping_profile PRIMARY KEY (id), 
	CONSTRAINT uq_column_mapping_profile_profile_name UNIQUE (profile_name)
);

CREATE UNIQUE INDEX uq_default_profile_per_type ON column_mapping_profile (file_type) WHERE is_default;

CREATE TABLE batch_rollback (
	id BIGSERIAL NOT NULL, 
	batch_id BIGINT NOT NULL, 
	reason TEXT NOT NULL, 
	performed_by VARCHAR(120) NOT NULL, 
	performed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	transactions_deleted INTEGER NOT NULL, 
	sightings_removed INTEGER NOT NULL, 
	snapshots_deleted INTEGER NOT NULL, 
	original_forecast_rows_deleted INTEGER NOT NULL, 
	net_income_reversed NUMERIC(14, 2), 
	forecast_reversed NUMERIC(14, 2), 
	detail JSONB, 
	CONSTRAINT pk_batch_rollback PRIMARY KEY (id), 
	CONSTRAINT fk_batch_rollback_batch_id_upload_batch FOREIGN KEY(batch_id) REFERENCES upload_batch (id)
);

CREATE TABLE import_staging (
	id BIGSERIAL NOT NULL, 
	batch_id BIGINT NOT NULL, 
	source_row_number INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	fingerprint VARCHAR(64), 
	existing_transaction_id BIGINT, 
	policy_id BIGINT, 
	period_month TIMESTAMP WITHOUT TIME ZONE, 
	source_manager VARCHAR(120), 
	category VARCHAR(20), 
	positive_income NUMERIC(14, 2), 
	return_income NUMERIC(14, 2), 
	net_income NUMERIC(14, 2), 
	expected_income NUMERIC(14, 2), 
	forecast_contribution NUMERIC(14, 2), 
	is_excluded BOOLEAN DEFAULT false NOT NULL, 
	exclusion_rule_id INTEGER, 
	exclusion_field VARCHAR(60), 
	exclusion_value VARCHAR(120), 
	exception_flags VARCHAR(40)[] DEFAULT '{}'::varchar[] NOT NULL, 
	reject_reason TEXT, 
	changed_fields JSONB, 
	prepared JSONB NOT NULL, 
	source_row JSONB NOT NULL, 
	CONSTRAINT pk_import_staging PRIMARY KEY (id), 
	CONSTRAINT ck_import_staging_staging_status CHECK (status IN ('valid', 'duplicate', 'excluded', 'rejected', 'restated')), 
	CONSTRAINT uq_staging_batch_row UNIQUE (batch_id, source_row_number), 
	CONSTRAINT fk_import_staging_batch_id_upload_batch FOREIGN KEY(batch_id) REFERENCES upload_batch (id) ON DELETE CASCADE, 
	CONSTRAINT fk_import_staging_exclusion_rule_id_exclusion_rule FOREIGN KEY(exclusion_rule_id) REFERENCES exclusion_rule (id)
);

CREATE INDEX ix_import_staging_fingerprint ON import_staging (fingerprint);

CREATE INDEX ix_staging_pending ON import_staging (batch_id, status);

CREATE INDEX ix_import_staging_batch_id ON import_staging (batch_id);

CREATE INDEX ix_import_staging_status ON import_staging (status);

CREATE TABLE transaction_sighting (
	id BIGSERIAL NOT NULL, 
	transaction_id BIGINT NOT NULL, 
	batch_id BIGINT NOT NULL, 
	source_row_number INTEGER, 
	seen_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_transaction_sighting PRIMARY KEY (id), 
	CONSTRAINT uq_sighting_txn_batch UNIQUE (transaction_id, batch_id), 
	CONSTRAINT fk_transaction_sighting_transaction_id_sales_transaction FOREIGN KEY(transaction_id) REFERENCES sales_transaction (id) ON DELETE CASCADE, 
	CONSTRAINT fk_transaction_sighting_batch_id_upload_batch FOREIGN KEY(batch_id) REFERENCES upload_batch (id) ON DELETE CASCADE
);

CREATE INDEX ix_sighting_batch ON transaction_sighting (batch_id);
