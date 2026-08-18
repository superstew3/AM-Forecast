--
-- PostgreSQL database dump
--

\restrict eYp9UdoLMqwpghNfxrKIWlWLVQ6lrdn0GpCKkMseHG1J6ZeoNP93KiXxmFjRSqS

-- Dumped from database version 16.10
-- Dumped by pg_dump version 16.10

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: postgres
--

-- *not* creating schema, since initdb creates it


ALTER SCHEMA public OWNER TO postgres;

--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: postgres
--

COMMENT ON SCHEMA public IS '';


--
-- Name: actual_coverage(); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.actual_coverage() RETURNS datemultirange
    LANGUAGE sql STABLE
    AS $$
    SELECT COALESCE(range_agg(daterange(b.coverage_start, b.coverage_end, '[]')),
                    '{}'::datemultirange)
    FROM upload_batch b
    WHERE b.file_type = 'sales'
      AND b.status = 'accepted'
      AND b.coverage_start IS NOT NULL
      AND b.coverage_end IS NOT NULL;
$$;


ALTER FUNCTION public.actual_coverage() OWNER TO postgres;

--
-- Name: FUNCTION actual_coverage(); Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON FUNCTION public.actual_coverage() IS 'Union of days covered by accepted sales imports. Built from the coverage each file records, not from whether rows landed -- a quiet week is not an unloaded one.';


--
-- Name: actual_load_state(date); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.actual_load_state(m date) RETURNS text
    LANGUAGE sql STABLE
    AS $$
    SELECT CASE
        WHEN actual_coverage() @> daterange(m, (m + INTERVAL '1 month')::date, '[)')
            THEN 'full'
        WHEN actual_coverage() && daterange(m, (m + INTERVAL '1 month')::date, '[)')
            THEN 'partial'
        ELSE 'none'
    END;
$$;


ALTER FUNCTION public.actual_load_state(m date) OWNER TO postgres;

--
-- Name: FUNCTION actual_load_state(m date); Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON FUNCTION public.actual_load_state(m date) IS 'full | partial | none. A completed month is only scoreable on full.';


--
-- Name: actual_loaded_to(date); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.actual_loaded_to(m date) RETURNS date
    LANGUAGE sql STABLE
    AS $$
    -- The end of the CONTINUOUS run from the first of the month, not the last
    -- covered day anywhere in it. Two files covering the 1st to the 10th and the
    -- 20th to the 30th would otherwise report "loaded to the 30th" over a hole in
    -- the middle, which is a worse claim than admitting the month is incomplete:
    -- it invites the reader to treat the figure as month-to-date when nine days
    -- are missing from the middle of it.
    --
    -- Null when the month has no coverage starting at its first day. There is no
    -- honest "to" date for a month whose beginning is missing.
    SELECT (upper(r) - 1)
    FROM unnest(actual_coverage()
                * datemultirange(daterange(m, (m + INTERVAL '1 month')::date, '[)'))) AS r
    WHERE lower(r) = m
    LIMIT 1;
$$;


ALTER FUNCTION public.actual_loaded_to(m date) OWNER TO postgres;

--
-- Name: FUNCTION actual_loaded_to(m date); Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON FUNCTION public.actual_loaded_to(m date) IS 'Last day of an unbroken run of imported transactions from the first of the month. Null if the month does not start covered. What the UI shows beside a month-to-date figure so the reader knows how far it runs.';


--
-- Name: au_financial_year(date); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.au_financial_year(d date) RETURNS integer
    LANGUAGE sql IMMUTABLE
    AS $$
    SELECT CASE WHEN EXTRACT(MONTH FROM d) >= 7
                THEN EXTRACT(YEAR FROM d)::int
                ELSE EXTRACT(YEAR FROM d)::int - 1 END;
$$;


ALTER FUNCTION public.au_financial_year(d date) OWNER TO postgres;

--
-- Name: au_quarter(date); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.au_quarter(d date) RETURNS smallint
    LANGUAGE sql IMMUTABLE
    AS $$
    SELECT ((((EXTRACT(MONTH FROM d)::int - 7) % 12 + 12) % 12) / 3 + 1)::smallint;
$$;


ALTER FUNCTION public.au_quarter(d date) OWNER TO postgres;

--
-- Name: check_allocation_total(); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.check_allocation_total() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    txn_income numeric(14,2);
    allocated  numeric(14,2);
BEGIN
    SELECT actual_income INTO txn_income
    FROM sales_transaction WHERE id = NEW.transaction_id;

    SELECT COALESCE(SUM(allocated_income), 0) INTO allocated
    FROM match_allocation WHERE transaction_id = NEW.transaction_id;

    IF txn_income >= 0 AND (allocated < 0 OR allocated > txn_income + 0.001) THEN
        RAISE EXCEPTION
            'allocation total % exceeds income % of transaction %',
            allocated, txn_income, NEW.transaction_id;
    END IF;
    IF txn_income < 0 AND (allocated > 0 OR allocated < txn_income - 0.001) THEN
        RAISE EXCEPTION
            'allocation total % exceeds income % of transaction %',
            allocated, txn_income, NEW.transaction_id;
    END IF;
    RETURN NEW;
END;
$$;


ALTER FUNCTION public.check_allocation_total() OWNER TO postgres;

--
-- Name: forecast_month_is_open(date); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.forecast_month_is_open(m date) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
    SELECT m > reporting_current_month()
       AND NOT EXISTS (SELECT 1 FROM forecast_month_lock l
                       WHERE l.forecast_month = m AND l.active);
$$;


ALTER FUNCTION public.forecast_month_is_open(m date) OWNER TO postgres;

--
-- Name: FUNCTION forecast_month_is_open(m date); Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON FUNCTION public.forecast_month_is_open(m date) IS 'True when a routine forecast upload may overwrite this month. False for the current month, every past month, and any month explicitly pinned.';


--
-- Name: forecast_month_writable(date); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.forecast_month_writable(m date) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
    SELECT forecast_month_is_open(m)
        OR EXISTS (SELECT 1 FROM forecast_month_override o
                   WHERE o.forecast_month = m AND o.consumed_at IS NULL);
$$;


ALTER FUNCTION public.forecast_month_writable(m date) OWNER TO postgres;

--
-- Name: month_state(date); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.month_state(m date) RETURNS text
    LANGUAGE sql STABLE
    AS $$
    SELECT CASE
        WHEN m < reporting_current_month()  THEN 'completed'
        WHEN m = reporting_current_month()  THEN 'in_progress'
        ELSE 'future'
    END;
$$;


ALTER FUNCTION public.month_state(m date) OWNER TO postgres;

--
-- Name: FUNCTION month_state(m date); Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON FUNCTION public.month_state(m date) IS 'completed | in_progress | future. in_progress is never scored: a full month target against a part month actual is not a result.';


--
-- Name: reporting_current_month(); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.reporting_current_month() RETURNS date
    LANGUAGE sql STABLE
    AS $$
    SELECT date_trunc('month', (now() AT TIME ZONE 'Australia/Melbourne'))::date;
$$;


ALTER FUNCTION public.reporting_current_month() OWNER TO postgres;

--
-- Name: FUNCTION reporting_current_month(); Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON FUNCTION public.reporting_current_month() IS 'First day of the current calendar month in Australia/Melbourne. The single source of the actual/expected boundary. Never use CURRENT_DATE for this.';


--
-- Name: resolve_growth(text, integer, smallint); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.resolve_growth(p_manager text, p_fy integer, p_quarter smallint) RETURNS TABLE(basis text, growth_pct numeric, dollar_override numeric, note text)
    LANGUAGE sql STABLE
    AS $$
    SELECT basis, growth_pct, dollar_override, note FROM (
        SELECT 'manager_quarter'::text AS basis, g.growth_pct, g.dollar_override, g.note, 1 AS rank
        FROM growth_rate g
        WHERE g.active AND g.scope = 'manager_quarter'
          AND g.canonical_manager = p_manager
          AND g.financial_year = p_fy AND g.financial_quarter = p_quarter
        UNION ALL
        SELECT 'manager', g.growth_pct, g.dollar_override, g.note, 2
        FROM growth_rate g
        WHERE g.active AND g.scope = 'manager'
          AND g.canonical_manager = p_manager
          AND (g.financial_year IS NULL OR g.financial_year = p_fy)
        UNION ALL
        SELECT 'global', g.growth_pct, g.dollar_override, g.note, 3
        FROM growth_rate g
        WHERE g.active AND g.scope = 'global'
    ) candidates
    ORDER BY rank
    LIMIT 1;
$$;


ALTER FUNCTION public.resolve_growth(p_manager text, p_fy integer, p_quarter smallint) OWNER TO postgres;

--
-- Name: resolve_growth_month(text, date); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.resolve_growth_month(p_manager text, p_month date) RETURNS TABLE(basis text, growth_pct numeric, dollar_override numeric, note text)
    LANGUAGE sql STABLE
    AS $$
    SELECT basis, growth_pct, dollar_override, note FROM (
        SELECT 'manager_month'::text AS basis, g.growth_pct, g.dollar_override,
               g.note, 1 AS rank
        FROM growth_rate g
        WHERE g.active AND g.scope = 'manager_month'
          AND g.canonical_manager = p_manager AND g.target_month = p_month
        UNION ALL
        SELECT 'manager_quarter', g.growth_pct, g.dollar_override, g.note, 2
        FROM growth_rate g
        WHERE g.active AND g.scope = 'manager_quarter'
          AND g.canonical_manager = p_manager
          AND g.financial_year = au_financial_year(p_month)
          AND g.financial_quarter = au_quarter(p_month)
        UNION ALL
        SELECT 'manager', g.growth_pct, g.dollar_override, g.note, 3
        FROM growth_rate g
        WHERE g.active AND g.scope = 'manager'
          AND g.canonical_manager = p_manager
          AND (g.financial_year IS NULL
               OR g.financial_year = au_financial_year(p_month))
        UNION ALL
        SELECT 'global', g.growth_pct, g.dollar_override, g.note, 4
        FROM growth_rate g
        WHERE g.active AND g.scope = 'global'
    ) candidates
    ORDER BY rank
    LIMIT 1;
$$;


ALTER FUNCTION public.resolve_growth_month(p_manager text, p_month date) OWNER TO postgres;

--
-- Name: safe_div(numeric, numeric); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.safe_div(numerator numeric, denominator numeric) RETURNS numeric
    LANGUAGE sql IMMUTABLE
    AS $$
    SELECT CASE WHEN denominator IS NULL OR denominator = 0
                THEN NULL
                ELSE numerator / denominator END;
$$;


ALTER FUNCTION public.safe_div(numerator numeric, denominator numeric) OWNER TO postgres;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: app_user; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.app_user (
    id integer NOT NULL,
    username character varying(120) NOT NULL,
    display_name character varying(160) NOT NULL,
    role character varying(20) NOT NULL,
    password_hash character varying(255),
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    email character varying(255),
    password_salt character varying(32),
    password_algo character varying(20) DEFAULT 'scrypt'::character varying NOT NULL,
    password_n integer DEFAULT 32768 NOT NULL,
    password_r integer DEFAULT 8 NOT NULL,
    password_p integer DEFAULT 1 NOT NULL,
    password_set_at timestamp with time zone,
    must_change_password boolean DEFAULT false NOT NULL,
    failed_attempts integer DEFAULT 0 NOT NULL,
    locked_until timestamp with time zone,
    last_login_at timestamp with time zone,
    last_login_ip inet,
    canonical_manager character varying(120),
    created_by character varying(120),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_app_user_user_role CHECK (((role)::text = ANY ((ARRAY['viewer'::character varying, 'manager'::character varying, 'administrator'::character varying])::text[])))
);


ALTER TABLE public.app_user OWNER TO postgres;

--
-- Name: app_user_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.app_user_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.app_user_id_seq OWNER TO postgres;

--
-- Name: app_user_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.app_user_id_seq OWNED BY public.app_user.id;


--
-- Name: auth_event; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.auth_event (
    id bigint NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    email character varying(255),
    user_id integer,
    event character varying(40) NOT NULL,
    ip inet,
    user_agent text,
    detail jsonb,
    CONSTRAINT auth_event_event_check CHECK (((event)::text = ANY ((ARRAY['login_success'::character varying, 'login_failed_password'::character varying, 'login_failed_unknown_user'::character varying, 'login_failed_inactive'::character varying, 'login_failed_locked'::character varying, 'account_locked'::character varying, 'logout'::character varying, 'password_changed'::character varying, 'password_reset_by_admin'::character varying, 'session_expired'::character varying, 'session_revoked'::character varying, 'user_created'::character varying, 'user_disabled'::character varying])::text[])))
);


ALTER TABLE public.auth_event OWNER TO postgres;

--
-- Name: auth_event_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.auth_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.auth_event_id_seq OWNER TO postgres;

--
-- Name: auth_event_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.auth_event_id_seq OWNED BY public.auth_event.id;


--
-- Name: batch_rollback; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.batch_rollback (
    id bigint NOT NULL,
    batch_id bigint NOT NULL,
    reason text NOT NULL,
    performed_by character varying(120) NOT NULL,
    performed_at timestamp with time zone DEFAULT now() NOT NULL,
    transactions_deleted integer NOT NULL,
    sightings_removed integer NOT NULL,
    snapshots_deleted integer NOT NULL,
    original_forecast_rows_deleted integer NOT NULL,
    net_income_reversed numeric(14,2),
    forecast_reversed numeric(14,2),
    detail jsonb
);


ALTER TABLE public.batch_rollback OWNER TO postgres;

--
-- Name: batch_rollback_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.batch_rollback_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.batch_rollback_id_seq OWNER TO postgres;

--
-- Name: batch_rollback_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.batch_rollback_id_seq OWNED BY public.batch_rollback.id;


