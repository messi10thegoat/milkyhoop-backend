"""BUG-180 (opsi b, putusan pemilik 12 Nov): operasi BOM per pcs yang punya Work Center menanggung overhead = menit/60 x tarif overhead WC (V495 calculate_bom_cost). Per jam tak berubah;
per pcs tanpa WC = overhead 0; upah per pcs tetap cost_per_piece. Semantik angka (52.300) = uji salinan di klon dgn migrasi diterapkan; di sini penjaga bentuk SQL migrasi + rollback + tarif WC ber-tenant."""
import pathlib
import re

import app.routers.bom as B

MIG = pathlib.Path(__file__).resolve().parents[2].parent / "migrations"
SQL = (MIG / "V495__bom_overhead_per_pcs_wc.sql").read_text()
ROLLBACK = (MIG / "V495__bom_overhead_per_pcs_wc_ROLLBACK.sql").read_text()
BERKAS = " ".join(SQL.split())


def _kolom_overhead() -> str:
    m = re.search(r"COALESCE\(SUM\(\s*CASE\s+WHEN labor_mode = 'time_based'.*?END\s*\), 0\)", SQL, re.S)
    assert m
    return " ".join(m.group(0).split())


def test_per_pcs_berwc_menanggung_overhead_dari_menit_dan_tarif():
    k = _kolom_overhead()
    assert "WHEN labor_mode = 'piece_rate' AND work_center_id IS NOT NULL THEN (COALESCE(setup_time_minutes,0) + COALESCE(run_time_minutes,0)) * overhead_rate_per_hour / 60" in k


def test_per_jam_tak_berubah_dan_tak_dobel():
    k = _kolom_overhead()
    assert k.count("WHEN labor_mode = 'time_based' THEN (COALESCE(setup_time_minutes,0) + COALESCE(run_time_minutes,0)) * overhead_rate_per_hour / 60") == 1
    assert k.count("overhead_rate_per_hour") == 2 and "ELSE 0" in k             # tepat dua cabang (per jam, per pcs ber-WC); tak ada penjumlahan ganda per operasi


def test_upah_per_pcs_tetap_cost_per_piece_tanpa_tarif_labor_jam():
    labor = re.search(r"SELECT\s+COALESCE\(SUM\(\s*CASE labor_mode(.*?)END\s*\), 0\)", SQL, re.S).group(1)
    assert "WHEN 'piece_rate' THEN COALESCE(cost_per_piece, 0)" in labor and "labor_rate_per_hour / 60" in labor
    assert labor.count("labor_rate_per_hour") == 1                               # hanya cabang time_based memakai tarif labor jam


def test_total_menjumlah_bahan_labor_overhead_subkontrak_dan_hanya_bom_ini():
    assert "total_cost = v_material_cost + v_labor_cost + v_overhead_cost + v_subcontract_cost" in BERKAS
    assert "WHERE bom_id = p_bom_id" in BERKAS and "WHERE id = p_bom_id" in BERKAS
    assert not re.search(r"\b(BEGIN;|COMMIT;|ROLLBACK;|START TRANSACTION|CONCURRENTLY)\b", SQL)   # apply_mig.sh menolak kendali transaksi


def test_rollback_kembali_ke_v134():
    assert "CREATE OR REPLACE FUNCTION calculate_bom_cost" in ROLLBACK and "piece_rate' AND work_center_id" not in " ".join(ROLLBACK.split())
    assert "WHEN 'time_based' THEN (COALESCE(setup_time_minutes,0) + COALESCE(run_time_minutes,0)) * overhead_rate_per_hour / 60" in " ".join(ROLLBACK.split())


def test_tarif_wc_yang_dipakai_per_pcs_tetap_bersaring_tenant():
    import inspect
    assert "FROM work_centers WHERE id = $1 AND tenant_id = $2" in inspect.getsource(B.tarif_work_center)
    for fn in (B.create_bom, B.add_operation, B.update_bom):
        assert 'tarif_work_center(conn, ctx["tenant_id"]' in inspect.getsource(fn)
