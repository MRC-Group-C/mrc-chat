-- =============================================================================
-- CRM POSTGRESQL PREPARATION FOR DEBEZIUM (MRC-CHT-HD-01 Section 12.3, STT 14)
-- Location: mrc/infrastructure/debezium/sql/setup_crm_publication.sql
--
-- Prerequisite (needs a PostgreSQL restart, schedule a maintenance window):
--   wal_level=logical, max_replication_slots=4, max_wal_senders=4,
--   max_slot_wal_keep_size=2GB
--
-- Usage (run on staging first):
--   psql -U <superuser> -d byd -v dbz_password="$DBZ_CRM_PASSWORD" \
--     -f setup_crm_publication.sql
-- =============================================================================

\set ON_ERROR_STOP on

CREATE ROLE dbz_crm WITH LOGIN REPLICATION PASSWORD :'dbz_password';

SELECT format('GRANT CONNECT ON DATABASE %I TO dbz_crm', current_database()) \gexec
GRANT USAGE ON SCHEMA public TO dbz_crm;
GRANT SELECT ON
    public.users,
    public.org_units,
    public.user_org_membership,
    public.resignation_decisions
TO dbz_crm;

-- Never FOR ALL TABLES: payroll and expense data must stay out of the publication.
CREATE PUBLICATION dbz_crm_hr FOR TABLE
    public.users,
    public.org_units,
    public.user_org_membership,
    public.resignation_decisions;

-- offboard() deletes membership rows, so the old row must be in the WAL to tell
-- iam-sync which org unit the person just left.
ALTER TABLE public.users REPLICA IDENTITY FULL;
ALTER TABLE public.user_org_membership REPLICA IDENTITY FULL;
