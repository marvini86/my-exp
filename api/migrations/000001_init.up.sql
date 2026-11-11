
-- =========================================================
-- EXPENSE ASSISTANT
-- Initial database migration
-- PostgreSQL 16+
-- =========================================================

-- Accent-insensitive product search ("pão" → "PAO FRANCES").
CREATE EXTENSION IF NOT EXISTS unaccent;

-- =========================================================
-- 1. USERS
-- Global user identity.
-- identity_provider/external_subject = login provider and the "sub" of the
-- token (Google). Both null = invited user who has not signed in yet.
-- =========================================================

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    name VARCHAR(255),
    email VARCHAR(255),

    identity_provider VARCHAR(50),
    external_subject VARCHAR(255),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_users_identity
        UNIQUE (identity_provider, external_subject),

    CONSTRAINT ck_users_identity
        CHECK (
            (identity_provider IS NULL AND external_subject IS NULL)
            OR
            (identity_provider IS NOT NULL AND external_subject IS NOT NULL)
        )
);

-- Case-insensitive unique e-mail.
CREATE UNIQUE INDEX uq_users_email_lower
    ON users (LOWER(email))
    WHERE email IS NOT NULL;


-- =========================================================
-- 1.1 USER SESSIONS
-- API login sessions. Only stores the SHA-256 of the tokens
-- (random, 256 bits); the plain token only exists on the client.
-- No RLS: it does not belong to an account and is queried before
-- there is a context (to find out who the user is).
-- =========================================================

CREATE TABLE user_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    access_token_hash CHAR(64) NOT NULL,
    access_expires_at TIMESTAMPTZ NOT NULL,

    refresh_token_hash CHAR(64) NOT NULL,
    refresh_expires_at TIMESTAMPTZ NOT NULL,
    -- Previous refresh token (already rotated). If it shows up again, someone
    -- copied it: the whole session is revoked.
    previous_refresh_token_hash CHAR(64),
    -- Until when the previous refresh token is treated as a race (two tabs or
    -- parallel requests refreshing together) rather than theft.
    previous_refresh_grace_until TIMESTAMPTZ,

    user_agent VARCHAR(255),
    revoked_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_user_sessions_access UNIQUE (access_token_hash),
    CONSTRAINT uq_user_sessions_refresh UNIQUE (refresh_token_hash),
    CONSTRAINT uq_user_sessions_previous_refresh UNIQUE (previous_refresh_token_hash)
);

CREATE INDEX ix_user_sessions_user
    ON user_sessions (user_id);


-- =========================================================
-- 1.2 PLATFORM ACCESS
-- E-mails allowed by the platform administrator to sign in
-- with Google. (People invited by account members can also sign in;
-- administrators come from AUTH_ADMIN_EMAILS.) No RLS: it does not belong
-- to an account and is queried at login, before there is a context.
-- =========================================================

CREATE TABLE platform_access (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    email VARCHAR(255) NOT NULL,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX uq_platform_access_email
    ON platform_access (LOWER(email));


-- =========================================================
-- 2. ACCOUNTS
-- Each account is a tenant.
-- =========================================================


CREATE TABLE accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) NOT NULL UNIQUE,

    created_by UUID REFERENCES users(id),
    updated_by UUID REFERENCES users(id),

    active BOOLEAN NOT NULL DEFAULT TRUE,

    activated_at TIMESTAMPTZ DEFAULT NOW(),
    deactivated_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_accounts_activation_dates
    CHECK (
        (
            active = TRUE
            AND deactivated_at IS NULL
        )
        OR
        (
            active = FALSE
            AND deactivated_at IS NOT NULL
        )
    ),

    CONSTRAINT ck_accounts_deleted
    CHECK (
        deleted_at IS NULL OR active = FALSE
    )
);



-- =========================================================
-- 3. ACCOUNT MEMBERS
-- Many-to-many relationship between users and accounts.
-- =========================================================

CREATE TABLE account_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    account_id UUID NOT NULL REFERENCES accounts(id),
    user_id UUID NOT NULL REFERENCES users(id),

    role VARCHAR(30) NOT NULL DEFAULT 'member',

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_account_member
        UNIQUE (account_id, user_id),

    CONSTRAINT ck_account_member_role
        CHECK (role IN ('owner', 'admin', 'member'))
);

CREATE INDEX idx_account_members_user
    ON account_members (user_id);


-- =========================================================
-- 4. CATEGORIES
-- Custom financial categories per account.
-- =========================================================

CREATE TABLE categories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    account_id UUID NOT NULL REFERENCES accounts(id),

    name VARCHAR(150) NOT NULL,
    description TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_category_account_id
        UNIQUE (account_id, id),

    CONSTRAINT uq_category_name
        UNIQUE (account_id, name)
);


