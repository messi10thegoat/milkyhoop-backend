"""Gudang G3 (P6 BE; KONTRAK-gudang-g3-aksi-tulis-20261115 + putusan MASTER 15 Nov): pratinjau + penulis SATU penentu, kode otomatis, blok/peringatan berkode,
nonaktif gudang utama = BLOK, nonaktif gudang ber-stok = PERINGATAN, jenis_text/jenis_catatan dari server."""
import asyncio
import inspect
import uuid
from pathlib import Path

import pytest

from app.routers import warehouses as WH
from app.schemas.warehouses import WarehouseData
from app.services import gudang_daftar as GD
from app.services import gudang_jenis as GJ
from app.services import gudang_rencana as GR
from app.services.galat_berkode import GalatBerkode

APP = Path(__file__).resolve().parents[2] / "app"
T = "kaos-biru-konveksi"
W1, W2 = uuid.uuid4(), uuid.uuid4()


def jalan(aw):
    return asyncio.run(aw)


class Kon:
    def __init__(self, kode_ada=(), gdg=(), berstok=0, utama_lain=None):
        self.kode_ada, self.gdg, self.berstok, self.utama_lain, self.sql = set(kode_ada), list(gdg), berstok, utama_lain, []

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        if "FROM warehouses WHERE tenant_id = $1 AND code = $2" in sql:
            return uuid.uuid4() if a[1] in self.kode_ada else None
        if "FROM inventory_ledger" in sql:
            return self.berstok
        if "AND is_default" in sql:
            return self.utama_lain
        raise AssertionError(sql)

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        assert "code ~ '^GDG-[0-9]+$'" in sql
        import re
        return [{"code": c} for c in self.gdg if re.match(r"^GDG-[0-9]+$", c)]            # meniru saringan regex SQL


def gud(**kw):
    d = {"id": W1, "code": "GDG-001", "name": "Toko", "address": "Jl. A", "city": "Bandung", "province": None, "postal_code": None, "country": "Indonesia",
         "phone": "0812", "email": None, "manager_name": None, "is_default": False, "is_active": True, "is_branch": False, "branch_code": None, "is_system": False}
    d.update(kw)
    return d


def buat(kon=None, **b):
    b.setdefault("name", "Gudang Baru")
    return jalan(GR.rencana_buat(kon or Kon(), T, b))


def kode(r, jenis="blocks"):
    return [x["code"] for x in r[jenis]]


# ---------------------------------------------------------------- BUAT
def test_kode_otomatis_gdg_nnn_melompati_terpakai_dan_manual_tetap_boleh():
    assert buat()["nilai"]["code"] == "GDG-001" and buat(code="")["rencana"]["gudang"]["kode"] == "GDG-001"
    r = buat(Kon(gdg=["GDG-002", "GDG-010", "GUD-77"]), code=None)
    assert r["nilai"]["code"] == "GDG-011" and r["rencana"]["gudang"]["kode"] == "GDG-011" and r["blocks"] == []
    assert buat(code="  TOKO-1 ")["nilai"]["code"] == "TOKO-1"                                   # manual menang, di-trim


def test_buat_blok_kode_nama_dan_gudang_sistem_dengan_field_dan_status():
    r = buat(Kon(kode_ada=["TOKO-1"]), code="TOKO-1")
    assert r["blocks"] == [{"code": "GUDANG_KODE_SUDAH_ADA", "message": "Kode gudang 'TOKO-1' sudah dipakai.", "field": "code", "status": 400}]
    n = buat(name="   ")
    assert (n["blocks"][0]["code"], n["blocks"][0]["field"], n["blocks"][0]["status"]) == ("GUDANG_NAMA_KOSONG", "name", 400)
    s = buat(code="sys-transit")
    assert (s["blocks"][0]["code"], s["blocks"][0]["status"], s["blocks"][0]["field"]) == ("GUDANG_SISTEM", 409, "code")


def test_buat_utama_diganti_peringatan_bentuk_207_dan_nonaktif_tak_bisa_utama():
    r = buat(Kon(utama_lain="Gudang Lama"), is_default=True)
    assert r["blocks"] == [] and r["peringatan"] == [{"code": "GUDANG_UTAMA_DIGANTI", "butuh_konfirmasi": True, "pesan": r["peringatan"][0]["message"], "message": r["peringatan"][0]["message"], "field": None}]
    assert "Gudang Lama" in r["peringatan"][0]["message"]
    assert buat(is_default=True)["peringatan"] == []                                             # tak ada utama lama: tanpa peringatan
    n = buat(is_default=True, is_active=False)
    assert (n["blocks"][0]["code"], n["blocks"][0]["field"]) == ("GUDANG_NONAKTIF_TAK_BISA_UTAMA", "is_active")


