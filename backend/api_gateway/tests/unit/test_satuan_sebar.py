"""Satuan G3-b (17 Nov 2026): mesin penyebaran teks satuan (services/satuan_sebar) + GABUNG (satuan_rencana.rencana_gabung) + rute merge + kamus alias V504."""
import asyncio
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from app.routers import units as U
from app.services import satuan_dasar as SD
from app.services import satuan_master as SM
from app.services import satuan_pakai as SP
from app.services import satuan_rencana as SR
from app.services import satuan_sebar as SS
from app.services.galat_berkode import GalatBerkode
from tests.unit.test_satuan_g3 import Kon, Req, T, U1, U2, galat, jalan, kode, muat, sat  # noqa: F401  (fake bersama)

GW = Path(__file__).resolve().parents[2]
BE = GW.parent
f = SS.peta_teks({"krg"}, {"karung"}, "krng", "Karung Besar")


def kv(i, pid, base, conv, faktor, aktif=True):
    return {"id": i, "product_id": pid, "base_unit": base, "conversion_unit": conv, "conversion_factor": faktor, "aktif": aktif}


# ------------------------------------------------------------------ pemetaan teks
def test_peta_teks_singkatan_menang_nama_dan_lainnya_tetap():
    assert f("KRG ") == "krng" and f(" karung") == "Karung Besar" and f("pcs") == "pcs" and f(None) is None
    assert SS.peta_teks({"x"}, {"x"}, "S", "N")("x") == "S"                                    # satu bentuk di keduanya: singkatan menang


# ------------------------------------------------------------------ konversi
def test_konversi_tanpa_perubahan_tak_dinilai():
    r = SS.analisis_konversi([kv("c1", "p1", "pcs", "dus", 12)], f, {"p1": "Kaos"})
    assert r == {"hapus": [], "blocks": [], "dibuang": 0}


def test_konversi_menjadi_satuan_dasar_sendiri_faktor_satu_dibuang_selain_itu_blok():
    g = SS.peta_teks({"dus"}, set(), "pcs", "pcs")                                              # dus digabung ke pcs
    assert SS.analisis_konversi([kv("c1", "p1", "pcs", "dus", 1)], g, {"p1": "Kaos"})["hapus"] == ["c1"]
    r = SS.analisis_konversi([kv("c1", "p1", "pcs", "dus", 12)], g, {"p1": "Kaos"})
    assert r["hapus"] == [] and r["blocks"][0][0] == "SATUAN_FAKTOR_BEDA" and "Kaos" in r["blocks"][0][1] and "faktornya 12" in r["blocks"][0][1]


def test_konversi_dua_satuan_jadi_satu_faktor_sama_dibuang_satu_faktor_beda_blok_bernama():
    g = SS.peta_teks({"dus"}, set(), "Karton", "Karton")                                        # dus digabung ke Karton; barang punya dus DAN karton
    sama = SS.analisis_konversi([kv("c1", "p1", "pcs", "dus", 12), kv("c2", "p1", "pcs", "Karton", 12)], g, {"p1": "Kaos"})
    assert sama["hapus"] == ["c1"] and sama["blocks"] == [] and sama["dibuang"] == 1                # yang BERUBAH (dus) dibuang, yang asli dipertahankan
    beda = SS.analisis_konversi([kv("c1", "p1", "pcs", "dus", 12), kv("c2", "p1", "pcs", "Karton", 10)], g, {"p1": "Kaos"})
    assert beda["hapus"] == [] and [b[0] for b in beda["blocks"]] == ["SATUAN_FAKTOR_BEDA"] and "Kaos" in beda["blocks"][0][1] and "12 dan 10" in beda["blocks"][0][1]


def test_konversi_nonaktif_yang_menabrak_indeks_unik_dibuang_dan_barang_lain_tak_tersentuh():
    g = SS.peta_teks({"dus"}, set(), "Karton", "Karton")
    r = SS.analisis_konversi([kv("c1", "p1", "pcs", "dus", 12), kv("c2", "p1", "pcs", "Karton", 12, aktif=False), kv("c3", "p2", "pcs", "box", 6)], g, {"p1": "Kaos", "p2": "Topi"})
    assert r["hapus"] == ["c2"] and r["blocks"] == []                                           # aktif dipertahankan, nonaktif kembar dibuang


# ------------------------------------------------------------------ harga
def hv(i, pl, item, unit, price, mq=1, start=None):
    return {"id": i, "price_list_id": pl, "item_id": item, "unit": unit, "price": price, "min_quantity": mq, "start_date": start}


