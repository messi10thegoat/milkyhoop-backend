"""Satuan G3 (BE, 17 Nov 2026; pola test_gudang_g3): pratinjau + penulis SATU penentu (services/satuan_rencana), galat berkode, *_text, bawaan diblok,
satuan terpakai tak bisa diganti nama/singkatan (cegah barang yatim) maupun dinonaktifkan, idempotensi aksi, rute terdaftar + izin."""
import asyncio
import re
import uuid
from pathlib import Path

import pytest

from app.routers import units as U
from app.services import satuan_master as SM
from app.services import satuan_pakai as SP
from app.services import satuan_rencana as SR
from app.services.galat_berkode import GalatBerkode

APP = Path(__file__).resolve().parents[2] / "app"
T = "kaos-biru-konveksi"
U1, U2 = str(uuid.uuid4()), str(uuid.uuid4())


def jalan(aw):
    return asyncio.run(aw)


class Kon:
    """Fake koneksi: satuan tenant = units [{id,name,abbreviation,is_system,is_active}], teks pemakai = barang/konv [(teks, n)]."""
    def __init__(self, units=(), barang=(), konv=(), turunan=(), dampak=None, alias=()):
        self.units, self.barang, self.konv, self.turunan = list(units), list(barang), list(konv), list(turunan)
        self.dampak, self.alias = dict(dampak or {}), list(alias)         # dampak = {tabel: jumlah baris yang berubah} untuk hitungan satuan_sebar
        self.sql, self.exec = [], []

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        if "FROM product_unit_aliases WHERE tenant_id = $1 AND unit_id" in sql:     # SS.alias_unit
            return [{"alias": x} for x in self.alias]
        if "FROM unit_conversions" in sql and "product_id IN" in sql or "FROM price_list_items t JOIN" in sql or "FROM products WHERE tenant_id = $1 AND id::text" in sql \
                or "FROM price_lists WHERE tenant_id = $1 AND id::text" in sql:        # pemuat konflik satuan_sebar (data konflik diuji murni di test_satuan_sebar)
            return []
        if "UNION ALL" in sql:                                  # SM.daftar
            return [{**{"id": u["id"], "name": u["name"], "abbreviation": u["abbreviation"], "is_system": u.get("is_system", False)}} for u in self.units if u.get("is_active", True)] + \
                   [{"id": None, "name": t, "abbreviation": t, "is_system": False} for t in self.turunan]
        if "FROM products" in sql:
            return [{"u": t, "n": n} for t, n in self.barang]
        if "FROM unit_conversions" in sql:
            return [{"u": t, "n": n} for t, n in self.konv]
        raise AssertionError(sql)

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        if sql.startswith("SELECT count(*) FROM"):                                  # SS.siapkan: hitungan per tabel
            return next((n for t, n in self.dampak.items() if f"FROM {t} t" in sql), 0)
        assert "FROM product_units WHERE tenant_id = $1 AND lower(abbreviation)" in sql
        for u in self.units:
            if u.get("is_active", True) and u["abbreviation"].lower() == a[1].lower() and (a[2] is None or u["id"] != a[2]):
                return uuid.uuid4()
        return None

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        assert "FROM product_units" in sql and "id::text = $1" in sql
        return next(({"id": u["id"], "name": u["name"], "abbreviation": u["abbreviation"], "is_system": u.get("is_system", False), "is_active": u.get("is_active", True)}
                     for u in self.units if u["id"] == a[0]), None)

    async def execute(self, sql, *a):
        self.exec.append((sql, a))

    def transaction(self):
        class _T:
            async def __aenter__(s):
                return None

            async def __aexit__(s, *e):
                return False
        return _T()


def sat(i, nama, singkatan=None, **kw):
    return {"id": i, "name": nama, "abbreviation": singkatan or nama, **kw}


def kode(r, jenis="blocks"):
    return [x["code"] for x in r[jenis]]


def muat(kon, uid):
    return jalan(SR.muat(kon, T, uid))


# ------------------------------------------------------------------ buat
def test_buat_sukses_nilai_dan_teks():
    r = jalan(SR.rencana_buat(Kon(), T, {"name": "  Karung  Besar ", "abbreviation": " krg "}))
    assert r["blocks"] == [] and r["nilai"] == {"name": "Karung Besar", "abbreviation": "krg"} and r["rencana"]["teks"] == "Buat satuan Karung Besar (krg)"
    assert [p["field"] for p in r["rencana"]["perubahan"]] == ["name", "abbreviation"] and r["rencana"]["aksi"] == "buat"