def test_buat_kosong_jadi_null_jenis_text_dan_perubahan_berlabel():
    r = buat(address="  ", phone=" 0812 ", is_branch=True, branch_code="")
    assert r["nilai"]["address"] is None and r["nilai"]["phone"] == "0812" and r["nilai"]["branch_code"] is None and r["nilai"]["country"] == "Indonesia"
    assert r["rencana"]["jenis_text"] == "Cabang" and r["rencana"]["jenis_catatan"] == GJ.CATATAN_CABANG
    assert buat()["rencana"]["jenis_text"] == "Gudang biasa" and buat()["rencana"]["jenis_catatan"] is None
    lab = {p["field"]: p["label"] for p in r["rencana"]["perubahan"]}
    assert lab["name"] == "Nama" and lab["phone"] == "Telepon" and lab["is_branch"] == "Jenis" and "address" not in lab


# ---------------------------------------------------------------- UBAH
def ubah(w, kon=None, **b):
    return jalan(GR.rencana_ubah(kon or Kon(), T, w, b))


def test_ubah_null_diabaikan_kosong_menghapus_dan_perubahan_berlabel():
    r = ubah(gud(), name=None, address="", city="Jakarta", phone=None)
    assert r["blocks"] == [] and r["nilai"] == {"address": None, "city": "Jakarta"}               # null = tak diubah; "" = kosongkan (NULL)
    pf = {p["field"]: p for p in r["rencana"]["perubahan"]}
    assert pf["address"] == {"field": "address", "label": "Alamat", "before": "Jl. A", "after": None} and pf["city"]["label"] == "Kota"
    assert ubah(gud(), name="Toko")["rencana"]["perubahan"] == [] and ubah(gud(), name="Toko")["nilai"] == {}   # nilai sama = bukan perubahan
    b = ubah(gud(), is_branch=True)["rencana"]["perubahan"][0]
    assert (b["label"], b["before_text"], b["after_text"]) == ("Jenis", "Gudang biasa", "Cabang")


def test_ubah_blok_sistem_nama_kode_dan_utama_nonaktif():
    assert ubah(gud(is_system=True, code="SYS-TRANSIT"), name="X")["blocks"][0]["code"] == "GUDANG_SISTEM"
    assert ubah(gud(is_system=True), name="X")["blocks"][0]["status"] == 409
    assert ubah(gud(), name="  ")["blocks"][0]["code"] == "GUDANG_NAMA_KOSONG"
    d = ubah(gud(), Kon(kode_ada=["B-2"]), code="B-2")
    assert (d["blocks"][0]["code"], d["blocks"][0]["field"]) == ("GUDANG_KODE_SUDAH_ADA", "code")
    assert ubah(gud(), Kon(kode_ada=["GDG-001"]), code="GDG-001")["blocks"] == []                # kode sendiri tak diperiksa
    u = ubah(gud(is_default=True), is_active=False)
    assert (u["blocks"][0]["code"], u["blocks"][0]["field"], u["blocks"][0]["message"]) == ("GUDANG_UTAMA_TAK_BISA_NONAKTIF", "is_active", "Jadikan gudang lain sebagai Utama dulu.")
    assert ubah(gud(), is_default=True, is_active=False)["blocks"][0]["code"] == "GUDANG_NONAKTIF_TAK_BISA_UTAMA"


# ---------------------------------------------------------------- STATUS
def status(w, aktif, kon=None):
    return jalan(GR.rencana_status(kon or Kon(), T, w, aktif))