-- =========================================================
-- 5. DOCUMENTS
-- Any file ingested by the system.
-- Type-specific data lives in JSONB.
-- =========================================================

CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    account_id UUID NOT NULL REFERENCES accounts(id),

    uploaded_by UUID REFERENCES users(id),

    original_filename VARCHAR(500) NOT NULL,
    mime_type VARCHAR(150),
    file_size BIGINT,

    sha256 VARCHAR(64),

    blob_path TEXT NOT NULL UNIQUE,

    document_type VARCHAR(100),

    extracted_data JSONB NOT NULL DEFAULT '{}'::JSONB,

    processing_status VARCHAR(30) NOT NULL DEFAULT 'pending',

    processing_error TEXT,

    -- When the document was indexed for semantic search (Qdrant).
    indexed_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_documents_account_id
        UNIQUE (account_id, id),

    CONSTRAINT ck_documents_status
        CHECK (
            processing_status IN (
                'pending',
                'processing',
                'completed',
                'failed'
            )
        ),

    CONSTRAINT ck_documents_file_size
        CHECK (file_size IS NULL OR file_size >= 0)
);

CREATE INDEX idx_documents_account
    ON documents (account_id);

CREATE INDEX idx_documents_type
    ON documents (account_id, document_type);

CREATE INDEX idx_documents_created
    ON documents (account_id, created_at DESC);

CREATE INDEX idx_documents_extracted_data
    ON documents USING GIN (extracted_data);

CREATE UNIQUE INDEX uq_documents_hash
    ON documents (account_id, sha256)
    WHERE sha256 IS NOT NULL;


-- =========================================================
-- 6. FINANCIAL ENTRIES
-- Generic representation of financial transactions.
-- An energy bill produces one entry.
-- An invoice may produce one entry.
-- =========================================================

CREATE TABLE financial_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    account_id UUID NOT NULL REFERENCES accounts(id),

    document_id UUID,

    category_id UUID,

    entry_type VARCHAR(30) NOT NULL DEFAULT 'expense',

    description VARCHAR(500) NOT NULL,

    establishment VARCHAR(255),

    amount NUMERIC(14,2) NOT NULL,

    currency CHAR(3) NOT NULL DEFAULT 'BRL',

    occurred_at DATE NOT NULL,

    due_date DATE,
    paid_at TIMESTAMPTZ,

    payment_status VARCHAR(30) NOT NULL DEFAULT 'unknown',

    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_financial_entries_account_id
        UNIQUE (account_id, id),

    CONSTRAINT fk_financial_entries_document
        FOREIGN KEY (account_id, document_id)
        REFERENCES documents (account_id, id),

    CONSTRAINT fk_financial_entries_category
        FOREIGN KEY (account_id, category_id)
        REFERENCES categories (account_id, id),

    CONSTRAINT ck_financial_entry_type
        CHECK (
            entry_type IN (
                'expense',
                'income',
                'refund',
                'transfer'
            )
        ),

    CONSTRAINT ck_financial_payment_status
        CHECK (
            payment_status IN (
                'unknown',
                'pending',
                'paid',
                'overdue',
                'cancelled'
            )
        ),

    CONSTRAINT ck_financial_amount
        CHECK (amount >= 0)
);

CREATE INDEX idx_financial_account_date
    ON financial_entries (account_id, occurred_at DESC);

CREATE INDEX idx_financial_account_category
    ON financial_entries (account_id, category_id);

CREATE INDEX idx_financial_document
    ON financial_entries (account_id, document_id);

CREATE INDEX idx_financial_metadata
    ON financial_entries USING GIN (metadata);


-- =========================================================
-- 7. DOCUMENT ITEMS
-- Individual items found in the documents.
-- Example: supermarket products.
-- Not required for every document type.
-- =========================================================

CREATE TABLE document_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    account_id UUID NOT NULL REFERENCES accounts(id),

    document_id UUID NOT NULL,

    financial_entry_id UUID,

    description VARCHAR(500) NOT NULL,

    brand VARCHAR(255),

    sku VARCHAR(100),

    quantity NUMERIC(14,3),

    unit VARCHAR(30),

    unit_price NUMERIC(14,4),

    total_price NUMERIC(14,2),

    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_document_items_document
        FOREIGN KEY (account_id, document_id)
        REFERENCES documents (account_id, id),

    CONSTRAINT fk_document_items_entry
        FOREIGN KEY (account_id, financial_entry_id)
        REFERENCES financial_entries (account_id, id),

    CONSTRAINT ck_document_item_quantity
        CHECK (quantity IS NULL OR quantity >= 0),

    CONSTRAINT ck_document_item_unit_price
        CHECK (unit_price IS NULL OR unit_price >= 0),

    CONSTRAINT ck_document_item_total_price
        CHECK (total_price IS NULL OR total_price >= 0)
);

