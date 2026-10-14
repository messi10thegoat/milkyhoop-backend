"""Pill AKSI daftar Pesanan (W-20261014-AKSI-SO, 14 Okt 2026; putusan MASTER B1). Pola quote 864f5ed2: ?aksi=<kunci>,
`aksi` per baris = kunci paling mendesak, /summary <kunci>_count/_value -- SATU sumber (services/so_aksi.fakta)."""
import re
import typing
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal as D

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import sales_orders as SO
from app.schemas.sales_orders import SalesOrderListItem, SalesOrderSummary
from app.services import sj_so, so_aksi as A, so_kirim

TENANT = "kaos-biru-konveksi"
HARI = date(2026, 10, 14)


def _b(q, perlu, stok, kirim=0, fak=0):
    return {"quantity": D(str(q)), "perlu": perlu, "stok": stok, "terkirim": D(str(kirim)), "difaktur": D(str(fak))}


# ---------- aksi_so murni ----------

def test_draf_hanya_perlu_dikonfirmasi_walau_baris_bersisa():
    assert A.aksi_so("draft", date(2026, 1, 1), HARI, [_b(5, True, True)]) == ["perlu_dikonfirmasi"]


@pytest.mark.parametrize("st", ["cancelled", "completed"])
def test_arsip_tanpa_aksi(st):
    assert A.aksi_so(st, date(2026, 1, 1), HARI, [_b(5, True, True), _b(3, False, False)]) == []


def test_sisa_kirim_tanggal_lewat_telat_dan_perlu_dikirim():
    assert A.aksi_so("confirmed", date(2026, 10, 13), HARI, [_b(5, True, True, kirim=2)]) == ["telat_kirim", "perlu_dikirim", "perlu_ditagih"]


def test_tanggal_hari_ini_atau_kosong_bukan_telat():
    for tgl in (HARI, date(2026, 10, 20), None):
        assert "telat_kirim" not in A.aksi_so("confirmed", tgl, HARI, [_b(5, True, True)])


def test_tanggal_lewat_tanpa_sisa_kirim_bukan_telat():
    assert A.aksi_so("partial_invoiced", date(2026, 9, 1), HARI, [_b(5, True, True, kirim=5, fak=2)]) == ["perlu_ditagih"]


def test_b1_non_stok_wajib_serah_bisa_ditagih_penuh_sebelum_kirim():
    # grapgrap: perlu_kirim=true (flag serah) pada barang NON-stok -> tagih = qty pesan (B1), kirim tetap bersisa
    assert A.aksi_so("confirmed", None, HARI, [_b(4, True, False)]) == ["perlu_dikirim", "perlu_ditagih"]
    assert A.aksi_so("invoiced", None, HARI, [_b(4, True, False, fak=4)]) == ["perlu_dikirim"]


def test_b1_barang_stok_tagih_hanya_yang_terkirim():
    assert A.aksi_so("confirmed", None, HARI, [_b(5, True, True)]) == ["perlu_dikirim"]          # 0 terkirim -> belum bisa ditagih
    assert A.aksi_so("partial_shipped", None, HARI, [_b(5, True, True, kirim=3, fak=3)]) == ["perlu_dikirim"]
    assert A.aksi_so("partial_shipped", None, HARI, [_b(5, True, True, kirim=3, fak=2)]) == ["perlu_dikirim", "perlu_ditagih"]


def test_stok_tanpa_wajib_kirim_tetap_tagih_dari_terkirim_bukan_kirim():
    # perlu_kirim=false eksplisit pada barang stok: tak ada sisa kirim; tagih tetap mengikuti terkirim (B1 = stok)
    assert A.aksi_so("confirmed", None, HARI, [_b(5, False, True)]) == []


def test_semua_beres_tanpa_aksi_dan_tanpa_baris():
    assert A.aksi_so("invoiced", None, HARI, [_b(2, False, False, fak=2), _b(1, True, True, kirim=1, fak=1)]) == []
    assert A.aksi_so("confirmed", date(2026, 1, 1), HARI, []) == []


def test_lebih_kirim_atau_lebih_faktur_tak_negatif_menjadi_aksi():
    assert A.aksi_so("invoiced", None, HARI, [_b(2, True, True, kirim=3, fak=3)]) == []


def test_aksi_utama_urutan_mendesak():
    assert A.AKSI == ("telat_kirim", "menunggu_dp", "perlu_dikonfirmasi", "perlu_dikirim", "siap_diserahkan",
                      "perlu_ditagih")  # AKSI-SO-2 (MASTER 14 Okt)
    assert A.aksi_utama(["perlu_dikirim", "perlu_ditagih"]) == "perlu_dikirim"
    assert A.aksi_utama([]) is None


