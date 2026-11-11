-- =========================================================
-- Deduplication by access key and review of doubtful readings
-- =========================================================

-- Access key (44 digits, check digit verified) of the NF-e/NFC-e/NF3e/NFCom.
-- Uniquely identifies the invoice: another photo of the same receipt, or the
-- photo + the XML of the same NFC-e, have the same key even as different files.
ALTER TABLE documents ADD COLUMN access_key VARCHAR(44);

-- Receipts already read: the key comes from the extraction. If the account already has duplicates,
-- only the first (oldest) one gets the key, so the unique index can be created.
WITH keyed AS (
    SELECT id,
           extracted_data -> 'result' -> 'metadata' ->> 'access_key' AS access_key,
           ROW_NUMBER() OVER (
               PARTITION BY account_id, extracted_data -> 'result' -> 'metadata' ->> 'access_key'
               ORDER BY created_at, id
           ) AS position
    FROM documents
    WHERE extracted_data -> 'result' -> 'metadata' ->> 'access_key' ~ '^[0-9]{44}$'
)
UPDATE documents d
SET access_key = keyed.access_key
FROM keyed
WHERE d.id = keyed.id AND keyed.position = 1;

-- Database guarantee: two workers at the same time never store the same receipt twice.
CREATE UNIQUE INDEX uq_documents_access_key
    ON documents (account_id, access_key)
    WHERE access_key IS NOT NULL;

-- Doubtful reading (items do not add up to the total, count differs from the printed one,
-- possible duplicate without a key): left OUT of the totals until someone confirms it.
ALTER TABLE financial_entries ADD COLUMN needs_review BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX idx_financial_entries_needs_review
    ON financial_entries (account_id)
    WHERE needs_review;
