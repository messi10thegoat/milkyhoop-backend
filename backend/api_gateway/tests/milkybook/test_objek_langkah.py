"""Objek langkah di transkrip (persetujuan MASTER 25-26 Okt): `objek` <= 40 karakter, nomor dokumen hanya dari data
server, tidak masuk audit / tampilan (hanya dipancarkan hidup). Additif: langkah tanpa pemeta tak berubah."""
from dataclasses import replace

import pytest

from app.milkybook.inti import aliran
from app.milkybook.inti.audit import AuditMemori
from app.milkybook.inti.pelaksana import JawabanBE
from app.milkybook.inti.putaran import MAKS_OBJEK, Putaran, Sesi, _dengan_objek
from app.milkybook.inti.registri import Kelas, Paket, Registri
from app.milkybook.paket import penawaran as P

from .conftest import ModelPalsu, izin_semua, panggil, teks
from .test_inti import T_CARI, T_HITUNG, T_UBAH, T_UBAH_PR, be_standar
from .test_ringkasan_dokumen import DETAIL, T_DETAIL, _ringkas


def _nomor(m, j):
    return (j or {}).get("dokumen", {}).get("nomor") if j and j.get("dokumen") else None


CARI = replace(T_CARI, objek=lambda m, j: f"“{m['q']}”")
DETAIL_OB = replace(T_DETAIL, objek=lambda m, j: (j or {}).get("isi", {}).get("data", {}).get("nomor"))
UBAH = replace(T_UBAH, ringkasan_dari="detail_x", ringkas_dokumen=_ringkas, objek=_nomor)


async def _langkah(naskah, be=None):
    reg = Registri([Paket("contoh", [CARI, T_HITUNG, T_UBAH_PR, UBAH, DETAIL_OB])])
    audit = AuditMemori()
    p = Putaran(reg, ModelPalsu(naskah), audit, gap_mendarat=("G1",))
    a = aliran.Aliran()
    tok = aliran.pasang(a)
    try:
        ev = await p.giliran(Sesi("s1", "kaos-biru-konveksi", "u1"), "x", be or be_standar(), izin_semua)
    finally:
        aliran.lepas(tok)
    keluar = []
    while not a.antrian.empty():
        e = a.antrian.get_nowait()
        if e and e.get("jenis") == "langkah":
            keluar.append(e)
    return keluar, ev, audit


async def test_objek_kata_kunci_di_mulai_dan_selesai():
    lk, _, _ = await _langkah([panggil(("cari_x", {"q": "Flowry"})), teks("ok")])
    assert [(e["status"], e.get("objek")) for e in lk] == [("mulai", "“Flowry”"), ("selesai", "“Flowry”")]


async def test_nomor_dokumen_hanya_sesudah_dari_server():
    be = be_standar()
    be.rute[("GET", "/api/x/a1")] = JawabanBE(200, DETAIL)
    lk, _, _ = await _langkah([panggil(("detail_x", {"id": "a1"})), teks("ok")], be)
    assert [(e["status"], e.get("objek")) for e in lk] == [("mulai", None), ("selesai", "X-1")]
    assert "objek" not in lk[0]


async def test_write_objek_dari_ringkasan_kartu():
    be = be_standar()
    be.rute[("GET", "/api/x/a1")] = JawabanBE(200, DETAIL)
    lk, _, _ = await _langkah([panggil(("ubah_x", {"id": "a1", "jumlah": 5})), teks("Tinjau.")], be)
    assert lk[-1]["status"] == "selesai" and lk[-1]["objek"] == "X-1"


async def test_langkah_galat_tanpa_objek():
    be = be_standar()
    be.rute[("GET", "/api/x/a1")] = JawabanBE(500, {"data": {"nomor": "X-1"}})
    lk, _, _ = await _langkah([panggil(("detail_x", {"id": "a1"})), teks("gagal")], be)
    assert lk[-1]["status"] == "galat" and "objek" not in lk[-1]


async def test_objek_tidak_masuk_audit_maupun_tampilan():
    be = be_standar()
    be.rute[("GET", "/api/x/a1")] = JawabanBE(200, DETAIL)
    lk, ev, audit = await _langkah([panggil(("cari_x", {"q": "RahasiaPelanggan"}), ("detail_x", {"id": "a1"})), teks("ok")], be)
    assert any(e.get("objek") for e in lk)
    teks_audit = repr([vars(b) for b in audit.baris])
    assert "RahasiaPelanggan" not in teks_audit and "X-1" not in teks_audit
    assert not [e for e in ev if e.get("jenis") == "langkah" or "objek" in e]


@pytest.mark.parametrize("hasil,harap", [
    ("  QUO-1  \n x ", "QUO-1 x"),
    ("a" * 60, "a" * (MAKS_OBJEK - 1) + "…"),
    ("", None), ("   ", None), (None, None), (123, None),
])
def test_dengan_objek_dirapikan_dan_dibatasi(hasil, harap):
    t = replace(T_CARI, objek=lambda m, j: hasil)
    out = _dengan_objek({"jenis": "langkah"}, t, {}, None)
    assert out.get("objek") == harap
    if harap:
        assert len(out["objek"]) <= MAKS_OBJEK


def test_pemeta_meledak_tanpa_objek_dan_tanpa_tool():
    t = replace(T_CARI, objek=lambda m, j: 1 / 0)
    assert _dengan_objek({"jenis": "langkah"}, t, {}, None) == {"jenis": "langkah"}
    assert _dengan_objek({"jenis": "langkah"}, None, {}, None) == {"jenis": "langkah"}
    assert _dengan_objek({"jenis": "langkah"}, T_CARI, {}, None) == {"jenis": "langkah"}


# ---------------------------------------------------------------- paket Penawaran
def test_objek_nomor_penawaran_hanya_dari_server():
    f = P.objek_nomor_penawaran
    assert f({"id": "QUO-palsu"}, None) is None  # masukan model tidak pernah jadi nomor
    assert f({}, {"dokumen": {"nomor": "QUO-2610-0015"}, "isi": {}}) == "QUO-2610-0015"
    assert f({}, {"dokumen": None, "isi": {"success": True, "data": {"quote_number": "QUO-2610-0040"}}}) == "QUO-2610-0040"
    assert f({}, {"isi": ["bukan dict"]}) is None


@pytest.mark.parametrize("nama", ["cari_pelanggan", "cari_barang", "daftar_penawaran", "detail_penawaran",
                                  "ubah_penawaran", "batalkan_penawaran", "jadikan_pesanan", "kirim_penawaran",
                                  "terima_penawaran", "tolak_penawaran", "hapus_penawaran", "buat_pelanggan", "buat_barang"])
def test_tool_penawaran_berobjek(nama):
    t = next(t for t in P.TOOLS if t.nama == nama)
    assert t.objek is not None
    if t.kelas == Kelas.WRITE and t.jalur.startswith("/api/quotes/{id}"):
        assert t.objek is P.objek_nomor_penawaran