def test_harga_kembar_sama_dibuang_beda_blok_bernama_daftar_dan_barang():
    g = SS.peta_teks({"dus"}, set(), "Karton", "Karton")
    sama = SS.analisis_harga([hv("h1", "L", "i1", "dus", 100), hv("h2", "L", "i1", "Karton", 100)], g, {"i1": "Kaos"}, {"L": "Grosir"})
    assert sama["hapus"] == ["h1"] and sama["blocks"] == []
    beda = SS.analisis_harga([hv("h1", "L", "i1", "dus", 100), hv("h2", "L", "i1", "Karton", 90)], g, {"i1": "Kaos"}, {"L": "Grosir"})
    assert beda["hapus"] == [] and beda["blocks"][0][0] == "SATUAN_HARGA_BENTROK" and "Grosir" in beda["blocks"][0][1] and "Kaos" in beda["blocks"][0][1]
    lain = SS.analisis_harga([hv("h1", "L", "i1", "dus", 100, mq=1), hv("h2", "L", "i1", "Karton", 90, mq=10)], g, {}, {})
    assert lain == {"hapus": [], "blocks": []}                                                  # min qty beda = baris beda, tak bentrok


def test_blok_panjang_diringkas():
    r = SS._ringkas_blok([("K", f"p{i}") for i in range(9)])
    assert len(r) == 6 and r[-1][1] == "... dan 4 konflik lain."


# ------------------------------------------------------------------ SQL penulis/penghitung (satu kondisi)
def test_sql_ubah_dan_hitung_tersaring_tenant_satu_pernyataan_per_tabel_dan_tanpa_noop():
    tabel = {x[1]: x for x in SS.KOLOM}
    assert set(tabel) == {"products", "unit_conversions", "price_list_items", "bom_components", "bill_of_materials", "recipe_ingredients", "recipes"}
    for nama, (kel, tb, kol, gabung, pemilik) in tabel.items():
        u, h = SS.sql_ubah(tb, kol, gabung, pemilik), SS.sql_hitung(tb, kol, gabung, pemilik)
        assert "$1" in u and "$1" in h and "IS DISTINCT FROM" in u and "IS DISTINCT FROM" in h, nama        # tenant + tanpa no-op
        assert "tenant_id = $1" in u, nama
    assert tabel["unit_conversions"][2] == ("base_unit", "conversion_unit") and tabel["products"][2] == ("satuan", "base_unit")   # satu pernyataan: tak ada keadaan antara
    assert "FROM price_lists p WHERE p.id = t.price_list_id AND p.tenant_id = $1" in SS.sql_ubah(*tabel["price_list_items"][1:])
    assert "FROM bill_of_materials b WHERE b.id = t.bom_id AND b.tenant_id = $1" in SS.sql_ubah(*tabel["bom_components"][1:])
    assert not any(x[1].endswith("_items") and x[1] != "price_list_items" for x in SS.KOLOM)                # dokumen historis TIDAK disentuh
    assert not any(t in x[1] for x in SS.KOLOM for t in ("inventory_ledger", "sales_order", "bill_items", "purchase_order"))


# ------------------------------------------------------------------ kamus alias V504
def test_kamus_alias_satuan_dasar_membaca_tabel_alias_satu_kueri_dan_menunjuk_kanon_hidup():
    class K:
        sql = None

        async def fetch(self, sql, *a):
            K.sql = sql
            return [{"name": "Karung Besar", "abbreviation": "krng"}, {"name": "Karung Besar", "abbreviation": "krg"}, {"name": "Karung Besar", "abbreviation": "Karung"}]
    peta = jalan(SD._alias(K(), T))
    assert "product_unit_aliases" in K.sql and "u.id = a.unit_id AND u.tenant_id = a.tenant_id" in K.sql and "COALESCE(u.is_active, true)" in K.sql
    assert SD.kanon("KRG", peta) == SD.kanon("krng", peta) == SD.kanon("Karung", peta) == "karung besar"          # nama lama = satuan yang sama