--
-- Name: budget_audit; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.budget_audit (
    id bigint NOT NULL,
    action character varying(60) NOT NULL,
    scope_description text NOT NULL,
    canonical_manager character varying(120),
    financial_year integer,
    financial_quarter smallint,
    before_value jsonb,
    after_value jsonb,
    reason text NOT NULL,
    performed_by character varying(120) NOT NULL,
    performed_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.budget_audit OWNER TO postgres;

--
-- Name: budget_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.budget_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.budget_audit_id_seq OWNER TO postgres;

--
-- Name: budget_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.budget_audit_id_seq OWNED BY public.budget_audit.id;


--
-- Name: budget_lock; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.budget_lock (
    id bigint NOT NULL,
    canonical_manager character varying(120) NOT NULL,
    target_month date NOT NULL,
    locked_budget numeric(14,2) NOT NULL,
    locked_renewal_forecast numeric(14,2) NOT NULL,
    locked_growth_target numeric(14,2) NOT NULL,
    locked_growth_pct numeric(6,4),
    reason text NOT NULL,
    locked_by character varying(120) NOT NULL,
    locked_at timestamp with time zone DEFAULT now() NOT NULL,
    active boolean DEFAULT true NOT NULL,
    unlocked_by character varying(120),
    unlocked_at timestamp with time zone,
    unlock_reason text
);


ALTER TABLE public.budget_lock OWNER TO postgres;

--
-- Name: budget_lock_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.budget_lock_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.budget_lock_id_seq OWNER TO postgres;

--
-- Name: budget_lock_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.budget_lock_id_seq OWNED BY public.budget_lock.id;


--
-- Name: category_map; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.category_map (
    category character varying(10) NOT NULL,
    business_classification character varying(60) NOT NULL,
    description text,
    active boolean DEFAULT true NOT NULL,
    CONSTRAINT ck_category_map_category_business_classification CHECK (((business_classification)::text = ANY ((ARRAY['Renewal'::character varying, 'Transfer Renewal'::character varying, 'New Business'::character varying, 'Endorsement'::character varying, 'Lapse / End-Term Lost Renewal'::character varying, 'Mid-Term Cancellation'::character varying, 'New Business Cancellation'::character varying, 'Adjustment'::character varying, 'Endorsement Cancellation'::character varying, 'Policy Reinstatement'::character varying, 'Unmapped'::character varying])::text[])))
);


ALTER TABLE public.category_map OWNER TO postgres;

--
-- Name: class_equivalence; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.class_equivalence (
    id integer NOT NULL,
    source_type character varying(20) NOT NULL,
    source_value character varying(80) NOT NULL,
    canonical_class character varying(60) NOT NULL,
    note text,
    updated_by character varying(120) NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT class_equivalence_source_type_check CHECK (((source_type)::text = ANY ((ARRAY['sales'::character varying, 'renewals'::character varying])::text[])))
);


ALTER TABLE public.class_equivalence OWNER TO postgres;

--
-- Name: class_equivalence_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.class_equivalence_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.class_equivalence_id_seq OWNER TO postgres;

--
-- Name: class_equivalence_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.class_equivalence_id_seq OWNED BY public.class_equivalence.id;


--
-- Name: column_mapping_profile; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.column_mapping_profile (
    id integer NOT NULL,
    profile_name character varying(120) NOT NULL,
    file_type character varying(20) NOT NULL,
    mapping jsonb NOT NULL,
    is_default boolean DEFAULT false NOT NULL,
    note text,
    created_by character varying(120) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.column_mapping_profile OWNER TO postgres;

--
-- Name: column_mapping_profile_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.column_mapping_profile_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.column_mapping_profile_id_seq OWNER TO postgres;

--
-- Name: column_mapping_profile_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.column_mapping_profile_id_seq OWNED BY public.column_mapping_profile.id;


--
-- Name: exclusion_rule; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.exclusion_rule (
    id integer NOT NULL,
    rule_group character varying(60) NOT NULL,
    rule_name character varying(120) NOT NULL,
    source_type character varying(20) NOT NULL,
    target_field character varying(60) NOT NULL,
    match_type character varying(20) NOT NULL,
    match_value character varying(120) NOT NULL,
    active boolean DEFAULT true NOT NULL,
    note text,
    updated_by character varying(120) NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_exclusion_rule_exclusion_match_type CHECK (((match_type)::text = ANY ((ARRAY['exact'::character varying, 'contains'::character varying])::text[]))),
    CONSTRAINT ck_exclusion_rule_exclusion_source_type CHECK (((source_type)::text = ANY ((ARRAY['sales'::character varying, 'renewals'::character varying, 'both'::character varying])::text[])))
);


ALTER TABLE public.exclusion_rule OWNER TO postgres;

--
-- Name: exclusion_rule_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.exclusion_rule_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.exclusion_rule_id_seq OWNER TO postgres;

--
-- Name: exclusion_rule_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.exclusion_rule_id_seq OWNED BY public.exclusion_rule.id;


--
-- Name: forecast_baseline; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.forecast_baseline (
    forecast_month date NOT NULL,
    financial_year integer NOT NULL,
    financial_quarter smallint NOT NULL,
    baseline_status character varying(20) NOT NULL,
    baseline_source character varying(60),
    suppress_achievement boolean DEFAULT false NOT NULL,
    manager_exceptions jsonb DEFAULT '[]'::jsonb NOT NULL,
    note text,
    updated_by character varying(120) NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_forecast_baseline_baseline_quarter_range CHECK (((financial_quarter >= 1) AND (financial_quarter <= 4))),
    CONSTRAINT ck_forecast_baseline_baseline_status CHECK (((baseline_status)::text = ANY ((ARRAY['complete'::character varying, 'incomplete'::character varying, 'unavailable'::character varying])::text[])))
);


ALTER TABLE public.forecast_baseline OWNER TO postgres;

--
-- Name: forecast_month_coverage; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.forecast_month_coverage (
    forecast_month date NOT NULL,
    original_snapshot_id bigint,
    latest_snapshot_id bigint,
    original_grain character varying(20) DEFAULT 'policy'::character varying NOT NULL,
    established_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_forecast_month_coverage_coverage_original_grain CHECK (((original_grain)::text = ANY ((ARRAY['policy'::character varying, 'manager_month'::character varying])::text[])))
);


ALTER TABLE public.forecast_month_coverage OWNER TO postgres;

--
-- Name: forecast_month_lock; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.forecast_month_lock (
    forecast_month date NOT NULL,
    locked_at timestamp with time zone DEFAULT now() NOT NULL,
    locked_by character varying(120) NOT NULL,
    reason text NOT NULL,
    source_description text,
    forecast_total numeric(14,2),
    active boolean DEFAULT true NOT NULL,
    released_at timestamp with time zone,
    released_by character varying(120),
    release_reason text
);


ALTER TABLE public.forecast_month_lock OWNER TO postgres;

--
-- Name: TABLE forecast_month_lock; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.forecast_month_lock IS 'Months whose Original Forecast is pinned regardless of later snapshots. Closed months are protected by the cut-off already; this is for pinning a month that is still open, or one established from a source other than the current snapshot.';


--
-- Name: forecast_month_override; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.forecast_month_override (
    id integer NOT NULL,
    forecast_month date NOT NULL,
    granted_by character varying(120) NOT NULL,
    granted_at timestamp with time zone DEFAULT now() NOT NULL,
    reason text NOT NULL,
    consumed_at timestamp with time zone,
    consumed_batch_id integer,
    before_total numeric(14,2),
    after_total numeric(14,2)
);


ALTER TABLE public.forecast_month_override OWNER TO postgres;

--
-- Name: TABLE forecast_month_override; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.forecast_month_override IS 'A single deliberate permission to write a frozen forecast month. Granted by an administrator with a reason, consumed by one upload, and retained afterwards so the before and after figures stay answerable.';


--
-- Name: forecast_month_override_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.forecast_month_override_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.forecast_month_override_id_seq OWNER TO postgres;

--
-- Name: forecast_month_override_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.forecast_month_override_id_seq OWNED BY public.forecast_month_override.id;


--
-- Name: forecast_movement; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.forecast_movement (
    id bigint NOT NULL,
    from_snapshot_id bigint,
    to_snapshot_id bigint NOT NULL,
    policy_id bigint NOT NULL,
    forecast_month date NOT NULL,
    movement_type character varying(30) NOT NULL,
    original_income numeric(14,2) DEFAULT 0 NOT NULL,
    previous_income numeric(14,2) DEFAULT 0 NOT NULL,
    latest_income numeric(14,2) DEFAULT 0 NOT NULL,
    movement_amount numeric(14,2) DEFAULT 0 NOT NULL,
    from_manager character varying(120),
    to_manager character varying(120),
    detail_changes jsonb,
    detected_at timestamp with time zone DEFAULT now() NOT NULL,
    added boolean DEFAULT false NOT NULL,
    removed boolean DEFAULT false NOT NULL,
    amount_changed boolean DEFAULT false NOT NULL,
    manager_changed boolean DEFAULT false NOT NULL,
    detail_changed boolean DEFAULT false NOT NULL,
    secondary_changes character varying(30)[] DEFAULT '{}'::character varying[] NOT NULL,
    CONSTRAINT ck_forecast_movement_movement_latest_non_negative CHECK ((latest_income >= (0)::numeric)),
    CONSTRAINT ck_forecast_movement_movement_type CHECK (((movement_type)::text = ANY ((ARRAY['removed_from_latest'::character varying, 'added_after_original'::character varying, 'amount_changed'::character varying, 'manager_changed'::character varying, 'detail_changed'::character varying, 'unchanged'::character varying])::text[])))
);


ALTER TABLE public.forecast_movement OWNER TO postgres;

--
-- Name: COLUMN forecast_movement.movement_type; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.forecast_movement.movement_type IS 'Primary classification for display. Use the boolean flags for counting: a policy that changed manager AND amount is movement_type=amount_changed but manager_changed is also true, and a manager-transfer count that reads only movement_type would miss it.';


--
-- Name: forecast_movement_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.forecast_movement_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.forecast_movement_id_seq OWNER TO postgres;

--
-- Name: forecast_movement_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.forecast_movement_id_seq OWNED BY public.forecast_movement.id;


--
-- Name: forecast_policy; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.forecast_policy (
    id bigint NOT NULL,
    snapshot_id bigint NOT NULL,
    policy_id bigint NOT NULL,
    client_id bigint,
    client_code character varying(60),
    client_code_norm character varying(60),
    policy_number character varying(120),
    policy_number_norm character varying(120),
    class_abbrev character varying(60),
    class_code character varying(60),
    class_description character varying(160),
    underwriter_abbrev character varying(60),
    inception_date date,
    expiry_date date NOT NULL,
    next_expiry_date date,
    renewal_months integer,
    forecast_month date NOT NULL,
    financial_year integer NOT NULL,
    financial_quarter smallint NOT NULL,
    source_manager character varying(120) NOT NULL,
    comm numeric(14,2) NOT NULL,
    comm_tax numeric(14,2) NOT NULL,
    fee numeric(14,2) NOT NULL,
    fee_tax numeric(14,2) NOT NULL,
    premium numeric(14,2),
    total_premium numeric(14,2),
    exception_flags character varying(30)[] DEFAULT '{}'::character varying[] NOT NULL,
    is_excluded boolean DEFAULT false NOT NULL,
    exclusion_rule_id integer,
    exclusion_field character varying(60),
    exclusion_value character varying(120),
    source_row jsonb NOT NULL,
    primary_assoc_comm_sum numeric(14,2) DEFAULT 0 NOT NULL,
    primary_assoc_comm_tax_sum numeric(14,2) DEFAULT 0 NOT NULL,
    primary_assoc_abbrev character varying(60),
    raw_expected_income numeric(14,2) GENERATED ALWAYS AS ((primary_assoc_comm_sum + primary_assoc_comm_tax_sum)) STORED NOT NULL,
    forecast_contribution numeric(14,2) GENERATED ALWAYS AS (GREATEST((primary_assoc_comm_sum + primary_assoc_comm_tax_sum), (0)::numeric)) STORED NOT NULL,
    gross_expected_income numeric(14,2) GENERATED ALWAYS AS ((((comm + comm_tax) + fee) + fee_tax)) STORED NOT NULL,
    CONSTRAINT ck_forecast_policy_fcst_quarter_range CHECK (((financial_quarter >= 1) AND (financial_quarter <= 4))),
    CONSTRAINT ck_forecast_policy_forecast_exception_flags CHECK ((exception_flags <@ ARRAY['negative_expected'::character varying, 'zero_expected'::character varying, 'overdue_pending'::character varying, 'residual_pending'::character varying]))
);


ALTER TABLE public.forecast_policy OWNER TO postgres;

--
-- Name: COLUMN forecast_policy.raw_expected_income; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.forecast_policy.raw_expected_income IS 'SIG expected income: primary associate commission plus its GST. The sum column is GST exclusive, so the tax column is required to keep this consistent with the GST-inclusive sales figures.';


--
-- Name: COLUMN forecast_policy.gross_expected_income; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.forecast_policy.gross_expected_income IS 'Comm + CommTax + Fee + FeeTax: the gross figure. Retained for audit. Not reported as expected income.';


--
-- Name: forecast_policy_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.forecast_policy_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.forecast_policy_id_seq OWNER TO postgres;

--
-- Name: forecast_policy_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.forecast_policy_id_seq OWNED BY public.forecast_policy.id;


--
-- Name: forecast_snapshot; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.forecast_snapshot (
    id bigint NOT NULL,
    batch_id bigint NOT NULL,
    as_of_date date NOT NULL,
    coverage_start date NOT NULL,
    coverage_end date NOT NULL,
    source_row_count integer NOT NULL,
    included_row_count integer NOT NULL,
    excluded_row_count integer NOT NULL,
    negative_row_count integer DEFAULT 0 NOT NULL,
    zero_row_count integer DEFAULT 0 NOT NULL,
    overdue_row_count integer DEFAULT 0 NOT NULL,
    raw_expected_income numeric(14,2) NOT NULL,
    forecast_contribution numeric(14,2) NOT NULL,
    is_superseded boolean DEFAULT false NOT NULL,
    validation_messages jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.forecast_snapshot OWNER TO postgres;

--
-- Name: forecast_snapshot_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.forecast_snapshot_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.forecast_snapshot_id_seq OWNER TO postgres;

--
-- Name: forecast_snapshot_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.forecast_snapshot_id_seq OWNED BY public.forecast_snapshot.id;


--
-- Name: growth_rate; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.growth_rate (
    id integer NOT NULL,
    scope character varying(20) NOT NULL,
    canonical_manager character varying(120),
    financial_year integer,
    financial_quarter smallint,
    growth_pct numeric(6,4),
    dollar_override numeric(14,2),
    note text,
    active boolean DEFAULT true NOT NULL,
    created_by character varying(120) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    target_month date,
    CONSTRAINT ck_growth_rate_growth_quarter_range CHECK (((financial_quarter IS NULL) OR ((financial_quarter >= 1) AND (financial_quarter <= 4)))),
    CONSTRAINT ck_growth_rate_growth_scope CHECK (((scope)::text = ANY ((ARRAY['global'::character varying, 'manager'::character varying, 'manager_quarter'::character varying, 'manager_month'::character varying])::text[]))),
    CONSTRAINT ck_growth_rate_growth_scope_consistency CHECK (((((scope)::text = 'global'::text) AND (canonical_manager IS NULL) AND (financial_quarter IS NULL) AND (target_month IS NULL)) OR (((scope)::text = 'manager'::text) AND (canonical_manager IS NOT NULL) AND (financial_quarter IS NULL) AND (target_month IS NULL)) OR (((scope)::text = 'manager_quarter'::text) AND (canonical_manager IS NOT NULL) AND (financial_year IS NOT NULL) AND (financial_quarter IS NOT NULL) AND (target_month IS NULL)) OR (((scope)::text = 'manager_month'::text) AND (canonical_manager IS NOT NULL) AND (target_month IS NOT NULL)))),
    CONSTRAINT ck_growth_rate_growth_value_present CHECK (((growth_pct IS NOT NULL) OR (dollar_override IS NOT NULL)))
);


ALTER TABLE public.growth_rate OWNER TO postgres;

--
-- Name: growth_rate_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.growth_rate_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.growth_rate_id_seq OWNER TO postgres;

--
-- Name: growth_rate_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.growth_rate_id_seq OWNED BY public.growth_rate.id;


--
-- Name: import_staging; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.import_staging (
    id bigint NOT NULL,
    batch_id bigint NOT NULL,
    source_row_number integer NOT NULL,
    status character varying(20) NOT NULL,
    fingerprint character varying(64),
    existing_transaction_id bigint,
    policy_id bigint,
    period_month date,
    source_manager character varying(120),
    category character varying(20),
    positive_income numeric(14,2),
    return_income numeric(14,2),
    net_income numeric(14,2),
    expected_income numeric(14,2),
    forecast_contribution numeric(14,2),
    is_excluded boolean DEFAULT false NOT NULL,
    exclusion_rule_id integer,
    exclusion_field character varying(60),
    exclusion_value character varying(120),
    exception_flags character varying(40)[] DEFAULT '{}'::character varying[] NOT NULL,
    reject_reason text,
    changed_fields jsonb,
    prepared jsonb NOT NULL,
    source_row jsonb NOT NULL,
    CONSTRAINT ck_import_staging_staging_status CHECK (((status)::text = ANY ((ARRAY['valid'::character varying, 'duplicate'::character varying, 'excluded'::character varying, 'rejected'::character varying, 'restated'::character varying])::text[])))
);


ALTER TABLE public.import_staging OWNER TO postgres;

--
-- Name: import_staging_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.import_staging_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.import_staging_id_seq OWNER TO postgres;

--
-- Name: import_staging_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.import_staging_id_seq OWNED BY public.import_staging.id;


--
-- Name: ingest_exception; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.ingest_exception (
    id bigint NOT NULL,
    batch_id bigint NOT NULL,
    exception_type character varying(60) NOT NULL,
    severity character varying(20) NOT NULL,
    source_row_number integer,
    field_name character varying(60),
    field_value text,
    message text NOT NULL,
    payload jsonb,
    detected_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_by character varying(120),
    resolved_at timestamp with time zone,
    CONSTRAINT ck_ingest_exception_exception_severity CHECK (((severity)::text = ANY ((ARRAY['info'::character varying, 'warning'::character varying, 'error'::character varying])::text[])))
);


ALTER TABLE public.ingest_exception OWNER TO postgres;

--
-- Name: ingest_exception_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.ingest_exception_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.ingest_exception_id_seq OWNER TO postgres;

--
-- Name: ingest_exception_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.ingest_exception_id_seq OWNED BY public.ingest_exception.id;


--
-- Name: legacy_forecast_reference; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.legacy_forecast_reference (
    id bigint NOT NULL,
    batch_id bigint,
    forecast_month date NOT NULL,
    financial_year integer NOT NULL,
    financial_quarter smallint NOT NULL,
    source_manager character varying(120) NOT NULL,
    forecast_amount numeric(14,2) NOT NULL,
    promoted_to_original boolean DEFAULT false NOT NULL,
    is_verified_exclusion_clean boolean DEFAULT true NOT NULL,
    note text,
    loaded_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.legacy_forecast_reference OWNER TO postgres;

--
-- Name: legacy_forecast_reference_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.legacy_forecast_reference_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.legacy_forecast_reference_id_seq OWNER TO postgres;

--
-- Name: legacy_forecast_reference_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.legacy_forecast_reference_id_seq OWNED BY public.legacy_forecast_reference.id;


--
-- Name: manager_alias; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.manager_alias (
    id integer NOT NULL,
    source_manager character varying(120) NOT NULL,
    source_manager_norm character varying(120) NOT NULL,
    canonical_manager character varying(120) NOT NULL,
    active boolean DEFAULT true NOT NULL,
    note text,
    updated_by character varying(120) NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.manager_alias OWNER TO postgres;

--
-- Name: manager_alias_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.manager_alias_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.manager_alias_id_seq OWNER TO postgres;

--
-- Name: manager_alias_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.manager_alias_id_seq OWNED BY public.manager_alias.id;


--
-- Name: match_allocation; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.match_allocation (
    id bigint NOT NULL,
    transaction_id bigint NOT NULL,
    policy_id bigint NOT NULL,
    forecast_month date NOT NULL,
    allocated_income numeric(14,2) NOT NULL,
    is_renewal_income boolean DEFAULT false NOT NULL,
    allocation_basis text NOT NULL,
    method character varying(10) DEFAULT 'auto'::character varying NOT NULL,
    tier smallint,
    confidence numeric(4,3),
    created_by character varying(120) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT match_allocation_method_check CHECK (((method)::text = ANY ((ARRAY['auto'::character varying, 'manual'::character varying])::text[])))
);


ALTER TABLE public.match_allocation OWNER TO postgres;

--
-- Name: match_allocation_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.match_allocation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.match_allocation_id_seq OWNER TO postgres;

--
-- Name: match_allocation_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.match_allocation_id_seq OWNED BY public.match_allocation.id;


--
-- Name: match_candidate; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.match_candidate (
    id bigint NOT NULL,
    transaction_id bigint,
    policy_id bigint,
    forecast_month date,
    tier smallint,
    confidence numeric(4,3),
    reason character varying(40) NOT NULL,
    candidate_rank smallint,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    detail jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT match_candidate_reason_check CHECK (((reason)::text = ANY ((ARRAY['multiple_policies_for_transaction'::character varying, 'multiple_transactions_for_policy'::character varying, 'low_tier_requires_review'::character varying, 'class_conflict'::character varying, 'unmatched_actual_renewal'::character varying, 'unmatched_forecast_policy'::character varying])::text[]))),
    CONSTRAINT match_candidate_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'accepted'::character varying, 'rejected'::character varying, 'superseded'::character varying])::text[])))
);


ALTER TABLE public.match_candidate OWNER TO postgres;

--
-- Name: match_candidate_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.match_candidate_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.match_candidate_id_seq OWNER TO postgres;

--
-- Name: match_candidate_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.match_candidate_id_seq OWNED BY public.match_candidate.id;


--
-- Name: match_decision; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.match_decision (
    id bigint NOT NULL,
    policy_id bigint,
    forecast_month date,
    transaction_id bigint,
    action character varying(20) NOT NULL,
    previous_decision jsonb,
    new_decision jsonb,
    reason text NOT NULL,
    reviewer character varying(120) NOT NULL,
    decided_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT match_decision_action_check CHECK (((action)::text = ANY ((ARRAY['manual_match'::character varying, 'reject_match'::character varying, 'rematch'::character varying, 'apportion'::character varying, 'set_outcome'::character varying])::text[])))
);


ALTER TABLE public.match_decision OWNER TO postgres;

--
-- Name: match_decision_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.match_decision_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.match_decision_id_seq OWNER TO postgres;

--
-- Name: match_decision_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.match_decision_id_seq OWNED BY public.match_decision.id;


--
-- Name: match_run; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.match_run (
    id bigint NOT NULL,
    run_by character varying(120) NOT NULL,
    run_at timestamp with time zone DEFAULT now() NOT NULL,
    cut_off_date date NOT NULL,
    date_tolerance_days integer NOT NULL,
    forecast_policies integer DEFAULT 0 NOT NULL,
    auto_matched integer DEFAULT 0 NOT NULL,
    auto_matched_income numeric(14,2) DEFAULT 0 NOT NULL,
    review_queue integer DEFAULT 0 NOT NULL,
    unmatched_policies integer DEFAULT 0 NOT NULL,
    unmatched_actuals integer DEFAULT 0 NOT NULL,
    by_tier jsonb DEFAULT '{}'::jsonb NOT NULL,
    note text
);


ALTER TABLE public.match_run OWNER TO postgres;

--
-- Name: match_run_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.match_run_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.match_run_id_seq OWNER TO postgres;

--
-- Name: match_run_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.match_run_id_seq OWNED BY public.match_run.id;


--
-- Name: monthly_target_override; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.monthly_target_override (
    id integer NOT NULL,
    canonical_manager character varying(120) NOT NULL,
    target_month date NOT NULL,
    override_amount numeric(14,2) NOT NULL,
    reason text NOT NULL,
    created_by character varying(120) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    active boolean DEFAULT true NOT NULL
);


