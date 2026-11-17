"""BUG-261 (putusan pemilik LANGSUNG 17 Nov 2026; V503): Penyesuaian Persediaan -- gudang PER BARIS + baris yang menyelesaikan selisih terima Pemindahan (Transit, terkunci).
Tertunda = dikirim - diterima - Σ baris penyesuaian POSTED non-void bertaut. Penentu SATU: SS.susun_baris (buat/ubah/pratinjau), SS.rencana_posting (kunci + revalidasi), pd2.item_baris."""
import asyncio
import inspect
import re
import uuid
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.routers import stock_adjustments as RA
from app.routers import stock_transfers as RT
from app.services import pemindahan_dua_langkah as D2
from app.services import stok_sesuai as SS
from app.services.bayar_keluar_rencana import Blok

BE = Path(__file__).resolve().parents[2]
MIG = BE.parent / "migrations"
T = "kaos-biru-konveksi"
GD, W2, TR = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()          # gudang dokumen, gudang lain, Transit (sistem)
A, B = uuid.uuid4(), uuid.uuid4()
PM = uuid.uuid4()                                               # pemindahan IT-1: A dikirim 10, diterima 8 (tertunda 2); B dikirim 5, diterima 5
PM_RECEIVED = uuid.uuid4()


def jalan(aw):
    return asyncio.run(aw)


class Kon:
    """Tiruan asyncpg. diselesaikan = {product_id: qty} (baris penyesuaian POSTED bertaut PM). status = status pemindahan PM."""
    BRG = {A: ("Kaos", 1000), B: ("Benang", 500)}
    STOK = {}

    def __init__(self, diselesaikan=None, status="in_transit"):
        self.diselesaikan, self.status, self.q = diselesaikan or {}, status, []
        self.STOK = {(A, TR): D("2"), (B, TR): D("0"), (A, GD): D("20"), (A, W2): D("7")}

    async def fetchrow(self, sql, *a):
        self.q.append(sql)
        if "FROM products p" in sql:
            n, h = self.BRG[a[0]]
            return {"id": a[0], "name": n, "unit": "Pcs", "code": "K", "purchase_price": h}
        if "SELECT item_type, track_inventory" in sql and "FROM products" in sql:
            return {"item_type": "goods", "track_inventory": True, "unit": "Pcs"}
        if "FROM warehouses WHERE id = $1 AND tenant_id = $2 AND NOT is_system" in sql:
            return {"id": a[0], "name": "Gudang Dua", "is_active": True} if a[0] == W2 else None      # Transit = gudang sistem -> tak bisa dipilih lewat jalur biasa
        if "FROM stock_transfers WHERE id = $1 AND tenant_id = $2" in sql:
            if a[0] == PM:
                return {"id": PM, "transfer_number": "IT-1", "status": self.status}
            if a[0] == PM_RECEIVED:
                return {"id": PM_RECEIVED, "transfer_number": "IT-2", "status": "received"}
            return None
        return None

    async def fetchval(self, sql, *a):
        if "SELECT average_cost FROM inventory_ledger" in sql:
            return self.BRG[a[1]][1]
        if "is_system AND code" in sql:
            return TR
        if "SELECT name FROM warehouses" in sql:
            return "Transit" if a[0] == TR else "Gudang Dua"
        if "warehouse_id = $3" in sql:
            return self.STOK.get((a[1], a[2]), D("0"))
        return D("0")

    async def fetch(self, sql, *a):
        self.q.append(sql)
        if "RELOKASI_TERIMA" in sql or "il.source_type" in sql:
            return [{"product_id": A, "diterima": D("8")}, {"product_id": B, "diterima": D("5")}]
        if "stock_adjustment_items" in sql:
            return [{"product_id": p, "selesai": q} for p, q in self.diselesaikan.items()]
        if "stock_transfer_items" in sql:
            return [{"id": uuid.uuid4(), "item_id": A, "item_name": "Kaos", "item_code": "K", "unit": "Pcs", "quantity_shipped": D("10"), "unit_cost": D("1000")},
                    {"id": uuid.uuid4(), "item_id": B, "item_name": "Benang", "item_code": "B", "unit": "Pcs", "quantity_shipped": D("5"), "unit_cost": D("500")}]
        return []


