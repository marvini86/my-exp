DROP INDEX IF EXISTS idx_financial_entries_needs_review;
ALTER TABLE financial_entries DROP COLUMN IF EXISTS needs_review;

DROP INDEX IF EXISTS uq_documents_access_key;
ALTER TABLE documents DROP COLUMN IF EXISTS access_key;