ALTER TABLE public.monthly_target_override OWNER TO postgres;

--
-- Name: monthly_target_override_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.monthly_target_override_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.monthly_target_override_id_seq OWNER TO postgres;

--
-- Name: monthly_target_override_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.monthly_target_override_id_seq OWNED BY public.monthly_target_override.id;


--
-- Name: original_forecast; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.original_forecast (
    id bigint NOT NULL,
    grain character varying(20) DEFAULT 'policy'::character varying NOT NULL,
    policy_id bigint,
    forecast_month date NOT NULL,
    financial_year integer NOT NULL,
    financial_quarter smallint NOT NULL,
    origin character varying(30) DEFAULT 'snapshot'::character varying NOT NULL,
    established_snapshot_id bigint,
    established_batch_id bigint,
    established_by character varying(120) NOT NULL,
    established_at timestamp with time zone DEFAULT now() NOT NULL,
    source_manager character varying(120) NOT NULL,
    client_code character varying(60),
    policy_number character varying(120),
    class_abbrev character varying(60),
    expected_income numeric(14,2) NOT NULL,
    forecast_contribution numeric(14,2) NOT NULL,
    note text,
    income_basis character varying(24) DEFAULT 'associate'::character varying NOT NULL,
    basis_verified_by character varying(120),
    basis_verified_at timestamp with time zone,
    CONSTRAINT ck_original_forecast_orig_contribution_non_negative CHECK ((forecast_contribution >= (0)::numeric)),
    CONSTRAINT ck_original_forecast_orig_grain CHECK (((grain)::text = ANY ((ARRAY['policy'::character varying, 'manager_month'::character varying])::text[]))),
    CONSTRAINT ck_original_forecast_orig_grain_policy_consistency CHECK (((((grain)::text = 'policy'::text) AND (policy_id IS NOT NULL)) OR (((grain)::text = 'manager_month'::text) AND (policy_id IS NULL)))),
    CONSTRAINT ck_original_forecast_orig_origin CHECK (((origin)::text = ANY ((ARRAY['snapshot'::character varying, 'legacy_dashboard'::character varying, 'prior_year_actual'::character varying, 'manual_entry'::character varying, 'rebaseline'::character varying, 'derived_from_actuals'::character varying])::text[])))
);


ALTER TABLE public.original_forecast OWNER TO postgres;

--
-- Name: COLUMN original_forecast.origin; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.original_forecast.origin IS 'snapshot: from a Renewals Pending file. legacy_dashboard: carried from the old workbook. prior_year_actual: the same month last year, used where no policy-level forecast exists. manual_entry: figures supplied directly for a month with no usable pending forecast. derived_from_actuals: never used, because a period''s own result must not become its own target.';


--
-- Name: COLUMN original_forecast.income_basis; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.original_forecast.income_basis IS 'associate = confirmed on the primary associate basis and scoreable. gross_unverified = predates the change, could not be rebased, excluded from achievement and bonus until an audited reconstruction is approved.';


--
-- Name: original_forecast_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.original_forecast_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.original_forecast_id_seq OWNER TO postgres;

--
-- Name: original_forecast_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.original_forecast_id_seq OWNED BY public.original_forecast.id;


--
-- Name: period_coverage; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.period_coverage (
    id integer NOT NULL,
    financial_year integer NOT NULL,
    data_domain character varying(20) NOT NULL,
    coverage_status character varying(20) NOT NULL,
    months_present integer NOT NULL,
    first_month date NOT NULL,
    last_month date NOT NULL,
    label character varying(160),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_period_coverage_coverage_status CHECK (((coverage_status)::text = ANY ((ARRAY['complete'::character varying, 'partial'::character varying])::text[])))
);


ALTER TABLE public.period_coverage OWNER TO postgres;

--
-- Name: period_coverage_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.period_coverage_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.period_coverage_id_seq OWNER TO postgres;

--
-- Name: period_coverage_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.period_coverage_id_seq OWNED BY public.period_coverage.id;


--
-- Name: policy_outcome; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.policy_outcome (
    id bigint NOT NULL,
    policy_id bigint NOT NULL,
    forecast_month date NOT NULL,
    canonical_manager character varying(120),
    outcome character varying(40) NOT NULL,
    renewal_transaction_income numeric(14,2) DEFAULT 0 NOT NULL,
    total_associated_income numeric(14,2) DEFAULT 0 NOT NULL,
    original_forecast_income numeric(14,2) DEFAULT 0 NOT NULL,
    latest_forecast_income numeric(14,2),
    matched_transaction_count integer DEFAULT 0 NOT NULL,
    best_tier smallint,
    confidence numeric(4,3),
    requires_review boolean DEFAULT false NOT NULL,
    is_manual boolean DEFAULT false NOT NULL,
    note text,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT policy_outcome_confidence_check CHECK (((confidence IS NULL) OR ((confidence >= (0)::numeric) AND (confidence <= (1)::numeric)))),
    CONSTRAINT policy_outcome_outcome_check CHECK (((outcome)::text = ANY ((ARRAY['renewed'::character varying, 'transfer_renewed'::character varying, 'lapsed_lost'::character varying, 'pending'::character varying, 'removed_from_latest'::character varying, 'multiple_candidates'::character varying, 'unmatched'::character varying, 'manually_resolved'::character varying])::text[])))
);


ALTER TABLE public.policy_outcome OWNER TO postgres;

--
-- Name: policy_outcome_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.policy_outcome_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.policy_outcome_id_seq OWNER TO postgres;

--
-- Name: policy_outcome_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.policy_outcome_id_seq OWNED BY public.policy_outcome.id;


--
-- Name: rebaseline_audit; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.rebaseline_audit (
    id bigint NOT NULL,
    scope_description text NOT NULL,
    forecast_month_from date NOT NULL,
    forecast_month_to date NOT NULL,
    reason text NOT NULL,
    performed_by character varying(120) NOT NULL,
    performed_at timestamp with time zone DEFAULT now() NOT NULL,
    before_total numeric(14,2) NOT NULL,
    after_total numeric(14,2) NOT NULL,
    before_detail jsonb,
    after_detail jsonb
);


ALTER TABLE public.rebaseline_audit OWNER TO postgres;

--
-- Name: rebaseline_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.rebaseline_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.rebaseline_audit_id_seq OWNER TO postgres;

--
-- Name: rebaseline_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.rebaseline_audit_id_seq OWNED BY public.rebaseline_audit.id;


--
-- Name: reporting_manager; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.reporting_manager (
    id integer NOT NULL,
    canonical_manager character varying(120) NOT NULL,
    status character varying(20) NOT NULL,
    include_in_rankings boolean NOT NULL,
    include_in_business_totals boolean NOT NULL,
    display_order integer,
    note text,
    updated_by character varying(120) NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_reporting_manager_manager_status CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'legacy_unmapped'::character varying, 'inactive'::character varying])::text[])))
);


ALTER TABLE public.reporting_manager OWNER TO postgres;

--
-- Name: reporting_manager_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.reporting_manager_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.reporting_manager_id_seq OWNER TO postgres;

--
-- Name: reporting_manager_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.reporting_manager_id_seq OWNED BY public.reporting_manager.id;


--
-- Name: reporting_settings; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.reporting_settings (
    id smallint NOT NULL,
    cut_off_date date NOT NULL,
    cut_off_set_by character varying(120) NOT NULL,
    cut_off_set_at timestamp with time zone DEFAULT now() NOT NULL,
    match_date_tolerance_days integer NOT NULL,
    default_growth_pct numeric(14,2) NOT NULL,
    gst_note text NOT NULL,
    bonus_base_divisor numeric(6,2) DEFAULT 3 NOT NULL,
    bonus_above_target_rate numeric(6,4) DEFAULT 0.20 NOT NULL,
    bonus_gst_divisor numeric(6,4) DEFAULT 1.1 NOT NULL,
    CONSTRAINT ck_reporting_settings_match_tolerance_range CHECK (((match_date_tolerance_days >= 0) AND (match_date_tolerance_days <= 365))),
    CONSTRAINT ck_reporting_settings_reporting_settings_singleton CHECK ((id = 1))
);


ALTER TABLE public.reporting_settings OWNER TO postgres;

--
-- Name: COLUMN reporting_settings.bonus_base_divisor; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.reporting_settings.bonus_base_divisor IS 'Base bonus is the monetary growth target divided by this. Default 3.';


--
-- Name: COLUMN reporting_settings.bonus_above_target_rate; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.reporting_settings.bonus_above_target_rate IS 'Share of income above the budget target paid as additional bonus. Default 0.20.';


--
-- Name: restated_transaction; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.restated_transaction (
    id bigint NOT NULL,
    transaction_id bigint NOT NULL,
    batch_id bigint NOT NULL,
    changed_fields jsonb NOT NULL,
    detected_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_by character varying(120),
    resolved_at timestamp with time zone,
    resolution character varying(30),
    note text
);


ALTER TABLE public.restated_transaction OWNER TO postgres;

--
-- Name: restated_transaction_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.restated_transaction_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.restated_transaction_id_seq OWNER TO postgres;

--
-- Name: restated_transaction_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.restated_transaction_id_seq OWNED BY public.restated_transaction.id;


--
-- Name: sales_transaction; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.sales_transaction (
    id bigint NOT NULL,
    fingerprint character varying(64) NOT NULL,
    first_seen_batch_id bigint NOT NULL,
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_batch_id bigint NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    seen_count integer DEFAULT 1 NOT NULL,
    transaction_date timestamp without time zone NOT NULL,
    period_month date NOT NULL,
    financial_year integer NOT NULL,
    financial_quarter smallint NOT NULL,
    source_manager character varying(120) NOT NULL,
    group1_id integer,
    group2_description character varying(120),
    client_id bigint,
    client_code character varying(60),
    client_code_norm character varying(60),
    policy_number character varying(120),
    policy_number_norm character varying(120),
    invoice_number bigint,
    username character varying(120),
    category character varying(10) NOT NULL,
    business_classification character varying(60) NOT NULL,
    derived_classification character varying(60) NOT NULL,
    policy_class character varying(60),
    uw_code character varying(60),
    reason text,
    premium numeric(14,2),
    nett numeric(14,2),
    commission numeric(14,2) NOT NULL,
    fees numeric(14,2) NOT NULL,
    sub_comm numeric(14,2),
    financial_direction character varying(10) NOT NULL,
    primary_assoc_code character varying(60),
    primary_assoc_amount numeric(14,2) DEFAULT 0 NOT NULL,
    secondary_assoc_code character varying(60),
    secondary_assoc_amount numeric(14,2),
    is_excluded boolean DEFAULT false NOT NULL,
    exclusion_rule_id integer,
    exclusion_field character varying(60),
    exclusion_value character varying(120),
    source_row jsonb NOT NULL,
    actual_income numeric(14,2) GENERATED ALWAYS AS (primary_assoc_amount) STORED NOT NULL,
    positive_income numeric(14,2) GENERATED ALWAYS AS (GREATEST(primary_assoc_amount, (0)::numeric)) STORED NOT NULL,
    signed_return_income numeric(14,2) GENERATED ALWAYS AS (LEAST(primary_assoc_amount, (0)::numeric)) STORED NOT NULL,
    absolute_return_income numeric(14,2) GENERATED ALWAYS AS (abs(LEAST(primary_assoc_amount, (0)::numeric))) STORED NOT NULL,
    gross_income numeric(14,2) GENERATED ALWAYS AS ((commission + fees)) STORED NOT NULL,
    CONSTRAINT ck_sales_transaction_txn_business_classification CHECK (((business_classification)::text = ANY ((ARRAY['Renewal'::character varying, 'Transfer Renewal'::character varying, 'New Business'::character varying, 'Endorsement'::character varying, 'Lapse / End-Term Lost Renewal'::character varying, 'Mid-Term Cancellation'::character varying, 'New Business Cancellation'::character varying, 'Adjustment'::character varying, 'Endorsement Cancellation'::character varying, 'Policy Reinstatement'::character varying, 'Unmapped'::character varying])::text[]))),
    CONSTRAINT ck_sales_transaction_txn_derived_classification CHECK (((derived_classification)::text = ANY ((ARRAY['Positive Renewal'::character varying, 'Renewal Return or Correction'::character varying, 'Positive Transfer Renewal'::character varying, 'Transfer Renewal Return or Correction'::character varying, 'Positive New Business'::character varying, 'Negative New Business Correction'::character varying, 'New Business Cancellation'::character varying, 'Positive Endorsement'::character varying, 'Negative Endorsement'::character varying, 'Endorsement Cancellation'::character varying, 'Lapse / Lost Renewal'::character varying, 'Mid-Term Cancellation'::character varying, 'Positive Adjustment'::character varying, 'Negative Adjustment'::character varying, 'Policy Reinstatement'::character varying, 'Unmapped'::character varying])::text[]))),
    CONSTRAINT ck_sales_transaction_txn_exclusion_consistency CHECK ((((is_excluded = false) AND (exclusion_rule_id IS NULL)) OR ((is_excluded = true) AND (exclusion_rule_id IS NOT NULL)))),
    CONSTRAINT ck_sales_transaction_txn_financial_direction CHECK (((financial_direction)::text = ANY ((ARRAY['positive'::character varying, 'negative'::character varying, 'nil'::character varying])::text[]))),
    CONSTRAINT ck_sales_transaction_txn_quarter_range CHECK (((financial_quarter >= 1) AND (financial_quarter <= 4)))
);


ALTER TABLE public.sales_transaction OWNER TO postgres;

--
-- Name: COLUMN sales_transaction.actual_income; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.sales_transaction.actual_income IS 'SIG income: the primary associate amount, GST inclusive. This is what the brokerage receives, and it drives every reported figure.';


--
-- Name: COLUMN sales_transaction.gross_income; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.sales_transaction.gross_income IS 'Commission plus fees: the gross brokerage figure. Retained for audit and for reconciliation against the source report. Not reported as income.';


--
-- Name: sales_transaction_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.sales_transaction_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.sales_transaction_id_seq OWNER TO postgres;

--
-- Name: sales_transaction_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.sales_transaction_id_seq OWNED BY public.sales_transaction.id;


--
-- Name: schema_migration; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.schema_migration (
    filename character varying(200) NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL,
    applied_by character varying(120)
);


ALTER TABLE public.schema_migration OWNER TO postgres;

--
-- Name: snapshot_month_coverage; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.snapshot_month_coverage (
    id bigint NOT NULL,
    snapshot_id bigint NOT NULL,
    forecast_month date NOT NULL,
    policy_count integer NOT NULL,
    forecast_contribution numeric(14,2) NOT NULL,
    is_confirmed_complete boolean DEFAULT false NOT NULL,
    coverage_basis text DEFAULT 'observed'::text NOT NULL,
    CONSTRAINT snapshot_month_coverage_coverage_basis_check CHECK ((coverage_basis = ANY (ARRAY['observed'::text, 'confirmed_by_user'::text, 'declared_by_file'::text])))
);


ALTER TABLE public.snapshot_month_coverage OWNER TO postgres;

--
-- Name: snapshot_month_coverage_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.snapshot_month_coverage_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.snapshot_month_coverage_id_seq OWNER TO postgres;

--
-- Name: snapshot_month_coverage_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.snapshot_month_coverage_id_seq OWNED BY public.snapshot_month_coverage.id;


--
-- Name: transaction_sighting; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.transaction_sighting (
    id bigint NOT NULL,
    transaction_id bigint NOT NULL,
    batch_id bigint NOT NULL,
    source_row_number integer,
    seen_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.transaction_sighting OWNER TO postgres;

--
-- Name: transaction_sighting_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.transaction_sighting_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.transaction_sighting_id_seq OWNER TO postgres;

--
-- Name: transaction_sighting_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.transaction_sighting_id_seq OWNED BY public.transaction_sighting.id;


--
-- Name: upload_batch; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.upload_batch (
    id bigint NOT NULL,
    file_name character varying(260) NOT NULL,
    file_type character varying(20) NOT NULL,
    file_sha256 character varying(64) NOT NULL,
    file_size_bytes bigint,
    uploaded_by character varying(120) NOT NULL,
    uploaded_at timestamp with time zone DEFAULT now() NOT NULL,
    accepted_by character varying(120),
    accepted_at timestamp with time zone,
    status character varying(20) NOT NULL,
    source_row_count integer,
    accepted_row_count integer,
    duplicate_row_count integer,
    excluded_row_count integer,
    rejected_row_count integer,
    coverage_start date,
    coverage_end date,
    positive_income numeric(14,2),
    return_income numeric(14,2),
    net_income numeric(14,2),
    expected_forecast_income numeric(14,2),
    exception_count integer,
    rolled_back_by character varying(120),
    rolled_back_at timestamp with time zone,
    rollback_reason text,
    validation_messages jsonb DEFAULT '[]'::jsonb NOT NULL,
    column_mapping jsonb DEFAULT '{}'::jsonb NOT NULL,
    requires_confirmation boolean DEFAULT false NOT NULL,
    confirmation_note text,
    confirmed_by character varying(120),
    confirmed_at timestamp with time zone,
    confirmed_months date[] DEFAULT '{}'::date[] NOT NULL,
    coverage_warnings jsonb DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT ck_upload_batch_batch_file_type CHECK (((file_type)::text = ANY ((ARRAY['sales'::character varying, 'renewals'::character varying, 'legacy_forecast'::character varying])::text[]))),
    CONSTRAINT ck_upload_batch_batch_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'accepted'::character varying, 'rejected'::character varying, 'rolled_back'::character varying])::text[])))
);


ALTER TABLE public.upload_batch OWNER TO postgres;

--
-- Name: upload_batch_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.upload_batch_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.upload_batch_id_seq OWNER TO postgres;

--
-- Name: upload_batch_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.upload_batch_id_seq OWNED BY public.upload_batch.id;


--
-- Name: user_session; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.user_session (
    id bigint NOT NULL,
    user_id integer NOT NULL,
    token_hash character varying(64) NOT NULL,
    issued_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    ip inet,
    user_agent text,
    revoked_at timestamp with time zone,
    revoked_by character varying(120),
    revoke_reason text
);


ALTER TABLE public.user_session OWNER TO postgres;

--
-- Name: user_session_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.user_session_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.user_session_id_seq OWNER TO postgres;

--
-- Name: user_session_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.user_session_id_seq OWNED BY public.user_session.id;


--
-- Name: v_manager_resolution; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_manager_resolution AS
 SELECT a.source_manager,
    a.source_manager_norm,
    a.canonical_manager,
    m.status,
    m.include_in_rankings,
    m.include_in_business_totals,
    m.display_order
   FROM (public.manager_alias a
     JOIN public.reporting_manager m ON (((m.canonical_manager)::text = (a.canonical_manager)::text)))
  WHERE a.active;


ALTER VIEW public.v_manager_resolution OWNER TO postgres;

