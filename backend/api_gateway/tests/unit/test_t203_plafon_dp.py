"""T203 (B-01) — plafon total uang muka per Sales Order.

Temuan audit: tak ada batas akumulasi DP; 5 klik "Terima DP" atas pesanan
Rp 2.280.000 menghasilkan liabilitas Uang Muka Pelanggan Rp 6.840.000.

Setiap tes di berkas ini sudah dibuktikan BISA MERAH lewat sabotase pada KODE
PRODUKSI (bukan pada tes), lalu dipulihkan. Lihat laporan tiket.
"""

import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers import customer_deposits as cd

ROUTER_SRC = Path(cd.__file__).read_text()


def _kode_saja(src: str) -> str:
    """Buang docstring dan komentar — kita menguji KODE, bukan prosa."""
    tanpa_doc = re.sub(r'"""(?:.|\n)*?"""', "", src)
    return "\n".join(
        baris for baris in tanpa_doc.splitlines() if not baris.strip().startswith("#")
    )


ROUTER_KODE = _kode_saja(ROUTER_SRC)

TENANT = "kaos-biru-konveksi"
SO = "6552e5bc-641c-48d0-b99d-893d8b5adcc6"


class _FakeConn:
    """Mencatat tiap SQL; menjawab fetchrow dengan nilai yang KITA tentukan."""

    def __init__(self, answers=None):
        self.sqls = []
        self.args = []
        self._answers = list(answers or [])

    async def fetchrow(self, sql, *args):
        self.sqls.append(sql)
        self.args.append(args)
        if self._answers:
            return self._answers.pop(0)
        return None

    async def fetch(self, sql, *args):
        self.sqls.append(sql)
        self.args.append(args)
        return []

    async def fetchval(self, sql, *args):
        self.sqls.append(sql)
        self.args.append(args)
        return 0


def _conn_so(order_total, sudah):
    """fetchrow #1 = baris sales_orders, #2 = akumulasi deposit."""
    return _FakeConn(
        [
            {"order_number": "SO-2608-0001", "total_amount": order_total},
            {"total": sudah},
        ]
    )


# ------------------------------------------------------------- PAGAR MENOLAK


async def test_melebihi_sisa_ditolak_400():
    conn = _conn_so(5_000_000, 1_600_000)
    with pytest.raises(HTTPException) as ex:
        await cd.assert_deposit_within_order_total(conn, TENANT, SO, 3_400_001)
    assert ex.value.status_code == 400


async def test_pesan_penolakan_menyebut_tiga_angka():
    """Pesan WAJIB menyebut nilai SO, sudah diterima, dan sisa."""
    conn = _conn_so(5_000_000, 1_600_000)
    with pytest.raises(HTTPException) as ex:
        await cd.assert_deposit_within_order_total(conn, TENANT, SO, 4_000_000)
    detail = ex.value.detail
    assert "5,000,000.00" in detail, detail
    assert "1,600,000.00" in detail, detail
    assert "3,400,000.00" in detail, detail


# ------------------------------------------- KONTROL BATAS: TEPAT SISA LOLOS


async def test_tepat_sisa_diterima():
    """DP tepat sebesar sisa TIDAK boleh ditolak — pagar tak boleh galak."""
    conn = _conn_so(5_000_000, 1_600_000)
    await cd.assert_deposit_within_order_total(conn, TENANT, SO, 3_400_000)


async def test_dp_bertahap_tetap_diizinkan():
    """DP dicicil 30/30/40 adalah skenario NORMAL konveksi: cicilan kedua
    dan ketiga tidak boleh ditolak selama total <= nilai SO."""
    for sudah, minta in ((1_500_000, 1_500_000), (3_000_000, 2_000_000)):
        conn = _conn_so(5_000_000, sudah)
        await cd.assert_deposit_within_order_total(conn, TENANT, SO, minta)


# ------------------------------------------------------ AKUMULASI: VOID KELUAR


async def test_akumulasi_mengecualikan_void():
    conn = _FakeConn([{"total": 1_600_000}])
    await cd.received_total_for_order(conn, TENANT, SO)
    sql = conn.sqls[0]
    assert "status <> 'void'" in sql, sql
    assert "customer_deposits" in sql


async def test_akumulasi_menyaring_tenant_id():
    """Gateway BYPASSRLS — WHERE tenant_id WAJIB eksplisit."""
    conn = _FakeConn([{"total": 0}])
    await cd.received_total_for_order(conn, TENANT, SO)
    assert "tenant_id = $1" in conn.sqls[0]
    assert conn.args[0][0] == TENANT


async def test_lookup_sales_order_menyaring_tenant_id():
    conn = _conn_so(5_000_000, 0)
    await cd.assert_deposit_within_order_total(conn, TENANT, SO, 1)
    assert "tenant_id = $2" in conn.sqls[0], conn.sqls[0]
    assert conn.args[0] == (SO, TENANT)


# --------------------------------------------------------- TANPA SO = LOLOS


async def test_tanpa_sales_order_lolos_tanpa_query():
    """Deposit tanpa SO tak punya acuan — sah, dan tak boleh menyentuh DB."""
    conn = _FakeConn()
    await cd.assert_deposit_within_order_total(conn, TENANT, None, 999_999_999)
    assert conn.sqls == []


# ------------------------------------------------- PROFORMA MENURUNKAN SO (B3)


async def test_proforma_menurunkan_sales_order():
    conn = _FakeConn([{"sales_order_id": SO}])
    got = await cd.resolve_order_id_for_deposit(conn, TENANT, None, "pf-1")
    assert got == SO
    assert "tenant_id = $2" in conn.sqls[0]


async def test_sales_order_eksplisit_tidak_query_proforma():
    conn = _FakeConn()
    got = await cd.resolve_order_id_for_deposit(conn, TENANT, SO, "pf-1")
    assert got == SO
    assert conn.sqls == []


# ------------------------------------------------- PAGAR TERPASANG DI HANDLER


async def test_pagar_dipanggil_di_create_sebelum_insert():
    kode = ROUTER_SRC
    # Jangkar = PEMANGGILAN pagar di dalam handler (indentasi 16 spasi),
    # BUKAN baris def-nya dan BUKAN baris resolve_order_id_for_deposit:
    # sabotase S7 pernah menyisakan resolve saja dan lolos HIJAU PALSU.
    i_guard = kode.find("\n                await assert_deposit_within_order_total(")
    i_resolve = kode.find("_guard_order_id = await resolve_order_id_for_deposit")
    i_insert = kode.find("INSERT INTO customer_deposits")
    assert i_guard != -1, "pagar tidak DIPANGGIL di handler create"
    assert i_resolve != -1, "SO acuan tidak diturunkan di handler create"
    assert i_insert != -1
    assert i_resolve < i_guard < i_insert, "pagar harus dievaluasi SEBELUM INSERT"


async def test_sales_order_tidak_ada_ditolak():
    conn = _FakeConn([None])
    with pytest.raises(HTTPException) as ex:
        await cd.assert_deposit_within_order_total(conn, TENANT, SO, 1)
    assert ex.value.status_code == 400
