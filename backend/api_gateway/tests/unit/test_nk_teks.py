"""NK-TEKS (3 Nov 2026, tiket CONVERSATIONAL via MASTER): pratinjau aksi Nota Kredit (post/void/apply/unapply/refund) membawa
before/after.status_text + sisa_keadaan dari penentu YANG SAMA dengan daftar/detail (nota_kredit_daftar.status_text_nk/keadaan_nk;
NK bertaut 'posted' -> Terpakai), simetri ND. ADITIF: kunci lama tetap. after memakai tautan faktur SESUDAH aksi (apply mengisi,
unapply mengosongkan == penulis credit_notes). Plus: kalimat 'sudah dipakai' SATU fungsi untuk pelanggan dan vendor."""
import ast
import asyncio
import inspect
import uuid
from decimal import Decimal

import pytest

from app.services import nota_kredit_daftar as ND
from app.services import nota_kredit_rencana as NKR
from app.services import pelanggan_unik as PU
from app.services import teks_galat as tg
from app.services import vendor_unik as VU
from app.services.galat_berkode import GalatBerkode

FID = uuid.uuid4()


def _k(status, detail=None, fid=None, **x):
    return {"exists": True, "status": status, "status_detail": detail or status, "original_invoice_id": fid, **x}


@pytest.mark.parametrize("status,detail,fid,teks,keadaan", [
    ("posted", "posted", FID, "Terpakai", "terpakai"),      # NK bertaut terbit = kredit sudah memotong faktur
    ("posted", "posted", None, "Terbit", "utuh"),
    ("partial", "partial", None, "Sebagian terpakai", "sebagian"),
    ("applied", "applied", None, "Terpakai", "terpakai"),
    ("void", "void", FID, "Batal", "batal"),
    ("draft", "draft", FID, "Draf", "draf"),
])
def test_teks_keadaan_sama_dengan_penentu_daftar(status, detail, fid, teks, keadaan):
    k = NKR.teks_keadaan_nk(_k(status, detail, fid, remaining=1.0))
    assert (k["status_text"], k["sisa_keadaan"]) == (teks, keadaan)
    assert k["status_text"] == ND.status_text_nk(status, detail, fid) and k["sisa_keadaan"] == ND.keadaan_nk(status, detail, fid)
    assert k["remaining"] == 1.0 and k["status"] == status  # kunci lama utuh


def test_tak_ada_dokumen_tak_diberi_teks():
    for k in ({"exists": False}, {"exists": False, "original_invoice_id": None}):
        d = NKR.teks_keadaan_nk(dict(k))
        assert "status_text" not in d and "sisa_keadaan" not in d and d == k


def test_sesudah_nk_hanya_dipakai_pratinjau():
    """Syarat MASTER: _sesudah_nk murni pratinjau -> semua pemanggil di app/ berada di dalam pratinjau_aksi_nk (AST)."""
    import pathlib
    akar = pathlib.Path(NKR.__file__).resolve().parents[1]
    pemanggil = []
    for f in akar.rglob("*.py"):
        pohon = ast.parse(f.read_text())
        for fn in ast.walk(pohon):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for c in ast.walk(fn):
                    if isinstance(c, ast.Call) and (getattr(c.func, "id", None) == "_sesudah_nk" or getattr(c.func, "attr", None) == "_sesudah_nk"):
                        pemanggil.append((f.name, fn.name))
    luar = {p for p in pemanggil if p != ("nota_kredit_rencana.py", "pratinjau_aksi_nk")}
    assert len(pemanggil) == 3 and not luar, pemanggil


def _cn(status="posted", diterapkan=0, kembali=0, fid=None):
    return {"status": status, "total_amount": Decimal("100000"), "amount_applied": diterapkan, "amount_refunded": kembali,
            "original_invoice_id": fid}


def test_after_memakai_tautan_sesudah_aksi():
    sb = _k("posted", fid=None, remaining=100000.0)
    apply_ = NKR._sesudah_nk(dict(sb), _cn(), Decimal("100000"), 0, FID)
    assert apply_["original_invoice_id"] == FID
    assert NKR.teks_keadaan_nk(apply_)["status_text"] == "Terpakai"
    sb2 = _k("applied", fid=FID)
    unapply = NKR._sesudah_nk(dict(sb2), _cn("applied", 100000, 0, FID), Decimal("-100000"), 0, None)
    assert unapply["original_invoice_id"] is None
    assert (NKR.teks_keadaan_nk(unapply)["status_text"], unapply["sisa_keadaan"]) == ("Terbit", "utuh")
    refund = NKR._sesudah_nk(dict(sb), _cn(), 0, Decimal("100000"), None)
    assert refund["original_invoice_id"] is None and NKR.teks_keadaan_nk(refund)["sisa_keadaan"] == "dikembalikan"


