-- =============================================
-- V074: Partition Automation for Journal Entries
-- Purpose: Auto-create monthly partitions to prevent production failures
-- Recommendation: Run create_future_partitions() monthly via cron/scheduler
-- =============================================

-- ============================================================================
-- FUNCTION: Create a single month partition if it doesn't exist
-- ============================================================================
CREATE OR REPLACE FUNCTION create_journal_partition(
    p_year INTEGER,
    p_month INTEGER
) RETURNS TEXT AS $$
DECLARE
    v_partition_name TEXT;
    v_start_date DATE;
    v_end_date DATE;
BEGIN
    -- Build partition name: journal_entries_YYYY_MM
    v_partition_name := format('journal_entries_%s_%s',
                               p_year::TEXT,
                               LPAD(p_month::TEXT, 2, '0'));

    -- Calculate date range
    v_start_date := make_date(p_year, p_month, 1);
    v_end_date := v_start_date + INTERVAL '1 month';

    -- Check if partition already exists
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = v_partition_name) THEN
        RETURN format('SKIP: Partition %s already exists', v_partition_name);
    END IF;

    -- Create the partition
    EXECUTE format(
        'CREATE TABLE %I PARTITION OF journal_entries FOR VALUES FROM (%L) TO (%L)',
        v_partition_name,
        v_start_date,
        v_end_date
    );

    RETURN format('CREATED: %s (range: %s to %s)',
                  v_partition_name, v_start_date, v_end_date);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- FUNCTION: Create partitions for the next N months from current date
-- Default: 6 months ahead (safe buffer)
-- ============================================================================
CREATE OR REPLACE FUNCTION create_future_partitions(
    p_months_ahead INTEGER DEFAULT 6
) RETURNS TABLE (partition_name TEXT, result TEXT) AS $$
DECLARE
    v_current_date DATE := CURRENT_DATE;
    v_target_date DATE;
    v_year INTEGER;
    v_month INTEGER;
BEGIN
    FOR i IN 0..p_months_ahead LOOP
        v_target_date := v_current_date + (i || ' months')::INTERVAL;
        v_year := EXTRACT(YEAR FROM v_target_date)::INTEGER;
        v_month := EXTRACT(MONTH FROM v_target_date)::INTEGER;

        partition_name := format('journal_entries_%s_%s',
                                 v_year::TEXT,
                                 LPAD(v_month::TEXT, 2, '0'));
        result := create_journal_partition(v_year, v_month);
        RETURN NEXT;
    END LOOP;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- FUNCTION: Get partition status report
-- ============================================================================
CREATE OR REPLACE FUNCTION get_partition_status()
RETURNS TABLE (
    partition_name TEXT,
    range_start DATE,
    range_end DATE,
    row_count BIGINT,
    status TEXT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        c.relname::TEXT,
        (regexp_matches(pg_get_expr(c.relpartbound, c.oid), '''(\d{4}-\d{2}-\d{2})'''))[1]::DATE as range_start,
        (regexp_matches(pg_get_expr(c.relpartbound, c.oid), '''(\d{4}-\d{2}-\d{2})''', 'g'))[1]::DATE as range_end,
        pg_stat_get_live_tuples(c.oid) as row_count,
        CASE
            WHEN (regexp_matches(pg_get_expr(c.relpartbound, c.oid), '''(\d{4}-\d{2}-\d{2})'''))[1]::DATE <= CURRENT_DATE
                 AND (regexp_matches(pg_get_expr(c.relpartbound, c.oid), '''(\d{4}-\d{2}-\d{2})''', 'g'))[1]::DATE > CURRENT_DATE
            THEN 'CURRENT'
            WHEN (regexp_matches(pg_get_expr(c.relpartbound, c.oid), '''(\d{4}-\d{2}-\d{2})''', 'g'))[1]::DATE <= CURRENT_DATE
            THEN 'PAST'
            ELSE 'FUTURE'
        END as status
    FROM pg_class c
    JOIN pg_inherits i ON c.oid = i.inhrelid
    JOIN pg_class p ON i.inhparent = p.oid
    WHERE p.relname = 'journal_entries'
    ORDER BY c.relname;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- Create 2027 partitions immediately (since we're in January 2026)
-- ============================================================================
SELECT create_journal_partition(2027, 1);
SELECT create_journal_partition(2027, 2);
SELECT create_journal_partition(2027, 3);
SELECT create_journal_partition(2027, 4);
SELECT create_journal_partition(2027, 5);
SELECT create_journal_partition(2027, 6);
SELECT create_journal_partition(2027, 7);
SELECT create_journal_partition(2027, 8);
SELECT create_journal_partition(2027, 9);
SELECT create_journal_partition(2027, 10);
SELECT create_journal_partition(2027, 11);
SELECT create_journal_partition(2027, 12);

-- ============================================================================
-- COMMENTS & DOCUMENTATION
-- ============================================================================
COMMENT ON FUNCTION create_journal_partition IS
'Creates a single monthly partition for journal_entries table.
Usage: SELECT create_journal_partition(2028, 1);  -- Creates January 2028 partition';

COMMENT ON FUNCTION create_future_partitions IS
'Creates partitions for the next N months from current date.
Default: 6 months ahead for safety buffer.
Recommendation: Run monthly via cron job.
Usage: SELECT * FROM create_future_partitions(12);  -- Create next 12 months';

COMMENT ON FUNCTION get_partition_status IS
'Returns a report of all journal_entries partitions with their status and row counts.
Usage: SELECT * FROM get_partition_status();';

-- ============================================================================
-- RECOMMENDED CRON SETUP (for pg_cron extension)
-- ============================================================================
-- If using pg_cron extension:
-- SELECT cron.schedule('partition-maintenance', '0 1 1 * *', 'SELECT create_future_partitions(6)');
-- This runs on 1st of every month at 1 AM

-- If using external scheduler (cron):
-- 0 1 1 * * psql -U postgres -d milkyhoop -c "SELECT create_future_partitions(6);"
