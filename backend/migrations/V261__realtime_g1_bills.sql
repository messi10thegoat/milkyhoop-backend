-- V261: Realtime Tahap 2 G1 — trigger doc_changed di bills + bill_payments_v2.
-- Fungsi notify_doc_changed() sudah ada (V259, generik via TG_TABLE_NAME) — TAK dibuat ulang.
-- Header-only: bills (amount_paid/status_v2 ter-UPDATE saat bayar → tampilan segar) dan
-- bill_payments_v2 (INSERT buat + UPDATE void). pg_notify transaksional: rollback = 0 event.
-- Additive & idempotent (DROP IF EXISTS + CREATE). TAK menyentuh journal_lines/journal_entries.

DROP TRIGGER IF EXISTS trg_notify_doc_changed ON bills;
CREATE TRIGGER trg_notify_doc_changed
  AFTER INSERT OR UPDATE OR DELETE ON bills
  FOR EACH ROW EXECUTE FUNCTION notify_doc_changed();

DROP TRIGGER IF EXISTS trg_notify_doc_changed ON bill_payments_v2;
CREATE TRIGGER trg_notify_doc_changed
  AFTER INSERT OR UPDATE OR DELETE ON bill_payments_v2
  FOR EACH ROW EXECUTE FUNCTION notify_doc_changed();
