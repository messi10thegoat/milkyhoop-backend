"""V444 (23 Okt 2026): setiap peran akun yang DIRUJUK kode (AccountRole.X) harus DISEMAI untuk tenant baru (seed_default_account_roles),
supaya tenant baru tak lahir tanpa peran yang dipakai jalur posting (temuan: EMPLOYEE_ADVANCE, BANK_ADJUSTMENT_GAIN/LOSS,
REVENUE_SALES_SERVICE dulu hanya diisi per tenant lama). PERAN_SEMAI = daftar baris VALUES fungsi semai sesudah V444; tiap peran di
daftar harus benar-benar muncul sebagai baris semai di migrasi (daftar tak boleh mengklaim lebih dari DB)."""
import asyncio
import re
from pathlib import Path

from app.services import role_resolver as RR

APP = Path(RR.__file__).resolve().parents[1]
MIG = APP.parents[1] / "migrations"


def _dirujuk_kode_semai() -> set:
    out = set()
    for f in APP.rglob("*.py"):
        if f.name == "role_resolver.py":
            continue
        out |= set(re.findall(r"AccountRole\.([A-Z][A-Z0-9_]*)", f.read_text(errors="ignore")))
    return {getattr(RR.AccountRole, n) for n in out}  # nama atribut -> NILAI peran (yang disimpan account_roles)


def test_setiap_peran_yang_dirujuk_kode_disemai():
    hilang = sorted(_dirujuk_kode_semai() - RR.PERAN_SEMAI)
    assert not hilang, f"peran dirujuk kode tapi TIDAK disemai untuk tenant baru: {hilang} -> tambah baris semai (migrasi) + PERAN_SEMAI"


def test_kontrol_merah_peran_baru_tak_disemai_tertangkap():
    dirujuk = _dirujuk_kode_semai() | {"PERAN_UJI_BARU_TAK_DISEMAI"}
    assert sorted(dirujuk - RR.PERAN_SEMAI) == ["PERAN_UJI_BARU_TAK_DISEMAI"]


def test_daftar_semai_ada_di_katalog_dan_di_baris_semai_migrasi():
    assert RR.PERAN_SEMAI <= RR._CATALOG
    teks = "\n".join(f.read_text(errors="ignore") for f in MIG.glob("V*.sql") if "ROLLBACK" not in f.name)
    baris = set(re.findall(r"\(\s*'{1,2}([A-Z][A-Z0-9_]*)'{1,2},\s+'{1,2}\d-\d{5}'{1,2},\s*(?:false|true)", teks))
    assert RR.PERAN_SEMAI <= baris, sorted(RR.PERAN_SEMAI - baris)
    for p in ("EMPLOYEE_ADVANCE", "BANK_ADJUSTMENT_GAIN", "BANK_ADJUSTMENT_LOSS", "REVENUE_SALES_SERVICE"):
        assert f"''{p}''" in (MIG / "V444__peran_semai_lengkap.sql").read_text()


class _PoolPeran:
    def __init__(self, rows=None, galat=None):
        self.rows, self.galat, self.sql = rows or [], galat, []

    def acquire(self):
        luar = self

        class CM:
            async def __aenter__(s):
                class K:
                    async def fetch(k, sql, *a):
                        if luar.galat:
                            raise luar.galat
                        luar.sql.append((sql, a))
                        return luar.rows
                return K()

            async def __aexit__(s, *e):
                return False
        return CM()


def test_peringatan_startup_satu_kueri_baca_dan_tak_pernah_jatuh(caplog):
    p = _PoolPeran(rows=[{"tenant_id": "t-baru", "peran": ["BANK_ADJUSTMENT_GAIN"]}])
    out = asyncio.run(RR.laporkan_peran_tak_terpetakan(p))
    assert out == [("t-baru", ["BANK_ADJUSTMENT_GAIN"])] and len(p.sql) == 1
    s, a = p.sql[0]
    assert "account_roles" in s and set(a[0]) == RR.PERAN_SEMAI
    for t in ("INSERT", "UPDATE ", "DELETE", "FOR UPDATE"):
        assert t not in s.upper()
    assert any("t-baru" in r.getMessage() for r in caplog.records)
    assert asyncio.run(RR.laporkan_peran_tak_terpetakan(_PoolPeran(galat=RuntimeError("db mati")))) == []


def test_lifespan_menjadwalkan_cek_sebagai_tugas_latar():
    m = (APP / "main.py").read_text()
    assert "asyncio.create_task(_cek_peran(_pool_idem))" in m
