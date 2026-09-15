-- V259: Realtime pembaruan-instan Tahap 1 — sumber kejadian pasca-commit via pg_notify.
-- Trigger HEADER-ONLY (C2): sales_invoices saja di Tahap 1. TIDAK di journal_lines/journal_entries.
-- pg_notify transaksional: hanya terkirim saat COMMIT; rollback => nol event.
-- Payload minimal {tenant_id, tbl, id, op} — tanpa nomor/nama/nominal (D).

CREATE OR REPLACE FUNCTION notify_doc_changed() RETURNS trigger AS $$
DECLARE
  v_tenant text;
  v_id text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    v_tenant := OLD.tenant_id;
    v_id := OLD.id::text;
  ELSE
    v_tenant := NEW.tenant_id;
    v_id := NEW.id::text;
  END IF;
  PERFORM pg_notify(
    'doc_changed',
    json_build_object('tenant_id', v_tenant, 'tbl', TG_TABLE_NAME, 'id', v_id, 'op', TG_OP)::text
  );
  RETURN NULL;  -- AFTER trigger: nilai balik diabaikan
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_notify_doc_changed ON sales_invoices;
CREATE TRIGGER trg_notify_doc_changed
  AFTER INSERT OR UPDATE OR DELETE ON sales_invoices
  FOR EACH ROW EXECUTE FUNCTION notify_doc_changed();

-- Pencabutan SEGERA (E): NOTIFY saat keanggotaan/peran berubah supaya koneksi
-- terbuka bisa diputus tanpa menunggu recheck 60s.
CREATE OR REPLACE FUNCTION notify_membership_changed() RETURNS trigger AS $$
DECLARE
  v_uid text;
  v_tenant text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    v_uid := OLD.user_id::text;
    v_tenant := OLD.tenant_id;
  ELSE
    v_uid := NEW.user_id::text;
    v_tenant := NEW.tenant_id;
  END IF;
  PERFORM pg_notify(
    'membership_changed',
    json_build_object('user_id', v_uid, 'tenant_id', v_tenant, 'op', TG_OP)::text
  );
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_notify_membership_changed ON user_tenant_roles;
CREATE TRIGGER trg_notify_membership_changed
  AFTER INSERT OR UPDATE OR DELETE ON user_tenant_roles
  FOR EACH ROW EXECUTE FUNCTION notify_membership_changed();