# ---------- satu predikat faktur terbit ----------

def test_sql_faktur_terbit_lama_tak_berubah_dan_predikat_dipakai_bersama():
    lama = """
    SELECT sii.sales_order_item_id AS soi_id, si.id AS invoice_id, si.invoice_number, si.invoice_date, SUM(sii.quantity) AS q
    FROM sales_invoices si JOIN sales_invoice_items sii ON sii.invoice_id = si.id
    WHERE si.tenant_id = $1 AND si.sales_order_id = $2 AND si.status NOT IN ('draft', 'void') AND sii.sales_order_item_id IS NOT NULL
    GROUP BY 1, 2, 3, 4 ORDER BY si.invoice_date, si.invoice_number
"""
    assert sj_so.SQL_FAKTUR_TERBIT_PER_BARIS == lama
    assert sj_so.PRED_FAKTUR_TERBIT in A.SQL_FAKTUR_TERBIT_PER_BARIS_SO
    assert "quantity_invoiced" not in A.SQL_FAKTUR_TERBIT_PER_BARIS_SO + A.SQL_BARIS


def test_ekspresi_wajib_kirim_sama_dengan_kartu_menunggu_kirim():
    import inspect
    assert "COALESCE(soi.perlu_kirim, p.track_inventory, false)" in A.SQL_BARIS
    assert "COALESCE(soi.perlu_kirim, p.track_inventory, false)" in inspect.getsource(so_kirim.ringkasan_menunggu_kirim)


# ---------- fakta: koneksi tiruan KETAT ----------

class _Conn:
    """Kueri tak dikenal -> AssertionError; $N wajib == 1..len(args) (asyncpg: tipe parameter tak tertentu)."""

    def __init__(self, so, baris, kirim, fak, pf=()):
        self.so, self.baris, self.kirim, self.fak, self.pf, self.q = so, baris, kirim, fak, list(pf), []

    async def fetch(self, q, *a):
        n = {int(x) for x in re.findall(r"\$(\d+)", q)}
        assert n == set(range(1, len(a) + 1)), (q, a)
        assert a[0] == TENANT
        self.q.append((q, a))
        if "FROM sales_orders WHERE tenant_id" in q:
            ids = set(a[1]) if len(a) > 1 else None
            return [r for r in self.so if ids is None or r["id"] in ids]
        if "invoice_fulfillment_items" in q:
            return [r for r in self.kirim if r["so_id"] in set(a[1])]
        if "SUM(sii.quantity) AS q" in q:
            return [r for r in self.fak if r["so_id"] in set(a[1])]
        if "JOIN sales_order_items soi" in q:
            return [r for r in self.baris if r["so_id"] in set(a[1])]
        if "FROM proformas" in q:  # AKSI-SO-2: so_posisi.id_menunggu_dp
            return [r for r in self.pf if r["sales_order_id"] in set(a[1])]
        raise AssertionError(q)


def _dunia():
    s = {k: uuid.uuid4() for k in ("draf", "telat", "tagih", "batal", "beres")}
    i = {k: uuid.uuid4() for k in ("telat", "tagih", "batal", "beres")}
    so = [{"id": s["draf"], "status": "draft", "expected_ship_date": None, "total_amount": D("100")},
          {"id": s["telat"], "status": "confirmed", "expected_ship_date": date(2026, 10, 1), "total_amount": D("1000")},
          {"id": s["tagih"], "status": "confirmed", "expected_ship_date": None, "total_amount": D("250.50")},
          {"id": s["batal"], "status": "cancelled", "expected_ship_date": date(2026, 1, 1), "total_amount": D("9")},
          {"id": s["beres"], "status": "invoiced", "expected_ship_date": None, "total_amount": D("7")}]
    baris = [{"id": i["telat"], "so_id": s["telat"], "quantity": D("4"), "perlu": True, "stok": True},
             {"id": i["tagih"], "so_id": s["tagih"], "quantity": D("2"), "perlu": False, "stok": False},
             {"id": i["batal"], "so_id": s["batal"], "quantity": D("2"), "perlu": True, "stok": True},
             {"id": i["beres"], "so_id": s["beres"], "quantity": D("3"), "perlu": True, "stok": False}]
    kirim = [{"so_id": s["telat"], "soi_id": i["telat"], "terkirim": D("1")},
             {"so_id": s["beres"], "soi_id": i["beres"], "terkirim": D("3")}]
    fak = [{"so_id": s["telat"], "soi_id": i["telat"], "q": D("1")}, {"so_id": s["beres"], "soi_id": i["beres"], "q": D("3")}]
    return s, _Conn(so, baris, kirim, fak)


