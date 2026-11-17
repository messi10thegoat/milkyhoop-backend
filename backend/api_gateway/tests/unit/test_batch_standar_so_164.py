"""BE-A (17 Nov 2026) + BUG-164: Batch standar SO. Penyesuaian batch = dokumen Penyesuaian Stok bernomor (+ jurnal + kartu stok; putusan pemilik 17 Nov);
teks tampil + aksi_tersedia dari server; pratinjau == penulis (rencana_sesuaikan)."""
import asyncio
import datetime as dt
import inspect
import json
import uuid
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.routers import item_batches as IB
from app.routers import stock_adjustments as SAR
from app.schemas.item_batches import AdjustBatchQuantityRequest, SetBatchStatusRequest
from app.services import batch_aksi as BA
from app.services import idem_buat
from app.services import stok_sesuai as SS
from app.services.bayar_keluar_rencana import Blok
from app.services.galat_berkode import GalatBerkode

T = "kaos-biru-konveksi"
HARI = dt.date(2026, 11, 17)
B_ID, P_ID, G_ID, SA_ID, JID = (uuid.uuid4() for _ in range(5))


def run(c):
    return asyncio.run(c)


def baris_batch(status="active", expiry=None, qty=D("10")):
    return {"id": B_ID, "item_id": P_ID, "batch_number": "LOT-A", "status": status, "expiry_date": expiry, "current_quantity": qty,
            "base_unit": "Pcs", "item_name": "Minyak", "item_code": "MYK", "item_type": "goods", "track_inventory": True}


# ---------- teks + aksi (murni) ----------
def test_teks_kedaluwarsa_dan_status():
    assert BA.expiry_text(None, HARI) == "Tanpa tanggal kedaluwarsa"
    assert BA.expiry_text(HARI - dt.timedelta(days=3), HARI) == "Kedaluwarsa 3 hari lalu"
    assert BA.expiry_text(HARI, HARI) == "Kedaluwarsa hari ini"
    assert BA.expiry_text(HARI + dt.timedelta(days=12), HARI) == "Kedaluwarsa 12 hari lagi"
    # kolom status 'active' basi, tanggal sudah lewat -> tampil Kedaluwarsa
    assert BA.status_text("active", HARI - dt.timedelta(days=1), HARI) == "Kedaluwarsa"
    assert BA.status_text("active", HARI + dt.timedelta(days=1), HARI) == "Aktif"
    assert BA.status_text("quarantine", None, HARI) == "Karantina"


def test_angka_tanpa_nol_belakang():
    assert BA.angka(D("12.500000")) == "12,5"
    assert BA.angka(D("10.000")) == "10"
    assert BA.stock_text(D("14"), "Karton") == "14 Karton"


def test_aksi_tersedia_karantina_dan_aktifkan():
    a = BA.aksi_tersedia(baris_batch("active"), HARI)
    assert a["karantina"]["tersedia"] and not a["aktifkan"]["tersedia"] and a["aktifkan"]["alasan"]
    q = BA.aksi_tersedia(baris_batch("quarantine"), HARI)
    assert not q["karantina"]["tersedia"] and q["aktifkan"]["tersedia"]
    d = BA.aksi_tersedia(baris_batch("depleted"), HARI)
    assert not d["karantina"]["tersedia"] and d["karantina"]["alasan"] == "Batch sudah habis."
    h = BA.hiasi(baris_batch(), HARI)
    assert set(h["aksi_tersedia"]) == set(BA.AKSI) == set(h["aksi_semua"])


# ---------- rencana (pratinjau == penulis) ----------
class Kon:
    def __init__(self, batch=True, stok_batch=D("10"), stok=D("50"), goods=True):
        self.batch, self.stok_batch, self.stok, self.goods = batch, stok_batch, stok, goods
        self.exe, self.ledger_batch = [], None

    def transaction(self, **kw):
        class Tx:
            async def __aenter__(s):
                return None

            async def __aexit__(s, *e):
                return False
        return Tx()

    async def execute(self, sql, *a):
        self.exe.append((sql, a))
        return "UPDATE 1"

    async def fetchrow(self, sql, *a):
        if "FROM item_batches ib JOIN products" in sql:
            b = baris_batch()
            b["item_type"] = "goods" if self.goods else "non_inventory"
            return b if self.batch else None
        if "FROM warehouses" in sql:
            return {"id": G_ID, "name": "Gudang Utama", "is_active": True}
        if "FROM item_batches WHERE id" in sql:
            return {"id": B_ID, "item_id": P_ID, "batch_number": "LOT-A", "initial_quantity": D("10"), "current_quantity": self.stok_batch,
                    "unit_cost": 0, "total_value": 0, "status": "active", "created_at": dt.datetime(2026, 11, 1)}
        if "batch_warehouse_stock bws" in sql:
            return {"warehouse_id": G_ID, "warehouse_name": "Gudang Utama", "quantity": self.stok_batch, "reserved_quantity": D(0), "available_quantity": self.stok_batch}
        raise AssertionError(sql[:80])

    async def fetchval(self, sql, *a):
        if "FROM batch_warehouse_stock" in sql:
            return self.stok_batch
        if "FROM inventory_ledger" in sql:
            return self.stok
        raise AssertionError(sql[:80])