--
-- Name: v_sales_reported; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_sales_reported AS
 SELECT t.id,
    t.fingerprint,
    t.first_seen_batch_id,
    t.first_seen_at,
    t.last_seen_batch_id,
    t.last_seen_at,
    t.seen_count,
    t.transaction_date,
    t.period_month,
    t.financial_year,
    t.financial_quarter,
    t.source_manager,
    t.group1_id,
    t.group2_description,
    t.client_id,
    t.client_code,
    t.client_code_norm,
    t.policy_number,
    t.policy_number_norm,
    t.invoice_number,
    t.username,
    t.category,
    t.business_classification,
    t.derived_classification,
    t.policy_class,
    t.uw_code,
    t.reason,
    t.premium,
    t.nett,
    t.commission,
    t.fees,
    t.sub_comm,
    t.actual_income,
    t.positive_income,
    t.signed_return_income,
    t.absolute_return_income,
    t.financial_direction,
    t.primary_assoc_code,
    t.primary_assoc_amount,
    t.secondary_assoc_code,
    t.secondary_assoc_amount,
    t.is_excluded,
    t.exclusion_rule_id,
    t.exclusion_field,
    t.exclusion_value,
    t.source_row,
    r.canonical_manager,
    r.include_in_rankings,
    r.include_in_business_totals
   FROM (public.sales_transaction t
     LEFT JOIN public.v_manager_resolution r ON (((r.source_manager)::text = (t.source_manager)::text)))
  WHERE (NOT t.is_excluded);


ALTER VIEW public.v_sales_reported OWNER TO postgres;

--
-- Name: v_actual_month; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_actual_month AS
 SELECT COALESCE(canonical_manager, source_manager) AS canonical_manager,
    period_month,
    financial_year,
    financial_quarter,
    sum(positive_income) AS positive_actual_income,
    sum(signed_return_income) AS signed_return_income,
    sum(absolute_return_income) AS absolute_return_income,
    sum(actual_income) AS net_actual_income,
    sum(actual_income) FILTER (WHERE ((category)::text = ANY (ARRAY[('RWL'::character varying)::text, ('TRW'::character varying)::text]))) AS actual_renewal_income,
    sum(actual_income) FILTER (WHERE ((category)::text = 'N/B'::text)) AS actual_new_business,
    sum(absolute_return_income) FILTER (WHERE ((category)::text = 'NCN'::text)) AS new_business_cancellation,
    sum(absolute_return_income) FILTER (WHERE ((category)::text = 'LAP'::text)) AS lapse_income_returned,
    sum(absolute_return_income) FILTER (WHERE ((category)::text = 'MCN'::text)) AS midterm_cancellation_returned,
    sum(actual_income) FILTER (WHERE (((category)::text = 'END'::text) AND (actual_income > (0)::numeric))) AS positive_endorsements,
    sum(absolute_return_income) FILTER (WHERE (((category)::text = 'END'::text) AND (actual_income < (0)::numeric))) AS negative_endorsements,
    sum(absolute_return_income) FILTER (WHERE ((category)::text = 'ECN'::text)) AS endorsement_cancellations,
    count(*) AS transaction_rows
   FROM public.v_sales_reported
  GROUP BY COALESCE(canonical_manager, source_manager), period_month, financial_year, financial_quarter;


ALTER VIEW public.v_actual_month OWNER TO postgres;

--
-- Name: v_allocation_integrity; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_allocation_integrity AS
 SELECT a.transaction_id,
    t.actual_income AS transaction_income,
    sum(a.allocated_income) AS allocated_total,
    count(*) AS allocation_count,
    count(DISTINCT a.policy_id) AS policies_credited,
    count(*) FILTER (WHERE ((a.method)::text = 'auto'::text)) AS auto_allocations,
        CASE
            WHEN ((t.actual_income >= (0)::numeric) AND (sum(a.allocated_income) > (t.actual_income + 0.001))) THEN 'over_allocated'::text
            WHEN ((t.actual_income < (0)::numeric) AND (sum(a.allocated_income) < (t.actual_income - 0.001))) THEN 'over_allocated'::text
            WHEN (count(*) FILTER (WHERE ((a.method)::text = 'auto'::text)) > 1) THEN 'multiple_auto_allocations'::text
            ELSE 'ok'::text
        END AS status
   FROM (public.match_allocation a
     JOIN public.sales_transaction t ON ((t.id = a.transaction_id)))
  GROUP BY a.transaction_id, t.actual_income;


ALTER VIEW public.v_allocation_integrity OWNER TO postgres;

--
-- Name: v_allocation_breaches; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_allocation_breaches AS
 SELECT transaction_id,
    transaction_income,
    allocated_total,
    allocation_count,
    policies_credited,
    auto_allocations,
    status
   FROM public.v_allocation_integrity
  WHERE (status <> 'ok'::text);


ALTER VIEW public.v_allocation_breaches OWNER TO postgres;

--
-- Name: v_baseline_basis_month; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_baseline_basis_month AS
 SELECT forecast_month AS month,
    count(*) AS baseline_rows,
    count(*) FILTER (WHERE ((income_basis)::text = 'associate'::text)) AS rows_associate,
    count(*) FILTER (WHERE ((income_basis)::text <> 'associate'::text)) AS rows_unverified,
    sum(forecast_contribution) FILTER (WHERE ((income_basis)::text <> 'associate'::text)) AS value_unverified,
    bool_and(((income_basis)::text = 'associate'::text)) AS scoreable
   FROM public.original_forecast o
  GROUP BY forecast_month;


ALTER VIEW public.v_baseline_basis_month OWNER TO postgres;

--
-- Name: VIEW v_baseline_basis_month; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON VIEW public.v_baseline_basis_month IS 'Whether a month rests entirely on confirmed associate-basis figures. A month with any unverified row is not scored at all -- part of a target on one basis and part on another is not a target.';


--
-- Name: v_baseline_usable; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_baseline_usable AS
 SELECT b.forecast_month,
    b.financial_year,
    b.financial_quarter,
    m.canonical_manager,
    b.baseline_status,
    b.baseline_source,
    (((b.baseline_status)::text = 'complete'::text) AND (NOT b.suppress_achievement) AND (NOT (b.manager_exceptions ? (m.canonical_manager)::text))) AS baseline_usable,
    b.note
   FROM (public.forecast_baseline b
     CROSS JOIN public.reporting_manager m);


ALTER VIEW public.v_baseline_usable OWNER TO postgres;

--
-- Name: v_original_forecast_month; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_original_forecast_month AS
 SELECT COALESCE(r.canonical_manager, o.source_manager) AS canonical_manager,
    o.forecast_month,
    o.financial_year,
    o.financial_quarter,
    o.grain,
    o.origin,
    sum(o.forecast_contribution) AS original_forecast,
    count(*) FILTER (WHERE ((o.grain)::text = 'policy'::text)) AS original_policy_count
   FROM (public.original_forecast o
     LEFT JOIN public.v_manager_resolution r ON (((r.source_manager)::text = (o.source_manager)::text)))
  GROUP BY COALESCE(r.canonical_manager, o.source_manager), o.forecast_month, o.financial_year, o.financial_quarter, o.grain, o.origin;


ALTER VIEW public.v_original_forecast_month OWNER TO postgres;

--
-- Name: v_monthly_budget; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_monthly_budget AS
 WITH monthly AS (
         SELECT v_original_forecast_month.canonical_manager,
            v_original_forecast_month.forecast_month,
            v_original_forecast_month.financial_year,
            v_original_forecast_month.financial_quarter,
            sum(v_original_forecast_month.original_forecast) AS original_forecast
           FROM public.v_original_forecast_month
          GROUP BY v_original_forecast_month.canonical_manager, v_original_forecast_month.forecast_month, v_original_forecast_month.financial_year, v_original_forecast_month.financial_quarter
        ), quarterly AS (
         SELECT monthly.canonical_manager,
            monthly.financial_year,
            monthly.financial_quarter,
            sum(monthly.original_forecast) AS quarter_original,
            count(*) AS months_in_quarter
           FROM monthly
          GROUP BY monthly.canonical_manager, monthly.financial_year, monthly.financial_quarter
        ), resolved AS (
         SELECT m.canonical_manager,
            m.forecast_month,
            m.financial_year,
            m.financial_quarter,
            m.original_forecast,
            q.quarter_original,
            q.months_in_quarter,
            g.basis AS growth_basis,
            g.growth_pct,
            g.dollar_override
           FROM ((monthly m
             JOIN quarterly q ON ((((q.canonical_manager)::text = (m.canonical_manager)::text) AND (q.financial_year = m.financial_year) AND (q.financial_quarter = m.financial_quarter))))
             CROSS JOIN LATERAL public.resolve_growth_month((m.canonical_manager)::text, m.forecast_month) g(basis, growth_pct, dollar_override, note))
        ), calculated AS (
         SELECT r.canonical_manager,
            r.forecast_month,
            r.financial_year,
            r.financial_quarter,
            r.original_forecast,
            r.quarter_original,
            r.months_in_quarter,
            r.growth_basis,
            r.growth_pct,
            r.dollar_override,
                CASE
                    WHEN ((r.dollar_override IS NOT NULL) AND (r.growth_basis = 'manager_month'::text)) THEN r.dollar_override
                    WHEN ((r.dollar_override IS NOT NULL) AND (r.quarter_original > (0)::numeric)) THEN (r.dollar_override * (r.original_forecast / r.quarter_original))
                    WHEN (r.dollar_override IS NOT NULL) THEN (r.dollar_override / (NULLIF(r.months_in_quarter, 0))::numeric)
                    ELSE (r.original_forecast * r.growth_pct)
                END AS calculated_growth_target,
                CASE
                    WHEN (r.dollar_override IS NOT NULL) THEN 'dollar_override'::text
                    ELSE 'growth_percentage'::text
                END AS allocation_method
           FROM resolved r
        )
 SELECT c.canonical_manager,
    c.forecast_month,
    c.financial_year,
    c.financial_quarter,
    c.original_forecast,
    c.growth_basis,
    c.growth_pct,
    c.allocation_method,
    c.calculated_growth_target,
    o.override_amount,
    (l.locked_budget IS NOT NULL) AS is_locked,
    l.locked_at,
    l.locked_by,
    l.reason AS lock_reason,
    COALESCE(o.override_amount, c.calculated_growth_target) AS new_business_growth_target,
    (o.override_amount IS NOT NULL) AS is_overridden,
    o.reason AS override_reason,
    COALESCE(l.locked_budget, (c.original_forecast + COALESCE(o.override_amount, c.calculated_growth_target))) AS total_budget
   FROM ((calculated c
     LEFT JOIN public.monthly_target_override o ON ((((o.canonical_manager)::text = (c.canonical_manager)::text) AND (o.target_month = c.forecast_month) AND o.active)))
     LEFT JOIN public.budget_lock l ON ((((l.canonical_manager)::text = (c.canonical_manager)::text) AND (l.target_month = c.forecast_month) AND l.active)));


ALTER VIEW public.v_monthly_budget OWNER TO postgres;

--
-- Name: v_bonus_month; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_bonus_month AS
 WITH settings AS (
         SELECT reporting_settings.bonus_base_divisor AS divisor,
            reporting_settings.bonus_above_target_rate AS above_rate,
            (date_trunc('month'::text, (reporting_settings.cut_off_date)::timestamp with time zone))::date AS cut_month
           FROM public.reporting_settings
          WHERE (reporting_settings.id = 1)
        )
 SELECT b.canonical_manager,
    b.forecast_month AS period_month,
    b.financial_year,
    b.financial_quarter,
    (b.forecast_month <= s.cut_month) AS month_started,
    b.original_forecast AS expected_income,
    b.total_budget AS budget_target,
    (b.total_budget - b.original_forecast) AS growth_target_amount,
    a.net_actual_income AS actual_income,
        CASE
            WHEN (b.forecast_month <= s.cut_month) THEN (COALESCE(a.net_actual_income, (0)::numeric) >= b.total_budget)
            ELSE NULL::boolean
        END AS target_reached,
    round(
        CASE
            WHEN (b.forecast_month > s.cut_month) THEN NULL::numeric
            WHEN (COALESCE(a.net_actual_income, (0)::numeric) < b.total_budget) THEN (0)::numeric
            ELSE (((b.total_budget - b.original_forecast) / NULLIF(s.divisor, (0)::numeric)) + ((COALESCE(a.net_actual_income, (0)::numeric) - b.total_budget) * s.above_rate))
        END, 2) AS indicative_bonus
   FROM ((public.v_monthly_budget b
     CROSS JOIN settings s)
     LEFT JOIN public.v_actual_month a ON ((((a.canonical_manager)::text = (b.canonical_manager)::text) AND (a.period_month = b.forecast_month))));


ALTER VIEW public.v_bonus_month OWNER TO postgres;

--
-- Name: v_bonus_quarter; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_bonus_quarter AS
 WITH settings AS (
         SELECT reporting_settings.bonus_base_divisor AS divisor,
            reporting_settings.bonus_above_target_rate AS above_rate,
            (date_trunc('month'::text, (reporting_settings.cut_off_date)::timestamp with time zone))::date AS cut_month
           FROM public.reporting_settings
          WHERE (reporting_settings.id = 1)
        ), budget AS (
         SELECT v_monthly_budget.canonical_manager,
            v_monthly_budget.financial_year,
            v_monthly_budget.financial_quarter,
            sum(v_monthly_budget.original_forecast) AS expected_income,
            sum(v_monthly_budget.total_budget) AS budget_target,
            count(*) AS months_in_quarter,
            count(*) FILTER (WHERE (v_monthly_budget.forecast_month <= ( SELECT settings.cut_month
                   FROM settings))) AS months_elapsed,
            sum(v_monthly_budget.total_budget) FILTER (WHERE (v_monthly_budget.forecast_month <= ( SELECT settings.cut_month
                   FROM settings))) AS budget_to_date,
            bool_or(v_monthly_budget.is_locked) AS has_locked_months,
                CASE
                    WHEN (count(DISTINCT v_monthly_budget.growth_pct) = 1) THEN min(v_monthly_budget.growth_pct)
                    ELSE NULL::numeric
                END AS growth_pct
           FROM public.v_monthly_budget
          GROUP BY v_monthly_budget.canonical_manager, v_monthly_budget.financial_year, v_monthly_budget.financial_quarter
        ), actual AS (
         SELECT v_actual_month.canonical_manager,
            v_actual_month.financial_year,
            v_actual_month.financial_quarter,
            sum(v_actual_month.net_actual_income) AS actual_income,
            sum(v_actual_month.positive_actual_income) AS positive_income,
            sum(v_actual_month.absolute_return_income) AS return_income
           FROM public.v_actual_month
          GROUP BY v_actual_month.canonical_manager, v_actual_month.financial_year, v_actual_month.financial_quarter
        )
 SELECT b.canonical_manager,
    b.financial_year,
    b.financial_quarter,
    b.months_in_quarter,
    b.months_elapsed,
    (b.months_elapsed >= b.months_in_quarter) AS quarter_complete,
    (b.months_elapsed > 0) AS quarter_started,
    b.has_locked_months,
    b.expected_income,
    b.growth_pct,
    b.budget_target,
    (b.budget_target - b.expected_income) AS growth_target_amount,
    b.budget_to_date,
    COALESCE(a.actual_income, (0)::numeric) AS actual_income,
    a.positive_income,
    a.return_income,
    (COALESCE(a.actual_income, (0)::numeric) - b.budget_target) AS above_below_target,
    public.safe_div(COALESCE(a.actual_income, (0)::numeric), b.budget_target) AS target_achievement,
    (COALESCE(a.actual_income, (0)::numeric) >= b.budget_target) AS target_reached,
    round(
        CASE
            WHEN (b.months_elapsed = 0) THEN NULL::numeric
            WHEN (COALESCE(a.actual_income, (0)::numeric) < b.budget_target) THEN (0)::numeric
            ELSE ((b.budget_target - b.expected_income) / NULLIF(s.divisor, (0)::numeric))
        END, 2) AS base_bonus,
    round(
        CASE
            WHEN (b.months_elapsed = 0) THEN NULL::numeric
            WHEN (COALESCE(a.actual_income, (0)::numeric) < b.budget_target) THEN (0)::numeric
            ELSE ((COALESCE(a.actual_income, (0)::numeric) - b.budget_target) * s.above_rate)
        END, 2) AS above_target_bonus,
    round(
        CASE
            WHEN (b.months_elapsed = 0) THEN NULL::numeric
            WHEN (COALESCE(a.actual_income, (0)::numeric) < b.budget_target) THEN (0)::numeric
            ELSE (((b.budget_target - b.expected_income) / NULLIF(s.divisor, (0)::numeric)) + ((COALESCE(a.actual_income, (0)::numeric) - b.budget_target) * s.above_rate))
        END, 2) AS total_bonus,
    round(((b.budget_target - b.expected_income) / NULLIF(s.divisor, (0)::numeric)), 2) AS bonus_at_target,
    round(GREATEST((b.budget_target - COALESCE(a.actual_income, (0)::numeric)), (0)::numeric), 2) AS income_still_required,
    round(
        CASE
            WHEN ((b.months_elapsed > 0) AND (b.months_elapsed < b.months_in_quarter)) THEN (COALESCE(a.actual_income, (0)::numeric) * ((b.months_in_quarter)::numeric / (b.months_elapsed)::numeric))
            ELSE NULL::numeric
        END, 2) AS projected_income,
    round(
        CASE
            WHEN ((b.months_elapsed = 0) OR (b.months_elapsed >= b.months_in_quarter)) THEN NULL::numeric
            WHEN ((COALESCE(a.actual_income, (0)::numeric) * ((b.months_in_quarter)::numeric / (b.months_elapsed)::numeric)) < b.budget_target) THEN (0)::numeric
            ELSE (((b.budget_target - b.expected_income) / NULLIF(s.divisor, (0)::numeric)) + (((COALESCE(a.actual_income, (0)::numeric) * ((b.months_in_quarter)::numeric / (b.months_elapsed)::numeric)) - b.budget_target) * s.above_rate))
        END, 2) AS projected_bonus,
    s.divisor AS bonus_base_divisor,
    s.above_rate AS bonus_above_target_rate
   FROM ((budget b
     CROSS JOIN settings s)
     LEFT JOIN actual a ON ((((a.canonical_manager)::text = (b.canonical_manager)::text) AND (a.financial_year = b.financial_year) AND (a.financial_quarter = b.financial_quarter))));


ALTER VIEW public.v_bonus_quarter OWNER TO postgres;

--
-- Name: v_budget_quarter; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_budget_quarter AS
 SELECT canonical_manager,
    financial_year,
    financial_quarter,
    sum(original_forecast) AS original_renewal_forecast,
        CASE
            WHEN (count(DISTINCT growth_basis) = 1) THEN min(growth_basis)
            ELSE 'mixed'::text
        END AS growth_basis,
        CASE
            WHEN (count(DISTINCT growth_pct) = 1) THEN min(growth_pct)
            ELSE NULL::numeric
        END AS growth_pct,
    NULLIF(sum(override_amount), (0)::numeric) AS dollar_override,
    sum(new_business_growth_target) AS new_business_growth_target,
    sum(total_budget) AS total_budget,
    bool_or(is_locked) AS has_locked_months,
    count(*) FILTER (WHERE is_locked) AS locked_months
   FROM public.v_monthly_budget
  GROUP BY canonical_manager, financial_year, financial_quarter;


