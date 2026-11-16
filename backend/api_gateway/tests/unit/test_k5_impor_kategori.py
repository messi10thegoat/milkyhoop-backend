"""K-5 (17 Nov 2026; putusan MASTER): impor + ekspor master Kategori -- SATU pola berkas dgn Impor Barang: baca (nol DB) -> pratinjau READ ONLY -> tulis ATOMIK + X-Idempotency-Key;
ekspor xlsx/csv dgn kolom = templat impor, aman rumus. Tahap 1 BUAT saja; sub kategori satu tingkat."""
import asyncio
import io
import pathlib
import uuid

import pytest
from fastapi import HTTPException

from app.services import impor_kategori as IK

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
CTX = {"tenant_id": "t1", "user_id": "u1"}


def xlsx(baris, judul=None):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(judul or ["Nama kategori", "Kategori induk", "Deskripsi"])
    for b in baris:
        ws.append(b)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_templat_kolom_contoh_dan_petunjuk():
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(IK.templat_xlsx()))
    assert wb.sheetnames == ["Kategori", "Petunjuk"]
    assert [c.value for c in wb["Kategori"][1]] == ["Nama kategori", "Kategori induk", "Deskripsi"] == [j for _, j, _ in IK.KOLOM]
    assert any("Maksimal 200 baris" in str(r[0].value) for r in wb["Petunjuk"].iter_rows())


def test_baca_judul_bebas_kolom_tak_dikenal_disebut_baris_kosong_dilewati():
    r = IK.baca_xlsx(xlsx([["Teh", "Minuman", "enak"], [None, None, None], ["Kopi", None, None]], ["Category", "Parent", "Keterangan"]))
    assert r["baris"] == [{"nama": "Teh", "induk": "Minuman", "deskripsi": "enak"}, {"nama": "Kopi"}]
    assert [p["medan"] for p in r["pemetaan"]] == ["nama", "induk", "deskripsi"] and r["peringatan"] == []
    x = IK.baca_xlsx(xlsx([["A", "zzz"]], ["Nama", "Warna"]))
    assert x["tak_dipakai"] == ["Warna"] and x["peringatan"] == ["Kolom 'Warna' tidak dipakai"] and x["baris"] == [{"nama": "A"}]


def test_baca_galat_berkas_tanpa_kolom_nama_kolom_ganda():
    for isi, kode in ((b"bukan excel", "BERKAS_TIDAK_TERBACA"), (xlsx([["x"]], ["Warna"]), "KOLOM_TIDAK_DIKENAL"), (xlsx([["a", "b"]], ["Nama", "Kategori"]), "KOLOM_GANDA")):
        with pytest.raises(HTTPException) as e:
            IK.baca_xlsx(isi)
        assert e.value.status_code == 400 and e.value.detail["code"] == kode
    assert IK.baca_xlsx(xlsx([])) ["baris"] == []


class Konn:
    def __init__(self, ada=None, dalam_tx=True):
        self.ada, self.sql, self.dalam_tx, self.ids = ada or [], [], dalam_tx, {}

    def is_in_transaction(self):
        return self.dalam_tx

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        if sql == IK.SQL_EKSPOR:
            return [{"nama": "Minuman", "induk": None, "deskripsi": None}, {"nama": "Teh", "induk": "Minuman", "deskripsi": "hijau"}]
        if sql.startswith("SELECT id, name, parent_id, is_active"):
            return self.ada
        if sql.startswith("SELECT id, name FROM item_categories"):
            return [{"id": r["id"], "name": r["name"]} for r in self.ada] + [{"id": i, "name": n} for n, i in self.ids.items()]
        return []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        assert sql.startswith("INSERT INTO item_categories")
        nid = uuid.uuid4()
        self.ids[a[1]] = nid
        return {"id": nid, "name": a[1]}

    async def execute(self, sql, *a):
        self.sql.append((sql, a))
        return "OK"


def kat(nama, parent=None, aktif=True):
    return {"id": uuid.uuid4(), "name": nama, "parent_id": parent, "is_active": aktif}


def jalan(aw):
    return asyncio.run(aw)


def kode(x):
    return [b["code"] for b in x["blocks"]]


def test_rencana_ok_induk_di_berkas_dan_ringkas():
    r = jalan(IK.rencana(Konn(), CTX, [{"nama": "Teh", "induk": "Minuman"}, {"nama": "Minuman", "deskripsi": "semua"}, {"nama": "Snack"}]))
    assert r["ok"] and r["ringkas"] == {"kategori": 3, "sub": 1, "induk": 2}
    assert [x["rencana"]["tingkat"] for x in r["baris"]] == ["anak", "induk", "induk"] and r["baris"][1]["payload"]["deskripsi"] == "semua"


def test_rencana_blok_per_baris():
    ada = [kat("Minuman"), kat("Teh", parent="p"), kat("Lama", aktif=False)]
    r = jalan(IK.rencana(Konn(ada), CTX, [
        {"nama": " "}, {"nama": "minuman"}, {"nama": "Kopi", "induk": "Teh"}, {"nama": "Susu", "induk": "Lama"}, {"nama": "Roti", "induk": "Tak Ada"},
        {"nama": "Diri", "induk": "diri"}, {"nama": "X"}, {"nama": "x"}, {"nama": "Sub2", "induk": "Sub"}, {"nama": "Sub", "induk": "Minuman"}, {"nama": "B" * 101}]))
    k = [kode(x) for x in r["baris"]]
    assert k[0] == ["NAMA_KOSONG"] and k[1] == ["KATEGORI_SUDAH_ADA"] and k[2] == ["KATEGORI_INDUK_BUKAN_TINGKAT_ATAS"] and k[3] == ["KATEGORI_INDUK_NONAKTIF"]
    assert k[4] == ["KATEGORI_INDUK_TAK_ADA"] and k[5] == ["KATEGORI_INDUK_DIRI_SENDIRI"] and k[6] == [] and k[7] == ["NAMA_GANDA_DI_BERKAS"]
    assert k[8] == ["KATEGORI_INDUK_TAK_ADA"] and k[9] == [] and k[10] == ["NAMA_TERLALU_PANJANG"]    # induk baris 'Sub' sendiri punya induk -> bukan tingkat atas di berkas
    assert r["ok"] is False and r["baris"][0]["blocks"][0]["field"] == "nama"


