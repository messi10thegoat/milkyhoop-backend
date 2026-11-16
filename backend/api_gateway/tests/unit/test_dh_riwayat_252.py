"""BUG-252 (16 Nov 2026): riwayat perubahan Daftar Harga di audit_logs (immutable) -- penulis tunggal, pembaca bersaring tenant+daftar, hook di semua titik tulis."""
import asyncio
import inspect
import json
import pathlib
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException

from zoneinfo import ZoneInfo

from app.routers import bulk_price_lists as BP
from app.routers import price_lists as PL
from app.services import harga_riwayat as HR
from app.services import impor_harga as IH

GW = pathlib.Path(__file__).resolve().parents[2]
PID = uuid4()


@pytest.fixture(autouse=True)
def zona_jakarta(monkeypatch):
    async def zona(conn, tid):
        return ZoneInfo("Asia/Jakarta")
    monkeypatch.setattr(HR, "zona_tenant", zona)


def snap(harga=10000, minimum=None, diskon=None, mulai=None, akhir=None, qty=1, nama="Minyak", satuan="Pcs"):
    return {"line_id": str(uuid4()), "item_id": str(uuid4()), "kode": "A-1", "nama": nama, "satuan": satuan, "min_quantity": qty, "harga": harga,
            "min_price": minimum, "diskon_persen": diskon, "mulai": mulai, "berakhir": akhir}


# ---------------------------------------------------------------- ringkas + bentuk
def test_ringkas_tambah_hapus_dan_ubah_menyebut_lama_dan_baru():
    assert HR.ringkas_baris(None, snap(12500)) == "Minyak (Pcs, min 1): Rp 12.500"
    assert HR.ringkas_baris(snap(12500), None) == "Minyak (Pcs, min 1): Rp 12.500"
    assert HR.ringkas_baris(snap(10000), snap(11000)) == "Minyak (Pcs, min 1): harga Rp 10.000 → Rp 11.000"
    r = HR.ringkas_baris(snap(10000, minimum=9000, diskon=5), snap(10000, minimum=9500, diskon=7.5, akhir="2026-12-31"))
    assert "minimum Rp 9.000 → Rp 9.500" in r and "diskon 5% → 7.5%" in r and "berakhir - → 2026-12-31" in r and "harga" not in r.replace("harga Rp", "X")
    assert "mulai 2026-11-20" in HR.ringkas_baris(None, snap(9000, mulai="2026-11-20", qty=10)) and "min 10" in HR.ringkas_baris(None, snap(9000, qty=10))
    assert HR.ringkas_baris(snap(1), snap(1)).endswith("tanpa perubahan nilai")


def test_bentuk_dari_baris_db_satuan_kosong_jadi_satuan_dasar_dan_nilai_float():
    r = HR.bentuk({"id": uuid4(), "item_id": PID, "kode": "K", "nama": "N", "unit": None, "dasar": "Pcs", "min_quantity": Decimal("1.000"), "price": Decimal("10000.00"),
                   "min_price": None, "discount_percent": Decimal("0"), "start_date": date(2026, 11, 20), "end_date": None})
    assert r["satuan"] == "Pcs" and r["harga"] == 10000.0 and r["diskon_persen"] == 0.0 and r["min_price"] is None and r["mulai"] == "2026-11-20" and r["berakhir"] is None


# ---------------------------------------------------------------- catat (penulis tunggal)
class Konn:
    def __init__(self, kode="D-1", rows=None, total=0, awal=None):
        self.exec, self.kode, self.rows, self.total, self.awal, self.sql = [], kode, rows or [], total, awal, []

    async def execute(self, sql, *a):
        self.exec.append((sql, a))

    async def fetchval(self, sql, *a):
        self.sql.append((sql, a))
        if "SELECT code FROM price_lists" in sql:
            return self.kode
        if "count(*)" in sql:
            return self.total
        if "min(a.\"createdAt\")" in sql:
            return self.awal
        raise AssertionError(sql)

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        return self.rows