ALTER VIEW public.v_budget_quarter OWNER TO postgres;

--
-- Name: v_budget_performance_quarter; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_budget_performance_quarter AS
 WITH act AS (
         SELECT v_actual_month.canonical_manager,
            v_actual_month.financial_year,
            v_actual_month.financial_quarter,
            sum(v_actual_month.net_actual_income) AS net_actual_income,
            sum(v_actual_month.positive_actual_income) AS positive_actual_income,
            sum(v_actual_month.absolute_return_income) AS return_income,
            sum(v_actual_month.actual_new_business) AS actual_new_business
           FROM public.v_actual_month
          GROUP BY v_actual_month.canonical_manager, v_actual_month.financial_year, v_actual_month.financial_quarter
        ), usable AS (
         SELECT v_baseline_usable.canonical_manager,
            v_baseline_usable.financial_year,
            v_baseline_usable.financial_quarter,
            bool_and(v_baseline_usable.baseline_usable) AS quarter_baseline_usable
           FROM public.v_baseline_usable
          GROUP BY v_baseline_usable.canonical_manager, v_baseline_usable.financial_year, v_baseline_usable.financial_quarter
        )
 SELECT b.canonical_manager,
    b.financial_year,
    b.financial_quarter,
    b.original_renewal_forecast,
    b.growth_basis,
    b.growth_pct,
    b.new_business_growth_target,
    b.total_budget,
    a.net_actual_income,
    a.positive_actual_income,
    a.return_income,
    a.actual_new_business,
    public.safe_div(a.return_income, a.positive_actual_income) AS return_pct_of_positive,
    u.quarter_baseline_usable,
        CASE
            WHEN u.quarter_baseline_usable THEN (a.net_actual_income - b.total_budget)
            ELSE NULL::numeric
        END AS budget_variance,
        CASE
            WHEN u.quarter_baseline_usable THEN public.safe_div(a.net_actual_income, b.total_budget)
            ELSE NULL::numeric
        END AS budget_achievement
   FROM ((public.v_budget_quarter b
     LEFT JOIN act a ON ((((a.canonical_manager)::text = (b.canonical_manager)::text) AND (a.financial_year = b.financial_year) AND (a.financial_quarter = b.financial_quarter))))
     LEFT JOIN usable u ON ((((u.canonical_manager)::text = (b.canonical_manager)::text) AND (u.financial_year = b.financial_year) AND (u.financial_quarter = b.financial_quarter))));


ALTER VIEW public.v_budget_performance_quarter OWNER TO postgres;

--
-- Name: v_latest_forecast_policy; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_latest_forecast_policy AS
 SELECT p.id,
    p.snapshot_id,
    p.policy_id,
    p.client_id,
    p.client_code,
    p.client_code_norm,
    p.policy_number,
    p.policy_number_norm,
    p.class_abbrev,
    p.class_code,
    p.class_description,
    p.underwriter_abbrev,
    p.inception_date,
    p.expiry_date,
    p.next_expiry_date,
    p.renewal_months,
    p.forecast_month,
    p.financial_year,
    p.financial_quarter,
    p.source_manager,
    p.comm,
    p.comm_tax,
    p.fee,
    p.fee_tax,
    p.premium,
    p.total_premium,
    p.raw_expected_income,
    p.forecast_contribution,
    p.exception_flags,
    p.is_excluded,
    p.exclusion_rule_id,
    p.exclusion_field,
    p.exclusion_value,
    p.source_row,
    c.latest_snapshot_id,
    COALESCE(r.canonical_manager, p.source_manager) AS canonical_manager
   FROM ((public.forecast_month_coverage c
     JOIN public.forecast_policy p ON (((p.snapshot_id = c.latest_snapshot_id) AND (p.forecast_month = c.forecast_month))))
     LEFT JOIN public.v_manager_resolution r ON (((r.source_manager)::text = (p.source_manager)::text)))
  WHERE (NOT p.is_excluded);


ALTER VIEW public.v_latest_forecast_policy OWNER TO postgres;

--
-- Name: v_latest_forecast_month; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_latest_forecast_month AS
 SELECT canonical_manager,
    forecast_month,
    financial_year,
    financial_quarter,
    sum(forecast_contribution) AS latest_forecast,
    sum(raw_expected_income) AS latest_raw_expected,
    count(*) AS policy_count,
    count(*) FILTER (WHERE (cardinality(exception_flags) > 0)) AS exception_policies
   FROM public.v_latest_forecast_policy
  GROUP BY canonical_manager, forecast_month, financial_year, financial_quarter;


ALTER VIEW public.v_latest_forecast_month OWNER TO postgres;

--
-- Name: v_forecast_position_month; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_forecast_position_month AS
 WITH cut AS (
         SELECT (date_trunc('month'::text, (reporting_settings.cut_off_date)::timestamp with time zone))::date AS cut_month
           FROM public.reporting_settings
          WHERE (reporting_settings.id = 1)
        ), pos AS (
         SELECT COALESCE(o.canonical_manager, l.canonical_manager) AS canonical_manager,
            COALESCE(o.forecast_month, l.forecast_month) AS forecast_month,
            COALESCE(o.financial_year, l.financial_year) AS financial_year,
            COALESCE(o.financial_quarter, l.financial_quarter) AS financial_quarter,
            COALESCE(sum(o.original_forecast), (0)::numeric) AS original_forecast,
            sum(l.latest_forecast) AS latest_forecast_raw
           FROM (public.v_original_forecast_month o
             FULL JOIN public.v_latest_forecast_month l ON ((((l.canonical_manager)::text = (o.canonical_manager)::text) AND (l.forecast_month = o.forecast_month))))
          GROUP BY COALESCE(o.canonical_manager, l.canonical_manager), COALESCE(o.forecast_month, l.forecast_month), COALESCE(o.financial_year, l.financial_year), COALESCE(o.financial_quarter, l.financial_quarter)
        )
 SELECT p.canonical_manager,
    p.forecast_month,
    p.financial_year,
    p.financial_quarter,
    p.original_forecast,
    (p.forecast_month > cut.cut_month) AS is_future_period,
        CASE
            WHEN (p.forecast_month > cut.cut_month) THEN COALESCE(p.latest_forecast_raw, (0)::numeric)
            ELSE NULL::numeric
        END AS latest_forecast,
        CASE
            WHEN (p.forecast_month > cut.cut_month) THEN (COALESCE(p.latest_forecast_raw, (0)::numeric) - p.original_forecast)
            ELSE NULL::numeric
        END AS forecast_movement
   FROM (pos p
     CROSS JOIN cut);


ALTER VIEW public.v_forecast_position_month OWNER TO postgres;

--
-- Name: v_outlook_month; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_outlook_month AS
 WITH cut AS (
         SELECT (date_trunc('month'::text, (reporting_settings.cut_off_date)::timestamp with time zone))::date AS cut_month
           FROM public.reporting_settings
          WHERE (reporting_settings.id = 1)
        ), periods AS (
         SELECT v_actual_month.canonical_manager,
            v_actual_month.period_month AS month,
            v_actual_month.financial_year,
            v_actual_month.financial_quarter,
            v_actual_month.net_actual_income,
            NULL::numeric AS latest_forecast,
            'actual'::text AS basis
           FROM public.v_actual_month,
            cut
          WHERE (v_actual_month.period_month <= cut.cut_month)
        UNION ALL
         SELECT v_latest_forecast_month.canonical_manager,
            v_latest_forecast_month.forecast_month,
            v_latest_forecast_month.financial_year,
            v_latest_forecast_month.financial_quarter,
            NULL::numeric AS "numeric",
            v_latest_forecast_month.latest_forecast,
            'forecast'::text AS text
           FROM public.v_latest_forecast_month,
            cut
          WHERE (v_latest_forecast_month.forecast_month > cut.cut_month)
        )
 SELECT canonical_manager,
    month,
    financial_year,
    financial_quarter,
    basis,
    COALESCE(net_actual_income, (0)::numeric) AS net_actual_income,
    COALESCE(latest_forecast, (0)::numeric) AS latest_forecast,
    COALESCE(net_actual_income, latest_forecast, (0)::numeric) AS outlook_income
   FROM periods;


ALTER VIEW public.v_outlook_month OWNER TO postgres;

--
-- Name: v_outlook_quarter; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_outlook_quarter AS
 SELECT o.canonical_manager,
    o.financial_year,
    o.financial_quarter,
    sum(o.outlook_income) FILTER (WHERE (o.basis = 'actual'::text)) AS completed_actual,
    sum(o.outlook_income) FILTER (WHERE (o.basis = 'forecast'::text)) AS future_latest_forecast,
    sum(o.outlook_income) AS latest_outlook,
    b.total_budget,
    (b.total_budget - sum(o.outlook_income)) AS remaining_budget_gap
   FROM (public.v_outlook_month o
     LEFT JOIN public.v_budget_quarter b ON ((((b.canonical_manager)::text = (o.canonical_manager)::text) AND (b.financial_year = o.financial_year) AND (b.financial_quarter = o.financial_quarter))))
  GROUP BY o.canonical_manager, o.financial_year, o.financial_quarter, b.total_budget;


ALTER VIEW public.v_outlook_quarter OWNER TO postgres;

--
-- Name: v_business_dashboard; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_business_dashboard AS
 WITH a AS (
         SELECT v_actual_month.financial_year,
            sum(v_actual_month.net_actual_income) AS net_actual_income,
            sum(v_actual_month.positive_actual_income) AS positive_actual_income,
            sum(v_actual_month.absolute_return_income) AS return_income,
            sum(v_actual_month.actual_new_business) AS actual_new_business,
            sum(v_actual_month.new_business_cancellation) AS new_business_cancellation,
            sum(v_actual_month.lapse_income_returned) AS lapse_income_returned,
            sum(v_actual_month.midterm_cancellation_returned) AS midterm_cancellation_returned,
            sum(v_actual_month.negative_endorsements) AS negative_endorsements,
            sum(v_actual_month.endorsement_cancellations) AS endorsement_cancellations
           FROM public.v_actual_month
          GROUP BY v_actual_month.financial_year
        ), f AS (
         SELECT v_forecast_position_month.financial_year,
            sum(v_forecast_position_month.original_forecast) AS original_renewal_forecast,
            sum(v_forecast_position_month.latest_forecast) AS latest_renewal_forecast,
            sum(v_forecast_position_month.forecast_movement) AS forecast_movement
           FROM public.v_forecast_position_month
          GROUP BY v_forecast_position_month.financial_year
        ), b AS (
         SELECT v_budget_quarter.financial_year,
            sum(v_budget_quarter.total_budget) AS total_budget
           FROM public.v_budget_quarter
          GROUP BY v_budget_quarter.financial_year
        ), o AS (
         SELECT v_outlook_quarter.financial_year,
            sum(v_outlook_quarter.latest_outlook) AS latest_outlook
           FROM public.v_outlook_quarter
          GROUP BY v_outlook_quarter.financial_year
        )
 SELECT COALESCE(a.financial_year, f.financial_year, b.financial_year) AS financial_year,
    pc.coverage_status,
    pc.label AS period_label,
    a.net_actual_income,
    a.positive_actual_income,
    a.return_income,
    f.original_renewal_forecast,
    f.latest_renewal_forecast,
    f.forecast_movement,
    b.total_budget,
    public.safe_div(a.net_actual_income, b.total_budget) AS budget_achievement,
    o.latest_outlook,
    (b.total_budget - o.latest_outlook) AS remaining_budget_gap,
    a.actual_new_business,
    a.lapse_income_returned,
    a.midterm_cancellation_returned,
    a.new_business_cancellation,
    a.negative_endorsements,
    a.endorsement_cancellations,
    'All income figures are GST inclusive.'::text AS gst_note
   FROM ((((a
     FULL JOIN f ON ((f.financial_year = a.financial_year)))
     FULL JOIN b ON ((b.financial_year = COALESCE(a.financial_year, f.financial_year))))
     FULL JOIN o ON ((o.financial_year = COALESCE(a.financial_year, f.financial_year))))
     LEFT JOIN public.period_coverage pc ON (((pc.financial_year = COALESCE(a.financial_year, f.financial_year)) AND ((pc.data_domain)::text = 'actuals'::text))));


ALTER VIEW public.v_business_dashboard OWNER TO postgres;

--
-- Name: v_expected_month; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_expected_month AS
 SELECT b.canonical_manager,
    b.forecast_month,
    b.financial_year,
    b.financial_quarter,
    b.original_forecast AS forecast_income,
    b.total_budget AS target_income,
        CASE
            WHEN (b.original_forecast > (0)::numeric) THEN round((b.total_budget / b.original_forecast), 4)
            ELSE NULL::numeric
        END AS uplift_applied,
    public.month_state(b.forecast_month) AS month_state,
    public.forecast_month_is_open(b.forecast_month) AS accepts_upload,
    COALESCE(bb.scoreable, true) AS basis_scoreable
   FROM (public.v_monthly_budget b
     LEFT JOIN public.v_baseline_basis_month bb ON ((bb.month = b.forecast_month)));


ALTER VIEW public.v_expected_month OWNER TO postgres;

--
-- Name: VIEW v_expected_month; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON VIEW public.v_expected_month IS 'The expected-income ledger. forecast_income is the imported figure on the associate basis; target_income is that times the growth uplift. Driven only by forecast uploads -- a transaction import never changes a row here.';


--
-- Name: v_forecast_month_writable; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_forecast_month_writable AS
 SELECT m.forecast_month,
    cut.cut_month,
    (m.forecast_month > cut.cut_month) AS is_open,
    (l.forecast_month IS NOT NULL) AS is_pinned,
    ((m.forecast_month > cut.cut_month) AND (l.forecast_month IS NULL)) AS is_writable,
        CASE
            WHEN (m.forecast_month <= cut.cut_month) THEN 'closed: at or before the reporting cut-off'::text
            WHEN (l.forecast_month IS NOT NULL) THEN ('pinned: '::text || COALESCE(l.reason, 'locked'::text))
            ELSE 'open: a newer snapshot will replace this month'::text
        END AS status
   FROM ((( SELECT DISTINCT original_forecast.forecast_month
           FROM public.original_forecast
        UNION
         SELECT DISTINCT forecast_policy.forecast_month
           FROM public.forecast_policy
          WHERE (NOT forecast_policy.is_excluded)) m
     CROSS JOIN ( SELECT (date_trunc('month'::text, (reporting_settings.cut_off_date)::timestamp with time zone))::date AS cut_month
           FROM public.reporting_settings
          WHERE (reporting_settings.id = 1)) cut)
     LEFT JOIN public.forecast_month_lock l ON (((l.forecast_month = m.forecast_month) AND l.active)));


ALTER VIEW public.v_forecast_month_writable OWNER TO postgres;

--
-- Name: v_forecast_movement_detail; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_forecast_movement_detail AS
 SELECT m.id,
    m.from_snapshot_id,
    m.to_snapshot_id,
    m.policy_id,
    m.forecast_month,
    m.movement_type,
    m.original_income,
    m.previous_income,
    m.latest_income,
    m.movement_amount,
    m.from_manager,
    m.to_manager,
    m.detail_changes,
    m.detected_at,
    m.added,
    m.removed,
    m.amount_changed,
    m.manager_changed,
    m.detail_changed,
    m.secondary_changes,
    COALESCE(rt.canonical_manager, m.to_manager) AS canonical_to_manager,
    COALESCE(rf.canonical_manager, m.from_manager) AS canonical_from_manager,
    p.client_code,
    p.policy_number,
    p.class_abbrev,
    p.underwriter_abbrev,
    p.expiry_date
   FROM (((public.forecast_movement m
     LEFT JOIN public.v_manager_resolution rt ON (((rt.source_manager)::text = (m.to_manager)::text)))
     LEFT JOIN public.v_manager_resolution rf ON (((rf.source_manager)::text = (m.from_manager)::text)))
     LEFT JOIN LATERAL ( SELECT fp.client_code,
            fp.policy_number,
            fp.class_abbrev,
            fp.underwriter_abbrev,
            fp.expiry_date
           FROM public.forecast_policy fp
          WHERE (fp.policy_id = m.policy_id)
          ORDER BY fp.snapshot_id DESC
         LIMIT 1) p ON (true));


ALTER VIEW public.v_forecast_movement_detail OWNER TO postgres;

--
-- Name: v_forecast_movement_summary; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_forecast_movement_summary AS
 SELECT forecast_month,
    COALESCE(canonical_from_manager, canonical_to_manager) AS canonical_manager,
    sum(original_income) AS original_expected_income,
    count(*) FILTER (WHERE removed) AS policies_removed,
    COALESCE(sum(previous_income) FILTER (WHERE removed), (0)::numeric) AS expected_income_removed,
    count(*) FILTER (WHERE added) AS policies_added,
    COALESCE(sum(latest_income) FILTER (WHERE added), (0)::numeric) AS expected_income_added,
    COALESCE(sum(movement_amount) FILTER (WHERE amount_changed), (0)::numeric) AS amount_changes,
    count(*) FILTER (WHERE amount_changed) AS policies_amount_changed,
    count(*) FILTER (WHERE manager_changed) AS manager_transfers,
    count(*) FILTER (WHERE detail_changed) AS detail_changes,
    count(*) FILTER (WHERE (cardinality(secondary_changes) > 1)) AS multi_attribute_changes,
    sum(latest_income) AS latest_expected_income
   FROM public.v_forecast_movement_detail
  GROUP BY forecast_month, COALESCE(canonical_from_manager, canonical_to_manager);


ALTER VIEW public.v_forecast_movement_summary OWNER TO postgres;

--
-- Name: v_manager_transfer_detail; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_manager_transfer_detail AS
 SELECT policy_id,
    forecast_month,
    canonical_from_manager,
    canonical_to_manager,
    from_manager AS source_from_manager,
    to_manager AS source_to_manager,
    previous_income,
    latest_income,
    movement_amount,
    amount_changed,
    detail_changed,
    secondary_changes,
    movement_type AS primary_movement_type,
    client_code,
    policy_number,
    class_abbrev,
    expiry_date
   FROM public.v_forecast_movement_detail
  WHERE manager_changed;


ALTER VIEW public.v_manager_transfer_detail OWNER TO postgres;

--
-- Name: v_match_decision_history; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_match_decision_history AS
 SELECT d.id,
    d.decided_at,
    d.reviewer,
    d.action,
    d.reason,
    d.policy_id,
    d.forecast_month,
    d.transaction_id,
    d.previous_decision,
    d.new_decision,
    t.client_code,
    t.policy_number,
    t.category,
    t.actual_income
   FROM (public.match_decision d
     LEFT JOIN public.sales_transaction t ON ((t.id = d.transaction_id)))
  ORDER BY d.decided_at DESC;