def test_buat_singkatan_kosong_mengikuti_nama():
    assert jalan(SR.rencana_buat(Kon(), T, {"name": "Lusin"}))["nilai"] == {"name": "Lusin", "abbreviation": "Lusin"}


@pytest.mark.parametrize("b,kd,field", [({"name": " "}, "SATUAN_NAMA_KOSONG", "name"), ({"name": "x" * 51, "abbreviation": "ok"}, "SATUAN_NAMA_TERLALU_PANJANG", "name"),
                                        ({"name": "ok", "abbreviation": "y" * 21}, "SATUAN_SINGKATAN_TERLALU_PANJANG", "abbreviation")])
def test_buat_blok_validasi(b, kd, field):
    r = jalan(SR.rencana_buat(Kon(), T, b))
    assert kode(r) == [kd] and r["blocks"][0]["field"] == field


def test_buat_blok_bentrok_bawaan_dan_tenant_tanpa_beda_huruf():
    assert kode(jalan(SR.rencana_buat(Kon(), T, {"name": "Kilogram", "abbreviation": "KG"}))) == ["SATUAN_SUDAH_ADA"]          # bawaan kg
    assert kode(jalan(SR.rencana_buat(Kon([sat(U1, "Karung", "krg")]), T, {"name": "Karung 2", "abbreviation": "KRG"}))) == ["SATUAN_SUDAH_ADA"]


def test_buat_satuan_yang_sudah_terpakai_teks_memberi_peringatan_tidak_blok():
    r = jalan(SR.rencana_buat(Kon(barang=[("ikat", 3)], turunan=["ikat"]), T, {"name": "Ikat"}))
    assert r["blocks"] == [] and kode(r, "peringatan") == ["SATUAN_TERPAKAI_DIDAFTARKAN"] and "3 barang" in r["peringatan"][0]["message"]


# ------------------------------------------------------------------ ubah
def test_ubah_bawaan_diblok_409_berkalimat_untuk_id_bawaan_dan_is_system():
    kon = Kon([sat(U1, "Kilogram", "kg", is_system=True)])
    r1 = jalan(SR.rencana_ubah(kon, T, muat(kon, SM.ID_BAWAAN + "Pcs"), {"name": "Biji"}))
    r2 = jalan(SR.rencana_ubah(kon, T, muat(kon, U1), {"name": "Kilo"}))
    for r in (r1, r2):
        assert kode(r) == ["SATUAN_BAWAAN_TAK_DAPAT_DIUBAH"] and r["blocks"][0]["status"] == 409 and r["nilai"] == {}


def test_ubah_satuan_terdaftar_belum_dipakai_boleh_dan_diff_before_after():
    kon = Kon([sat(U1, "Karung", "krg")])
    r = jalan(SR.rencana_ubah(kon, T, muat(kon, U1), {"name": "Karung Besar", "abbreviation": "krg"}))
    assert r["blocks"] == [] and r["nilai"] == {"name": "Karung Besar"}
    assert r["rencana"]["perubahan"] == [{"field": "name", "label": "Nama", "before": "Karung", "after": "Karung Besar", "before_text": "Karung", "after_text": "Karung Besar"}]
    assert r["rencana"]["pemakai"]["teks"] == "Belum dipakai" and r["rencana"]["teks"] == "Ubah satuan Karung Besar"


def test_ubah_tanpa_perubahan_nilai_kosong_idempoten():
    kon = Kon([sat(U1, "Karung", "krg")])
    r = jalan(SR.rencana_ubah(kon, T, muat(kon, U1), {"name": "Karung", "abbreviation": " krg "}))
    assert r["blocks"] == [] and r["nilai"] == {}


