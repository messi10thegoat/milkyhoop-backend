"""AS-GP2 (24 Okt 2026): idempotensi aksi Faktur Pembelian -- POST /{id}/post, POST /{id}/void, DELETE /{id}, PATCH /v2/{id}. Kunci klien saja
(idem_buat.mulai_aksi, pola BILL_PAY): di DALAM transaksi SEBELUM kunci domain, respons dicatat hanya di jalur sukses; awalan PER AKSI (FE CW =
SATU kunci per buka halaman). PATCH v2: tagihan FOR UPDATE SEBELUM blok_ubah (dulu cek draf di luar transaksi tanpa kunci = TOCTOU)."""
import ast
import asyncio
from pathlib import Path

import pytest

from app.services import bills_service as BS

APP = Path(__file__).resolve().parents[2] / "app"
SVC = (APP / "services/bills_service.py").read_text()
RTR = (APP / "routers/bills.py").read_text()
AKSI = {"post_bill": "BILL_POST", "void_bill": "BILL_VOID", "delete_bill": "BILL_DELETE", "update_bill_v2": "BILL_UPDATE"}
SUKSES = {"post_bill": "Bill posted successfully", "void_bill": "Bill voided successfully", "delete_bill": "Bill deleted successfully",
          "update_bill_v2": "Bill updated successfully"}


def _metode(src, kelas_atau_none, nama):
    t = ast.parse(src)
    for n in ast.walk(t):
        if isinstance(n, ast.AsyncFunctionDef) and n.name == nama:
            if kelas_atau_none is None or any(isinstance(p, ast.ClassDef) and n in p.body for p in ast.walk(t)):
                return ast.get_source_segment(src, n)
    raise AssertionError(nama)


def _svc(nama):
    t = ast.parse(SVC)
    kelas = next(n for n in t.body if isinstance(n, ast.ClassDef) and n.name == "BillsService")
    f = next(n for n in kelas.body if isinstance(n, ast.AsyncFunctionDef) and n.name == nama)
    return ast.get_source_segment(SVC, f)


@pytest.mark.parametrize("nama", list(AKSI))
def test_idem_sebelum_kunci_domain_dan_simpan_di_jalur_sukses(nama):
    f, awal = _svc(nama), AKSI[nama]
    i_idem = f.index(f'_idem_mulai(conn, idem, "{awal}", bill_id)')
    i_tx = f.index("conn.transaction()")
    kunci = [f.index(k) for k in ("pg_advisory_xact_lock", "FOR UPDATE") if k in f]
    assert i_tx < i_idem < min(kunci), (nama, i_tx, i_idem, kunci)
    assert "if _lama is not None:\n" in f[i_idem:i_idem + 200] and "return _lama" in f[i_idem:i_idem + 260]
    i_simpan = f.index(f'_idem_simpan(conn, idem, _kp, _sidik, "{awal}"')
    assert SUKSES[nama] in f[i_simpan:i_simpan + 300]  # yang dicatat = respons SUKSES (galat tak pernah dicatat)
    assert "idem: Optional[Dict[str, Any]] = None" in f  # tanpa kunci = perilaku lama


def test_awalan_per_aksi_unik():
    assert len(set(AKSI.values())) == 4
    for awal in AKSI.values():
        assert SVC.count(f'"{awal}", bill_id)') == 1


def test_void_pracek_sesudah_idem_di_dalam_transaksi():
    f = _svc("void_bill")
    assert f.index('_idem_mulai(conn, idem, "BILL_VOID"') < f.index('"Bill is already voided"') < f.index('f"BILL_VOID:{str(bill_id)}"')
    assert f.index("conn.transaction()") < f.index('"Bill is already voided"')


def test_ubah_v2_blok_sesudah_kunci_baris_di_dalam_transaksi():
    f = _svc("update_bill_v2")
    i_tx, i_fu, i_blok = f.index("conn.transaction()"), f.index("FOR UPDATE"), f.index("await TR.blok_ubah(")
    assert i_tx < i_fu < i_blok
    assert f.count("await TR.blok_ubah(") == 1


def test_hapus_respons_dicatat_di_transaksi_objek_sesudah_commit():
    f = _svc("delete_bill")
    assert f.index('_idem_simpan(conn, idem, _kp, _sidik, "BILL_DELETE"') < f.index("hapus_objek_sesudah_commit(")
    assert f.rstrip().endswith("return _hasil")


@pytest.mark.parametrize("nama,awal", [("post_bill", "BILL_POST"), ("void_bill", "BILL_VOID"), ("delete_bill", "BILL_DELETE"),
                                       ("update_bill_v2", "BILL_UPDATE")])
def test_rute_membaca_kunci_klien_dan_meneruskan_idem(nama, awal):
    f = _metode(RTR, None, nama)
    assert "response: Response = None" in f
    assert f.index("kunci = idem_buat.kunci_dari(request)") < f.index(f"service.{nama}(")
    assert 'idem={"ctx": ctx, "kunci": kunci' in f and "if kunci else None" in f


def test_pembantu_tanpa_kunci_tak_menyentuh_db():
    class Jebakan:
        def __getattr__(self, n):
            raise AssertionError("tanpa kunci: tak boleh ada kueri")
    assert asyncio.run(BS._idem_mulai(Jebakan(), None, "BILL_POST", "x")) == (None, None, None)
    assert asyncio.run(BS._idem_mulai(Jebakan(), {"kunci": None}, "BILL_POST", "x")) == (None, None, None)
    h = {"success": True}
    assert asyncio.run(BS._idem_simpan(Jebakan(), None, None, None, "BILL_POST", h, "x")) is h


def test_kontrol_merah_urutan_salah_tertangkap():
    palsu = '''async def post_bill(self, idem=None):
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", "k")
            _kp, _sidik, _lama = await _idem_mulai(conn, idem, "BILL_POST", bill_id)
'''
    with pytest.raises(AssertionError):
        assert palsu.index("_idem_mulai(") < palsu.index("pg_advisory_xact_lock")