def catat(c, aksi="UBAH_BARIS", baris=None, **kw):
    return asyncio.run(HR.catat(c, {"tenant_id": "t-1", "user_id": uuid4()}, uuid4(), kw.pop("kode", None), aksi, "ringkas", baris, **kw))


def test_catat_menulis_satu_baris_audit_logs_bersaring_daftar_dengan_metadata_terstruktur():
    c = Konn()
    catat(c, "UBAH_BARIS", [{"dari": snap(1), "ke": snap(2)}])
    sql, a = c.exec[0]
    assert "INSERT INTO audit_logs" in sql and 'entity_id' in sql and "$3::uuid" in sql and "'api:price_list'" in sql
    assert a[0] == "PRICE_LIST_UBAH_BARIS" and a[1] == "price_list" and a[3] == "D-1" and a[4] == "t-1"          # kode daftar dicari bila tak diberikan
    meta = json.loads(a[5])
    assert meta["aksi"] == "UBAH_BARIS" and meta["jumlah"] == 1 and meta["terpotong"] is False and meta["baris"][0]["ke"]["harga"] == 2 and a[6]


def test_catat_memotong_baris_dan_mencatat_jumlah_sebenarnya():
    c = Konn()
    catat(c, "UBAH_MASSAL", [{"dari": snap(1), "ke": snap(2)}] * 350, jumlah=350)
    meta = json.loads(c.exec[0][1][5])
    assert meta["terpotong"] is True and len(meta["baris"]) == HR.MAKS_BARIS == 200 and meta["jumlah"] == 350


def test_catat_header_decimal_dan_tanggal_serialisasi_dan_aksi_tak_dikenal_ditolak():
    c = Konn()
    catat(c, "UBAH_HEADER", header={"dari": {"end_date": date(2026, 12, 1)}, "ke": {"x": Decimal("1.5")}}, kode="K")
    meta = json.loads(c.exec[0][1][5])
    assert meta["header"] == {"dari": {"end_date": "2026-12-01"}, "ke": {"x": 1.5}} and c.exec[0][1][3] == "K" and not [s for s, _ in c.sql]    # kode diberikan: tanpa kueri
    with pytest.raises(ValueError):
        catat(Konn(), "HAPUS_SEMUA")
    assert set(HR.AKSI_TEKS) == {"BUAT", "UBAH_HEADER", "STATUS", "BAWAAN", "HAPUS", "TAMBAH_BARIS", "UBAH_BARIS", "HAPUS_BARIS", "UBAH_MASSAL", "IMPOR"}


# ---------------------------------------------------------------- daftar (pembaca)
def baris_audit(aksi="UBAH_BARIS", nama="Anton", username="anton"):
    return {"id": "e1", "eventType": f"PRICE_LIST_{aksi}", "createdAt": datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc),
            "metadata": json.dumps({"ringkas": "R", "jumlah": 1, "terpotong": False, "baris": [{"dari": None, "ke": snap(1)}]}),
            "userId": "u1", "nama": nama, "username": username, "peran": "ADMIN"}


def baca(c, **kw):
    return asyncio.run(HR.daftar(c, "t-1", uuid4(), **kw))


def test_daftar_bersaring_tenant_entity_terbaru_dulu_dengan_pelaku_dan_aksi_text():
    c = Konn(rows=[baris_audit(), baris_audit("HAPUS_BARIS", nama=None)], total=2, awal=datetime(2026, 11, 17, tzinfo=timezone.utc))
    r = baca(c, limit=10, offset=5)
    sql = c.sql[1][0]
    assert 'a.tenant_id = $1 AND a.entity_type = $2 AND a.entity_id = $3::uuid' in sql and 'ORDER BY a."createdAt" DESC, a.id DESC' in sql and 'u."tenantId" = a.tenant_id' in sql
    assert c.sql[1][1][-2:] == (10, 5) and c.sql[1][1][1] == "price_list"
    assert [i["aksi"] for i in r["items"]] == ["UBAH_BARIS", "HAPUS_BARIS"] and r["items"][0]["aksi_text"] == "Harga diubah" and r["total"] == 2
    assert r["items"][0]["oleh"] == {"id": "u1", "nama": "Anton", "peran": "ADMIN"} and r["items"][1]["oleh"]["nama"] == "anton"                      # nama kosong -> username
    assert r["items"][0]["waktu"].startswith("2026-11-20T09:00") and r["tercatat_sejak"] == "2026-11-17" and r["mulai_teks"].startswith("Riwayat tercatat sejak 2026-11-17")


