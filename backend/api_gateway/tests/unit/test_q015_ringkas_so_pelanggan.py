"""Q-015 (25 Sep 2026): GET /api/customers membawa order_count / last_order_date / last_dp_percent per baris.

Latar: saran pelanggan CW (BUG-008) ingin "N pesanan" + "DP x%"; FE tak boleh menghitung dari daftar SO yang
terpotong. Definisi disetujui MASTER: SO non-draf non-batal (SAMA dengan top_customers); DP% = dp_percent SO non-draf non-batal TERBARU (tak ada DP default
per pelanggan); satu kueri agregat per halaman (bukan N+1), berpagar tenant.
Probe prod read-only 25 Sep: SQL ini == hitungan naif Python dari baris mentah (kaos 13 pelanggan / grapgrap 43,
beda 0; sabotase naif ikut-batal 7 beda, terlama 8 beda).
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.routers import customers as cu
from app.schemas.customers import CustomerListItem, CustomerListResponse
from app.services import pelanggan_ringkas_so as RS

TENANT = "kaos-biru-konveksi"
A = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
B = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
C = uuid.UUID("cccccccc-0000-0000-0000-000000000003")


class Conn:
    def __init__(self, ringkas=(), pelanggan=()):
        self.ringkas, self.pelanggan = list(ringkas), list(pelanggan)
        self.calls = []

    async def fetch(self, sql, *a):
        self.calls.append((sql, a))
        if "FROM sales_orders" in sql:
            return self.ringkas
        if "compute_ar_outstanding" in sql:
            return []
        return self.pelanggan

    async def fetchval(self, sql, *a):
        self.calls.append((sql, a))
        return len(self.pelanggan)


def _ringkas(cid, n, tgl, dp):
    return {"customer_id": cid, "order_count": n, "last_order_date": tgl, "last_dp_percent": dp}


def _pelanggan(cid, nama):
    return {"id": cid, "nomor_member": None, "nama": nama, "company_name": None, "display_name": None,
            "tipe": None, "telepon": None, "email": None, "alamat": "Manado", "points": 0,
            "total_transaksi": 0, "total_nilai": 0, "is_active": True,
            "created_at": datetime(2026, 9, 1, tzinfo=timezone.utc), "phone2": None, "community": None}


def sql_so(conn):
    return [c for c in conn.calls if "FROM sales_orders" in c[0]]


# ---------- definisi kueri ----------

def test_sql_berpagar_tenant_dan_id():
    s = RS.SQL_RINGKAS_SO
    assert "so.tenant_id = $1" in s
    assert "so.customer_id = ANY($2::uuid[])" in s


def test_sql_buang_draf_dan_batal():
    s = RS.SQL_RINGKAS_SO
    assert "AND so.status NOT IN ('draft', 'cancelled')" in s


def test_definisi_sama_dengan_top_customers():
    """Dua "jumlah pesanan" berbeda definisi = kelas dua sumber: filter status HARUS sama dengan top_customers."""
    import inspect
    from app.services import so_agregat as SA
    assert "so.status NOT IN ('draft', 'cancelled')" in inspect.getsource(SA.top_customers)
    assert "so.status NOT IN ('draft', 'cancelled')" in RS.SQL_RINGKAS_SO


def test_sql_terbaru_menang():
    s = " ".join(RS.SQL_RINGKAS_SO.split())
    assert "DISTINCT ON (so.customer_id)" in s
    assert "ORDER BY so.customer_id, so.order_date DESC, so.created_at DESC, so.id DESC" in s
    assert "COUNT(*) OVER (PARTITION BY so.customer_id)" in s


# ---------- helper ----------

@pytest.mark.asyncio
async def test_satu_kueri_untuk_banyak_pelanggan():
    conn = Conn(ringkas=[_ringkas(A, 3, date(2026, 9, 20), D("50.00"))])
    await RS.ringkas_so_pelanggan(conn, TENANT, [A, B, C])
    assert len(conn.calls) == 1                               # bukan N+1
    sql, args = conn.calls[0]
    assert args == (TENANT, [str(A), str(B), str(C)])


@pytest.mark.asyncio
async def test_daftar_kosong_tanpa_kueri():
    conn = Conn()
    assert await RS.ringkas_so_pelanggan(conn, TENANT, []) == {}
    assert conn.calls == []


@pytest.mark.asyncio
async def test_pemetaan_nilai():
    conn = Conn(ringkas=[
        _ringkas(A, 3, date(2026, 9, 20), D("60.00")),
        _ringkas(B, 1, date(2026, 8, 2), None),               # SO terbaru tanpa dp_percent -> None, bukan 0
        _ringkas(uuid.uuid4(), 9, date(2026, 9, 1), D("10")),  # id tak diminta -> diabaikan
    ])
    h = await RS.ringkas_so_pelanggan(conn, TENANT, [A, B, C])
    assert h == {
        str(A): {"order_count": 3, "last_order_date": "2026-09-20", "last_dp_percent": 60.0},
        str(B): {"order_count": 1, "last_order_date": "2026-08-02", "last_dp_percent": None},
        str(C): {"order_count": 0, "last_order_date": None, "last_dp_percent": None},
    }
    assert isinstance(h[str(A)]["last_dp_percent"], float)


# ---------- penyambungan router + skema (response_model membuang medan tak dideklarasikan) ----------

def pasang(monkeypatch, conn):
    class _Acq:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False

    async def _pool():
        return SimpleNamespace(acquire=lambda: _Acq())

    monkeypatch.setattr(cu, "get_pool", _pool)


def req():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT,
                                                       "user_id": "22222222-2222-2222-2222-222222222222"}))


@pytest.mark.asyncio
async def test_daftar_pelanggan_membawa_ringkasan(monkeypatch):
    conn = Conn(pelanggan=[_pelanggan(A, "Rahayu"), _pelanggan(B, "Nutrindo")],
                ringkas=[_ringkas(A, 4, date(2026, 9, 22), D("60"))])
    pasang(monkeypatch, conn)
    r = await cu.list_customers(req(), skip=0, limit=20, search=None, tipe=None, is_active=None,
                                sort_by="created_at", sort_order="desc")
    # tepat satu kueri SO, berpagar tenant sesi, berisi id halaman ini
    q = sql_so(conn)
    assert len(q) == 1 and q[0][1] == (TENANT, [str(A), str(B)])
    # lewat response_model: medan TIDAK terbuang
    keluar = CustomerListResponse(**r).model_dump()["items"]
    assert keluar[0]["order_count"] == 4
    assert keluar[0]["last_order_date"] == "2026-09-22"
    assert keluar[0]["last_dp_percent"] == 60.0
    assert keluar[1]["order_count"] == 0
    assert keluar[1]["last_order_date"] is None and keluar[1]["last_dp_percent"] is None


def test_skema_mendeklarasikan_medan():
    f = CustomerListItem.model_fields
    for k in ("order_count", "last_order_date", "last_dp_percent"):
        assert k in f