@pytest.mark.asyncio
async def test_fakta_seluruh_tenant_dan_arsip_tak_dikueri_barisnya():
    s, c = _dunia()
    f = await A.fakta(c, TENANT, HARI)
    assert f[s["draf"]]["aksi"] == ["perlu_dikonfirmasi"]
    assert f[s["telat"]]["aksi"] == ["telat_kirim", "perlu_dikirim"]      # stok: terkirim 1 = difaktur 1
    assert f[s["tagih"]]["aksi"] == ["perlu_ditagih"]
    assert f[s["batal"]]["aksi"] == [] and f[s["beres"]]["aksi"] == []
    for q, a in c.q[1:]:
        assert s["batal"] not in a[1] and s["draf"] not in a[1]          # hanya SO berjalan yang barisnya dihitung
    assert A.id_aksi(f, "perlu_ditagih") == [s["tagih"]]
    r = A.ringkasan(f)
    assert r == {"telat_kirim_count": 1, "telat_kirim_value": 1000.0, "perlu_dikonfirmasi_count": 1,
                 "perlu_dikonfirmasi_value": 100.0, "perlu_dikirim_count": 1, "perlu_dikirim_value": 1000.0,
                 "perlu_ditagih_count": 1, "perlu_ditagih_value": 250.5, "menunggu_dp_count": 0,
                 "menunggu_dp_value": 0.0, "siap_diserahkan_count": 0, "siap_diserahkan_value": 0.0}


@pytest.mark.asyncio
async def test_fakta_subset_id_dan_kosong():
    s, c = _dunia()
    f = await A.fakta(c, TENANT, HARI, [s["tagih"], s["batal"]])
    assert set(f) == {s["tagih"], s["batal"]}
    assert await A.fakta(c, TENANT, HARI, []) == {}


# ---------- kontrak rute + skema ----------

def test_kunci_rute_skema_sama_dengan_AKSI():
    ann = typing.get_type_hints(SO.list_sales_orders)["aksi"]
    lit = next(x for x in typing.get_args(ann) if x is not type(None))
    assert set(typing.get_args(lit)) == set(A.AKSI)
    for k in A.AKSI:
        assert f"{k}_count" in SalesOrderSummary.model_fields and f"{k}_value" in SalesOrderSummary.model_fields
    assert "aksi" in SalesOrderListItem.model_fields


def _klien(monkeypatch):
    app = FastAPI()

    @app.middleware("http")
    async def user(request, call_next):
        request.state.user = {"tenant_id": TENANT, "user_id": str(uuid.uuid4())}
        return await call_next(request)
    app.include_router(SO.router, prefix="/api/sales-orders")
    return TestClient(app)


def test_kunci_asing_422(monkeypatch):
    async def gp():
        raise AssertionError("tak boleh sampai DB")
    monkeypatch.setattr(SO, "get_pool", gp)
    r = _klien(monkeypatch).get("/api/sales-orders?aksi=perlu_ditutup")
    assert r.status_code == 422


class _ConnDaftar:
    def __init__(self, rows):
        self.rows, self.hitung = rows, []

    async def fetchval(self, q, *a):
        assert q.startswith("SELECT COUNT(*) FROM sales_orders WHERE"), q
        self.hitung.append((q, a))
        return len(self.rows)

    async def fetch(self, q, *a):
        assert "FROM sales_orders" in q and "LIMIT" in q, q
        n = {int(x) for x in re.findall(r"\$(\d+)", q)}
        assert n == set(range(1, len(a) + 1)), (q, a)
        return self.rows


def _baris_so(i, status="confirmed"):
    return {"id": i, "order_number": f"SO-{str(i)[:4]}", "order_date": HARI, "expected_ship_date": None,
            "customer_id": uuid.uuid4(), "customer_name": "C", "subtotal": 1, "discount_amount": 0, "tax_amount": 0,
            "shipping_amount": 0, "total_amount": 1, "status": status, "shipped_qty": 0, "invoiced_qty": 0,
            "created_at": datetime(2026, 10, 14, tzinfo=timezone.utc), "quote_id": None, "order_code": None,
            "order_title": None, "fase_lokasi": None, "fase_gudang_id": None}