def baris(pid, qty, gudang=None, transfer=None, alasan=None):
    return SimpleNamespace(product_id=str(pid), quantity_adjustment=D(str(qty)), physical_quantity=None, reason_detail=alasan,
                           warehouse_id=None if gudang is None else str(gudang), source_transfer_id=None if transfer is None else str(transfer))


def susun(tipe, items, kon=None):
    bl = Blok(True)
    hasil = jalan(SS.susun_baris(kon or Kon(), T, items, tipe, GD, bl, []))
    return hasil, [b["code"] for b in bl.daftar], bl.daftar


# ------------------------------------------------------------------ migrasi
def test_v503_aditif_nullable_dan_pembalik():
    v = (MIG / "V503__penyesuaian_gudang_baris_sumber_pemindahan.sql").read_text()
    r = (MIG / "V503__penyesuaian_gudang_baris_sumber_pemindahan_ROLLBACK.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS warehouse_id UUID REFERENCES warehouses(id)" in v and "ADD COLUMN IF NOT EXISTS source_transfer_id UUID REFERENCES stock_transfers(id)" in v
    assert not re.search(r"(?<!IS )NOT NULL|DEFAULT|UPDATE |INSERT |DELETE ", v.split("\n", 3)[3])                      # tanpa backfill/default/NOT NULL di pernyataan
    assert not re.search(r"^\s*(BEGIN|COMMIT|ROLLBACK)\s*;", v + r, re.M)                                        # apply_mig membungkus sendiri
    assert "DROP COLUMN IF EXISTS source_transfer_id" in r and "DROP COLUMN IF EXISTS warehouse_id" in r


# ------------------------------------------------------------------ gudang per baris
def test_gudang_per_baris_stok_sebelum_per_gudang_dan_kosong_ikut_dokumen():
    h, bl, _ = susun("increase", [baris(A, 3), baris(B, 1, gudang=W2)])
    assert bl == [] and h[0]["warehouse_id"] is None and h[0]["quantity_before"] == D("20")            # kosong = gudang dokumen (perilaku lama)
    assert h[1]["warehouse_id"] == W2 and h[1]["quantity_before"] == D("0")


def test_barang_sama_beda_gudang_boleh_sama_gudang_ganda():
    h, bl, _ = susun("increase", [baris(A, 1), baris(A, 2, gudang=W2)])
    assert bl == [] and [x["quantity_before"] for x in h] == [D("20"), D("7")]
    _, kode, _ = susun("increase", [baris(A, 1), baris(A, 2)])                                          # sama gudang (keduanya gudang dokumen) = ganda
    assert kode == ["PENYESUAIAN_BARANG_GANDA"]
    _, kode2, _ = susun("increase", [baris(A, 1, gudang=W2), baris(A, 2, gudang=W2)])
    assert kode2 == ["PENYESUAIAN_BARANG_GANDA"]


def test_gudang_sistem_tak_bisa_dipilih_lewat_baris_biasa():
    _, kode, _ = susun("increase", [baris(A, 1, gudang=TR)])
    assert kode == ["GUDANG_TIDAK_DITEMUKAN"]


# ------------------------------------------------------------------ tarik dari Pemindahan
def test_baris_pemindahan_gudang_transit_terkunci_alasan_hilang_dan_sebagian_boleh():
    h, kode, _ = susun("decrease", [baris(A, -2, transfer=PM)])
    assert kode == [] and h[0]["warehouse_id"] == TR and h[0]["source_transfer_id"] == PM and h[0]["quantity_before"] == D("2")
    assert h[0]["reason_detail"] == "Hilang (selisih terima IT-1)" and h[0]["source_transfer_number"] == "IT-1"
    h2, kode2, _ = susun("decrease", [baris(A, -1, transfer=PM, alasan="Rusak di jalan")])       # sebagian (< tertunda) boleh; alasan pengguna dipertahankan
    assert kode2 == [] and h2[0]["reason_detail"] == "Rusak di jalan"


def test_baris_pemindahan_melebihi_tertunda_ditolak_dan_penyelesaian_mengurangi_tertunda():
    _, kode, bl = susun("decrease", [baris(A, -3, transfer=PM)])
    assert kode == ["PENYESUAIAN_MELEBIHI_TERTUNDA"] and "tertunda Pemindahan IT-1 (2)" in bl[0]["message"]
    # sudah diselesaikan 1 oleh penyesuaian POSTED lain -> tertunda 1: minta 2 ditolak, minta 1 lolos
    _, kode2, _ = susun("decrease", [baris(A, -2, transfer=PM)], Kon(diselesaikan={A: D("1")}))
    assert kode2 == ["PENYESUAIAN_MELEBIHI_TERTUNDA"]
    h, kode3, _ = susun("decrease", [baris(A, -1, transfer=PM)], Kon(diselesaikan={A: D("1")}))
    assert kode3 == [] and h[0]["quantity_adjustment"] == D("-1")


def test_baris_pemindahan_aturan_jenis_gudang_status_dan_barang():
    assert susun("increase", [baris(A, 1, transfer=PM)])[1] == ["PENYESUAIAN_PEMINDAHAN_JENIS"]
    assert susun("decrease", [baris(A, -1, gudang=W2, transfer=PM)])[1] == ["PENYESUAIAN_GUDANG_TERKUNCI"]
    assert susun("decrease", [baris(A, -1, gudang=TR, transfer=PM)])[1] == []                        # gudang = Transit sama dgn yang terkunci: sah
    assert susun("decrease", [baris(A, -1, transfer=PM)], Kon(status="received"))[1] == ["PENYESUAIAN_PEMINDAHAN_STATUS"]
    assert susun("decrease", [baris(A, -1, transfer=uuid.uuid4())])[1] == ["PENYESUAIAN_PEMINDAHAN_TIDAK_ADA"]
    assert susun("decrease", [baris(B, -1, transfer=PM)])[1] == ["PENYESUAIAN_MELEBIHI_TERTUNDA"]   # B diterima penuh (tertunda 0)


# ------------------------------------------------------------------ tertunda (pd2)
def test_item_baris_tertunda_dikirim_diterima_diselesaikan():
    items = jalan(D2.item_baris(Kon(diselesaikan={A: D("1")}), T, PM))
    a = [i for i in items if i["item_id"] == A][0]
    assert (a["quantity_received"], a["quantity_variance"], a["quantity_resolved"], a["quantity_pending"]) == (D("8"), D("2"), D("1"), D("1"))
    b = [i for i in items if i["item_id"] == B][0]
    assert b["quantity_pending"] == D("0")


def test_terima_tak_boleh_melebihi_tertunda_setelah_penyelesaian():
    k = Kon(diselesaikan={A: D("2")})
    items = jalan(D2.item_baris(k, T, PM))
    h = {"status": "in_transit", "transfer_number": "IT-1", "to_warehouse_id": GD, "id": PM}

    class K2(Kon):
        async def fetchrow(self, sql, *a):
            return {"id": GD, "name": "Gudang Dokumen", "is_active": True} if "FROM warehouses" in sql else await super().fetchrow(sql, *a)

        async def fetchval(self, sql, *a):
            return D("5") if "warehouse_stock" in sql else await super().fetchval(sql, *a)
    r = jalan(D2.rencana_terima(K2(diselesaikan={A: D("2")}), T, h, items, [{"item_id": A, "qty": D("1")}]))
    assert [b["code"] for b in r["blocks"]] == ["PB_TERIMA_QTY_MELEBIHI"]                           # tertunda A = 10 - 8 - 2 = 0
    r2 = jalan(D2.rencana_terima(K2(), T, h, jalan(D2.item_baris(K2(), T, PM)), [{"item_id": A, "qty": D("2")}]))
    assert r2["blocks"] == [] and r2["tuntas"] is True


def test_void_kirim_diblok_bila_ada_penyelesaian():
    k = Kon(diselesaikan={A: D("1")})
    r = jalan(D2.rencana_void_kirim(k, T, {"id": PM, "status": "in_transit", "transfer_number": "IT-1"}, False))
    assert [b["code"] for b in r["blocks"]] == ["PB_SUDAH_ADA_PENYELESAIAN"]


# ------------------------------------------------------------------ posting: kunci + revalidasi
class KonPosting(Kon):
    """Dokumen draf berisi satu baris penyelesaian PM (qty 2) di Transit."""
    SA = {"id": uuid.uuid4(), "status": "draft", "adjustment_date": __import__("datetime").date(2026, 11, 17), "adjustment_number": "SA-1", "warehouse_id": GD,
          "adjustment_type": "decrease", "storage_location_id": None, "journal_id": None, "total_value": D("2000")}

    def __init__(self, qty="2", **kw):
        super().__init__(**kw)
        self.qty, self.kunci = qty, []

    async def fetchrow(self, sql, *a):
        if "FROM stock_adjustments WHERE id = $1" in sql:
            return self.SA
        if "FROM fiscal_periods" in sql:
            return {"id": 1, "period_name": "2026-11", "status": "OPEN"}
        if "FROM warehouses WHERE id = $1 AND tenant_id = $2" in sql and "NOT is_system" in sql:
            return {"id": GD, "name": "Gudang Dokumen", "is_active": True}
        return await super().fetchrow(sql, *a)

    async def fetch(self, sql, *a):
        if "SELECT i.* FROM stock_adjustment_items i" in sql:
            return [_Rec({"id": uuid.uuid4(), "product_id": A, "product_code": "K", "product_name": "Kaos", "quantity_adjustment": -D(self.qty), "unit_cost": 1000,
                          "total_value": 1000 * D(self.qty), "warehouse_id": TR, "source_transfer_id": PM, "line_number": 1})]
        return await super().fetch(sql, *a)

    async def execute(self, sql, *a):
        self.kunci.append((sql, a))


class _Rec(dict):
    pass


@pytest.fixture(autouse=True)
def _akun(monkeypatch):
    async def peran(conn, tid, role):
        return uuid.uuid4()
    monkeypatch.setattr(SS, "resolve_account_id_by_role", peran)


def test_posting_baris_pemindahan_gerak_di_transit_dan_kunci_stok_transit():
    k = KonPosting()
    bl = Blok(True)
    r = jalan(SS.rencana_posting(k, T, KonPosting.SA["id"], bl, kunci=True))
    assert bl.daftar == [] and r["gerak"][0]["warehouse_id"] == TR and r["gerak"][0]["keluar"] == D("2") and r["gerak"][0]["gudang"] == "Transit"
    assert any("pg_advisory_xact_lock" in s and a[0] == f"STOCK:{A}:{TR}" for s, a in k.kunci)      # kunci SAMA dgn Terima (STOCK:<barang>:<Transit>)


def test_posting_revalidasi_melebihi_tertunda_dan_pemindahan_tak_lagi_transit():
    k = KonPosting(qty="3")                                                                          # tertunda 2 < 3 (mis. Terima/penyesuaian lain sejak draf)
    bl = Blok(True)
    jalan(SS.rencana_posting(k, T, KonPosting.SA["id"], bl))
    assert "PENYESUAIAN_MELEBIHI_TERTUNDA" in [b["code"] for b in bl.daftar]
    k2 = KonPosting(status="received")
    bl2 = Blok(True)
    jalan(SS.rencana_posting(k2, T, KonPosting.SA["id"], bl2))
    assert "PENYESUAIAN_PEMINDAHAN_STATUS" in [b["code"] for b in bl2.daftar]


def test_posting_stok_transit_kurang_diblok():
    k = KonPosting()
    k.STOK[(A, TR)] = D("1")                                                                        # Transit hanya 1, diminta 2
    bl = Blok(True)
    jalan(SS.rencana_posting(k, T, KonPosting.SA["id"], bl))
    assert "PENYESUAIAN_STOK_TRANSIT_KURANG" in [b["code"] for b in bl.daftar]


# ------------------------------------------------------------------ Ambil dari Pemindahan (baca)
def test_tarik_pemindahan_baris_siap_pakai():
    d = jalan(SS.tarik_pemindahan(Kon(), T, PM))
    assert d["ok"] and d["adjustment_type"] == "decrease" and d["gudang"] == {"id": str(TR), "name": "Transit", "terkunci": True}
    assert len(d["baris"]) == 1                                                                      # B tertunda 0: tak ikut
    b = d["baris"][0]
    assert b["quantity_adjustment"] == "-2" and b["gudang_terkunci"] is True and b["source_transfer_id"] == str(PM) and b["reason_detail"] == "Hilang (selisih terima IT-1)"
    assert jalan(SS.tarik_pemindahan(Kon(), T, uuid.uuid4())) is None
    d2 = jalan(SS.tarik_pemindahan(Kon(status="received"), T, PM))
    assert not d2["ok"] and d2["blocks"][0]["code"] == "PENYESUAIAN_PEMINDAHAN_STATUS"
    d3 = jalan(SS.tarik_pemindahan(Kon(diselesaikan={A: D("2")}), T, PM))
    assert not d3["ok"] and d3["blocks"][0]["code"] == "PENYESUAIAN_TIDAK_ADA_TERTUNDA"


# ------------------------------------------------------------------ kabel router
def test_router_tulis_kolom_baru_dan_gerak_per_gudang_baris():
    s = inspect.getsource(RA._tulis_baris_draf)
    assert "warehouse_id, source_transfer_id" in s and 'b.get("warehouse_id"), b.get("source_transfer_id")' in s
    p = inspect.getsource(RA._tulis_posting)
    assert 'adj_warehouse_id = g.get("warehouse_id") or r["gudang"]["id"]' in p
    d = inspect.getsource(RA.get_stock_adjustment)
    assert "i.source_transfer_id" in d and '"gudang_terkunci"' in d and '"source_transfer_number"' in d
    rutes = [r.path for r in RA.router.routes]
    assert rutes.index("/dari-pemindahan") < rutes.index("/{adjustment_id}")      # statis sebelum /{adjustment_id}
    assert rutes.index("/dari-pemindahan/{transfer_id}") < rutes.index("/{adjustment_id}")


def test_variance_k9_mengurangi_penyelesaian():
    s = inspect.getsource(RT.get_transfer_variance)
    assert "stock_adjustment_items" in s and "sa.status = 'posted'" in s and "d.diterima + r.selesai" in s and 'r.get("quantity_resolved")' in s


def test_detail_pemindahan_meneruskan_diselesaikan_ke_teks_status():
    src = inspect.getsource(RT)
    assert src.count("pd2.teks_status(row[\"status\"], dikirim, diterima, selesai)") == 1 and 'pd2.teks_status(row["status"], _dik, _dit, _sel)' in src


# ------------------------------------------------------------------ SQL tertunda: hanya POSTED (void/draf tak terhitung), tenant disaring
def test_sql_diselesaikan_hanya_posted_dan_tersaring_tenant():
    for sql in (D2.SQL_DISELESAIKAN, D2.SQL_DISELESAIKAN_BANYAK):
        assert "s.status = 'posted'" in sql and "s.tenant_id = $1" in sql and "ABS(i.quantity_adjustment)" in sql and "i.source_transfer_id" in sql
    assert "sa.status = 'posted'" in SS._SQL_TERTUNDA and "sa.tenant_id = st.tenant_id" in SS._SQL_TERTUNDA and "st.status = 'in_transit'" in SS._SQL_TERTUNDA
    assert "st.tenant_id = $1" in SS._SQL_TERTUNDA and "fw.tenant_id = st.tenant_id" in SS._SQL_TERTUNDA


# ------------------------------------------------------------------ batal penyesuaian bertaut: pemindahan harus masih di Transit
class KonBatal(KonPosting):
    SA = dict(KonPosting.SA, status="posted", journal_id=None)

    def __init__(self, status_pm="in_transit", **kw):
        super().__init__(**kw)
        self.status_pm = status_pm

    async def fetchrow(self, sql, *a):
        if "FROM stock_adjustments WHERE id = $1" in sql:
            return self.SA
        return await super().fetchrow(sql, *a)

    async def fetch(self, sql, *a):
        if "SELECT DISTINCT t.transfer_number, t.status" in sql:
            return [{"transfer_number": "IT-1", "status": self.status_pm}]
        if "FROM inventory_ledger WHERE tenant_id = $1 AND source_type = 'STOCK_ADJUSTMENT'" in sql:
            return []
        return await super().fetch(sql, *a)


def test_batal_penyesuaian_diblok_bila_pemindahan_tak_lagi_transit():
    bl = Blok(True)
    jalan(SS.rencana_batal(KonBatal(status_pm="received"), T, KonBatal.SA["id"], "salah", bl))
    assert [b["code"] for b in bl.daftar] == ["PENYESUAIAN_PEMINDAHAN_STATUS"]
    bl2 = Blok(True)
    jalan(SS.rencana_batal(KonBatal(), T, KonBatal.SA["id"], "salah", bl2))
    assert bl2.daftar == []