def isi(ubah, gudang=None):
    return AdjustBatchQuantityRequest(warehouse_id=G_ID, quantity_change=D(str(ubah)), reason="Kedaluwarsa")


def test_rencana_pratinjau_mengumpulkan_blok_tulis_melempar():
    k = Kon(stok_batch=D("3"))
    bl = Blok(True)
    r = run(BA.rencana_sesuaikan(k, T, B_ID, isi(-5), bl))
    assert [b["code"] for b in bl.daftar] == ["BATCH_STOK_TIDAK_CUKUP"]
    assert r["batch_sebelum"] == D("3") and r["batch_sesudah"] == D("-2")
    with pytest.raises(GalatBerkode) as e:
        run(BA.rencana_sesuaikan(k, T, B_ID, isi(-5), Blok(False)))
    assert e.value.code == "BATCH_STOK_TIDAK_CUKUP" and e.value.status_code == 422


def test_rencana_stok_barang_tak_cukup_nol_dan_tanpa_stok():
    with pytest.raises(GalatBerkode) as e:
        run(BA.rencana_sesuaikan(Kon(stok_batch=D("10"), stok=D("2")), T, B_ID, isi(-5), Blok(False)))
    assert e.value.code == "STOK_TIDAK_CUKUP"
    with pytest.raises(GalatBerkode) as e:
        run(BA.rencana_sesuaikan(Kon(), T, B_ID, isi(0), Blok(False)))
    assert e.value.code == "JUMLAH_NOL"
    with pytest.raises(GalatBerkode) as e:
        run(BA.rencana_sesuaikan(Kon(goods=False), T, B_ID, isi(1), Blok(False)))
    assert e.value.code == "BARANG_TANPA_STOK"
    with pytest.raises(GalatBerkode) as e:
        run(BA.rencana_sesuaikan(Kon(batch=False), T, B_ID, isi(1), Blok(False)))
    assert e.value.code == "BATCH_TIDAK_DITEMUKAN" and e.value.status_code == 404


def test_rencana_tambah_lolos():
    bl = Blok(True)
    r = run(BA.rencana_sesuaikan(Kon(), T, B_ID, isi(4), bl))
    assert not bl.daftar and r["batch_sesudah"] == D("14") and r["stok_sesudah"] == D("54")


# ---------- penulis: dokumen SA + kartu stok bertanda batch + saldo batch ----------
def req(body):
    raw = json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)
    r.state.user = {"tenant_id": T, "user_id": "00000000-0000-0000-0000-000000000001"}
    return r


def pasang(monkeypatch, kon):
    pangg = []

    async def get_pool():
        return SimpleNamespace(acquire=lambda: _Ac(kon))

    class _Ac:
        def __init__(s, k):
            s.k = k

        async def __aenter__(s):
            return s.k

        async def __aexit__(s, *e):
            return False

    async def pre(pool, tid=None):
        return None

    async def buat(conn, ctx, body, r):
        pangg.append(("buat", body))
        return SA_ID, "SA-2611-0001", D(0)

    async def post(conn, ctx, sa_id, r):
        pangg.append(("post", sa_id))
        return JID, "SAJ-1"

    async def rbuat(conn, tid, body, bl):
        return {"baris_draf": []}

    async def rpost(conn, tid, sa_id, bl, kunci=False, counter_account_id=None):
        pangg.append(("lawan", counter_account_id))
        return {}

    async def tgl(conn, tid):
        return HARI
    monkeypatch.setattr(IB, "get_pool", get_pool)
    monkeypatch.setattr(IB, "tanggal_dokumen", tgl)
    monkeypatch.setattr(SAR, "_ensure_stock_adj_role_preconditions", pre)
    monkeypatch.setattr(SAR, "_buat_draf", buat)
    monkeypatch.setattr(SAR, "_tulis_posting", post)
    monkeypatch.setattr(SS, "rencana_buat", rbuat)
    monkeypatch.setattr(SS, "rencana_posting", rpost)
    return pangg