CREATE INDEX idx_document_items_account
    ON document_items (account_id);

CREATE INDEX idx_document_items_document
    ON document_items (account_id, document_id);

CREATE INDEX idx_document_items_description
    ON document_items (account_id, description);

CREATE INDEX idx_document_items_brand
    ON document_items (account_id, brand);


-- =========================================================
-- 8. INGESTION JOBS
-- Persistent queue for asynchronous processing.
-- =========================================================

CREATE TABLE ingestion_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    account_id UUID NOT NULL REFERENCES accounts(id),

    document_id UUID NOT NULL,

    -- ingest: financial extraction | index: indexing for semantic search
    job_type VARCHAR(30) NOT NULL DEFAULT 'ingest',

    status VARCHAR(30) NOT NULL DEFAULT 'pending',

    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,

    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    locked_at TIMESTAMPTZ,
    locked_by VARCHAR(255),

    last_error TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_ingestion_document
        FOREIGN KEY (account_id, document_id)
        REFERENCES documents (account_id, id),

    CONSTRAINT ck_ingestion_status
        CHECK (
            status IN (
                'pending',
                'processing',
                'completed',
                'failed'
            )
        ),

    CONSTRAINT ck_ingestion_job_type
        CHECK (job_type IN ('ingest', 'index')),

    CONSTRAINT ck_ingestion_attempts
        CHECK (
            attempts >= 0 AND max_attempts > 0
        )
);

CREATE INDEX idx_ingestion_pending
    ON ingestion_jobs (available_at, created_at)
    WHERE status = 'pending';

CREATE INDEX idx_ingestion_account
    ON ingestion_jobs (account_id, created_at DESC);


-- =========================================================
-- 9. CONVERSATIONS
-- Conversation history per account.
-- =========================================================

CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    account_id UUID NOT NULL REFERENCES accounts(id),

    user_id UUID NOT NULL REFERENCES users(id),

    title VARCHAR(255),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_conversations_account_id
        UNIQUE (account_id, id)
);

CREATE INDEX idx_conversations_account
    ON conversations (account_id, updated_at DESC);


-- =========================================================
-- 10. MESSAGES
-- Messages and assistant answers.
-- =========================================================

CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    account_id UUID NOT NULL REFERENCES accounts(id),

    conversation_id UUID NOT NULL,

    role VARCHAR(30) NOT NULL,

    content TEXT NOT NULL,

    citations JSONB NOT NULL DEFAULT '[]'::JSONB,

    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_messages_conversation
        FOREIGN KEY (account_id, conversation_id)
        REFERENCES conversations (account_id, id),

    CONSTRAINT ck_message_role
        CHECK (
            role IN ('user', 'assistant', 'system', 'tool')
        )
);

CREATE INDEX idx_messages_conversation
    ON messages (account_id, conversation_id, created_at);



-- =========================================================
-- 11. TENANT CONTEXT
-- The backend must validate the user's membership
-- before setting the account context.
-- =========================================================

CREATE OR REPLACE FUNCTION app_set_account_context(
    p_account_id UUID,
    p_user_id UUID
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM public.account_members
        WHERE account_id = p_account_id
          AND user_id = p_user_id
    ) THEN
        RAISE EXCEPTION 'User is not a member of this account';
    END IF;

    PERFORM set_config(
        'app.current_account_id',
        p_account_id::TEXT,
        TRUE
    );
END;
$$;


CREATE OR REPLACE FUNCTION app_current_account_id()
RETURNS UUID
LANGUAGE sql
STABLE
AS $$
    SELECT NULLIF(
        current_setting('app.current_account_id', TRUE),
        ''
    )::UUID;
$$;


-- =========================================================
-- 12. ROW LEVEL SECURITY
-- =========================================================

DO $$
DECLARE
    table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'documents',
        'categories',
        'financial_entries',
        'document_items',
        'ingestion_jobs',
        'conversations',
        'messages'
    ]
    LOOP
        EXECUTE format(
            'ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',
            table_name
        );

        EXECUTE format(
            'ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',
            table_name
        );

        EXECUTE format(
            'CREATE POLICY tenant_isolation ON public.%I
             USING (account_id = app_current_account_id())
             WITH CHECK (account_id = app_current_account_id())',
            table_name
        );
    END LOOP;
END;
$$;



