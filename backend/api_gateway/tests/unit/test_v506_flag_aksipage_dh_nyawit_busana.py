"""V506 (19 Nov 2026): aksipage_daftar_harga_v1 dinyalakan di nyawit + busana SAJA (flag-only; grapgrap = migrasi terpisah sesudah r56 FE live)."""
from pathlib import Path

MIG = Path(__file__).resolve().parents[3] / "migrations"
BERKAS = "V506__flag_aksipage_daftar_harga_nyawit_busana"


def test_v506_hanya_flag_aksipage_dh_dua_tenant_dan_tak_bocor():
    s = MIG.joinpath(BERKAS + ".sql").read_text()
    assert "'aksipage_daftar_harga_v1'" in s
    assert "t.id IN ('pt-kita-nyawit-bersama', 'pt-busana-indah-semesta')" in s
    assert "grapgrap-manado'" not in s.split("DO $$")[0]                       # tak ada INSERT grapgrap
    assert "ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true" in s
    assert "<> 2 THEN" in s                                                    # penjaga: tepat 2 tenant terpasang
    assert "bocor ke tenant lain" in s and "'kaos-biru-konveksi', 'pt-kita-nyawit-bersama', 'pt-busana-indah-semesta'" in s
    for terlarang in ("journal", "UPDATE products", "ALTER TABLE", "CREATE TABLE", "DROP "):   # flag-only: tak menyentuh buku/skema
        assert terlarang.lower() not in s.lower()


def test_v506_rollback_hanya_dua_tenant_flag_itu():
    r = MIG.joinpath(BERKAS + "_ROLLBACK.sql").read_text()
    kode = "\n".join(x for x in r.splitlines() if not x.startswith("--"))
    assert kode.count("DELETE FROM tenant_features") == 1
    assert "tenant_id IN ('pt-kita-nyawit-bersama', 'pt-busana-indah-semesta')" in kode
    assert "feature = 'aksipage_daftar_harga_v1'" in kode
    assert "kaos-biru-konveksi" not in kode and "grapgrap" not in kode