def test_ubah_satuan_terpakai_menyebar_dengan_peringatan_dan_nama_lama_jadi_alias():
    kon = Kon([sat(U1, "Karung", "krg")], barang=[("krg", 4)], konv=[("krg", 2)], dampak={"products": 4, "unit_conversions": 2, "price_list_items": 1})
    r = jalan(SR.rencana_ubah(kon, T, muat(kon, U1), {"abbreviation": "krng"}))
    assert r["blocks"] == [] and kode(r, "peringatan") == ["SATUAN_NAMA_MENYEBAR"] and r["peringatan"][0]["butuh_konfirmasi"] is True
    assert r["rencana"]["penyebaran"] == {"barang": 4, "konversi": 2, "harga": 1, "bom": 0, "teks": "4 barang, 2 konversi, 1 baris harga"}
    assert "4 barang, 2 konversi, 1 baris harga ikut memakai 'krng'" in r["peringatan"][0]["message"] and "Dokumen lama tidak diubah" in r["peringatan"][0]["message"]
    p = r["sebar"]["peta"]
    assert p["bentuk_singkatan"] == ["krg"] and p["bentuk_nama"] == ["karung"] and p["teks_singkatan"] == "krng" and p["teks_nama"] == "Karung"
    assert r["alias_baru"] == ["Karung", "krg"] or r["alias_baru"] == ["krg"]                                  # nama lama (singkatan) -> alias
    assert "krg" in r["alias_baru"] and r["rencana"]["pemakai"] == {"jumlah_barang": 4, "jumlah_konversi": 2, "teks": "Dipakai 4 barang dan 2 konversi"}


def test_ubah_satuan_belum_dipakai_tetap_mencatat_alias_tanpa_peringatan():
    kon = Kon([sat(U1, "Karung", "krg")])
    r = jalan(SR.rencana_ubah(kon, T, muat(kon, U1), {"abbreviation": "krng"}))
    assert r["blocks"] == [] and r["peringatan"] == [] and r["alias_baru"] == ["krg"] and r["rencana"]["penyebaran"]["teks"].startswith("Tidak ada")


def test_ubah_bentuk_alias_lama_ikut_dipetakan():
    kon = Kon([sat(U1, "Karung", "krg")], alias=["kg-lama"], dampak={"products": 1})
    r = jalan(SR.rencana_ubah(kon, T, muat(kon, U1), {"abbreviation": "krng"}))
    assert r["sebar"]["peta"]["bentuk_singkatan"] == ["kg-lama", "krg"]


def test_ubah_blok_bentrok_singkatan_lain_dan_validasi():
    kon = Kon([sat(U1, "Karung", "krg"), sat(U2, "Kardus", "krd")])
    assert kode(jalan(SR.rencana_ubah(kon, T, muat(kon, U1), {"abbreviation": "KRD"}))) == ["SATUAN_SUDAH_ADA"]
    assert kode(jalan(SR.rencana_ubah(kon, T, muat(kon, U1), {"abbreviation": "pcs"}))) == ["SATUAN_SUDAH_ADA"]                 # bawaan
    assert kode(jalan(SR.rencana_ubah(kon, T, muat(kon, U1), {"name": " "}))) == ["SATUAN_NAMA_KOSONG"]
    assert kode(jalan(SR.rencana_ubah(kon, T, muat(kon, U1), {"abbreviation": ""}))) == ["SATUAN_SINGKATAN_KOSONG"]


def test_ubah_hanya_huruf_besar_kecil_singkatan_sendiri_bukan_bentrok():
    kon = Kon([sat(U1, "Karung", "krg")])
    r = jalan(SR.rencana_ubah(kon, T, muat(kon, U1), {"abbreviation": "KRG"}))
    assert r["blocks"] == [] and r["nilai"] == {"abbreviation": "KRG"}


def test_ubah_satuan_terpakai_hanya_teks_belum_terdaftar_blok_berkalimat():
    kon = Kon(barang=[("ikat", 3)], turunan=["ikat"])
    u = muat(kon, SM.ID_TERPAKAI + "ikat")
    r = jalan(SR.rencana_ubah(kon, T, u, {"name": "Ikatan"}))
    assert kode(r) == ["SATUAN_BELUM_TERDAFTAR"] and r["blocks"][0]["status"] == 409


# ------------------------------------------------------------------ status
def test_nonaktif_sukses_bila_tak_dipakai():
    kon = Kon([sat(U1, "Karung", "krg")])
    r = jalan(SR.rencana_status(kon, T, muat(kon, U1)))
    assert r["blocks"] == [] and r["nilai"] == {"is_active": False} and r["rencana"]["teks"] == "Nonaktifkan satuan Karung"
    assert r["rencana"]["perubahan"][0]["after_text"] == "Nonaktif" and r["payload"] == {"aktif": False}


