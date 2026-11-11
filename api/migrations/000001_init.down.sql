
BEGIN;

DROP TABLE IF EXISTS
    messages,
    conversations,
    ingestion_jobs,
    document_items,
    financial_entries,
    documents,
    categories,
    account_members,
    accounts,
    user_sessions,
    platform_access,
    users
CASCADE;

DROP FUNCTION IF EXISTS app_claim_ingestion_job(TEXT, INTERVAL) CASCADE;
DROP FUNCTION IF EXISTS app_set_worker_context(UUID, TEXT) CASCADE;
DROP FUNCTION IF EXISTS app_set_account_context(UUID, UUID) CASCADE;
DROP FUNCTION IF EXISTS app_current_account_id() CASCADE;

DROP EXTENSION IF EXISTS unaccent;

-- Runtime roles. Roles are cluster-wide: removes the privileges
-- on this database and only drops the role if no other database depends on it.
DO $$
DECLARE
    role_name TEXT;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['myexp_app', 'myexp_worker']
    LOOP
        CONTINUE WHEN NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name);

        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public
             REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM %I',
            role_name
        );
        EXECUTE format('DROP OWNED BY %I', role_name);
        EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM %I', current_database(), role_name);

        BEGIN
            EXECUTE format('DROP ROLE %I', role_name);
        EXCEPTION WHEN dependent_objects_still_exist THEN
            RAISE NOTICE '% mantido: ainda usado por outro banco do cluster', role_name;
        END;
    END LOOP;
END;
$$;

COMMIT;
