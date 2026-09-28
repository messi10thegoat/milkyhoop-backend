-- V328 (D3 dashboard v2, 28 Sep 2026) — status "✓ Selesai" manual per pengguna untuk tugas dashboard.
--
-- Satu baris = satu pengguna menyembunyikan satu tugas. Tugas adalah TURUNAN data (dihitung ulang tiap
-- GET /api/dashboard/tasks); kunci tugas memuat nominal/tanggal, jadi bila kondisinya berubah kuncinya
-- berubah dan tugas muncul lagi. Tak ada jurnal/dokumen yang berubah (nol dampak buku).
--
-- tenant_id/user_id = TEXT (spek menulis uuid — keliru: "Tenant".id dan "User".id bertipe text; D0 G.4).
-- CATATAN JUJUR (Law 24/34): gateway memakai peran BYPASSRLS, jadi kebijakan RLS di bawah TIDAK menjaga
-- lalu lintas aplikasi; pagarnya = filter tenant_id + user_id EKSPLISIT di setiap SQL handler.
-- Aditif murni (tabel baru). Maju saja; rollback = DROP tabel (V328__..._ROLLBACK.sql).

CREATE TABLE IF NOT EXISTS dashboard_task_state (
    tenant_id  text        NOT NULL REFERENCES "Tenant"(id) ON DELETE CASCADE,
    user_id    text        NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
    task_key   text        NOT NULL CHECK (length(task_key) BETWEEN 1 AND 300),
    state      text        NOT NULL CHECK (state IN ('dismissed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id, task_key)
);

ALTER TABLE dashboard_task_state ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rls_dashboard_task_state ON dashboard_task_state;
CREATE POLICY rls_dashboard_task_state ON dashboard_task_state
    USING (tenant_id = current_setting('app.tenant_id', true));