def test_nonaktif_terpakai_bawaan_belum_terdaftar_dan_sudah_nonaktif_diblok():
    kon = Kon([sat(U1, "Karung", "krg"), sat(U2, "Lama", "lm", is_active=False)], barang=[("krg", 1)], turunan=["ikat"])
    assert kode(jalan(SR.rencana_status(kon, T, muat(kon, U1)))) == ["SATUAN_TERPAKAI"]
    assert kode(jalan(SR.rencana_status(kon, T, muat(kon, SM.ID_BAWAAN + "Kg")))) == ["SATUAN_BAWAAN_TAK_DAPAT_DIUBAH"]
    assert kode(jalan(SR.rencana_status(kon, T, muat(kon, SM.ID_TERPAKAI + "ikat")))) == ["SATUAN_BELUM_TERDAFTAR"]
    assert kode(jalan(SR.rencana_status(kon, T, muat(kon, U2)))) == ["SATUAN_SUDAH_NONAKTIF"]


def test_muat_id_tak_dikenal_none():
    kon = Kon()
    assert muat(kon, str(uuid.uuid4())) is None and muat(kon, "bawaan:tidak-ada") is None


# ------------------------------------------------------------------ pratinjau + tolak
def test_pratinjau_bentuk_dan_tolak_galat_berkode_toplevel():
    r = jalan(SR.rencana_buat(Kon(), T, {"name": ""}))
    p = SR.pratinjau(r, {"name": ""})
    assert p["success"] is True and p["data"]["ok"] is False and p["data"]["blocks"] == [{"code": "SATUAN_NAMA_KOSONG", "message": "Nama satuan wajib diisi.", "field": "name"}]
    with pytest.raises(GalatBerkode) as e:
        SR.tolak(r["blocks"])
    assert e.value.status_code == 400 and e.value.badan() == {"detail": "Nama satuan wajib diisi.", "code": "SATUAN_NAMA_KOSONG", "message": "Nama satuan wajib diisi.", "field": "name"}
    SR.tolak([])


def test_baris_teks_pemakai_jenis_dan_status():
    baris = SP.susun([{"id": U1, "name": "Karung", "abbreviation": "krg", "is_system": False, "bawaan": False},
                      {"id": SM.ID_BAWAAN + "Kg", "name": "kg", "abbreviation": "kg", "is_system": True, "bawaan": True},
                      {"id": SM.ID_TERPAKAI + "ikat", "name": "ikat", "abbreviation": "ikat", "is_system": False, "bawaan": False}],
                     {U1: {"jumlah_barang": 2, "jumlah_konversi": 0}})
    t = [SR.baris_teks(b) for b in baris]
    assert t[0] == {"pemakai_text": "Dipakai 2 barang", "jenis": "master", "status_text": "Aktif", "jenis_text": "Kustom"}
    assert t[1]["jenis"] == "bawaan" and t[1]["jenis_text"] == "Bawaan" and t[2]["jenis"] == "terpakai" and t[2]["jenis_text"] == "Belum terdaftar"
    assert t[1]["pemakai_text"] == "Belum dipakai"


# ------------------------------------------------------------------ rute (penulis = rencana yang sama)
class Req:
    def __init__(self, body=None, headers=None):
        self.state = type("S", (), {"user": {"tenant_id": T, "user_id": "u1"}})()
        self._b, self.headers = body, headers or {}

    async def json(self):
        if self._b is None:
            raise ValueError("kosong")
        return self._b


@pytest.fixture
def kon(monkeypatch):
    k = Kon([sat(U1, "Karung", "krg"), sat(U2, "Kardus", "krd")], barang=[("krd", 1)])

    class _Acq:
        async def __aenter__(s):
            return k

        async def __aexit__(s, *e):
            return False

    class _Pool:
        def acquire(s):
            return _Acq()

    async def _pool():
        return _Pool()

    monkeypatch.setattr(U, "get_pool", _pool)

    async def _buat(conn, tid, nama, singkatan=None):
        return {"id": "baru-id", "name": nama, "abbreviation": singkatan}
    monkeypatch.setattr(SM, "buat", _buat)
    return k


def galat(coro):
    with pytest.raises(GalatBerkode) as e:
        jalan(coro)
    return e.value


def test_rute_preview_tiga_aksi_dan_404_berkode(kon):
    assert jalan(U.preview_buat_satuan(Req({"name": "Pak"})))["data"]["ok"] is True
    assert jalan(U.preview_ubah_satuan(Req({"name": "Karung B"}), U1))["data"]["rencana"]["perubahan"][0]["after"] == "Karung B"
    assert jalan(U.preview_nonaktif_satuan(Req(), U1))["data"]["ok"] is True
    e = galat(U.preview_nonaktif_satuan(Req(), str(uuid.uuid4())))
    assert (e.status_code, e.code) == (404, "SATUAN_TIDAK_ADA")
    assert galat(U.preview_buat_satuan(Req(None))).code == "BADAN_TIDAK_VALID"