def panggil(body):
    return run(IB.adjust_batch_quantity(req(body), B_ID, AdjustBatchQuantityRequest(**body)))


def test_adjust_menulis_dokumen_sa_menandai_kartu_dan_menggeser_batch(monkeypatch):
    k = Kon(stok_batch=D("10"))
    pangg = pasang(monkeypatch, k)
    hasil = panggil({"warehouse_id": str(G_ID), "quantity_change": "-4", "reason": "Rusak"})
    assert [p[0] for p in pangg] == ["buat", "lawan", "post"]
    body = pangg[0][1]
    assert body.adjustment_type == "decrease" and body.items[0].quantity_adjustment == D("-4") and "LOT-A" in body.notes
    sql = " ".join(s for s, _ in k.exe)
    assert "UPDATE inventory_ledger SET batch_id" in sql            # kartu stok dokumen ditandai batch -> void SA membalik batch
    assert "UPDATE batch_warehouse_stock SET quantity = quantity + $4" in sql
    assert "ON CONFLICT" not in sql                                # INSERT..ON CONFLICT dgn jumlah negatif melanggar chk_bws_qty (temuan harness salinan)
    geser = [a for s, a in k.exe if "UPDATE batch_warehouse_stock SET quantity = quantity + $4" in s][0]
    assert geser[3] == D("-4")
    assert hasil["adjustment_number"] == "SA-2611-0001" and hasil["journal_number"] == "SAJ-1"
    assert hasil["data"]["position_text"]


def test_adjust_tolak_tak_cukup_tanpa_menulis_apa_pun(monkeypatch):
    k = Kon(stok_batch=D("3"))
    pangg = pasang(monkeypatch, k)
    with pytest.raises(GalatBerkode) as e:
        panggil({"warehouse_id": str(G_ID), "quantity_change": "-4", "reason": "Rusak"})
    assert e.value.code == "BATCH_STOK_TIDAK_CUKUP"
    assert pangg == [] and not any("batch_warehouse_stock" in s for s, _ in k.exe)


def test_adjust_meneruskan_akun_lawan(monkeypatch):
    pangg = pasang(monkeypatch, Kon())
    panggil({"warehouse_id": str(G_ID), "quantity_change": "2", "reason": "Temuan", "counter_account_id": "akun-x"})
    assert ("lawan", "akun-x") in pangg
    assert pangg[0][1].adjustment_type == "increase"


def test_pratinjau_tak_menulis(monkeypatch):
    k = Kon(stok_batch=D("3"))
    pasang(monkeypatch, k)
    hasil = run(IB.preview_adjust_batch(req({}), B_ID, isi(-5)))
    d = hasil["data"]
    assert d["ok"] is False and d["blocks"][0]["code"] == "BATCH_STOK_TIDAK_CUKUP" and d["blocks"][0]["field"] == "quantity_change"   # bentuk G3
    assert d["rencana"]["batch_after_text"] and d["peringatan"] == [] and d["payload"]["quantity_change"] == "-5"
    assert not any(("INSERT" in s or "UPDATE" in s) for s, _ in k.exe)


def test_sumber_adjust_memakai_penulis_sa_bukan_update_batch_saja():
    src = inspect.getsource(IB.adjust_batch_quantity)
    assert "SAR._buat_draf" in src and "SAR._tulis_posting" in src
    assert "pg_advisory_xact_lock" in src and "idem_buat" in src


def test_void_sa_tak_menggeser_batch_dua_kali():
    """Trigger DB trg_update_batch_stock sudah menggeser batch dari baris pembalik ber-batch_id; helper tak boleh menambah lagi untuk SA (temuan harness salinan)."""
    from app.services import inventory_helpers as IH
    assert inspect.signature(IH.record_inventory_reversal).parameters["pindah_batch"].default is True      # perilaku lama bagi pemanggil lain tak berubah
    assert "pindah_batch=False" in inspect.getsource(SAR.void_stock_adjustment)
    assert "if batch_id and pindah_batch" in inspect.getsource(IH.record_inventory_reversal)


