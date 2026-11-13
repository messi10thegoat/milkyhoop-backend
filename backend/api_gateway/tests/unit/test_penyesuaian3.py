"""penyesuaian-3 (putusan pemilik 12 Nov #7b): penyesuaian CEPAT (POST /items/{id}/stock-adjustment) disatukan ke penulis DOKUMEN SA (SAR._buat_draf +
SAR._tulis_posting): per gudang, harga pokok rata-rata, bernomor, bisa batal; alias akun adjustment_account_id; pagar minus dan barang tanpa stok dipertahankan.
Paritas lama/baru + dokumen/void nyata = uji salinan (scripts/ops/uji-salinan-persediaan.sh)."""
import asyncio
import datetime as dt
import inspect
import uuid
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.routers import items as IT
from app.routers import stock_adjustments as SAR
from app.services import stok_sesuai as SS
from app.services.galat_berkode import GalatBerkode

T = "kaos-biru-konveksi"
P, G, SA_ID, JID = (uuid.uuid4() for _ in range(4))


def req(body):
    import json
    raw = json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)
    r.state.user = {"tenant_id": T, "user_id": "00000000-0000-0000-0000-000000000001"}
    return r


class Kon:
    def __init__(self, goods=True, ledger_cost=D("1000")):
        self.goods, self.ledger_cost, self.exe = goods, ledger_cost, []

    def transaction(self, **kw):
        class Tx:
            async def __aenter__(s):
                return None

            async def __aexit__(s, *e):
                return False
        return Tx()

    async def execute(self, sql, *a):
        self.exe.append((sql, a))

    async def fetchrow(self, sql, *a):
        if "FROM products WHERE id = $1 AND tenant_id = $2" in sql:
            return {"item_type": "goods" if self.goods else "non_inventory", "track_inventory": True}
        raise AssertionError(sql[:70])

    async def fetchval(self, sql, *a):
        if "average_cost FROM inventory_ledger" in sql:
            return self.ledger_cost
        raise AssertionError(sql[:70])

    def acquire(self):
        kon = self

        class Ac:
            async def __aenter__(s):
                return kon

            async def __aexit__(s, *e):
                return False
        return Ac()


@pytest.fixture
def pasang(monkeypatch):
    cat = SimpleNamespace(rencana_buat_req=None, posting_lawan="BELUM", draf=0, tulis=[], stok=D("10"), harga=1000, jurnal=(JID, "SAJ-2611-0001"))

    async def tgl(conn, tid):
        return dt.date(2026, 11, 12)

    async def pre(pool, tid=None):
        return None

    async def info(conn, tid, pid):
        return {"id": pid, "name": "Kaos", "code": "K1", "unit": "pcs", "current_stock": 10, "unit_cost": cat.harga}

    async def gudang(conn, tid, wid, bl):
        return {"id": G, "name": "Gudang Utama", "is_active": True}

    async def stok(conn, tid, pid, wid):
        return cat.stok

    async def rbuat(conn, tid, body, bl):
        cat.rencana_buat_req = body
        return {"baris_draf": [{"unit_cost": cat.harga, "total_value": D("1000")}], "gudang": {"id": G}}

    async def rpost(conn, tid, sa_id, bl, kunci=False, counter_account_id=None):
        cat.posting_lawan = counter_account_id
        return {"sa": {"id": sa_id}, "baris": [(1, D("1"), D("0"), "x")] if cat.harga else [], "total": D("1000")}

    async def draf(conn, ctx, body, r):
        cat.draf += 1
        return SA_ID, "SA-2611-0001", D("1000")

    async def tulis(conn, ctx, aid, r):
        cat.tulis.append("tulis")
        return cat.jurnal if cat.harga else (None, None)

    async def pool():
        return Kon()
    kon = Kon()
    monkeypatch.setattr(IT, "tanggal_dokumen", tgl)
    monkeypatch.setattr(IT, "get_pool", lambda: _aw(SimpleNamespace(acquire=kon.acquire)))
    monkeypatch.setattr(SAR, "_ensure_stock_adj_role_preconditions", pre)
    monkeypatch.setattr(SAR, "get_product_info", info)
    monkeypatch.setattr(SAR, "_buat_draf", draf)
    monkeypatch.setattr(SAR, "_tulis_posting", tulis)
    monkeypatch.setattr(SS, "gudang", gudang)
    monkeypatch.setattr(SS, "stok_gudang", stok)
    monkeypatch.setattr(SS, "rencana_buat", rbuat)
    monkeypatch.setattr(SS, "rencana_posting", rpost)
    cat.kon = kon
    return cat


