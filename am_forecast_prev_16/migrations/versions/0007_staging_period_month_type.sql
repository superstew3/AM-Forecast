-- import_staging.period_month holds a month, not an instant. It was created as
-- a timestamp, which made coverage analysis compare a datetime against the date
-- values coming back from the forecast tables.
ALTER TABLE import_staging
    ALTER COLUMN period_month TYPE date USING period_month::date;
