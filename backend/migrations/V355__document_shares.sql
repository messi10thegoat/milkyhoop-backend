-- V355 (P5 SO-dokumen, 1 Okt 2026, pemilik "lanjut p5"): tautan publik dokumen + lacak terkirim/dibuka.
-- 02-DATA-DAN-API §Kirim & lacak. Token TIDAK disimpan -- hanya sha256(token) (bocor DB != bocor tautan); token 256-bit
-- dikembalikan SEKALI saat dibuat. Kedaluwarsa 30 hari; bisa dicabut. Aditif: tabel baru, nol perubahan tabel lama.
CREATE TABLE IF NOT EXISTS document_shares (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        text NOT NULL,
    kind             text NOT NULL CHECK (kind IN ('rekap', 'quotation', 'proforma', 'receipt', 'delivery', 'invoice')),
    doc_id           uuid NOT NULL,
    token_hash       char(64) NOT NULL UNIQUE,
    channel          text NOT NULL CHECK (channel IN ('wa', 'email', 'link')),
    created_by       uuid,
    sent_at          timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz NOT NULL,
    first_viewed_at  timestamptz,
    last_viewed_at   timestamptz,
    view_count       integer NOT NULL DEFAULT 0,
    revoked_at       timestamptz,
    revoked_by       uuid
);
CREATE INDEX IF NOT EXISTS idx_document_shares_doc ON document_shares (tenant_id, kind, doc_id);

-- RLS (spek). Gateway tersambung sebagai peran BYPASSRLS -> pagar SEBENARNYA = filter tenant eksplisit di tiap kueri;
-- kebijakan ini lapis kedua untuk peran lain.
ALTER TABLE document_shares ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'document_shares' AND policyname = 'document_shares_tenant') THEN
        CREATE POLICY document_shares_tenant ON document_shares
            USING (tenant_id = current_setting('app.tenant_id', true));
    END IF;
END $$;