def test_rute_tulis_memakai_penentu_sama_blok_jadi_galat_dan_tak_menulis(kon):
    assert galat(U.create_unit(Req({"name": ""}))).code == "SATUAN_NAMA_KOSONG"
    assert galat(U.update_unit(Req({"abbreviation": "krd"}), U1)).code == "SATUAN_SUDAH_ADA"
    assert galat(U.update_unit(Req({"name": "x"}), SM.ID_BAWAAN + "Kg")).code == "SATUAN_BAWAAN_TAK_DAPAT_DIUBAH"
    assert galat(U.nonaktifkan_satuan(Req(), U2)).code == "SATUAN_TERPAKAI"
    assert galat(U.delete_unit(Req(), U2)).code == "SATUAN_TERPAKAI"                                 # jalur lama = penentu sama
    assert kon.exec == []                                                                           # ditolak = tak ada tulis


def test_rute_tulis_sukses_menulis_kolom_dari_rencana(kon):
    assert jalan(U.create_unit(Req({"name": "Pak", "abbreviation": "pk"})))["data"]["id"] == "baru-id"
    r = jalan(U.update_unit(Req({"name": "Karung Besar"}), U1))
    assert r["success"] is True
    sql, a = kon.exec[-1]
    assert "SET name = $1, updated_at = NOW()" in sql and "tenant_id = $3" in sql and a == ("Karung Besar", U1, T)
    assert jalan(U.update_unit(Req({"name": "Karung"}), U1))["message"] == "Tidak ada perubahan"
    assert jalan(U.nonaktifkan_satuan(Req(), U1))["message"] == "Satuan berhasil dinonaktifkan"
    assert "is_active = false" in kon.exec[-1][0] and kon.exec[-1][1] == (U1, T)


def test_rute_terdaftar_izin_dan_penulis_tanpa_kolom_dari_masukan():
    jalur = {(m, x.path) for x in U.router.routes for m in x.methods}
    for p in [("POST", "/preview"), ("POST", "/{unit_id}/edit/preview"), ("POST", "/{unit_id}/status/preview"), ("POST", "/{unit_id}/status"),
              ("POST", ""), ("PATCH", "/{unit_id}"), ("DELETE", "/{unit_id}")]:
        assert p in jalur, p
    pm = (APP / "middleware/permission_middleware.py").read_text()
    assert '(r"^/api/units", ["POST"], "unit", "C")' in pm and '(r"^/api/units/[^/]+", ["PATCH", "PUT"], "unit", "U")' in pm
    src = (APP / "routers/units.py").read_text()
    assert "for k in SR.URUT if k in r[\"nilai\"]" in src and "HTTPException(status_code=400, detail=f\"Satuan '" not in src


def test_daftar_dan_detail_memuat_teks(kon):
    d = jalan(U.get_unit(Req(), U1))["data"]
    assert d["pemakai_text"] == "Belum dipakai" and d["jenis_text"] == "Kustom" and d["status_text"] == "Aktif"
    assert (galat(U.get_unit(Req(), "tak-ada")).status_code, ) == (404,)
    l = jalan(U.list_units(Req(), search=None, is_active=None, aksi=None, limit=500))
    assert all({"pemakai_text", "jenis_text", "status_text", "jenis"} <= set(x) for x in l["items"])


def test_aksi_tersedia_daftar_sama_dengan_vonis_rencana_dan_gabung_bawaan_mati():
    """aksi_tersedia (satuan_pakai.susun) harus sejalan dengan blok rencana: tak ada tombol yang pasti ditolak penulis."""
    kon = Kon([sat(U1, "Karung", "krg"), sat(U2, "Kardus", "krd", is_system=False)], barang=[("krd", 2)], turunan=["ikat"])
    semua = jalan(SM.daftar(kon, T))
    baris = SP.susun(semua, jalan(SP.muat_pemakai(kon, T, semua)))
    diuji = [b for b in baris if b["id"] in (U1, U2, SM.ID_BAWAAN + "Kg", SM.ID_TERPAKAI + "ikat")]
    assert len(diuji) == 4
    for b in diuji:
        u = muat(kon, b["id"])
        ubah = jalan(SR.rencana_ubah(kon, T, u, {"name": b["name"] + " baru"}))
        non = jalan(SR.rencana_status(kon, T, u))
        assert b["aksi_tersedia"]["ubah_nama"]["tersedia"] == (not ubah["blocks"]), b["id"]
        assert b["aksi_tersedia"]["nonaktifkan"]["tersedia"] == (not non["blocks"]), b["id"]
        bawaan = b["id"].startswith(SM.ID_BAWAAN) or b.get("bawaan") or b.get("is_system")
        assert b["aksi_tersedia"]["gabung"]["tersedia"] == (not bawaan), b["id"]                       # G3-b: gabung tersedia untuk kustom/terpakai-teks, mati untuk bawaan
        assert bool(b["aksi_tersedia"]["gabung"]["alasan"]) == bool(bawaan)