ALTER VIEW public.v_match_decision_history OWNER TO postgres;

--
-- Name: v_match_outcome_summary; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_match_outcome_summary AS
 SELECT canonical_manager,
    forecast_month,
    outcome,
    count(*) AS policies,
    sum(original_forecast_income) AS original_forecast_income,
    sum(renewal_transaction_income) AS renewal_transaction_income,
    sum(total_associated_income) AS total_associated_income
   FROM public.policy_outcome po
  GROUP BY canonical_manager, forecast_month, outcome;


ALTER VIEW public.v_match_outcome_summary OWNER TO postgres;

--
-- Name: v_match_review_queue; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_match_review_queue AS
 SELECT mc.id,
    mc.reason,
    mc.status,
    mc.tier,
    mc.confidence,
    mc.candidate_rank,
    mc.transaction_id,
    t.client_code AS txn_client,
    t.policy_number AS txn_policy_number,
    t.policy_class AS txn_policy_class,
    t.category AS txn_category,
    t.transaction_date,
    t.actual_income AS txn_income,
    mc.policy_id,
    fp.client_code AS policy_client,
    fp.policy_number AS policy_policy_number,
    fp.class_abbrev AS policy_class,
    fp.expiry_date,
    fp.forecast_contribution,
    mc.detail,
    mc.created_at
   FROM ((public.match_candidate mc
     LEFT JOIN public.sales_transaction t ON ((t.id = mc.transaction_id)))
     LEFT JOIN LATERAL ( SELECT p.client_code,
            p.policy_number,
            p.class_abbrev,
            p.expiry_date,
            p.forecast_contribution
           FROM public.forecast_policy p
          WHERE (p.policy_id = mc.policy_id)
          ORDER BY p.snapshot_id DESC
         LIMIT 1) fp ON (true));


ALTER VIEW public.v_match_review_queue OWNER TO postgres;

--
-- Name: v_match_tier_summary; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_match_tier_summary AS
 SELECT tier,
        CASE tier
            WHEN 1 THEN 'client + policy number + compatible class + date'::text
            WHEN 2 THEN 'client + policy number + date'::text
            WHEN 3 THEN 'client + policy number, same financial year'::text
            WHEN 4 THEN 'client + compatible class + date'::text
            ELSE NULL::text
        END AS tier_description,
    method,
    count(*) AS allocations,
    count(DISTINCT policy_id) AS policies,
    count(DISTINCT transaction_id) AS transactions,
    sum(allocated_income) AS allocated_income,
    sum(allocated_income) FILTER (WHERE is_renewal_income) AS renewal_income,
    min(confidence) AS min_confidence,
    max(confidence) AS max_confidence
   FROM public.match_allocation a
  GROUP BY tier, method;


ALTER VIEW public.v_match_tier_summary OWNER TO postgres;

--
-- Name: v_missing_forecast_month; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_missing_forecast_month AS
 SELECT DISTINCT period_month AS month,
    public.month_state(period_month) AS month_state,
    (EXISTS ( SELECT 1
           FROM public.forecast_month_override o
          WHERE ((o.forecast_month = a.period_month) AND (o.consumed_at IS NULL)))) AS override_pending
   FROM public.v_actual_month a
  WHERE ((period_month <= public.reporting_current_month()) AND (NOT (EXISTS ( SELECT 1
           FROM public.v_monthly_budget b
          WHERE (b.forecast_month = a.period_month)))));


ALTER VIEW public.v_missing_forecast_month OWNER TO postgres;

--
-- Name: VIEW v_missing_forecast_month; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON VIEW public.v_missing_forecast_month IS 'A month carrying actuals but no expected income, which a routine upload is not allowed to fill once the month has started. Needs an audited override.';


--
-- Name: v_month_performance; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_month_performance AS
 WITH combined AS (
         SELECT COALESCE(e.canonical_manager, a.canonical_manager) AS canonical_manager,
            COALESCE(e.forecast_month, a.period_month) AS month,
            COALESCE(e.financial_year, a.financial_year) AS financial_year,
            COALESCE(e.financial_quarter, a.financial_quarter) AS financial_quarter,
            e.forecast_income,
            e.target_income,
            e.uplift_applied,
            COALESCE(e.basis_scoreable, true) AS basis_scoreable,
            a.net_actual_income AS actual_income,
            a.transaction_rows
           FROM (public.v_expected_month e
             FULL JOIN public.v_actual_month a ON ((((a.canonical_manager)::text = (e.canonical_manager)::text) AND (a.period_month = e.forecast_month))))
        ), flagged AS (
         SELECT c.canonical_manager,
            c.month,
            c.financial_year,
            c.financial_quarter,
            c.forecast_income,
            c.target_income,
            c.uplift_applied,
            c.basis_scoreable,
            c.actual_income,
            c.transaction_rows,
            public.month_state(c.month) AS ms,
            public.actual_load_state(c.month) AS load_state,
            public.actual_loaded_to(c.month) AS loaded_to,
            (public.actual_load_state(c.month) = 'full'::text) AS actuals_loaded
           FROM combined c
        )
 SELECT canonical_manager,
    month,
    financial_year,
    financial_quarter,
    ms AS month_state,
    forecast_income,
    target_income,
    uplift_applied,
        CASE
            WHEN ((ms = 'future'::text) OR (load_state = 'none'::text)) THEN NULL::numeric
            ELSE COALESCE(actual_income, (0)::numeric)
        END AS actual_income,
    loaded_to AS actual_income_to,
        CASE
            WHEN ((ms = 'future'::text) OR (load_state = 'none'::text)) THEN NULL::numeric
            ELSE (COALESCE(actual_income, (0)::numeric) - COALESCE(target_income, (0)::numeric))
        END AS variance,
        CASE
            WHEN ((ms <> 'completed'::text) OR (NOT actuals_loaded)) THEN NULL::numeric
            WHEN (NOT basis_scoreable) THEN NULL::numeric
            WHEN (COALESCE(target_income, (0)::numeric) = (0)::numeric) THEN NULL::numeric
            ELSE round(((100.0 * COALESCE(actual_income, (0)::numeric)) / target_income), 1)
        END AS achievement_pct,
        CASE
            WHEN ((target_income IS NULL) AND (ms <> 'future'::text)) THEN 'missing_forecast'::text
            WHEN (ms = 'future'::text) THEN 'not_started'::text
            WHEN (NOT basis_scoreable) THEN 'baseline_unverified'::text
            WHEN (ms = 'in_progress'::text) THEN 'in_progress'::text
            WHEN (load_state = 'none'::text) THEN 'actuals_not_loaded'::text
            WHEN (load_state = 'partial'::text) THEN 'actuals_partial'::text
            WHEN (COALESCE(actual_income, (0)::numeric) >= COALESCE(target_income, (0)::numeric)) THEN 'achieved'::text
            ELSE 'below_target'::text
        END AS status,
        CASE
            WHEN ((target_income IS NULL) AND (ms <> 'future'::text)) THEN 'Missing forecast - no target was set before this month began'::text
            WHEN (ms = 'future'::text) THEN NULL::text
            WHEN (NOT basis_scoreable) THEN (('Baseline not on the confirmed associate basis - excluded '::text || 'from achievement and bonus until a reconstructed '::text) || 'baseline is approved'::text)
            WHEN ((ms = 'in_progress'::text) AND (load_state = 'none'::text)) THEN 'Month in progress - no actuals loaded yet'::text
            WHEN ((ms = 'in_progress'::text) AND (loaded_to IS NOT NULL)) THEN ('Month in progress - actual income to '::text || to_char((loaded_to)::timestamp with time zone, 'DD Mon'::text))
            WHEN (ms = 'in_progress'::text) THEN ('Month in progress - transactions loaded do not cover the '::text || 'start of the month'::text)
            WHEN (load_state = 'none'::text) THEN 'Actuals not loaded - outlook using expected income'::text
            WHEN ((load_state = 'partial'::text) AND (loaded_to IS NOT NULL)) THEN (('Actuals loaded only to '::text || to_char((loaded_to)::timestamp with time zone, 'DD Mon'::text)) || ' - outlook using expected income'::text)
            WHEN (load_state = 'partial'::text) THEN ('Actuals only partly loaded and the month does not start '::text || 'covered - outlook using expected income'::text)
            ELSE NULL::text
        END AS status_note,
    load_state AS actuals_load_state,
    actuals_loaded,
    basis_scoreable,
    COALESCE(transaction_rows, (0)::bigint) AS transaction_rows
   FROM flagged f;


ALTER VIEW public.v_month_performance OWNER TO postgres;

--
-- Name: VIEW v_month_performance; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON VIEW public.v_month_performance IS 'Both ledgers per manager-month. status is in_progress for the current month and never achieved or below_target -- a month still running has no result. missing_forecast marks a month that began with no target.';


--
-- Name: v_new_business_analysis; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_new_business_analysis AS
 WITH nb AS (
         SELECT COALESCE(r.canonical_manager, t.source_manager) AS canonical_manager,
            t.financial_year,
            t.financial_quarter,
            sum(t.actual_income) FILTER (WHERE (((t.category)::text = 'N/B'::text) AND (t.actual_income > (0)::numeric))) AS gross_new_business,
            sum(t.absolute_return_income) FILTER (WHERE (((t.category)::text = 'N/B'::text) AND (t.actual_income < (0)::numeric))) AS negative_new_business_corrections,
            sum(t.absolute_return_income) FILTER (WHERE ((t.category)::text = 'NCN'::text)) AS new_business_cancellations,
            sum(t.actual_income) FILTER (WHERE ((t.category)::text = ANY (ARRAY[('N/B'::character varying)::text, ('NCN'::character varying)::text]))) AS net_new_business
           FROM (public.sales_transaction t
             LEFT JOIN public.v_manager_resolution r ON (((r.source_manager)::text = (t.source_manager)::text)))
          WHERE (NOT t.is_excluded)
          GROUP BY COALESCE(r.canonical_manager, t.source_manager), t.financial_year, t.financial_quarter
        )
 SELECT nb.canonical_manager,
    nb.financial_year,
    nb.financial_quarter,
    nb.gross_new_business,
    nb.negative_new_business_corrections,
    nb.new_business_cancellations,
    nb.net_new_business,
    b.new_business_growth_target,
    public.safe_div(nb.net_new_business, b.new_business_growth_target) AS growth_target_achievement
   FROM (nb
     LEFT JOIN public.v_budget_quarter b ON ((((b.canonical_manager)::text = (nb.canonical_manager)::text) AND (b.financial_year = nb.financial_year) AND (b.financial_quarter = nb.financial_quarter))));


ALTER VIEW public.v_new_business_analysis OWNER TO postgres;

--
-- Name: v_outlook_month_v2; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_outlook_month_v2 AS
 SELECT canonical_manager,
    month,
    financial_year,
    financial_quarter,
    month_state,
        CASE
            WHEN ((month_state = 'completed'::text) AND actuals_loaded) THEN COALESCE(actual_income, (0)::numeric)
            ELSE COALESCE(target_income, (0)::numeric)
        END AS outlook_income,
        CASE
            WHEN ((month_state = 'completed'::text) AND actuals_loaded) THEN 'actual'::text
            WHEN (month_state = 'completed'::text) THEN 'expected_fallback'::text
            ELSE 'expected'::text
        END AS outlook_basis,
        CASE
            WHEN ((month_state = 'completed'::text) AND (NOT actuals_loaded)) THEN 'Actuals not loaded - outlook using expected income'::text
            ELSE NULL::text
        END AS outlook_note
   FROM public.v_month_performance p;


ALTER VIEW public.v_outlook_month_v2 OWNER TO postgres;

--
-- Name: v_performance_quarter; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_performance_quarter AS
 SELECT canonical_manager,
    financial_year,
    financial_quarter,
    sum(forecast_income) AS forecast_income,
    sum(target_income) AS target_income,
    sum(actual_income) AS actual_income,
    sum(actual_income) FILTER (WHERE ((month_state = 'completed'::text) AND basis_scoreable)) AS actual_income_scoreable,
    sum(target_income) FILTER (WHERE ((month_state = 'completed'::text) AND basis_scoreable)) AS target_income_scoreable,
    count(*) FILTER (WHERE (NOT basis_scoreable)) AS months_basis_unverified,
    ( SELECT sum(o.outlook_income) AS sum
           FROM public.v_outlook_month_v2 o
          WHERE (((o.canonical_manager)::text = (p.canonical_manager)::text) AND (o.financial_year = p.financial_year) AND (o.financial_quarter = p.financial_quarter))) AS latest_outlook,
    count(*) FILTER (WHERE (month_state = 'in_progress'::text)) AS months_in_progress,
    count(*) FILTER (WHERE (status = 'missing_forecast'::text)) AS months_missing_forecast,
        CASE
            WHEN (sum(target_income) FILTER (WHERE ((month_state = 'completed'::text) AND basis_scoreable)) > (0)::numeric) THEN round(((100.0 * sum(actual_income) FILTER (WHERE ((month_state = 'completed'::text) AND basis_scoreable))) / sum(target_income) FILTER (WHERE ((month_state = 'completed'::text) AND basis_scoreable))), 1)
            ELSE NULL::numeric
        END AS achievement_pct_completed
   FROM public.v_month_performance p
  GROUP BY canonical_manager, financial_year, financial_quarter;


ALTER VIEW public.v_performance_quarter OWNER TO postgres;

--
-- Name: VIEW v_performance_quarter; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON VIEW public.v_performance_quarter IS 'achievement_pct_completed deliberately excludes a month still running, and is null until at least one month in the quarter has closed. months_in_progress tells the caller why.';


--
-- Name: v_policy_renewal; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_policy_renewal AS
 SELECT po.policy_id,
    po.forecast_month,
    po.canonical_manager,
    lp.source_manager AS original_manager,
    lp.client_code,
    lp.policy_number,
    lp.class_abbrev,
    lp.underwriter_abbrev,
    lp.expiry_date,
    po.original_forecast_income,
    po.latest_forecast_income,
    (po.latest_forecast_income - po.original_forecast_income) AS forecast_movement,
    po.outcome,
    po.renewal_transaction_income,
    po.total_associated_income,
    po.matched_transaction_count,
    po.best_tier,
    po.confidence,
    po.requires_review,
    po.is_manual,
    lp.exception_flags,
    lp.snapshot_id AS source_snapshot
   FROM (public.policy_outcome po
     LEFT JOIN LATERAL ( SELECT fp.client_code,
            fp.policy_number,
            fp.class_abbrev,
            fp.underwriter_abbrev,
            fp.expiry_date,
            fp.source_manager,
            fp.exception_flags,
            fp.snapshot_id
           FROM public.forecast_policy fp
          WHERE ((fp.policy_id = po.policy_id) AND (fp.forecast_month = po.forecast_month))
          ORDER BY fp.snapshot_id DESC
         LIMIT 1) lp ON (true));


ALTER VIEW public.v_policy_renewal OWNER TO postgres;

--
-- Name: v_prior_year_comparison; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_prior_year_comparison AS
 SELECT canonical_manager,
    (financial_year + 1) AS comparison_financial_year,
    financial_year AS prior_financial_year,
    sum(net_actual_income) AS prior_year_net_actual_income,
    sum(positive_actual_income) AS prior_year_positive_income,
    sum(absolute_return_income) AS prior_year_return_income,
    sum(actual_renewal_income) AS prior_year_renewal_income,
    sum(actual_new_business) AS prior_year_new_business
   FROM public.v_actual_month
  GROUP BY canonical_manager, financial_year;


ALTER VIEW public.v_prior_year_comparison OWNER TO postgres;

--
-- Name: v_renewal_income_month; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_renewal_income_month AS
 WITH actual AS (
         SELECT COALESCE(r.canonical_manager, t.source_manager) AS canonical_manager,
            t.period_month,
            t.financial_year,
            t.financial_quarter,
            sum(t.actual_income) AS renewal_income,
            sum(t.actual_income) FILTER (WHERE ((t.category)::text = 'RWL'::text)) AS renewal_only,
            sum(t.actual_income) FILTER (WHERE ((t.category)::text = 'TRW'::text)) AS transfer_only,
            count(*) AS renewal_transactions
           FROM (public.sales_transaction t
             LEFT JOIN public.v_manager_resolution r ON (((r.source_manager)::text = (t.source_manager)::text)))
          WHERE ((NOT t.is_excluded) AND ((t.category)::text = ANY (ARRAY[('RWL'::character varying)::text, ('TRW'::character varying)::text])))
          GROUP BY COALESCE(r.canonical_manager, t.source_manager), t.period_month, t.financial_year, t.financial_quarter
        ), forecast AS (
         SELECT v_original_forecast_month.canonical_manager,
            v_original_forecast_month.forecast_month,
            sum(v_original_forecast_month.original_forecast) AS original_forecast
           FROM public.v_original_forecast_month
          GROUP BY v_original_forecast_month.canonical_manager, v_original_forecast_month.forecast_month
        ), cut AS (
         SELECT (date_trunc('month'::text, (reporting_settings.cut_off_date)::timestamp with time zone))::date AS cut_month
           FROM public.reporting_settings
          WHERE (reporting_settings.id = 1)
        )
 SELECT COALESCE(a.canonical_manager, f.canonical_manager) AS canonical_manager,
    COALESCE(a.period_month, f.forecast_month) AS period_month,
    public.au_financial_year(COALESCE(a.period_month, f.forecast_month)) AS financial_year,
    public.au_quarter(COALESCE(a.period_month, f.forecast_month)) AS financial_quarter,
    (COALESCE(a.period_month, f.forecast_month) <= cut.cut_month) AS period_started,
    a.renewal_income,
    a.renewal_only,
    a.transfer_only,
    a.renewal_transactions,
    f.original_forecast,
        CASE
            WHEN ((COALESCE(a.period_month, f.forecast_month) <= cut.cut_month) AND (f.original_forecast IS NOT NULL)) THEN (COALESCE(a.renewal_income, (0)::numeric) - f.original_forecast)
            ELSE NULL::numeric
        END AS renewal_variance,
        CASE
            WHEN (COALESCE(a.period_month, f.forecast_month) <= cut.cut_month) THEN public.safe_div(COALESCE(a.renewal_income, (0)::numeric), f.original_forecast)
            ELSE NULL::numeric
        END AS renewal_achievement
   FROM ((actual a
     FULL JOIN forecast f ON ((((f.canonical_manager)::text = (a.canonical_manager)::text) AND (f.forecast_month = a.period_month))))
     CROSS JOIN cut);


ALTER VIEW public.v_renewal_income_month OWNER TO postgres;