# ================= tambahan BE-A (permintaan MASTER 17 Nov): ringkasan/saringan keadaan, teks gudang, pratinjau status =================
def test_syarat_keadaan_satu_penentu():
    assert BA.syarat("semua", "$3") is None
    assert "expiry_date >= $3::date" in BA.syarat("aktif", "$3") and "expiry_date < $3::date" in BA.syarat("kedaluwarsa", "$3")
    assert "<= $3::date + 30" in BA.syarat("segera_kedaluwarsa", "$3")
    assert BA.syarat("karantina", "$3") == "ib.status = 'quarantine'" and BA.syarat("habis", "$3") == "ib.status = 'depleted'"
    assert set(BA.KUNCI) == {"semua", "aktif", "kedaluwarsa", "segera_kedaluwarsa", "karantina", "habis"}


def test_kunci_dari_baris_cermin_python():
    h = HARI
    assert BA.kunci_dari_baris("active", None, h) == "aktif"
    assert BA.kunci_dari_baris("active", h + dt.timedelta(days=31), h) == "aktif"
    assert BA.kunci_dari_baris("active", h + dt.timedelta(days=30), h) == "segera_kedaluwarsa"
    assert BA.kunci_dari_baris("active", h, h) == "segera_kedaluwarsa"
    assert BA.kunci_dari_baris("active", h - dt.timedelta(days=1), h) == "kedaluwarsa"      # kolom basi: lewat tanggal = kedaluwarsa
    assert BA.kunci_dari_baris("expired", None, h) == "kedaluwarsa"
    assert BA.kunci_dari_baris("quarantine", h - dt.timedelta(days=9), h) == "karantina"
    assert BA.kunci_dari_baris("depleted", None, h) == "habis"


class KonDaftar(Kon):
    def __init__(self):
        super().__init__()
        self.sql = []

    async def fetchval(self, sql, *a):
        self.sql.append((sql, a))
        return 0

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        return []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        return {"semua": 9, "aktif": 3, "kedaluwarsa": 2, "segera_kedaluwarsa": 1, "karantina": 1, "habis": 2}


def daftar(monkeypatch, **q):
    k = KonDaftar()
    pasang(monkeypatch, k)
    kw = dict(skip=0, limit=50, item_id=None, status=None, search=None, aksi=None)
    kw.update(q)
    run(IB.list_item_batches(req({}), **kw))
    return k


def test_daftar_status_expired_dan_aksi_memakai_penentu_yang_sama(monkeypatch):
    k = daftar(monkeypatch, status="expired")
    sql, a = k.sql[0]
    assert "ib.status = 'active' AND ib.expiry_date < $2::date" in sql and a[1] == HARI      # melewatkan 'active' yang lewat tanggal sebelumnya
    k = daftar(monkeypatch, aksi="segera_kedaluwarsa")
    assert "<= $2::date + 30" in k.sql[0][0]
    k = daftar(monkeypatch, aksi="semua")
    assert len(k.sql[0][1]) == 1 and "$2" not in k.sql[0][0]                                 # tanpa parameter menggantung
    k = daftar(monkeypatch, status="quarantine", aksi="karantina")
    assert k.sql[0][0].count("ib.status = 'quarantine'") == 2


def test_daftar_aksi_tak_sah_422(monkeypatch):
    with pytest.raises(GalatBerkode) as e:
        daftar(monkeypatch, aksi="sembarang")
    assert e.value.code == "AKSI_TAK_SAH" and e.value.status_code == 422


def test_ringkasan_endpoint_membaca_penentu_bersama(monkeypatch):
    k = KonDaftar()
    pasang(monkeypatch, k)
    hasil = run(IB.get_batches_summary(req({}), None))["data"]
    assert [hasil[x] for x in BA.KUNCI] == [9, 3, 2, 1, 1, 2] and hasil["teks"]["aktif"] == "3 batch"
    sql = k.sql[0][0]
    for kunci in BA.KUNCI:
        if kunci != "semua":
            assert BA.syarat(kunci, "$2") in sql


def test_teks_stok_per_gudang():
    t = BA.gudang_text({"quantity": D("12.5"), "available_quantity": D("10"), "reserved_quantity": D("2.5")}, "Pcs")
    assert t == {"stock_text": "12,5 Pcs", "available_text": "10 Pcs", "reserved_text": "2,5 Pcs"}
    assert BA.gudang_text({"quantity": D("1"), "available_quantity": D("1"), "reserved_quantity": D("0")}, None)["reserved_text"] is None