def _panggil_sebelum_pop(fn) -> bool:
    """Di loop `for k in (sebelum, sesudah)`: teks_keadaan_nk(k) dipanggil SEBELUM k.pop("original_invoice_id")."""
    pohon = ast.parse(inspect.getsource(fn).replace("\n    ", "\n", 0))
    for n in ast.walk(pohon):
        if isinstance(n, ast.For) and isinstance(n.iter, ast.Tuple) and [getattr(e, "id", None) for e in n.iter.elts] == ["sebelum", "sesudah"]:
            urut = []
            for st in n.body:
                for c in ast.walk(st):
                    if isinstance(c, ast.Call):
                        f = c.func
                        if isinstance(f, ast.Name) and f.id == "teks_keadaan_nk":
                            urut.append("teks")
                        elif isinstance(f, ast.Attribute) and f.attr == "pop" and c.args and getattr(c.args[0], "value", None) == "original_invoice_id":
                            urut.append("pop")
            return urut == ["teks", "pop"]
    return False


@pytest.mark.parametrize("fn", [NKR.pratinjau_aksi_nk, NKR.pratinjau_post_void_nk])
def test_kedua_pratinjau_memberi_teks_sebelum_tautan_dibuang(fn):
    assert _panggil_sebelum_pop(fn)


# ---- kalimat 'sudah dipakai': satu fungsi pelanggan + vendor ----
T = "tenant-uji"


class Conn:
    def __init__(self, baris):
        self.baris = baris

    async def fetchrow(self, sql, *a):
        return self.baris

    async def fetchval(self, sql, *a):
        return self.baris and self.baris["id"]


def _galat(c) -> GalatBerkode:
    with pytest.raises(GalatBerkode) as e:
        asyncio.run(c)
    return e.value


@pytest.mark.parametrize("aktif", [True, False])
def test_vendor_dan_pelanggan_satu_kalimat(aktif):
    r = {"id": uuid.uuid4(), "is_active": aktif}
    v = _galat(VU.tolak_nama_dipakai(Conn(r), T, "A")).detail
    p = _galat(PU.tolak_nama_dipakai(Conn(r), T, "A")).detail
    assert v == tg.sudah_dipakai("vendor", "nama", "A", aktif) and p == tg.sudah_dipakai("pelanggan", "nama", "A", aktif)
    assert v.replace("vendor", "X") == p.replace("pelanggan", "X")  # bentuk identik, beda hanya pihak


def test_kalimat_persis():
    assert tg.sudah_dipakai("vendor", "nama", "PT A") == 'Nama vendor "PT A" sudah dipakai vendor lain. Gunakan nama lain.'
    assert tg.sudah_dipakai("vendor", "kode", "V1", False) == \
        'Kode vendor "V1" sudah dipakai vendor nonaktif — aktifkan lagi dari Kontak atau gunakan kode lain.'
    assert tg.sudah_dipakai("pelanggan", "nama", "Budi") == 'Nama pelanggan "Budi" sudah dipakai pelanggan lain. Gunakan nama lain.'
    k = _galat(VU.tolak_kode_dipakai(Conn({"id": uuid.uuid4(), "is_active": True}), T, "V7")).detail
    assert k == 'Kode vendor "V7" sudah dipakai vendor lain. Gunakan kode lain.'
    kn = _galat(VU.tolak_kode_dipakai(Conn({"id": uuid.uuid4(), "is_active": False}), T, "V8"))
    assert kn.detail == 'Kode vendor "V8" sudah dipakai vendor nonaktif — aktifkan lagi dari Kontak atau gunakan kode lain.'
    assert kn.badan()["code"] == "KODE_VENDOR_DIPAKAI" and kn.badan()["field"] == "code"  # kunci code/field tetap
    c = _galat(PU.tolak_nomor_dipakai(Conn({"id": uuid.uuid4()}), T, "C7")).detail
    assert c == 'Kode pelanggan "C7" sudah dipakai pelanggan lain. Gunakan kode lain.'


def test_tak_ada_kalimat_dipakai_tulisan_tangan():
    """pelanggan_unik/vendor_unik: tiap GalatBerkode berkalimat lewat tg.sudah_dipakai (AST: argumen ke-3 = panggilan itu)."""
    for mod in (PU, VU):
        n = 0
        for c in ast.walk(ast.parse(inspect.getsource(mod))):
            if isinstance(c, ast.Call) and getattr(c.func, "id", None) == "GalatBerkode":
                a = c.args[2]
                assert isinstance(a, ast.Call) and isinstance(a.func, ast.Attribute) and a.func.attr == "sudah_dipakai", mod.__name__
                n += 1
        assert n == 2, mod.__name__
