-- Optional: range-partition market_data by year (PostgreSQL 13+). Only worth it past ~50M bars
-- (intraday data or very large universes). Run in a maintenance window AFTER a verified backup.
-- The primary key (instrument_id, interval, ts) already includes the partition key, so the
-- application needs no change. Test on a restored copy first.
BEGIN;
ALTER TABLE market_data RENAME TO market_data_unpartitioned;
CREATE TABLE market_data (LIKE market_data_unpartitioned INCLUDING DEFAULTS INCLUDING CONSTRAINTS)
    PARTITION BY RANGE (ts);
ALTER TABLE market_data ADD PRIMARY KEY (instrument_id, interval, ts);
ALTER TABLE market_data ADD FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE;
DO $$
DECLARE y int;
BEGIN
  FOR y IN 1995..(extract(year FROM now())::int + 1) LOOP
    EXECUTE format('CREATE TABLE market_data_%s PARTITION OF market_data FOR VALUES FROM (%L) TO (%L)',
                   y, make_date(y, 1, 1), make_date(y + 1, 1, 1));
  END LOOP;
END $$;
CREATE TABLE market_data_default PARTITION OF market_data DEFAULT;
INSERT INTO market_data SELECT * FROM market_data_unpartitioned;
-- verify counts match before dropping the old table:
--   SELECT (SELECT count(*) FROM market_data), (SELECT count(*) FROM market_data_unpartitioned);
COMMIT;
-- DROP TABLE market_data_unpartitioned;   -- after verification
-- Each December: CREATE TABLE market_data_<next year> PARTITION OF market_data FOR VALUES FROM (...) TO (...);