@pytest.mark.asyncio
async def test_daftar_filter_aksi_dan_kolom_aksi_dari_fakta_yang_sama(monkeypatch):
    a1, a2, a3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rows = [_baris_so(a1), _baris_so(a2)]
    conn = _ConnDaftar(rows)
    semua = {a1: {"aksi": ["telat_kirim", "perlu_dikirim"], "total": D(1)},
             a2: {"aksi": ["perlu_dikirim"], "total": D(1)}, a3: {"aksi": ["perlu_ditagih"], "total": D(1)}}
    panggil = []

    async def fakta(c, t, hari, so_ids=None):
        panggil.append((t, hari, so_ids))
        return semua if so_ids is None else {k: v for k, v in semua.items() if k in so_ids}

    class _P:
        def acquire(self):
            class _A:
                async def __aenter__(s):
                    return conn

                async def __aexit__(s, *e):
                    return False
            return _A()

    async def gp():
        return _P()

    async def tgl(c, t):
        return HARI

    async def penanda(c, t, ids):
        return {str(i): {} for i in ids}

    async def posisi(c, t, r, h):
        assert h == HARI
        return {str(x["id"]): {} for x in r}

    async def setelan(c, t):
        return {"label": "Kode order", "title_label": "Judul order"}
    from app.services import kode_order
    monkeypatch.setattr(SO, "get_pool", gp)
    monkeypatch.setattr(SO, "get_user_context", lambda r: {"tenant_id": TENANT})
    monkeypatch.setattr(SO, "tanggal_dokumen", tgl)
    monkeypatch.setattr(SO, "penanda_faktur_so", penanda)
    monkeypatch.setattr(SO, "fakta_daftar", posisi)
    monkeypatch.setattr(kode_order, "muat_setelan", setelan)
    monkeypatch.setattr(SO.so_aksi, "fakta", fakta)
    r = await SO.list_sales_orders(None, status="all", customer_id=None, start_date=None, end_date=None, search=None,
                                   skip=0, limit=20, tugas=None, sort_by=None, sort_dir="desc", aksi="perlu_dikirim")
    q, a = conn.hitung[0]
    assert "id = ANY($2::uuid[])" in q and set(a[1]) == {a1, a2}
    assert [x.aksi for x in r.items] == ["telat_kirim", "perlu_dikirim"]
    assert panggil[0] == (TENANT, HARI, None) and set(panggil[1][2]) == {a1, a2}


def test_daftar_penjaga_aksi_tahan_objek_query():
    # pemanggil langsung (helper/tes lain) membawa default Query(None) -> bukan kunci AKSI -> tanpa filter (pelajaran quote)
    import inspect
    assert "if aksi in so_aksi.AKSI:" in inspect.getsource(SO.list_sales_orders)


@pytest.mark.asyncio
async def test_summary_pill_dari_ringkasan_fakta(monkeypatch):
    s, c0 = _dunia()

    class _C(_Conn):
        async def fetchrow(self, q, *a):
            return {k: 0 for k in ("total_orders", "draft_count", "confirmed_count", "partial_shipped_count",
                                   "shipped_count", "partial_invoiced_count", "invoiced_count", "completed_count",
                                   "cancelled_count", "total_value", "pending_invoice_value")}
    c = _C(c0.so, c0.baris, c0.kirim, c0.fak)

    class _P:
        def acquire(self):
            class _A:
                async def __aenter__(s):
                    return c

                async def __aexit__(s, *e):
                    return False
            return _A()

    async def gp():
        return _P()

    async def tgl(conn, t):
        return HARI

    async def nol(*a):
        return {"total": D(0), "count": 0}

    async def nol_int(*a):
        return 0
    monkeypatch.setattr(SO, "get_pool", gp)
    monkeypatch.setattr(SO, "get_user_context", lambda r: {"tenant_id": TENANT})
    monkeypatch.setattr(SO, "tanggal_dokumen", tgl)
    monkeypatch.setattr(SO.so_agregat, "uninvoiced", nol)
    monkeypatch.setattr(SO.so_agregat, "jumlah_tersedia", nol_int)
    monkeypatch.setattr(SO.so_kirim, "ringkasan_belum_dikirim", nol)
    monkeypatch.setattr(SO.so_kirim, "ringkasan_menunggu_kirim", nol)
    monkeypatch.setattr(SO.so_kirim, "jumlah_surat_jalan", nol_int)
    d = (await SO.get_sales_order_summary(None)).data
    d = d if isinstance(d, dict) else d.model_dump()
    assert d["telat_kirim_count"] == 1 and d["telat_kirim_value"] == 1000.0
    assert d["perlu_dikonfirmasi_count"] == 1 and d["perlu_ditagih_value"] == 250.5
    assert d["perlu_dikirim_count"] == 1