def test_nonaktif_gudang_utama_blok_dan_berstok_peringatan():
    u = status(gud(is_default=True), False)
    assert [b["code"] for b in u["blocks"]] == ["GUDANG_UTAMA_TAK_BISA_NONAKTIF"] and u["blocks"][0]["message"] == "Jadikan gudang lain sebagai Utama dulu."
    r = status(gud(), False, Kon(berstok=3))
    assert r["blocks"] == [] and kode(r, "peringatan") == ["GUDANG_BERSTOK_DINONAKTIFKAN"] and r["peringatan"][0]["butuh_konfirmasi"] is True
    assert "(3 barang)" in r["peringatan"][0]["message"] and r["rencana"]["aksi"] == "nonaktifkan" and r["nilai"] == {"is_active": False}
    assert status(gud(), False, Kon(berstok=0))["peringatan"] == []
    assert kode(status(gud(is_active=False), False)) == ["GUDANG_SUDAH_NONAKTIF"] and kode(status(gud(), True)) == ["GUDANG_SUDAH_AKTIF"]
    a = status(gud(is_active=False), True)
    assert a["blocks"] == [] and a["rencana"]["aksi"] == "aktifkan" and a["rencana"]["perubahan"][0]["after_text"] == "Aktif"
    assert status(gud(is_system=True), False)["blocks"][0]["code"] == "GUDANG_SISTEM"


def test_jadikan_utama_blok_nonaktif_peringatan_diganti_dan_sudah_utama_idempoten():
    assert kode(jalan(GR.rencana_utama(Kon(), T, gud(is_active=False)))) == ["GUDANG_NONAKTIF_TAK_BISA_UTAMA"]
    r = jalan(GR.rencana_utama(Kon(utama_lain="Gudang Lama"), T, gud()))
    assert r["blocks"] == [] and kode(r, "peringatan") == ["GUDANG_UTAMA_DIGANTI"] and r["nilai"] == {"is_default": True} and r["rencana"]["aksi"] == "jadikan_utama"
    s = jalan(GR.rencana_utama(Kon(), T, gud(is_default=True)))
    assert s["blocks"] == [] and s["nilai"] == {} and s["rencana"]["perubahan"] == []            # sudah utama: sukses diam seperti sekarang
    assert jalan(GR.rencana_utama(Kon(), T, gud(is_system=True)))["blocks"][0]["code"] == "GUDANG_SISTEM"


def test_aksi_tersedia_nonaktifkan_ikut_putusan_baru():
    class K(Kon):
        pass
    a = jalan(GD.aksi_tersedia(Kon(berstok=5), T, gud()))
    assert a["nonaktifkan"] == {"tersedia": True, "alasan": None}                                  # ber-stok: boleh (peringatan), bukan blok
    u = jalan(GD.aksi_tersedia(Kon(), T, gud(is_default=True)))
    assert u["nonaktifkan"] == {"tersedia": False, "alasan": "Jadikan gudang lain sebagai Utama dulu."}
    assert jalan(GD.aksi_tersedia(Kon(), T, gud(is_active=False)))["nonaktifkan"]["alasan"] == "Gudang ini sudah nonaktif."


# ---------------------------------------------------------------- bentuk respons + galat
def test_pratinjau_bentuk_respons_dan_tolak_berkode():
    r = buat(Kon(kode_ada=["X"]), code="X")
    out = GR.pratinjau(r, {"name": "Gudang Baru", "code": "X"})
    assert out["success"] is True and out["data"]["ok"] is False and set(out["data"]) == {"ok", "blocks", "peringatan", "rencana", "payload"}
    assert out["data"]["blocks"] == [{"code": "GUDANG_KODE_SUDAH_ADA", "message": "Kode gudang 'X' sudah dipakai.", "field": "code"}]   # tanpa status
    with pytest.raises(GalatBerkode) as e:
        GR.tolak(r["blocks"])
    assert e.value.status_code == 400 and e.value.badan() == {"detail": "Kode gudang 'X' sudah dipakai.", "code": "GUDANG_KODE_SUDAH_ADA", "message": "Kode gudang 'X' sudah dipakai.", "field": "code"}
    GR.tolak([])


def test_jenis_text_di_skema_dari_server():
    d = dict(id=uuid.uuid4(), code="A", name="A", is_default=False, is_active=True, is_branch=True, created_at="2026-11-15T00:00:00")
    w = WarehouseData(**d)
    assert (w.jenis_text, w.jenis_catatan) == ("Cabang", GJ.CATATAN_CABANG)
    w2 = WarehouseData(**{**d, "is_branch": False})
    assert (w2.jenis_text, w2.jenis_catatan) == ("Gudang biasa", None)
    assert GJ.CATATAN_CABANG == "Cabang hanya penanda jenis lokasi (mis. toko cabang); tidak mengubah stok, laporan, atau pajak."