def test_rencana_batas_dan_kosong():
    assert kode(jalan(IK.rencana(Konn(), CTX, []))) == ["TANPA_BARIS"]
    r = jalan(IK.rencana(Konn(), CTX, [{"nama": f"K{i}"} for i in range(IK.MAKS_BARIS + 1)]))
    assert kode(r) == ["TERLALU_BANYAK"] and r["ok"] is False and r["baris"] == []


def test_tulis_induk_dulu_lalu_sub_parent_id_audit_dan_kunci():
    c = Konn()
    h = jalan(IK.tulis(c, CTX, [{"nama": "Teh", "induk": "Minuman"}, {"nama": "Minuman", "deskripsi": "semua"}], "u1"))
    ins = [(s, a) for s, a in c.sql if s.startswith("INSERT INTO item_categories")]
    assert [a[1] for _, a in ins] == ["Minuman", "Teh"]                        # induk dulu
    assert ins[0][1][2] is None and ins[0][1][3] == "semua" and ins[1][1][2] == c.ids["Minuman"]   # sub memakai parent_id induk yang baru dibuat
    assert c.sql[0][0].startswith("SELECT pg_advisory_xact_lock")
    aud = [a for s, a in c.sql if s.startswith("INSERT INTO audit_logs")]
    assert [x[0] for x in aud] == ["ITEM_CATEGORY_CREATED"] * 2 and '"impor": true' in aud[1][5]
    assert h["jumlah"] == 2 and [x["name"] for x in h["kategori"]] == ["Teh", "Minuman"]      # urut sesuai nomor baris berkas


def test_tulis_blok_menolak_semua_tanpa_tulis_dan_wajib_transaksi():
    c = Konn([kat("Minuman")])
    with pytest.raises(HTTPException) as e:
        jalan(IK.tulis(c, CTX, [{"nama": "Baik"}, {"nama": "Minuman"}], "u1"))
    assert e.value.status_code == 422 and e.value.detail["code"] == "IMPOR_DITOLAK" and [b["no"] for b in e.value.detail["baris"]] == [2]
    assert not [s for s, _ in c.sql if s.startswith("INSERT")]                 # nol tulis
    with pytest.raises(RuntimeError):
        jalan(IK.tulis(Konn(dalam_tx=False), CTX, [{"nama": "A"}], "u1"))


def test_ekspor_kolom_templat_induk_dulu_aman_rumus_csv_dan_xlsx():
    from openpyxl import load_workbook
    baris = [["Minuman", "", "=cmd()"], ["Teh", "Minuman", "+1"], ["@x", "", ""]]
    csv = IK.ekspor_csv(baris)
    assert csv.startswith(b"\xef\xbb\xbf") and csv.decode("utf-8-sig").splitlines()[0] == "Nama kategori,Kategori induk,Deskripsi"
    teks = csv.decode("utf-8-sig")
    assert "'=cmd()" in teks and "'+1" in teks and "'@x" in teks                  # tak dieksekusi spreadsheet
    ws = load_workbook(io.BytesIO(IK.ekspor_xlsx(baris))).active
    assert [c.value for c in ws[1]] == [j for _, j, _ in IK.KOLOM] and ws.cell(2, 3).value == "'=cmd()" and ws.cell(3, 2).value == "Minuman"
    assert "ORDER BY (c.parent_id IS NOT NULL)" in IK.SQL_EKSPOR and "c.tenant_id = $1" in IK.SQL_EKSPOR


def test_rute_izin_dan_urutan_statis_sebelum_detail():
    src = (APP / "routers" / "items.py").read_text()
    for r in ('@router.get("/items/categories/impor/templat")', '@router.get("/items/categories/export")', '@router.post("/items/categories/impor/baca")',
              '@router.post("/items/categories/impor/pratinjau")', '@router.post("/items/categories/impor")'):
        assert r in src and src.index(r) < src.index('@router.get("/items/categories/{category_id}")')     # statis dulu: 'export' bukan id
    assert 'idem_buat.mulai_aksi(conn, ctx, kunci, "ITEM_KATEGORI_IMPOR", "batch"' in src and "IK.tulis(conn, ctx, baris, ctx.get(\"user_id\"))" in src and "_baca_saja(f)" in src
    izin = (APP / "middleware" / "permission_middleware.py").read_text()
    assert '(r"^/api/items/categories/impor/templat$", ["GET"], "item", "R")' in izin and '(r"^/api/items/categories/impor(/baca|/pratinjau)?$", ["POST"], "item", "C")' in izin


def test_baris_ekspor_kolom_sama_dengan_templat():
    assert jalan(IK.baris_ekspor(Konn(), "t1")) == [["Minuman", "", ""], ["Teh", "Minuman", "hijau"]]      # urutan kolom = KOLOM; kosong = ""
