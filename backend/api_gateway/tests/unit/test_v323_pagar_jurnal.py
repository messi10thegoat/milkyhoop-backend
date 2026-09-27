"""V323 (28 Sep 2026): Law 2 — status jurnal POSTED tak boleh berubah; Law 22 — UNIQUE chain_sequence POSTED.
Perilaku DB dibuktikan di journey v323_void (salinan prod: UPDATE POSTED->VOID/DRAFT & nomor ganda DITOLAK, semua jalur
void nyata + 10 posting paralel lulus). Di sini: isi migrasi & rollback (literal)."""
import os

MIG = os.path.join(os.path.dirname(__file__), "..", "..", "..", "migrations")
BARU = open(os.path.join(MIG, "V323__jurnal_posted_tak_berubah_status.sql")).read()
MUNDUR = open(os.path.join(MIG, "V323__jurnal_posted_tak_berubah_status_ROLLBACK.sql")).read()


def _kode(sql):
    return "\n".join(l for l in sql.split("\n") if not l.strip().startswith("--"))


def test_status_posted_tak_berubah():
    k = " ".join(_kode(BARU).split())
    assert "IF OLD.status = 'POSTED' THEN IF NEW.status IS DISTINCT FROM 'POSTED' THEN RAISE EXCEPTION" in k
    assert "USING ERRCODE = 'check_violation'" in k


def test_izin_void_lama_dibuang():
    k = _kode(BARU)
    assert "NEW.status = 'VOID'" not in k and "Allow legitimate void flow" not in k


def test_pautan_pembalik_tetap_diizinkan():
    k = " ".join(_kode(BARU).split())
    assert "NEW.reversed_by_id IS DISTINCT FROM OLD.reversed_by_id" in k and "AND NEW.status = OLD.status THEN RETURN NEW;" in k


def test_unique_nomor_urut_posted():
    k = " ".join(_kode(BARU).split())
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_je_chain_seq_posted ON journal_entries (tenant_id, chain_sequence) WHERE status = 'POSTED';" in k


def test_rollback_memulihkan_fungsi_lama_dan_melepas_indeks():
    assert "Allow legitimate void flow" in MUNDUR and "DROP INDEX IF EXISTS uq_je_chain_seq_posted;" in MUNDUR