# ---------- AKSI-SO-2 (14 Okt 2026, MASTER): menunggu_dp + siap_diserahkan ----------

def test_menunggu_dp_urutan_kedua_dan_bertumpuk():
    # grapgrap SO-2609-0009: telat + menunggu DP -> kolom aksi telat_kirim, tapi tetap ANGGOTA menunggu_dp (pill penuh)
    a = A.aksi_so("confirmed", date(2026, 10, 1), HARI, [_b(4, True, False)], menunggu_dp=True)
    assert a == ["telat_kirim", "menunggu_dp", "perlu_dikirim", "perlu_ditagih"]
    assert A.aksi_utama(a) == "telat_kirim"
    assert A.aksi_utama(A.aksi_so("confirmed", None, HARI, [_b(4, True, False)], menunggu_dp=True)) == "menunggu_dp"


def test_menunggu_dp_tanpa_baris_bersisa_tetap_muncul():
    assert A.aksi_so("invoiced", None, HARI, [], menunggu_dp=True) == ["menunggu_dp"]


@pytest.mark.parametrize("st", ["draft", "cancelled", "completed"])
def test_menunggu_dp_bukan_untuk_draf_dan_arsip(st):
    assert "menunggu_dp" not in A.aksi_so(st, None, HARI, [_b(1, True, False)], menunggu_dp=True)


def test_siap_diserahkan_eksklusif_dari_perlu_dikirim():
    b = [_b(4, True, False, kirim=1)]
    assert A.aksi_so("confirmed", None, HARI, b, fase_lokasi="tersedia") == ["siap_diserahkan", "perlu_ditagih"]
    assert A.aksi_so("confirmed", None, HARI, b, fase_lokasi="dikirim_ke_lokasi") == ["perlu_dikirim", "perlu_ditagih"]
    assert A.aksi_so("confirmed", None, HARI, b) == ["perlu_dikirim", "perlu_ditagih"]


def test_siap_diserahkan_butuh_sisa_kirim_dan_telat_tak_terpengaruh_fase():
    assert A.aksi_so("invoiced", None, HARI, [_b(4, True, False, kirim=4, fak=4)], fase_lokasi="tersedia") == []
    a = A.aksi_so("invoiced", date(2026, 10, 1), HARI, [_b(4, True, False, fak=4)], fase_lokasi="tersedia")
    assert a == ["telat_kirim", "siap_diserahkan"] and A.aksi_utama(a) == "telat_kirim"


def test_fase_siap_sama_dengan_predikat_tersedia_kartu_lama():
    from app.services import so_agregat
    assert f"fase_lokasi = '{A.FASE_SIAP}'" in so_agregat.PREDIKAT_TERSEDIA


@pytest.mark.asyncio
async def test_fakta_menunggu_dp_dan_siap_lewat_so_posisi(monkeypatch):
    s, c = _dunia()
    for r in c.so:
        if r["id"] == s["telat"]:
            r["fase_lokasi"] = "tersedia"
    terima = {}

    async def dp(conn, t, rows):
        terima["rows"] = {r["id"] for r in rows}
        return {s["tagih"], s["batal"]}       # batal diabaikan oleh aksi_so (arsip)
    monkeypatch.setattr(A.so_posisi, "id_menunggu_dp", dp)
    f = await A.fakta(c, TENANT, HARI)
    assert terima["rows"] == {s["telat"], s["tagih"], s["beres"]}     # hanya SO berjalan
    assert f[s["tagih"]]["aksi"] == ["menunggu_dp", "perlu_ditagih"]
    assert f[s["telat"]]["aksi"] == ["telat_kirim", "siap_diserahkan"]
    assert f[s["batal"]]["aksi"] == []
    r = A.ringkasan(f)
    assert (r["menunggu_dp_count"], r["menunggu_dp_value"]) == (1, 250.5)
    assert (r["siap_diserahkan_count"], r["perlu_dikirim_count"]) == (1, 0)


def test_rute_meneruskan_kunci_baru(monkeypatch):
    for k in ("menunggu_dp", "siap_diserahkan"):
        async def gp():
            raise RuntimeError("sampai-db")
        monkeypatch.setattr(SO, "get_pool", gp)
        r = _klien(monkeypatch).get(f"/api/sales-orders?aksi={k}")
        assert r.status_code != 422, k