def rs(status, batch, kon=None):
    bl = Blok(True)
    k = kon or Kon()
    kb = baris_batch(batch.get("status", "active"), batch.get("expiry"), batch.get("qty", D("10")))

    async def muat(conn, tid, bid, kunci=False):
        return kb if batch.get("ada", True) else None
    return k, bl, muat


def test_rencana_status_karantina_peringatan_dan_blok(monkeypatch):
    for status, batch, kode in (("quarantine", {"status": "quarantine"}, "BATCH_AKSI_TIDAK_TERSEDIA"), ("active", {"status": "active"}, "BATCH_AKSI_TIDAK_TERSEDIA"),
                                ("quarantine", {"status": "depleted"}, "BATCH_AKSI_TIDAK_TERSEDIA"), ("expired", {}, "STATUS_TAK_SAH"),
                                ("quarantine", {"ada": False}, "BATCH_TIDAK_DITEMUKAN")):
        k, bl, muat = rs(status, batch)
        monkeypatch.setattr(BA, "muat_batch", muat)
        r = run(BA.rencana_status(k, T, B_ID, status, bl, HARI))
        assert [b["code"] for b in bl.daftar] == [kode], (status, batch, bl.daftar)
        with pytest.raises(GalatBerkode):
            monkeypatch.setattr(BA, "muat_batch", muat)
            run(BA.rencana_status(k, T, B_ID, status, Blok(False), HARI))
    k, bl, muat = rs("quarantine", {"qty": D("7")})
    monkeypatch.setattr(BA, "muat_batch", muat)
    r = run(BA.rencana_status(k, T, B_ID, "quarantine", bl, HARI))
    assert not bl.daftar and r["peringatan"] and "tidak bisa dijual selama karantina" in r["peringatan"][0]
    assert r["rencana"]["status_sesudah_text"] == "Karantina" and r["rencana"]["ringkas"] == "Batch LOT-A: Aktif \u2192 Karantina"
    k, bl, muat = rs("active", {"status": "quarantine", "expiry": HARI - dt.timedelta(days=2)})
    monkeypatch.setattr(BA, "muat_batch", muat)
    r = run(BA.rencana_status(k, T, B_ID, "active", bl, HARI))
    assert not bl.daftar and "tetap tampil Kedaluwarsa" in r["peringatan"][0] and r["rencana"]["status_sesudah_text"] == "Kedaluwarsa"


def test_pratinjau_status_bentuk_g3_dan_penulis_memakai_rencana_sama(monkeypatch):
    k = Kon()
    pasang(monkeypatch, k)
    kb = baris_batch("quarantine")

    async def muat(conn, tid, bid, kunci=False):
        return kb
    monkeypatch.setattr(BA, "muat_batch", muat)
    d = run(IB.preview_status_batch(req({}), B_ID, SetBatchStatusRequest(status="quarantine")))["data"]
    assert d["ok"] is False and d["blocks"] == [{"code": "BATCH_AKSI_TIDAK_TERSEDIA", "message": "Batch sudah dikarantina.", "field": "status"}]
    assert set(d) == {"ok", "blocks", "peringatan", "rencana", "payload"} and d["payload"] == {"status": "quarantine"}
    src_patch, src_post = inspect.getsource(IB.update_item_batch), inspect.getsource(IB.set_batch_status)
    assert "BA.rencana_status(" in src_patch and "Blok(False)" in src_patch               # penulis PATCH: rencana yang SAMA
    assert "update_item_batch(" in src_post                                                # POST /status = PATCH status (satu penulis)


def test_daftar_parameter_cocok_dengan_placeholder_untuk_semua_kunci(monkeypatch):
    """Temuan harness salinan: ?aksi=karantina/habis mengirim parameter tanggal yang tak dipakai SQL -> asyncpg menolak. Placeholder tertinggi == jumlah argumen."""
    import re
    for kunci in BA.KUNCI:
        for status in (None, "active", "expired", "quarantine", "depleted", "lain"):
            k = daftar(monkeypatch, aksi=kunci, status=status)
            sql, args = k.sql[0]
            tertinggi = max([int(x) for x in re.findall(r"\$(\d+)", sql)] or [0])
            assert tertinggi == len(args), (kunci, status, sql, args)
    for kunci in BA.KUNCI:
        assert (kunci in BA.KUNCI_BERTANGGAL) == ("{h}" in (BA.syarat(kunci, "{h}") or "")), kunci   # daftar kunci bertanggal == syarat yang memakai placeholder