def test_migrasi_v504_aditif_dan_pembalik():
    m = (BE / "migrations/V504__product_unit_aliases.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS product_unit_aliases" in m and "REFERENCES product_units(id) ON DELETE CASCADE" in m
    assert "uq_product_unit_aliases_tenant_alias ON product_unit_aliases (tenant_id, lower(btrim(alias)))" in m
    kode_sql = "\n".join(x for x in m.splitlines() if not x.lstrip().startswith("--")).upper()
    assert "ALTER TABLE" not in kode_sql and "DROP" not in kode_sql and "BEGIN" not in kode_sql and "COMMIT" not in kode_sql     # aditif (tabel baru), tanpa transaksi
    assert "DROP TABLE IF EXISTS product_unit_aliases" in (BE / "migrations/V504__product_unit_aliases_ROLLBACK.sql").read_text()


# ------------------------------------------------------------------ GABUNG
def gab(kon, s, t, ke=None):
    return jalan(SR.rencana_gabung(kon, T, muat(kon, s), muat(kon, t), ke or t))


def test_gabung_sukses_peringatan_dampak_penyebaran_dan_tujuan():
    kon = Kon([sat(U1, "Karung", "krg"), sat(U2, "Kardus", "krd")], barang=[("krg", 3)], dampak={"products": 3, "unit_conversions": 1})
    r = gab(kon, U1, U2)
    assert r["blocks"] == [] and kode(r, "peringatan") == ["SATUAN_GABUNG_DAMPAK"] and r["rencana"]["aksi"] == "gabung"
    assert "3 barang, 1 konversi ikut memakai 'krd'" in r["peringatan"][0]["message"] and "tetap terbaca" in r["peringatan"][0]["message"]
    assert r["rencana"]["tujuan"] == {"id": U2, "nama": "Kardus", "singkatan": "krd", "dibuat_otomatis": False}
    assert r["rencana"]["teks"] == "Gabung satuan Karung ke Kardus" and r["rencana"]["penyebaran"]["barang"] == 3 and r["payload"] == {"ke_unit_id": U2}
    p = r["sebar"]["peta"]
    assert (p["teks_singkatan"], p["teks_nama"], p["bentuk_singkatan"]) == ("krd", "Kardus", ["krg"]) and r["nilai"] == {"is_active": False}


def test_gabung_terpakai_teks_ke_bawaan_tujuan_dibuat_otomatis():
    kon = Kon(barang=[("pcs.", 5)], turunan=["pcs."], dampak={"products": 5})
    r = gab(kon, SM.ID_TERPAKAI + "pcs.", SM.ID_BAWAAN + "Pcs")
    assert r["blocks"] == [] and r["rencana"]["tujuan"]["dibuat_otomatis"] is True and r["rencana"]["tujuan"]["singkatan"] == "Pcs"
    assert r["sebar"]["peta"]["bentuk_singkatan"] == ["pcs."] and r["sebar"]["peta"]["teks_singkatan"] == "Pcs"


def test_gabung_blok_bawaan_sumber_sama_tujuan_terpakai_nonaktif():
    kon = Kon([sat(U1, "Karung", "krg"), sat(U2, "Lama", "lm", is_active=False)], turunan=["ikat"])
    assert kode(gab(kon, SM.ID_BAWAAN + "Kg", U1)) == ["SATUAN_BAWAAN_TAK_DAPAT_DIUBAH"]
    assert kode(gab(kon, U1, U1)) == ["SATUAN_GABUNG_SAMA"]
    assert kode(gab(kon, U1, SM.ID_TERPAKAI + "ikat")) == ["SATUAN_TUJUAN_BELUM_TERDAFTAR"]
    assert kode(gab(kon, U1, U2)) == ["SATUAN_TUJUAN_NONAKTIF"]
    r = gab(kon, SM.ID_BAWAAN + "Kg", U1)
    assert r["blocks"][0]["status"] == 409 and "sebar" not in r                                 # diblok = tak menghitung dampak


def test_gabung_konflik_data_jadi_blok_bernama(monkeypatch):
    async def siap(conn, tid, singk, nama, ts, tn):
        return {"peta": {}, "jumlah": {"barang": 1, "konversi": 1, "harga": 0, "bom": 0}, "hapus_konversi": [], "hapus_harga": [],
                "blocks": [("SATUAN_FAKTOR_BEDA", "Barang Kaos: dua konversi ke satuan yang sama (Karton) berfaktor beda (12 dan 10).")]}
    monkeypatch.setattr(SS, "siapkan", siap)
    kon = Kon([sat(U1, "Ikat", "ikt"), sat(U2, "Bundel", "bdl")])
    r = gab(kon, U1, U2)
    assert kode(r) == ["SATUAN_FAKTOR_BEDA"] and "Kaos" in r["blocks"][0]["message"] and r["blocks"][0]["field"] == "ke_unit_id" and r["peringatan"] == []


# ------------------------------------------------------------------ rute
def test_rute_merge_terdaftar_izin_dan_inventaris():
    jalur = {(m, x.path) for x in U.router.routes for m in x.methods}
    assert ("POST", "/{unit_id}/merge") in jalur and ("POST", "/{unit_id}/merge/preview") in jalur
    pm = (GW / "app/middleware/permission_middleware.py").read_text()
    assert '(r"^/api/units/[^/]+/merge(/preview)?$", ["POST"], "unit", "U")' in pm
    inv = (GW / "tests/unit/inventaris_rute_tulis.txt").read_text()
    assert "POST /api/units/{unit_id}/merge\n" in inv and "POST /api/units/{unit_id}/merge/preview\n" in inv


@pytest.fixture
def kon(monkeypatch):
    k = Kon([sat(U1, "Karung", "krg"), sat(U2, "Kardus", "krd")], barang=[("krg", 2)], dampak={"products": 2}, alias=["krg-lama"])

    class _A:
        async def __aenter__(s):
            return k

        async def __aexit__(s, *e):
            return False

    class _P:
        def acquire(s):
            return _A()

    async def _pool():
        return _P()
    monkeypatch.setattr(U, "get_pool", _pool)
    k.fetchval0 = k.fetchval

    async def fv(sql, *a):                                   # INSERT ... RETURNING id (tujuan bawaan) -> id baru
        if sql.lstrip().startswith("INSERT INTO product_units"):
            k.exec.append((sql, a))
            return "uuid-bawaan-baru"
        return await k.fetchval0(sql, *a)
    k.fetchval = fv
    return k


def test_rute_preview_merge_dan_galat_tujuan(kon):
    p = jalan(U.preview_gabung_satuan(Req({"ke_unit_id": U2}), U1))["data"]
    assert p["ok"] is True and p["rencana"]["aksi"] == "gabung" and p["payload"] == {"ke_unit_id": U2}
    assert galat(U.preview_gabung_satuan(Req({}), U1)).code == "SATUAN_TUJUAN_KOSONG"
    e = galat(U.preview_gabung_satuan(Req({"ke_unit_id": str(uuid.uuid4())}), U1))
    assert (e.status_code, e.code) == (404, "SATUAN_TUJUAN_TIDAK_ADA")
    assert galat(U.preview_gabung_satuan(Req({"ke_unit_id": U2}), str(uuid.uuid4()))).code == "SATUAN_TIDAK_ADA"
    assert galat(U.gabung_satuan(Req({"ke_unit_id": U1}), U1)).code == "SATUAN_GABUNG_SAMA"
    assert kon.exec == []                                      # pratinjau + ditolak = tak ada tulis


def test_rute_merge_menulis_urutan_penyebaran_alias_nonaktif(kon):
    r = jalan(U.gabung_satuan(Req({"ke_unit_id": U2}), U1))
    assert r["success"] and r["data"]["id"] == U2 and r["data"]["sumber_id"] == U1 and r["data"]["penyebaran"]["barang"] == 2
    sql = [x[0] for x in kon.exec]
    iu = [i for i, q in enumerate(sql) if q.startswith("UPDATE products t SET")]
    ia = [i for i, q in enumerate(sql) if "UPDATE product_unit_aliases SET unit_id" in q]
    ic = [i for i, q in enumerate(sql) if q.startswith("INSERT INTO product_unit_aliases")]
    iN = [i for i, q in enumerate(sql) if q.startswith("UPDATE product_units SET is_active = false")]
    assert iu and ia and ic and iN and iu[0] < ia[0] < iN[0] < ic[0]                         # teks master -> alias sumber ikut tujuan -> sumber nonaktif -> alias nama lama
    assert [x[1][2] for x in kon.exec if x[0].startswith("INSERT INTO product_unit_aliases")] == ["Karung", "krg"]
    assert kon.exec[iN[0]][1] == (U1, T)


def test_rute_merge_ke_bawaan_membuat_baris_tujuan_dulu(kon):
    r = jalan(U.gabung_satuan(Req({"ke_unit_id": SM.ID_BAWAAN + "Pcs"}), U1))
    assert r["data"]["id"] == "uuid-bawaan-baru"
    assert kon.exec[0][0].lstrip().startswith("INSERT INTO product_units") and kon.exec[0][1] == (T, "Pcs")


def test_rute_ubah_terpakai_menulis_penyebaran_dan_alias_sebelum_baris(kon):
    jalan(U.update_unit(Req({"abbreviation": "krng"}), U1))
    sql = [x[0] for x in kon.exec]
    assert sql[0].startswith("UPDATE products t SET") and sql[-1].startswith("UPDATE product_units SET") and any(q.startswith("INSERT INTO product_unit_aliases") for q in sql)