async def _aw(x):
    return x


def jalan(body, item=None):
    return asyncio.run(IT.create_stock_adjustment(req(body), str(item or P)))


def test_peta_tipe_set_add_subtract_ke_dokumen(pasang):
    r = jalan({"adjustment_type": "add", "quantity": 3, "reason": "Salah hitung"})
    q = pasang.rencana_buat_req
    assert (q.adjustment_type, q.items[0].quantity_adjustment, q.warehouse_id) == ("increase", D("3"), str(G)) and q.items[0].physical_quantity is None
    assert (r["old_stock"], r["new_stock"], r["adjustment"]) == ("10", "13", "3")
    r = jalan({"type": "decrease", "quantity": 4, "reason": "Rusak"})            # alias lama increase/decrease + kunci "type"
    q = pasang.rencana_buat_req
    assert (q.adjustment_type, q.items[0].quantity_adjustment) == ("decrease", D("-4")) and r["new_stock"] == "6"
    r = jalan({"quantity": 7, "reason": "Opname"})                                # bawaan = set -> hitung ulang, fisik = jumlah, selisih per gudang
    q = pasang.rencana_buat_req
    assert (q.adjustment_type, q.items[0].physical_quantity, q.items[0].quantity_adjustment) == ("recount", D("7"), D("-3")) and r["new_stock"] == "7"
    assert pasang.tulis == ["tulis"] * 3                                           # quick adjust memanggil SATU penulis (BUG-212: nilai 0 = tanpa jurnal di penulis itu)


def test_respons_lama_tetap_plus_medan_baru(pasang):
    r = jalan({"adjustment_type": "add", "quantity": 1, "reason": "x"})
    for k in ("success", "message", "old_stock", "new_stock", "adjustment", "journal_id", "unit_cost", "cost_source"):
        assert k in r                                                            # kontrak lama (FE lama)
    assert r["adjustment_number"] == "SA-2611-0001" and r["journal_number"] == "SAJ-2611-0001" and r["warehouse_name"] == "Gudang Utama"
    assert r["cost_source"] == "wac" and r["unit_cost"] == "1000" and "journal_note" not in r


def test_tanpa_harga_pokok_stok_bergerak_tanpa_jurnal_dengan_catatan(pasang):
    pasang.harga = 0
    pasang.kon.ledger_cost = None
    r = jalan({"adjustment_type": "add", "quantity": 2, "reason": "x"})
    assert r["journal_id"] is None and r["unit_cost"] == "0" and r["cost_source"] is None and "harga pokok" in r["journal_note"]
    assert r["new_stock"] == "12" and pasang.draf == 1


def test_minus_ditolak_tanpa_tulisan(pasang):
    with pytest.raises(GalatBerkode) as e:
        jalan({"adjustment_type": "subtract", "quantity": 11, "reason": "x"})
    assert (e.value.status_code, e.value.code) == (422, "STOK_TIDAK_CUKUP") and "tersedia 10" in e.value.detail
    assert pasang.draf == 0 and pasang.tulis == []


def test_set_sama_dengan_stok_tak_menulis(pasang):
    r = jalan({"adjustment_type": "set", "quantity": 10, "reason": "x"})
    assert r["message"] == "Stock unchanged" and r["adjustment"] == 0 and pasang.draf == 0


def test_barang_tanpa_stok_ditolak(pasang):
    pasang.kon.goods = False
    with pytest.raises(GalatBerkode) as e:
        jalan({"adjustment_type": "add", "quantity": 1, "reason": "x"})
    assert (e.value.status_code, e.value.code) == (422, "BARANG_TANPA_STOK") and pasang.draf == 0


def test_alias_akun_lawan_kedua_nama(pasang):
    jalan({"adjustment_type": "add", "quantity": 1, "reason": "x", "adjustment_account_id": "A-FE"})
    assert pasang.posting_lawan == "A-FE"                                           # nama yang SUDAH dikirim FE form lama
    jalan({"adjustment_type": "add", "quantity": 1, "reason": "x", "adjustment_account_id": "A-FE", "counter_account_id": "A-BARU"})
    assert pasang.posting_lawan == "A-BARU"
    jalan({"adjustment_type": "add", "quantity": 1, "reason": "x"})
    assert pasang.posting_lawan is None