-- =========================================================
-- 13. INGESTION WORKER
-- The worker has no authenticated user: it gets jobs and the
-- account context through SECURITY DEFINER functions restricted
-- to the myexp_worker role.
--
-- The owner of these functions (the role that runs the migrations)
-- must bypass RLS (SUPERUSER or BYPASSRLS) to see
-- jobs from every account.
-- =========================================================

-- Takes the next available job and marks it as "processing".
-- Also recovers abandoned "processing" jobs (worker crashed)
-- older than p_lock_timeout. SKIP LOCKED: concurrent workers
-- never take the same job.
CREATE OR REPLACE FUNCTION app_claim_ingestion_job(
    p_worker_id TEXT,
    p_lock_timeout INTERVAL
)
RETURNS TABLE (
    job_id UUID,
    account_id UUID,
    document_id UUID,
    job_type VARCHAR,
    attempts INTEGER,
    max_attempts INTEGER
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
#variable_conflict use_column
BEGIN
    RETURN QUERY
    WITH next_job AS (
        SELECT j.id
        FROM public.ingestion_jobs j
        WHERE (j.status = 'pending' AND j.available_at <= NOW())
           OR (j.status = 'processing' AND j.locked_at < NOW() - p_lock_timeout)
        ORDER BY j.available_at, j.created_at
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    UPDATE public.ingestion_jobs j
    SET status = 'processing',
        attempts = j.attempts + 1,
        locked_at = NOW(),
        locked_by = p_worker_id,
        updated_at = NOW()
    FROM next_job
    WHERE j.id = next_job.id
    RETURNING j.id, j.account_id, j.document_id, j.job_type, j.attempts, j.max_attempts;
END;
$$;


-- Enables the context of the job's account, only if the job is
-- being processed by this same worker.
CREATE OR REPLACE FUNCTION app_set_worker_context(
    p_job_id UUID,
    p_worker_id TEXT
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_account_id UUID;
BEGIN
    SELECT j.account_id
    INTO v_account_id
    FROM public.ingestion_jobs j
    WHERE j.id = p_job_id
      AND j.status = 'processing'
      AND j.locked_by = p_worker_id;

    IF v_account_id IS NULL THEN
        RAISE EXCEPTION 'Job is not being processed by this worker';
    END IF;

    PERFORM set_config('app.current_account_id', v_account_id::TEXT, TRUE);

    RETURN v_account_id;
END;
$$;

CREATE INDEX idx_ingestion_processing
    ON ingestion_jobs (locked_at)
    WHERE status = 'processing';


-- =========================================================
-- 14. RUNTIME ROLES
--   myexp_app    → API
--   myexp_worker → ingestion worker
-- Both without SUPERUSER/BYPASSRLS (RLS applies) and without
-- CREATE on the schema (DDL only through migrations).
--
-- PASSWORDS ARE NOT SET HERE (secrets are not versioned).
-- After applying, run once with the migration role:
--   ALTER ROLE myexp_app    WITH PASSWORD '<strong password>';
--   ALTER ROLE myexp_worker WITH PASSWORD '<strong password>';
-- =========================================================

DO $$
DECLARE
    role_name TEXT;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['myexp_app', 'myexp_worker']
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format(
                'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                 NOBYPASSRLS NOINHERIT CONNECTION LIMIT 20',
                role_name
            );
        END IF;

        EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), role_name);
        EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', role_name);
        EXECUTE format(
            'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I',
            role_name
        );

        -- Migration version control is not the application's business.
        EXECUTE format('REVOKE ALL ON TABLE public.schema_migrations FROM %I', role_name);

        -- Tables created in the future by the migration role inherit the privileges.
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public
             GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
            role_name
        );
    END LOOP;
END;
$$;

-- The worker does not authenticate anyone: no access to sessions.
REVOKE ALL ON TABLE public.user_sessions FROM myexp_worker;
REVOKE ALL ON TABLE public.platform_access FROM myexp_worker;

-- Context functions: each role runs only its own.
REVOKE ALL ON FUNCTION app_set_account_context(UUID, UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION app_claim_ingestion_job(TEXT, INTERVAL) FROM PUBLIC;
REVOKE ALL ON FUNCTION app_set_worker_context(UUID, TEXT) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION app_set_account_context(UUID, UUID) TO myexp_app;
GRANT EXECUTE ON FUNCTION app_claim_ingestion_job(TEXT, INTERVAL) TO myexp_worker;
GRANT EXECUTE ON FUNCTION app_set_worker_context(UUID, TEXT) TO myexp_worker;
GRANT EXECUTE ON FUNCTION app_current_account_id() TO myexp_app, myexp_worker;