--
-- Name: v_renewal_outcome_performance; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_renewal_outcome_performance AS
 WITH agg AS (
         SELECT policy_outcome.canonical_manager,
            policy_outcome.forecast_month,
            sum(policy_outcome.original_forecast_income) AS original_forecast,
            sum(policy_outcome.renewal_transaction_income) AS actual_renewal_income,
            sum(policy_outcome.total_associated_income) AS total_associated_income,
            count(*) AS original_policies,
            count(*) FILTER (WHERE ((policy_outcome.outcome)::text = 'renewed'::text)) AS policies_renewed,
            count(*) FILTER (WHERE ((policy_outcome.outcome)::text = 'transfer_renewed'::text)) AS policies_transferred,
            count(*) FILTER (WHERE ((policy_outcome.outcome)::text = 'lapsed_lost'::text)) AS policies_lapsed,
            count(*) FILTER (WHERE ((policy_outcome.outcome)::text = 'pending'::text)) AS policies_pending,
            count(*) FILTER (WHERE ((policy_outcome.outcome)::text = 'removed_from_latest'::text)) AS policies_removed,
            count(*) FILTER (WHERE ((policy_outcome.outcome)::text = ANY (ARRAY[('multiple_candidates'::character varying)::text, ('unmatched'::character varying)::text]))) AS policies_unresolved,
            sum(policy_outcome.original_forecast_income) FILTER (WHERE ((policy_outcome.outcome)::text = ANY (ARRAY[('renewed'::character varying)::text, ('transfer_renewed'::character varying)::text]))) AS retained_forecast_income
           FROM public.policy_outcome
          GROUP BY policy_outcome.canonical_manager, policy_outcome.forecast_month
        )
 SELECT a.canonical_manager,
    a.forecast_month,
    a.original_forecast,
    a.actual_renewal_income,
    a.total_associated_income,
    a.original_policies,
    a.policies_renewed,
    a.policies_transferred,
    a.policies_lapsed,
    a.policies_pending,
    a.policies_removed,
    a.policies_unresolved,
    a.retained_forecast_income,
    u.baseline_usable,
        CASE
            WHEN u.baseline_usable THEN (a.actual_renewal_income - a.original_forecast)
            ELSE NULL::numeric
        END AS renewal_variance,
        CASE
            WHEN u.baseline_usable THEN public.safe_div(a.actual_renewal_income, a.original_forecast)
            ELSE NULL::numeric
        END AS renewal_achievement,
    public.safe_div(((a.policies_renewed + a.policies_transferred))::numeric, (NULLIF((a.original_policies - a.policies_pending), 0))::numeric) AS retention_by_policy_count,
    public.safe_div(a.retained_forecast_income, NULLIF(a.original_forecast, (0)::numeric)) AS retention_by_income
   FROM (agg a
     LEFT JOIN public.v_baseline_usable u ON ((((u.canonical_manager)::text = (a.canonical_manager)::text) AND (u.forecast_month = a.forecast_month))));


ALTER VIEW public.v_renewal_outcome_performance OWNER TO postgres;

--
-- Name: v_renewal_performance_month; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_renewal_performance_month AS
 SELECT a.canonical_manager,
    a.period_month,
    a.financial_year,
    a.financial_quarter,
    a.actual_renewal_income,
    o.original_forecast,
    u.baseline_usable,
    u.baseline_source,
        CASE
            WHEN u.baseline_usable THEN (a.actual_renewal_income - o.original_forecast)
            ELSE NULL::numeric
        END AS renewal_variance,
        CASE
            WHEN u.baseline_usable THEN public.safe_div(a.actual_renewal_income, o.original_forecast)
            ELSE NULL::numeric
        END AS renewal_achievement
   FROM ((public.v_actual_month a
     LEFT JOIN public.v_original_forecast_month o ON ((((o.canonical_manager)::text = (a.canonical_manager)::text) AND (o.forecast_month = a.period_month))))
     LEFT JOIN public.v_baseline_usable u ON ((((u.canonical_manager)::text = (a.canonical_manager)::text) AND (u.forecast_month = a.period_month))));


ALTER VIEW public.v_renewal_performance_month OWNER TO postgres;

--
-- Name: v_return_income_analysis; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_return_income_analysis AS
 SELECT COALESCE(r.canonical_manager, t.source_manager) AS canonical_manager,
    t.financial_year,
    t.financial_quarter,
    t.period_month,
    t.derived_classification,
    sum(t.signed_return_income) AS signed_return_income,
    sum(t.absolute_return_income) AS absolute_return_income,
    count(*) AS transaction_rows
   FROM (public.sales_transaction t
     LEFT JOIN public.v_manager_resolution r ON (((r.source_manager)::text = (t.source_manager)::text)))
  WHERE ((NOT t.is_excluded) AND (t.actual_income < (0)::numeric))
  GROUP BY COALESCE(r.canonical_manager, t.source_manager), t.financial_year, t.financial_quarter, t.period_month, t.derived_classification;


ALTER VIEW public.v_return_income_analysis OWNER TO postgres;

--
-- Name: v_snapshot_coverage; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.v_snapshot_coverage AS
 SELECT smc.snapshot_id,
    s.as_of_date,
    b.file_name,
    smc.forecast_month,
    smc.policy_count,
    smc.forecast_contribution,
    smc.is_confirmed_complete,
    smc.coverage_basis,
    (c.latest_snapshot_id = smc.snapshot_id) AS is_current_latest
   FROM (((public.snapshot_month_coverage smc
     JOIN public.forecast_snapshot s ON ((s.id = smc.snapshot_id)))
     JOIN public.upload_batch b ON ((b.id = s.batch_id)))
     LEFT JOIN public.forecast_month_coverage c ON ((c.forecast_month = smc.forecast_month)));


ALTER VIEW public.v_snapshot_coverage OWNER TO postgres;

--
-- Name: app_user id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.app_user ALTER COLUMN id SET DEFAULT nextval('public.app_user_id_seq'::regclass);


--
-- Name: auth_event id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.auth_event ALTER COLUMN id SET DEFAULT nextval('public.auth_event_id_seq'::regclass);


--
-- Name: batch_rollback id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.batch_rollback ALTER COLUMN id SET DEFAULT nextval('public.batch_rollback_id_seq'::regclass);


--
-- Name: budget_audit id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.budget_audit ALTER COLUMN id SET DEFAULT nextval('public.budget_audit_id_seq'::regclass);


--
-- Name: budget_lock id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.budget_lock ALTER COLUMN id SET DEFAULT nextval('public.budget_lock_id_seq'::regclass);


--
-- Name: class_equivalence id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.class_equivalence ALTER COLUMN id SET DEFAULT nextval('public.class_equivalence_id_seq'::regclass);


--
-- Name: column_mapping_profile id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.column_mapping_profile ALTER COLUMN id SET DEFAULT nextval('public.column_mapping_profile_id_seq'::regclass);


--
-- Name: exclusion_rule id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.exclusion_rule ALTER COLUMN id SET DEFAULT nextval('public.exclusion_rule_id_seq'::regclass);


--
-- Name: forecast_month_override id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.forecast_month_override ALTER COLUMN id SET DEFAULT nextval('public.forecast_month_override_id_seq'::regclass);


--
-- Name: forecast_movement id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.forecast_movement ALTER COLUMN id SET DEFAULT nextval('public.forecast_movement_id_seq'::regclass);


--
-- Name: forecast_policy id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.forecast_policy ALTER COLUMN id SET DEFAULT nextval('public.forecast_policy_id_seq'::regclass);


--
-- Name: forecast_snapshot id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.forecast_snapshot ALTER COLUMN id SET DEFAULT nextval('public.forecast_snapshot_id_seq'::regclass);


--
-- Name: growth_rate id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.growth_rate ALTER COLUMN id SET DEFAULT nextval('public.growth_rate_id_seq'::regclass);


--
-- Name: import_staging id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.import_staging ALTER COLUMN id SET DEFAULT nextval('public.import_staging_id_seq'::regclass);


--
-- Name: ingest_exception id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ingest_exception ALTER COLUMN id SET DEFAULT nextval('public.ingest_exception_id_seq'::regclass);


--
-- Name: legacy_forecast_reference id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.legacy_forecast_reference ALTER COLUMN id SET DEFAULT nextval('public.legacy_forecast_reference_id_seq'::regclass);


--
-- Name: manager_alias id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.manager_alias ALTER COLUMN id SET DEFAULT nextval('public.manager_alias_id_seq'::regclass);


--
-- Name: match_allocation id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.match_allocation ALTER COLUMN id SET DEFAULT nextval('public.match_allocation_id_seq'::regclass);


--
-- Name: match_candidate id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.match_candidate ALTER COLUMN id SET DEFAULT nextval('public.match_candidate_id_seq'::regclass);


--
-- Name: match_decision id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.match_decision ALTER COLUMN id SET DEFAULT nextval('public.match_decision_id_seq'::regclass);


--
-- Name: match_run id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.match_run ALTER COLUMN id SET DEFAULT nextval('public.match_run_id_seq'::regclass);


--
-- Name: monthly_target_override id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.monthly_target_override ALTER COLUMN id SET DEFAULT nextval('public.monthly_target_override_id_seq'::regclass);


--
-- Name: original_forecast id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.original_forecast ALTER COLUMN id SET DEFAULT nextval('public.original_forecast_id_seq'::regclass);


--
-- Name: period_coverage id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.period_coverage ALTER COLUMN id SET DEFAULT nextval('public.period_coverage_id_seq'::regclass);


--
-- Name: policy_outcome id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.policy_outcome ALTER COLUMN id SET DEFAULT nextval('public.policy_outcome_id_seq'::regclass);


--
-- Name: rebaseline_audit id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.rebaseline_audit ALTER COLUMN id SET DEFAULT nextval('public.rebaseline_audit_id_seq'::regclass);


--
-- Name: reporting_manager id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.reporting_manager ALTER COLUMN id SET DEFAULT nextval('public.reporting_manager_id_seq'::regclass);


--
-- Name: restated_transaction id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.restated_transaction ALTER COLUMN id SET DEFAULT nextval('public.restated_transaction_id_seq'::regclass);


--
-- Name: sales_transaction id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.sales_transaction ALTER COLUMN id SET DEFAULT nextval('public.sales_transaction_id_seq'::regclass);


--
-- Name: snapshot_month_coverage id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.snapshot_month_coverage ALTER COLUMN id SET DEFAULT nextval('public.snapshot_month_coverage_id_seq'::regclass);


--
-- Name: transaction_sighting id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.transaction_sighting ALTER COLUMN id SET DEFAULT nextval('public.transaction_sighting_id_seq'::regclass);


--
-- Name: upload_batch id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.upload_batch ALTER COLUMN id SET DEFAULT nextval('public.upload_batch_id_seq'::regclass);


--
-- Name: user_session id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_session ALTER COLUMN id SET DEFAULT nextval('public.user_session_id_seq'::regclass);


--
-- Name: auth_event auth_event_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.auth_event
    ADD CONSTRAINT auth_event_pkey PRIMARY KEY (id);


--
-- Name: budget_lock budget_lock_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.budget_lock
    ADD CONSTRAINT budget_lock_pkey PRIMARY KEY (id);


--
-- Name: class_equivalence class_equivalence_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.class_equivalence
    ADD CONSTRAINT class_equivalence_pkey PRIMARY KEY (id);


--
-- Name: class_equivalence class_equivalence_source_type_source_value_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.class_equivalence
    ADD CONSTRAINT class_equivalence_source_type_source_value_key UNIQUE (source_type, source_value);


--
-- Name: forecast_month_lock forecast_month_lock_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.forecast_month_lock
    ADD CONSTRAINT forecast_month_lock_pkey PRIMARY KEY (forecast_month);


--
-- Name: forecast_month_override forecast_month_override_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.forecast_month_override
    ADD CONSTRAINT forecast_month_override_pkey PRIMARY KEY (id);


--
-- Name: match_allocation match_allocation_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.match_allocation
    ADD CONSTRAINT match_allocation_pkey PRIMARY KEY (id);


--
-- Name: match_allocation match_allocation_transaction_id_policy_id_forecast_month_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.match_allocation
    ADD CONSTRAINT match_allocation_transaction_id_policy_id_forecast_month_key UNIQUE (transaction_id, policy_id, forecast_month);


--
-- Name: match_candidate match_candidate_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.match_candidate
    ADD CONSTRAINT match_candidate_pkey PRIMARY KEY (id);


--
-- Name: match_decision match_decision_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.match_decision
    ADD CONSTRAINT match_decision_pkey PRIMARY KEY (id);


--
-- Name: match_run match_run_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.match_run
    ADD CONSTRAINT match_run_pkey PRIMARY KEY (id);


--
-- Name: app_user pk_app_user; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.app_user
    ADD CONSTRAINT pk_app_user PRIMARY KEY (id);


--
-- Name: batch_rollback pk_batch_rollback; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.batch_rollback
    ADD CONSTRAINT pk_batch_rollback PRIMARY KEY (id);


--
-- Name: budget_audit pk_budget_audit; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.budget_audit
    ADD CONSTRAINT pk_budget_audit PRIMARY KEY (id);


--
-- Name: category_map pk_category_map; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.category_map
    ADD CONSTRAINT pk_category_map PRIMARY KEY (category);


--
-- Name: column_mapping_profile pk_column_mapping_profile; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.column_mapping_profile
    ADD CONSTRAINT pk_column_mapping_profile PRIMARY KEY (id);


--
-- Name: exclusion_rule pk_exclusion_rule; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.exclusion_rule
    ADD CONSTRAINT pk_exclusion_rule PRIMARY KEY (id);


--
-- Name: forecast_baseline pk_forecast_baseline; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.forecast_baseline
    ADD CONSTRAINT pk_forecast_baseline PRIMARY KEY (forecast_month);


--
-- Name: forecast_month_coverage pk_forecast_month_coverage; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.forecast_month_coverage
    ADD CONSTRAINT pk_forecast_month_coverage PRIMARY KEY (forecast_month);


--
-- Name: forecast_movement pk_forecast_movement; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.forecast_movement
    ADD CONSTRAINT pk_forecast_movement PRIMARY KEY (id);


--
-- Name: forecast_policy pk_forecast_policy; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.forecast_policy
    ADD CONSTRAINT pk_forecast_policy PRIMARY KEY (id);


--
-- Name: forecast_snapshot pk_forecast_snapshot; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.forecast_snapshot
    ADD CONSTRAINT pk_forecast_snapshot PRIMARY KEY (id);


--
-- Name: growth_rate pk_growth_rate; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.growth_rate
    ADD CONSTRAINT pk_growth_rate PRIMARY KEY (id);


--
-- Name: import_staging pk_import_staging; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.import_staging
    ADD CONSTRAINT pk_import_staging PRIMARY KEY (id);


--
-- Name: ingest_exception pk_ingest_exception; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ingest_exception
    ADD CONSTRAINT pk_ingest_exception PRIMARY KEY (id);


--
-- Name: legacy_forecast_reference pk_legacy_forecast_reference; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.legacy_forecast_reference
    ADD CONSTRAINT pk_legacy_forecast_reference PRIMARY KEY (id);


--
-- Name: manager_alias pk_manager_alias; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.manager_alias
    ADD CONSTRAINT pk_manager_alias PRIMARY KEY (id);


--
-- Name: monthly_target_override pk_monthly_target_override; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.monthly_target_override
    ADD CONSTRAINT pk_monthly_target_override PRIMARY KEY (id);


--
-- Name: original_forecast pk_original_forecast; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.original_forecast
    ADD CONSTRAINT pk_original_forecast PRIMARY KEY (id);


--
-- Name: period_coverage pk_period_coverage; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.period_coverage
    ADD CONSTRAINT pk_period_coverage PRIMARY KEY (id);


--
-- Name: rebaseline_audit pk_rebaseline_audit; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.rebaseline_audit
    ADD CONSTRAINT pk_rebaseline_audit PRIMARY KEY (id);


--
-- Name: reporting_manager pk_reporting_manager; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.reporting_manager
    ADD CONSTRAINT pk_reporting_manager PRIMARY KEY (id);


--
-- Name: reporting_settings pk_reporting_settings; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.reporting_settings
    ADD CONSTRAINT pk_reporting_settings PRIMARY KEY (id);


--
-- Name: restated_transaction pk_restated_transaction; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.restated_transaction
    ADD CONSTRAINT pk_restated_transaction PRIMARY KEY (id);


--
-- Name: sales_transaction pk_sales_transaction; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.sales_transaction
    ADD CONSTRAINT pk_sales_transaction PRIMARY KEY (id);


--
-- Name: transaction_sighting pk_transaction_sighting; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.transaction_sighting
    ADD CONSTRAINT pk_transaction_sighting PRIMARY KEY (id);


--
-- Name: upload_batch pk_upload_batch; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.upload_batch
    ADD CONSTRAINT pk_upload_batch PRIMARY KEY (id);


--
-- Name: policy_outcome policy_outcome_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.policy_outcome
    ADD CONSTRAINT policy_outcome_pkey PRIMARY KEY (id);


--
-- Name: policy_outcome policy_outcome_policy_id_forecast_month_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.policy_outcome
    ADD CONSTRAINT policy_outcome_policy_id_forecast_month_key UNIQUE (policy_id, forecast_month);


--
-- Name: schema_migration schema_migration_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.schema_migration
    ADD CONSTRAINT schema_migration_pkey PRIMARY KEY (filename);


--
-- Name: snapshot_month_coverage snapshot_month_coverage_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.snapshot_month_coverage
    ADD CONSTRAINT snapshot_month_coverage_pkey PRIMARY KEY (id);


--
-- Name: snapshot_month_coverage snapshot_month_coverage_snapshot_id_forecast_month_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.snapshot_month_coverage
    ADD CONSTRAINT snapshot_month_coverage_snapshot_id_forecast_month_key UNIQUE (snapshot_id, forecast_month);


--
-- Name: app_user uq_app_user_username; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.app_user
    ADD CONSTRAINT uq_app_user_username UNIQUE (username);


--
-- Name: column_mapping_profile uq_column_mapping_profile_profile_name; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.column_mapping_profile
    ADD CONSTRAINT uq_column_mapping_profile_profile_name UNIQUE (profile_name);


--
-- Name: exclusion_rule uq_exclusion_rule_definition; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.exclusion_rule
    ADD CONSTRAINT uq_exclusion_rule_definition UNIQUE (source_type, target_field, match_type, match_value);


--
-- Name: forecast_policy uq_forecast_policy_snapshot_policy; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.forecast_policy
    ADD CONSTRAINT uq_forecast_policy_snapshot_policy UNIQUE (snapshot_id, policy_id);


--
-- Name: legacy_forecast_reference uq_legacy_forecast_month_manager; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.legacy_forecast_reference
    ADD CONSTRAINT uq_legacy_forecast_month_manager UNIQUE (forecast_month, source_manager);


--
-- Name: manager_alias uq_manager_alias_source_manager; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.manager_alias
    ADD CONSTRAINT uq_manager_alias_source_manager UNIQUE (source_manager);


--
-- Name: period_coverage uq_period_coverage_fy_domain; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.period_coverage
    ADD CONSTRAINT uq_period_coverage_fy_domain UNIQUE (financial_year, data_domain);