@pytest.mark.parametrize("body,kode,status", [
    ({"adjustment_type": "bagi", "quantity": 1, "reason": "x"}, "TIPE_TAK_SAH", 422),
    ({"adjustment_type": "add", "reason": "x"}, "JUMLAH_WAJIB", 422),
    ({"adjustment_type": "add", "quantity": "abc", "reason": "x"}, "JUMLAH_TAK_SAH", 422),
    ({"adjustment_type": "add", "quantity": 0, "reason": "x"}, "JUMLAH_HARUS_POSITIF", 422),
    ({"adjustment_type": "add", "quantity": -5, "reason": "x"}, "JUMLAH_HARUS_POSITIF", 422),   # BatchExpiry kirim negatif: tetap ditolak (rusak sebelum unit ini)
    ({"adjustment_type": "add", "quantity": 1}, "ALASAN_WAJIB", 422),
])
def test_validasi_badan_lama_berkode(pasang, body, kode, status):
    with pytest.raises(GalatBerkode) as e:
        jalan(body)
    assert (e.value.code, e.value.status_code) == (kode, status) and pasang.draf == 0


def test_id_barang_tak_sah_400(pasang):
    with pytest.raises(GalatBerkode) as e:
        asyncio.run(IT.create_stock_adjustment(req({"adjustment_type": "add", "quantity": 1, "reason": "x"}), "bukan-uuid"))
    assert (e.value.status_code, e.value.code) == (400, "ID_BARANG_TAK_SAH")


def test_satu_penulis_tak_ada_sql_jurnal_sendiri_dan_kunci_stok():
    src = inspect.getsource(IT.create_stock_adjustment)
    assert "SAR._buat_draf(" in src and "SAR._tulis_posting(" in src and "tanpa_jurnal_bila_nol" not in src
    assert "INSERT INTO journal_entries" not in src and "INSERT INTO inventory_ledger" not in src and "record_inventory_inbound" not in src
    assert 'f"STOCK:{item_id}:{g[\'id\']}"' in src and 'f"STOCK_ADJ_CREATE:{item_id}"' in src
    assert src.index("STOCK:{item_id}") < src.index("SS_PEN.stok_gudang(")          # kunci SEBELUM membaca stok
    assert "sales_price" not in src                                                 # fallback harga jual DIBUANG (putusan 12 Nov)


def test_tulis_posting_nilai_nol_tanpa_jurnal_untuk_dokumen_dan_quick(monkeypatch):
    """BUG-212 (putusan pemilik 13 Nov): total nilai 0 = TANPA jurnal, stok tetap bergerak -- SATU aturan penulis (dokumen DAN quick adjust)."""
    class K:
        def __init__(self):
            self.exe = []

        async def execute(self, sql, *a):
            self.exe.append(" ".join(sql.split()))

        async def fetchrow(self, sql, *a):
            return None
    sa = {"adjustment_number": "SA-1", "adjustment_type": "increase", "adjustment_date": dt.date(2026, 11, 12), "storage_location_id": None}
    r = {"sa": sa, "baris": [], "gerak": [], "gudang": {"id": G}, "total": D("0")}
    ctx = {"tenant_id": T, "user_id": "u"}
    k = K()
    jid, jn = asyncio.run(SAR._tulis_posting(k, ctx, SA_ID, r))
    assert (jid, jn) == (None, None) and not any("INSERT INTO journal_entries" in s for s in k.exe) and any("UPDATE stock_adjustments" in s for s in k.exe)

    async def nomor(conn, tid, no, pfx):
        return "SAJ-1"
    monkeypatch.setattr(SAR.NJ, "nomor_jurnal_dokumen", nomor)
    k2 = K()
    r2 = dict(r, baris=[("akun-a", D("5"), D("0"), "m1"), ("akun-b", D("0"), D("5"), "m2")], total=D("5"))
    jid, jn = asyncio.run(SAR._tulis_posting(k2, ctx, SA_ID, r2))                    # nilai > 0: jurnal tetap ditulis
    assert jid is not None and jn == "SAJ-1" and any("INSERT INTO journal_entries" in s for s in k2.exe)
