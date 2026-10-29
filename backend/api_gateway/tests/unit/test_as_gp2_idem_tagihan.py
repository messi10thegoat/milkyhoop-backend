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
SUKSES = {"post_bill": "Faktur pembelian diposting.", "void_bill": "Faktur pembelian dibatalkan.", "delete_bill": "Faktur pembelian dihapus.",
          "update_bill_v2": "Faktur pembelian diubah."}


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


def _fn_ast(nama):
    t = ast.parse(SVC)
    kelas = next(n for n in t.body if isinstance(n, ast.ClassDef) and n.name == "BillsService")
    return next(n for n in kelas.body if isinstance(n, ast.AsyncFunctionDef) and n.name == nama)


# B1b-2 (29 Okt): isi hapus draf dipindah ke inti modul hapus_draf_tagihan_core (dipakai juga hapus massal). Kunci domain (FOR UPDATE)
# kini di DALAM inti -> untuk delete_bill "kunci" = baris PANGGILAN inti (idem harus mendahuluinya) DAN inti wajib memuat SQL FOR UPDATE.
INTI = {"delete_bill": "hapus_draf_tagihan_core"}


def _kunci_sql(fn, doc=None):
    return [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str) and n is not doc
            and ("pg_advisory_xact_lock" in n.value or "FOR UPDATE" in n.value)]


@pytest.mark.parametrize("nama", list(AKSI))
def test_idem_sebelum_kunci_domain_dan_simpan_di_jalur_sukses(nama):
    f, awal = _svc(nama), AKSI[nama]
    fn = _fn_ast(nama)
    doc = fn.body[0].value if isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant) else None
    # baris SQL kunci NYATA (konstanta string, bukan docstring/komentar) vs baris panggilan _idem_mulai
    kunci = _kunci_sql(fn, doc)
    if nama in INTI:  # kunci domain di inti: baris panggilan inti = titik kunci; inti sendiri wajib mengunci (konstanta SQL, bukan docstring)
        kunci += [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == INTI[nama]]
        inti = next(n for n in ast.parse(SVC).body if isinstance(n, ast.AsyncFunctionDef) and n.name == INTI[nama])
        doc_inti = inti.body[0].value if isinstance(inti.body[0], ast.Expr) and isinstance(inti.body[0].value, ast.Constant) else None
        assert _kunci_sql(inti, doc_inti), INTI[nama]
    idem = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_idem_mulai"]
    tx = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "transaction"]
    assert len(idem) == 1 and kunci and min(tx) < idem[0] < min(kunci), (nama, tx, idem, kunci)
    i_idem = f.index(f'_idem_mulai(conn, idem, "{awal}", bill_id)')
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
    fn = _fn_ast("void_bill")
    idem = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_idem_mulai"]
    pracek = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Constant) and n.value == "Faktur pembelian sudah dibatalkan."]
    adv = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str) and "pg_advisory_xact_lock" in n.value]
    tx = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "transaction"]
    assert min(tx) < idem[0] < min(pracek) < min(adv), (tx, idem, pracek, adv)


def test_ubah_v2_blok_sesudah_kunci_baris_di_dalam_transaksi():
    f = _svc("update_bill_v2")
    fn = _fn_ast("update_bill_v2")
    fu = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str) and "FOR UPDATE" in n.value]
    blok = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "blok_ubah"]
    tx = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "transaction"]
    assert fu and blok and min(tx) < min(fu) < min(blok)
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