def test_daftar_filter_aksi_dan_tanggal_menambah_parameter_dan_teks_awal_bila_belum_ada_catatan():
    c = Konn(rows=[], total=0, awal=None)
    r = baca(c, aksi="IMPOR", dari=date(2026, 11, 1), sampai=date(2026, 11, 30))
    sql, p = c.sql[0]
    assert 'a."eventType" = $4' in sql and 'a."createdAt" >= $5' in sql and 'a."createdAt" <= $6' in sql and p[3] == "PRICE_LIST_IMPOR"
    assert r["items"] == [] and r["tercatat_sejak"] is None and "Belum ada perubahan tercatat" in r["mulai_teks"]


# ---------------------------------------------------------------- hook di semua titik tulis
def test_hook_terpasang_di_setiap_penulis_harga_dalam_transaksi_yang_sama():
    s = {n: inspect.getsource(f) for n, f in (("buat", PL.create_price_list), ("header", PL.update_price_list), ("hapus", PL.delete_price_list), ("tambah", PL.add_item_to_price_list),
                                              ("ubah", PL.update_price_list_item), ("hapus_baris", PL.remove_item_from_price_list), ("status", PL.ubah_status_daftar),
                                              ("bawaan", PL.jadikan_bawaan_daftar))}
    for n, aksi in (("buat", "BUAT"), ("header", "UBAH_HEADER"), ("hapus", "HAPUS"), ("tambah", "TAMBAH_BARIS"), ("ubah", "UBAH_BARIS"), ("hapus_baris", "HAPUS_BARIS"),
                    ("status", "STATUS"), ("bawaan", "BAWAAN")):
        assert f'"{aksi}"' in s[n] and "HR.catat(" in s[n], n
    assert "kerja()" in s["tambah"] and s["ubah"].index("HR.snapshot") < s["ubah"].index("tulis_baris_harga") < s["ubah"].rindex("HR.snapshot")   # dari SEBELUM tulis, ke SESUDAH
    assert s["hapus_baris"].index("HR.snapshot") < s["hapus_baris"].index("DELETE FROM price_list_items")                                              # dari sebelum dihapus
    assert "n_baris = int(await HA.ukur_baris" in s["hapus"] and s["hapus"].index("n_baris") < s["hapus"].index("terapkan_hapus")


def test_impor_mencatat_satu_event_berbaris_tanpa_baris_tetap():
    s = inspect.getsource(IH.tulis)
    assert 'HR.catat(conn, ctx, pl_id, None, "IMPOR"' in s and 'x["aksi"] != "tetap"' in s


def test_ubah_massal_satu_event_dari_hasil_item_ok_bukan_replay_bukan_ditolak():
    data = {"items": [
        {"id": "i1", "number": "Minyak", "status": "ok", "replay": False, "result": {"baris": [{"id": "l1", "unit_text": "Pcs", "min_quantity": 1.0, "dari": Decimal("1000"), "ke": Decimal("1100"), "min_price": None}]}},
        {"id": "i2", "number": "Beras", "status": "ok", "replay": True, "result": {"baris": [{"id": "l2", "unit_text": "Kg", "min_quantity": 1.0, "dari": 1, "ke": 2, "min_price": None}]}},
        {"id": "i3", "number": "Gula", "status": "rejected", "replay": False, "result": None}]}
    b = BP.baris_riwayat_massal(data)
    assert len(b) == 1 and b[0]["dari"]["harga"] == 1000.0 and b[0]["ke"]["harga"] == 1100.0 and b[0]["ke"]["nama"] == "Minyak" and b[0]["ke"]["satuan"] == "Pcs"
    s = inspect.getsource(BP.bulk_price)
    assert "_catat_riwayat_massal(pool, ctx, price_list_id, body, data)" in s and 'return {"success": True, "data": data}' in s