--
-- Name: reporting_manager uq_reporting_manager_canonical_manager; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.reporting_manager
    ADD CONSTRAINT uq_reporting_manager_canonical_manager UNIQUE (canonical_manager);


--
-- Name: sales_transaction uq_sales_transaction_fingerprint; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.sales_transaction
    ADD CONSTRAINT uq_sales_transaction_fingerprint UNIQUE (fingerprint);


--
-- Name: transaction_sighting uq_sighting_txn_batch; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.transaction_sighting
    ADD CONSTRAINT uq_sighting_txn_batch UNIQUE (transaction_id, batch_id);


--
-- Name: import_staging uq_staging_batch_row; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.import_staging
    ADD CONSTRAINT uq_staging_batch_row UNIQUE (batch_id, source_row_number);


--
-- Name: user_session user_session_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_session
    ADD CONSTRAINT user_session_pkey PRIMARY KEY (id);


--
-- Name: ix_auth_event_email; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_auth_event_email ON public.auth_event USING btree (lower((email)::text));


--
-- Name: ix_auth_event_time; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_auth_event_time ON public.auth_event USING btree (occurred_at DESC);


--
-- Name: ix_budget_lock_month; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_budget_lock_month ON public.budget_lock USING btree (target_month) WHERE active;


--
-- Name: ix_class_equivalence_canonical; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_class_equivalence_canonical ON public.class_equivalence USING btree (canonical_class);


--
-- Name: ix_fcst_match_keys; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_fcst_match_keys ON public.forecast_policy USING btree (client_code_norm, policy_number_norm);


--
-- Name: ix_fcst_reporting; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_fcst_reporting ON public.forecast_policy USING btree (snapshot_id, forecast_month, source_manager) WHERE (NOT is_excluded);


--
-- Name: ix_forecast_baseline_financial_year; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_forecast_baseline_financial_year ON public.forecast_baseline USING btree (financial_year);


--
-- Name: ix_forecast_month_lock_active; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_forecast_month_lock_active ON public.forecast_month_lock USING btree (forecast_month) WHERE active;


--
-- Name: ix_forecast_month_override_open; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_forecast_month_override_open ON public.forecast_month_override USING btree (forecast_month) WHERE (consumed_at IS NULL);


--
-- Name: ix_forecast_movement_forecast_month; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_forecast_movement_forecast_month ON public.forecast_movement USING btree (forecast_month);


--
-- Name: ix_forecast_movement_movement_type; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_forecast_movement_movement_type ON public.forecast_movement USING btree (movement_type);


--
-- Name: ix_forecast_movement_policy_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_forecast_movement_policy_id ON public.forecast_movement USING btree (policy_id);


--
-- Name: ix_forecast_policy_class_abbrev; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_forecast_policy_class_abbrev ON public.forecast_policy USING btree (class_abbrev);


--
-- Name: ix_forecast_policy_forecast_month; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_forecast_policy_forecast_month ON public.forecast_policy USING btree (forecast_month);


--
-- Name: ix_forecast_policy_policy_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_forecast_policy_policy_id ON public.forecast_policy USING btree (policy_id);


--
-- Name: ix_forecast_policy_source_manager; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_forecast_policy_source_manager ON public.forecast_policy USING btree (source_manager);


--
-- Name: ix_forecast_policy_underwriter_abbrev; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_forecast_policy_underwriter_abbrev ON public.forecast_policy USING btree (underwriter_abbrev);


--
-- Name: ix_import_staging_batch_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_import_staging_batch_id ON public.import_staging USING btree (batch_id);


--
-- Name: ix_import_staging_fingerprint; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_import_staging_fingerprint ON public.import_staging USING btree (fingerprint);


--
-- Name: ix_import_staging_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_import_staging_status ON public.import_staging USING btree (status);


--
-- Name: ix_ingest_exception_exception_type; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_ingest_exception_exception_type ON public.ingest_exception USING btree (exception_type);


--
-- Name: ix_legacy_forecast_reference_forecast_month; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_legacy_forecast_reference_forecast_month ON public.legacy_forecast_reference USING btree (forecast_month);


--
-- Name: ix_manager_alias_source_manager_norm; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_manager_alias_source_manager_norm ON public.manager_alias USING btree (source_manager_norm);


--
-- Name: ix_match_allocation_policy; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_match_allocation_policy ON public.match_allocation USING btree (policy_id, forecast_month);


--
-- Name: ix_match_allocation_txn; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_match_allocation_txn ON public.match_allocation USING btree (transaction_id);


--
-- Name: ix_match_candidate_pending; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_match_candidate_pending ON public.match_candidate USING btree (reason) WHERE ((status)::text = 'pending'::text);


--
-- Name: ix_match_candidate_txn; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_match_candidate_txn ON public.match_candidate USING btree (transaction_id);


--
-- Name: ix_match_decision_policy; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_match_decision_policy ON public.match_decision USING btree (policy_id, forecast_month);


--
-- Name: ix_movement_detail_changed; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_movement_detail_changed ON public.forecast_movement USING btree (forecast_month) WHERE detail_changed;


--
-- Name: ix_movement_manager_changed; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_movement_manager_changed ON public.forecast_movement USING btree (forecast_month) WHERE manager_changed;


--
-- Name: ix_original_forecast_forecast_month; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_original_forecast_forecast_month ON public.original_forecast USING btree (forecast_month);


--
-- Name: ix_original_forecast_policy_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_original_forecast_policy_id ON public.original_forecast USING btree (policy_id);


--
-- Name: ix_original_forecast_source_manager; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_original_forecast_source_manager ON public.original_forecast USING btree (source_manager);


--
-- Name: ix_policy_outcome_outcome; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_policy_outcome_outcome ON public.policy_outcome USING btree (outcome);


--
-- Name: ix_policy_outcome_review; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_policy_outcome_review ON public.policy_outcome USING btree (forecast_month) WHERE requires_review;


--
-- Name: ix_sales_transaction_category; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_sales_transaction_category ON public.sales_transaction USING btree (category);


--
-- Name: ix_sales_transaction_invoice_number; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_sales_transaction_invoice_number ON public.sales_transaction USING btree (invoice_number);


--
-- Name: ix_sales_transaction_policy_class; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_sales_transaction_policy_class ON public.sales_transaction USING btree (policy_class);


--
-- Name: ix_sales_transaction_source_manager; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_sales_transaction_source_manager ON public.sales_transaction USING btree (source_manager);


--
-- Name: ix_sales_transaction_uw_code; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_sales_transaction_uw_code ON public.sales_transaction USING btree (uw_code);


--
-- Name: ix_sighting_batch; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_sighting_batch ON public.transaction_sighting USING btree (batch_id);


--
-- Name: ix_snapshot_month_coverage_month; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_snapshot_month_coverage_month ON public.snapshot_month_coverage USING btree (forecast_month);


--
-- Name: ix_staging_pending; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_staging_pending ON public.import_staging USING btree (batch_id, status);


--
-- Name: ix_txn_fy_quarter; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_txn_fy_quarter ON public.sales_transaction USING btree (financial_year, financial_quarter) WHERE (NOT is_excluded);


--
-- Name: ix_txn_match_keys; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_txn_match_keys ON public.sales_transaction USING btree (client_code_norm, policy_number_norm);


--
-- Name: ix_txn_reporting; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_txn_reporting ON public.sales_transaction USING btree (period_month, source_manager) WHERE (NOT is_excluded);


--
-- Name: ix_upload_batch_file_sha256; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_upload_batch_file_sha256 ON public.upload_batch USING btree (file_sha256);


--
-- Name: ix_user_session_live; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_user_session_live ON public.user_session USING btree (user_id) WHERE (revoked_at IS NULL);


--
-- Name: uq_app_user_email; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX uq_app_user_email ON public.app_user USING btree (lower((email)::text)) WHERE (email IS NOT NULL);


--
-- Name: uq_auto_allocation_per_transaction; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX uq_auto_allocation_per_transaction ON public.match_allocation USING btree (transaction_id) WHERE ((method)::text = 'auto'::text);


--
-- Name: uq_budget_lock_active; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX uq_budget_lock_active ON public.budget_lock USING btree (canonical_manager, target_month) WHERE active;


--
-- Name: uq_default_profile_per_type; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX uq_default_profile_per_type ON public.column_mapping_profile USING btree (file_type) WHERE is_default;


--
-- Name: uq_growth_global; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX uq_growth_global ON public.growth_rate USING btree (scope) WHERE (((scope)::text = 'global'::text) AND active);


--
-- Name: uq_growth_manager; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX uq_growth_manager ON public.growth_rate USING btree (canonical_manager, financial_year) WHERE (((scope)::text = 'manager'::text) AND active);


--
-- Name: uq_growth_manager_month; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX uq_growth_manager_month ON public.growth_rate USING btree (canonical_manager, target_month) WHERE (((scope)::text = 'manager_month'::text) AND active);


--
-- Name: uq_growth_manager_quarter; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX uq_growth_manager_quarter ON public.growth_rate USING btree (canonical_manager, financial_year, financial_quarter) WHERE (((scope)::text = 'manager_quarter'::text) AND active);


--
-- Name: uq_monthly_override; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX uq_monthly_override ON public.monthly_target_override USING btree (canonical_manager, target_month) WHERE active;


--
-- Name: uq_orig_manager_month; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX uq_orig_manager_month ON public.original_forecast USING btree (source_manager, forecast_month) WHERE ((grain)::text = 'manager_month'::text);


--
-- Name: uq_orig_policy; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX uq_orig_policy ON public.original_forecast USING btree (policy_id, forecast_month) WHERE ((grain)::text = 'policy'::text);


--
-- Name: uq_user_session_token; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX uq_user_session_token ON public.user_session USING btree (token_hash);


--
-- Name: match_allocation trg_allocation_total; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE CONSTRAINT TRIGGER trg_allocation_total AFTER INSERT OR UPDATE ON public.match_allocation DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.check_allocation_total();


--
-- Name: auth_event auth_event_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.auth_event
    ADD CONSTRAINT auth_event_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.app_user(id) ON DELETE SET NULL;


--
-- Name: budget_lock budget_lock_canonical_manager_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.budget_lock
    ADD CONSTRAINT budget_lock_canonical_manager_fkey FOREIGN KEY (canonical_manager) REFERENCES public.reporting_manager(canonical_manager) ON UPDATE CASCADE;


--
-- Name: batch_rollback fk_batch_rollback_batch_id_upload_batch; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.batch_rollback
    ADD CONSTRAINT fk_batch_rollback_batch_id_upload_batch FOREIGN KEY (batch_id) REFERENCES public.upload_batch(id);


--
-- Name: forecast_month_coverage fk_forecast_month_coverage_latest_snapshot_id_forecast_snapshot; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.forecast_month_coverage
    ADD CONSTRAINT fk_forecast_month_coverage_latest_snapshot_id_forecast_snapshot FOREIGN KEY (latest_snapshot_id) REFERENCES public.forecast_snapshot(id);


--
-- Name: forecast_month_coverage fk_forecast_month_coverage_original_snapshot_id_forecas_549e; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.forecast_month_coverage
    ADD CONSTRAINT fk_forecast_month_coverage_original_snapshot_id_forecas_549e FOREIGN KEY (original_snapshot_id) REFERENCES public.forecast_snapshot(id);


--
-- Name: forecast_movement fk_forecast_movement_from_snapshot_id_forecast_snapshot; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.forecast_movement
    ADD CONSTRAINT fk_forecast_movement_from_snapshot_id_forecast_snapshot FOREIGN KEY (from_snapshot_id) REFERENCES public.forecast_snapshot(id);


--
-- Name: forecast_movement fk_forecast_movement_to_snapshot_id_forecast_snapshot; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.forecast_movement
    ADD CONSTRAINT fk_forecast_movement_to_snapshot_id_forecast_snapshot FOREIGN KEY (to_snapshot_id) REFERENCES public.forecast_snapshot(id);


--
-- Name: forecast_policy fk_forecast_policy_exclusion_rule_id_exclusion_rule; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.forecast_policy
    ADD CONSTRAINT fk_forecast_policy_exclusion_rule_id_exclusion_rule FOREIGN KEY (exclusion_rule_id) REFERENCES public.exclusion_rule(id);


--
-- Name: forecast_policy fk_forecast_policy_snapshot_id_forecast_snapshot; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.forecast_policy
    ADD CONSTRAINT fk_forecast_policy_snapshot_id_forecast_snapshot FOREIGN KEY (snapshot_id) REFERENCES public.forecast_snapshot(id);


--
-- Name: forecast_snapshot fk_forecast_snapshot_batch_id_upload_batch; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.forecast_snapshot
    ADD CONSTRAINT fk_forecast_snapshot_batch_id_upload_batch FOREIGN KEY (batch_id) REFERENCES public.upload_batch(id);


--
-- Name: growth_rate fk_growth_rate_canonical_manager_reporting_manager; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.growth_rate
    ADD CONSTRAINT fk_growth_rate_canonical_manager_reporting_manager FOREIGN KEY (canonical_manager) REFERENCES public.reporting_manager(canonical_manager) ON UPDATE CASCADE;


--
-- Name: import_staging fk_import_staging_batch_id_upload_batch; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.import_staging
    ADD CONSTRAINT fk_import_staging_batch_id_upload_batch FOREIGN KEY (batch_id) REFERENCES public.upload_batch(id) ON DELETE CASCADE;


--
-- Name: import_staging fk_import_staging_exclusion_rule_id_exclusion_rule; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.import_staging
    ADD CONSTRAINT fk_import_staging_exclusion_rule_id_exclusion_rule FOREIGN KEY (exclusion_rule_id) REFERENCES public.exclusion_rule(id);


--
-- Name: ingest_exception fk_ingest_exception_batch_id_upload_batch; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ingest_exception
    ADD CONSTRAINT fk_ingest_exception_batch_id_upload_batch FOREIGN KEY (batch_id) REFERENCES public.upload_batch(id);


--
-- Name: legacy_forecast_reference fk_legacy_forecast_reference_batch_id_upload_batch; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.legacy_forecast_reference
    ADD CONSTRAINT fk_legacy_forecast_reference_batch_id_upload_batch FOREIGN KEY (batch_id) REFERENCES public.upload_batch(id);


--
-- Name: manager_alias fk_manager_alias_canonical_manager_reporting_manager; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.manager_alias
    ADD CONSTRAINT fk_manager_alias_canonical_manager_reporting_manager FOREIGN KEY (canonical_manager) REFERENCES public.reporting_manager(canonical_manager) ON UPDATE CASCADE;


--
-- Name: monthly_target_override fk_monthly_target_override_canonical_manager_reporting_manager; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.monthly_target_override
    ADD CONSTRAINT fk_monthly_target_override_canonical_manager_reporting_manager FOREIGN KEY (canonical_manager) REFERENCES public.reporting_manager(canonical_manager) ON UPDATE CASCADE;


--
-- Name: original_forecast fk_original_forecast_established_batch_id_upload_batch; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.original_forecast
    ADD CONSTRAINT fk_original_forecast_established_batch_id_upload_batch FOREIGN KEY (established_batch_id) REFERENCES public.upload_batch(id);


--
-- Name: original_forecast fk_original_forecast_established_snapshot_id_forecast_snapshot; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.original_forecast
    ADD CONSTRAINT fk_original_forecast_established_snapshot_id_forecast_snapshot FOREIGN KEY (established_snapshot_id) REFERENCES public.forecast_snapshot(id);


--
-- Name: restated_transaction fk_restated_transaction_batch_id_upload_batch; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.restated_transaction
    ADD CONSTRAINT fk_restated_transaction_batch_id_upload_batch FOREIGN KEY (batch_id) REFERENCES public.upload_batch(id);


--
-- Name: restated_transaction fk_restated_transaction_transaction_id_sales_transaction; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.restated_transaction
    ADD CONSTRAINT fk_restated_transaction_transaction_id_sales_transaction FOREIGN KEY (transaction_id) REFERENCES public.sales_transaction(id);


--
-- Name: sales_transaction fk_sales_transaction_exclusion_rule_id_exclusion_rule; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.sales_transaction
    ADD CONSTRAINT fk_sales_transaction_exclusion_rule_id_exclusion_rule FOREIGN KEY (exclusion_rule_id) REFERENCES public.exclusion_rule(id);


--
-- Name: sales_transaction fk_sales_transaction_first_seen_batch_id_upload_batch; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.sales_transaction
    ADD CONSTRAINT fk_sales_transaction_first_seen_batch_id_upload_batch FOREIGN KEY (first_seen_batch_id) REFERENCES public.upload_batch(id);


--
-- Name: sales_transaction fk_sales_transaction_last_seen_batch_id_upload_batch; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.sales_transaction
    ADD CONSTRAINT fk_sales_transaction_last_seen_batch_id_upload_batch FOREIGN KEY (last_seen_batch_id) REFERENCES public.upload_batch(id);


--
-- Name: transaction_sighting fk_transaction_sighting_batch_id_upload_batch; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.transaction_sighting
    ADD CONSTRAINT fk_transaction_sighting_batch_id_upload_batch FOREIGN KEY (batch_id) REFERENCES public.upload_batch(id) ON DELETE CASCADE;


--
-- Name: transaction_sighting fk_transaction_sighting_transaction_id_sales_transaction; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.transaction_sighting
    ADD CONSTRAINT fk_transaction_sighting_transaction_id_sales_transaction FOREIGN KEY (transaction_id) REFERENCES public.sales_transaction(id) ON DELETE CASCADE;


--
-- Name: forecast_month_override forecast_month_override_consumed_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.forecast_month_override
    ADD CONSTRAINT forecast_month_override_consumed_batch_id_fkey FOREIGN KEY (consumed_batch_id) REFERENCES public.upload_batch(id);


--
-- Name: match_allocation match_allocation_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.match_allocation
    ADD CONSTRAINT match_allocation_transaction_id_fkey FOREIGN KEY (transaction_id) REFERENCES public.sales_transaction(id) ON DELETE CASCADE;


--
-- Name: match_candidate match_candidate_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.match_candidate
    ADD CONSTRAINT match_candidate_transaction_id_fkey FOREIGN KEY (transaction_id) REFERENCES public.sales_transaction(id) ON DELETE CASCADE;


--
-- Name: snapshot_month_coverage snapshot_month_coverage_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.snapshot_month_coverage
    ADD CONSTRAINT snapshot_month_coverage_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES public.forecast_snapshot(id) ON DELETE CASCADE;


--
-- Name: user_session user_session_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_session
    ADD CONSTRAINT user_session_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.app_user(id) ON DELETE CASCADE;


--
-- Name: SCHEMA public; Type: ACL; Schema: -; Owner: postgres
--

REVOKE USAGE ON SCHEMA public FROM PUBLIC;


--
-- PostgreSQL database dump complete
--

\unrestrict eYp9UdoLMqwpghNfxrKIWlWLVQ6lrdn0GpCKkMseHG1J6ZeoNP93KiXxmFjRSqS