def test_baris_tenant_bernama_bawaan_tetap_dianggap_bawaan_diblok_dan_aksi_sejalan():
    """Tenant punya baris product_units 'Kg' (bukan is_system, mis. hasil seed): nama ada di DEFAULT_UNITS -> tetap BAWAAN (blok 409, tombol mati)."""
    kon = Kon([sat(U1, "Kilogram", "Kg", is_system=False)])
    u = muat(kon, U1)
    assert u["bawaan"] is True and u["is_system"] is False
    assert kode(jalan(SR.rencana_ubah(kon, T, u, {"name": "Kilo"}))) == ["SATUAN_BAWAAN_TAK_DAPAT_DIUBAH"]
    assert kode(jalan(SR.rencana_status(kon, T, u))) == ["SATUAN_BAWAAN_TAK_DAPAT_DIUBAH"]
    semua = jalan(SM.daftar(kon, T))
    b = next(x for x in SP.susun(semua, jalan(SP.muat_pemakai(kon, T, semua))) if x["id"] == U1)
    assert [b["aksi_tersedia"][k]["tersedia"] for k in ("ubah_nama", "gabung", "nonaktifkan")] == [False, False, False]


def test_baris_teks_baris_tenant_bernama_bawaan_jenis_bawaan():
    """Tenant punya baris 'Pcs' (uuid, bukan is_system): daftar harus mengatakan Bawaan (konsisten dengan blok rencana), bukan Kustom."""
    kon = Kon([sat(U1, "Pieces", "Pcs", is_system=False), sat(U2, "Karung", "krg")])
    semua = jalan(SM.daftar(kon, T))
    baris = {b["id"]: b for b in SP.susun(semua, jalan(SP.muat_pemakai(kon, T, semua)))}
    assert SR.baris_teks(baris[U1])["jenis"] == "bawaan" and SR.baris_teks(baris[U1])["jenis_text"] == "Bawaan"
    assert SR.baris_teks(baris[U2])["jenis_text"] == "Kustom"


def test_izin_pratinjau_dan_penulis_status_sama():
    pm = (APP / "middleware/permission_middleware.py").read_text()
    assert '(r"^/api/units/[^/]+/edit/preview$", ["POST"], "unit", "U")' in pm                    # = izin PATCH
    assert '(r"^/api/units/[^/]+/status(/preview)?$", ["POST"], "unit", "D")' in pm              # = izin DELETE lama (nonaktif = hapus lunak)
    assert pm.index("/edit/preview$\", [\"POST\"], \"unit\"") < pm.index('(r"^/api/units", ["POST"], "unit", "C")')   # pola khusus SEBELUM prefiks umum


def test_turunan_sama_nama_atau_singkatan_dengan_baris_tenant_tak_jadi_baris_kembar():
    """BUG-363: 'Kilogram'/'kg'/'meter' (teks di barang) = baris tenant Kilogram(kg)/Meter(m); turunan baru ('ikat') tetap muncul."""
    kon = Kon([sat(U1, "Kilogram", "kg"), sat(U2, "Meter", "m")], barang=[("Kilogram", 1), ("meter", 2), ("kg", 3), ("ikat", 1)],
              turunan=["Kilogram", "kg", "meter", "METER", "ikat"])
    semua = jalan(SM.daftar(kon, T))
    nama = [b["name"].lower() for b in semua]
    assert nama.count("kilogram") == 1 and nama.count("meter") == 1 and "kg" not in nama and "ikat" in nama
    baris = {b["id"]: b for b in SP.susun(semua, jalan(SP.muat_pemakai(kon, T, semua)))}
    assert baris[U1]["jumlah_barang"] == 4 and baris[U2]["jumlah_barang"] == 2                    # pemakai tetap terhitung ke baris tenant
    assert SP.ringkasan(list(baris.values()))["terpakai"] == 3                                    # Kilogram, Meter, ikat