def test_massal_tanpa_baris_tak_mencatat_dan_galat_catat_tak_merusak_respons(monkeypatch):
    class Pool:
        def acquire(self):
            raise RuntimeError("tak boleh dipanggil")
    asyncio.run(BP._catat_riwayat_massal(Pool(), {"tenant_id": "t"}, uuid4(), BP.BulkHargaRequest(mode="persen", nilai=10), {"items": []}))       # tanpa baris: nol akses DB

    class PoolRusak:
        def acquire(self):
            raise RuntimeError("DB mati")
    ada = {"items": [{"id": "i", "number": "N", "status": "ok", "replay": False, "result": {"baris": [{"id": "l", "unit_text": "Pcs", "min_quantity": 1.0, "dari": 1, "ke": 2, "min_price": None}]}}]}
    asyncio.run(BP._catat_riwayat_massal(PoolRusak(), {"tenant_id": "t"}, uuid4(), BP.BulkHargaRequest(mode="persen", nilai=10), ada))              # galat ditelan (dicatat)


# ---------------------------------------------------------------- rute + izin
def test_rute_riwayat_terdaftar_izin_baca_dan_aksi_tak_dikenal_400():
    assert ("GET", "/{price_list_id}/riwayat") in {(m, x.path) for x in BP.router.routes for m in x.methods}
    pm = (GW / "app/middleware/permission_middleware.py").read_text()
    assert 'price\\-lists/[^/]+/riwayat$", ["GET"], "price_list", "R"' in pm
    with pytest.raises(HTTPException) as e:
        asyncio.run(BP.riwayat_daftar(type("R", (), {"state": type("S", (), {"user": {"tenant_id": "t", "user_id": "u"}})()})(), uuid4(), limit=50, offset=0, aksi="NGAWUR", dari=None, sampai=None))
    assert e.value.status_code == 400 and e.value.detail["code"] == "RIWAYAT_AKSI_TIDAK_DIKENAL"
    s = inspect.getsource(BP.riwayat_daftar)
    assert "p.tenant_id" not in s and "tenant_id" in s and "dihapus" in s


# ---------------------------------------------------------------- waktu_text zona tenant (permintaan FRONTEND, pola riwayat Kategori)
def test_waktu_text_zona_tenant_pola_hari_bulan_singkat_tahun_jam():
    utc = datetime(2026, 11, 17, 8, 5, tzinfo=timezone.utc)
    assert HR.waktu_text(utc, ZoneInfo("Asia/Jakarta")) == "17 Nov 2026 15:05"                 # UTC+7
    assert HR.waktu_text(utc, ZoneInfo("Asia/Makassar")) == "17 Nov 2026 16:05"                # WITA UTC+8
    assert HR.waktu_text(datetime(2026, 12, 31, 20, 30, tzinfo=timezone.utc), ZoneInfo("Asia/Jakarta")) == "1 Jan 2027 03:30"   # lintas hari/tahun
    assert HR.waktu_text(datetime(2026, 8, 3, 1, 0), ZoneInfo("Asia/Jakarta")) == "3 Agu 2026 08:00"                         # tanpa zona = UTC; bulan Indonesia
    assert HR.waktu_text(datetime(2026, 5, 9, 0, 7, tzinfo=timezone.utc), ZoneInfo("UTC")) == "9 Mei 2026 00:07"            # jam/menit dua digit


def test_daftar_memberi_waktu_text_zona_tenant_di_samping_waktu_iso():
    c = Konn(rows=[baris_audit()], total=1, awal=datetime(2026, 11, 17, tzinfo=timezone.utc))
    it = baca(c)["items"][0]
    assert it["waktu"].startswith("2026-11-20T09:00") and it["waktu_text"] == "20 Nov 2026 16:00"                           # 09:00Z -> 16:00 WIB


def test_daftar_membaca_zona_tenant_satu_kali_per_panggilan():
    s = inspect.getsource(HR.daftar)
    assert s.count("zona_tenant(conn, tid)") == 1 and 'waktu_text(r["createdAt"], zona)' in s