# ---------------------------------------------------------------- titik pasang router + izin
def test_router_memakai_penentu_bersama():
    c, u, d, s, m = (inspect.getsource(f) for f in (WH.create_warehouse, WH.update_warehouse, WH.delete_warehouse, WH.set_default_warehouse, WH.set_warehouse_status))
    assert "GR.rencana_buat(" in c and "GR.tolak(" in c and "idem_buat" in c
    assert "GR.rencana_ubah(" in u and "GR.tolak(" in u
    assert "GR.rencana_status(" in d and "GR.rencana_status(" in m and "idem_buat" in m
    assert "GR.rencana_utama(" in s
    for f in (WH.preview_create_warehouse, WH.preview_edit_warehouse, WH.preview_status_warehouse, WH.preview_set_default_warehouse):
        assert "GR.pratinjau(" in inspect.getsource(f) and "conn.execute(\"UPDATE" not in inspect.getsource(f) and "INSERT" not in inspect.getsource(f)
    src = (APP / "routers/warehouses.py").read_text()
    assert "GD.alasan_nonaktif(True, await GD.jumlah_berstok" not in src
    from app.services import idem_buat
    assert idem_buat._TABEL["warehouses"] == "code"                                              # idempotensi buat gudang


@pytest.mark.parametrize("jalur,harap", [
    ("/api/warehouses/preview", "C"), (f"/api/warehouses/{W1}/edit/preview", "U"), (f"/api/warehouses/{W1}/status/preview", "U"),
    (f"/api/warehouses/{W1}/set-default/preview", "C"), (f"/api/warehouses/{W1}/status", "U")])
def test_izin_rute_baru_eksplisit(jalur, harap):
    """Aturan EKSPLISIT (bukan jatuh ke ^/api/warehouses POST = C): pratinjau ubah/status/utama dan /status = U, pratinjau buat = C."""
    import re

    from app.middleware import permission_middleware as PM
    hit = next(((p, mod, aksi) for p, ms, mod, aksi in PM.ROUTE_PERMISSIONS if re.compile(p).match(jalur) and "POST" in ms), None)
    assert hit is not None and hit[1:] == ("warehouse", harap) and (hit[0] != "^/api/warehouses")


def test_jalur_lama_patch_dan_delete_tetap_menolak_gudang_berstok_hanya_status_peringatan():
    """Putusan MASTER 15 Nov: form lama (PATCH is_active:false, DELETE) tanpa Tinjau TETAP ditolak 400 untuk gudang ber-stok; hanya POST /status memberi peringatan."""
    pesan = "Gudang masih berisi stok (3 barang). Pindahkan atau sesuaikan stoknya dulu."
    p = ubah(gud(), Kon(berstok=3), is_active=False)
    assert p["peringatan"] == [] and [(x["code"], x["message"], x["status"], x["field"]) for x in p["blocks"]] == [("GUDANG_BERSTOK", pesan, 400, "is_active")]
    d = jalan(GR.rencana_status(Kon(berstok=3), T, gud(), False, blok_berstok=True))
    assert d["peringatan"] == [] and [(x["code"], x["message"]) for x in d["blocks"]] == [("GUDANG_BERSTOK", pesan)]
    s = status(gud(), False, Kon(berstok=3))                                                      # POST /status & pratinjau-nya: peringatan, bukan blok
    assert s["blocks"] == [] and kode(s, "peringatan") == ["GUDANG_BERSTOK_DINONAKTIFKAN"]
    assert ubah(gud(), Kon(berstok=3), is_active=True)["blocks"] == [] and ubah(gud(), Kon(berstok=0), is_active=False)["blocks"] == []
    src = inspect.getsource(WH.delete_warehouse)
    assert "blok_berstok=True" in src and "blok_berstok" not in inspect.getsource(WH.set_warehouse_status) and "blok_berstok" not in inspect.getsource(WH.preview_status_warehouse)


def test_perubahan_membawa_before_text_after_text_dan_negara_jadi_nama():
    r = buat(country="ID", is_branch=True)
    for p in r["rencana"]["perubahan"]:
        assert {"before_text", "after_text", "label"} <= set(p), p
    p = {x["field"]: x for x in r["rencana"]["perubahan"]}
    assert p["is_branch"]["after_text"] == GJ.TEKS_CABANG and p["name"]["before_text"] == "—" and p["name"]["after_text"] == "Gudang Baru"
    u = ubah(gud(country="ID"), country="MY")
    c = [x for x in u["rencana"]["perubahan"] if x["field"] == "country"][0]
    assert (c["before_text"], c["after_text"]) == ("Indonesia", "Malaysia")
