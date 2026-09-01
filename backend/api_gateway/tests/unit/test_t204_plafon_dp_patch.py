"""T204 — plafon uang muka per Sales Order pada JALUR PATCH.

Lubang yang ditutup (dilaporkan sendiri oleh agen T203): pagar plafon hanya
terpasang di `POST /api/customer-deposits`. `PATCH /customer-deposits/{id}`
menerima `amount` baru dan TIDAK lewat pagar itu — plafon bisa dilewati dengan
membuat DP kecil lalu menaikkan nilainya selagi draft.

Tes memanggil handler `update_customer_deposit` YANG SEBENARNYA (pool + conn
palsu), bukan sekadar helper, supaya jalur PATCH benar-benar terlewati.

Setiap tes di berkas ini sudah dibuktikan BISA MERAH lewat sabotase pada KODE
PRODUKSI (bukan pada tes), lalu dipulihkan. Lihat laporan tiket.
"""

from uuid import UUID

import pytest
from fastapi import HTTPException

from app.routers import customer_deposits as cd
from app.schemas.customer_deposits import UpdateCustomerDepositRequest

TENANT = "kaos-biru-konveksi"
SO = UUID("6552e5bc-641c-48d0-b99d-893d8b5adcc6")
DEP = UUID("11111111-2222-3333-4444-555555555555")


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, rows):
        self.sqls = []
        self.args = []
        self._rows = list(rows)

    def transaction(self):
        return _Tx()

    async def fetchrow(self, sql, *args):
        self.sqls.append(sql)
        self.args.append(args)
        return self._rows.pop(0) if self._rows else None

    async def execute(self, sql, *args):
        self.sqls.append(sql)
        self.args.append(args)
        return "UPDATE 1"


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        pool = self

        class _Acq:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *a):
                return False

        return _Acq()


def _wire(monkeypatch, rows):
    conn = _Conn(rows)

    async def _pool():
        return _Pool(conn)

    monkeypatch.setattr(cd, "get_pool", _pool)
    monkeypatch.setattr(
        cd,
        "get_user_context",
        lambda request: {"user_id": "u-1", "tenant_id": TENANT},
    )
    return conn


def _rows(order_total, sudah_lain, sales_order_id=SO):
    """Baris yang dikonsumsi handler PATCH, berurutan."""
    return [
        {
            "id": DEP,
            "status": "draft",
            "sales_order_id": sales_order_id,
            "proforma_id": None,
        },
        {"order_number": "SO-2608-0001", "total_amount": order_total},
        {"total": sudah_lain},
    ]


async def _patch(monkeypatch, rows, **fields):
    conn = _wire(monkeypatch, rows)
    body = UpdateCustomerDepositRequest(**fields)
    res = await cd.update_customer_deposit(object(), DEP, body)
    return conn, res


# --------------------------------------------------------- PAGAR MENOLAK


async def test_patch_naikkan_melebihi_sisa_ditolak_400(monkeypatch):
    """Inti lubang: DP draft 1.000 dinaikkan jadi 9.000.000 atas SO 5.000.000."""
    with pytest.raises(HTTPException) as ex:
        await _patch(monkeypatch, _rows(5_000_000, 1_600_000), amount=9_000_000)
    assert ex.value.status_code == 400


async def test_patch_pesan_penolakan_menyebut_tiga_angka(monkeypatch):
    with pytest.raises(HTTPException) as ex:
        await _patch(monkeypatch, _rows(5_000_000, 1_600_000), amount=4_000_000)
    detail = ex.value.detail
    assert "5,000,000.00" in detail, detail
    assert "1,600,000.00" in detail, detail
    assert "3,400,000.00" in detail, detail


# ------------------------------------------------ KONTROL BATAS: TEPAT SISA


async def test_patch_tepat_sisa_diterima(monkeypatch):
    conn, res = await _patch(
        monkeypatch, _rows(5_000_000, 1_600_000), amount=3_400_000
    )
    assert res["success"] is True
    assert any("UPDATE customer_deposits" in s for s in conn.sqls)


# ------------------------------------- KONTROL exclude_id (REGRESI TERBESAR)


async def test_patch_tidak_menghitung_dirinya_sendiri(monkeypatch):
    """Tanpa exclude_id, menaikkan 1.000 -> 1.001 pada SO yang sudah terisi
    oleh deposit ITU SENDIRI akan ditolak KELIRU."""
    conn = _wire(monkeypatch, _rows(5_000_000, 0))
    body = UpdateCustomerDepositRequest(amount=1_001)
    res = await cd.update_customer_deposit(object(), DEP, body)
    assert res["success"] is True
    # query akumulasi menerima deposit_id sebagai exclude_id ($3)
    akum = [i for i, s in enumerate(conn.sqls) if "SUM(amount)" in s]
    assert akum, conn.sqls
    assert conn.args[akum[0]][2] == DEP, conn.args[akum[0]]


async def test_query_akumulasi_punya_klausa_exclude(monkeypatch):
    conn = _wire(monkeypatch, _rows(5_000_000, 0))
    await cd.update_customer_deposit(
        object(), DEP, UpdateCustomerDepositRequest(amount=1)
    )
    sql = [s for s in conn.sqls if "SUM(amount)" in s][0]
    assert "id <> $3" in sql, sql


# ------------------------------------------------------- DEPOSIT TANPA SO


async def test_patch_tanpa_sales_order_lolos(monkeypatch):
    """Deposit tanpa SO tak punya acuan — tak boleh dihalangi, dan tak boleh
    menyentuh tabel sales_orders."""
    rows = [
        {
            "id": DEP,
            "status": "draft",
            "sales_order_id": None,
            "proforma_id": None,
        }
    ]
    conn = _wire(monkeypatch, rows)
    res = await cd.update_customer_deposit(
        object(), DEP, UpdateCustomerDepositRequest(amount=999_999_999)
    )
    assert res["success"] is True
    assert not any("FROM sales_orders" in s for s in conn.sqls), conn.sqls


# -------------------------------------------------------- VOID & TENANT_ID


async def test_akumulasi_patch_mengecualikan_void(monkeypatch):
    conn = _wire(monkeypatch, _rows(5_000_000, 0))
    await cd.update_customer_deposit(
        object(), DEP, UpdateCustomerDepositRequest(amount=1)
    )
    sql = [s for s in conn.sqls if "SUM(amount)" in s][0]
    assert "status <> 'void'" in sql, sql


async def test_query_pagar_patch_menyaring_tenant_id(monkeypatch):
    conn = _wire(monkeypatch, _rows(5_000_000, 0))
    await cd.update_customer_deposit(
        object(), DEP, UpdateCustomerDepositRequest(amount=1)
    )
    so_i = [i for i, s in enumerate(conn.sqls) if "FROM sales_orders" in s][0]
    assert "tenant_id = $2" in conn.sqls[so_i]
    assert conn.args[so_i] == (SO, TENANT)
    ak_i = [i for i, s in enumerate(conn.sqls) if "SUM(amount)" in s][0]
    assert "tenant_id = $1" in conn.sqls[ak_i]
    assert conn.args[ak_i][0] == TENANT


# --------------------------------------------- PATCH TANPA amount TAK KENA


async def test_patch_tanpa_amount_tidak_memicu_pagar(monkeypatch):
    conn = _wire(monkeypatch, _rows(5_000_000, 0))
    res = await cd.update_customer_deposit(
        object(), DEP, UpdateCustomerDepositRequest(notes="ubah catatan")
    )
    assert res["success"] is True
    assert not any("FROM sales_orders" in s for s in conn.sqls), conn.sqls
