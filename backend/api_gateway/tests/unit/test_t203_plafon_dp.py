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
from app.services import uang_muka_pesanan as ump

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


@pytest.fixture(autouse=True)
def _uang_muka_palsu(monkeypatch):
    """KONTRAK-dp-neto (16 Okt 2026): akumulasi = services/uang_muka_pesanan (jurnal, neto refund). Baris jurnal per
    uang muka dipalsukan dari `conn.sudah` (neto); argumen panggilan dicatat untuk uji tenant."""
    async def palsu(conn, tenant_id, so_ids):
        conn.panggil_ump = (tenant_id, list(so_ids))
        n = getattr(conn, "sudah", 0) or 0
        return [{"id": "dp-1", "so_id": so_ids[0], "status": "posted", "amount": n, "proforma_id": None,
                 "terima": ump._d(n), "terap": ump.NOL, "lain": ump.NOL, "neto": ump._d(n)}] if n else []
    monkeypatch.setattr(ump, "per_uang_muka", palsu)


def _conn_so(order_total, sudah):
    """fetchrow #1 = baris sales_orders; akumulasi uang muka (neto) = `sudah` lewat _uang_muka_palsu."""
    c = _FakeConn([{"order_number": "SO-2608-0001", "total_amount": order_total, "status": "confirmed"}])  # C7: status SO
    c.sudah = sudah
    return c


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
    assert "Rp 5.000.000" in detail, detail
    assert "Rp 1.600.000" in detail, detail
    assert "Rp 3.400.000." in detail, detail


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
    # KONTRAK-dp-neto: satu sumber = ump.SQL_PER_UANG_MUKA (jurnal); void keluar di sana
    sql = ump.SQL_PER_UANG_MUKA
    assert "cd.status <> 'void'" in sql, sql
    assert "customer_deposits" in sql
    conn = _FakeConn()
    conn.sudah = 1_600_000
    assert await cd.received_total_for_order(conn, TENANT, SO) == 1_600_000


async def test_akumulasi_menyaring_tenant_id():
    """Gateway BYPASSRLS — WHERE tenant_id WAJIB eksplisit."""
    assert "cd.tenant_id = $1" in ump.SQL_PER_UANG_MUKA
    conn = _FakeConn()
    await cd.received_total_for_order(conn, TENANT, SO)
    assert conn.panggil_ump == (TENANT, [SO])


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
    # DP-PRATINJAU (3 Nov 2026, retarget OK MASTER): pagar dipanggil di penentu bersama rencana_buat_dp (AST, bukan jangkar
    # teks). Jangkar = PEMANGGILAN pagar, BUKAN def-nya dan BUKAN resolve_order_id_for_deposit: sabotase S7 pernah menyisakan
    # resolve saja dan lolos HIJAU PALSU. Penulis memanggil penentu SEBELUM INSERT dan memakai SO acuan dari penentu.
    import ast
    pohon = ast.parse(ROUTER_SRC)
    fn = {n.name: n for n in ast.walk(pohon) if isinstance(n, ast.AsyncFunctionDef)}
    pen = fn["rencana_buat_dp"]
    panggil = lambda f, nama: [c for c in ast.walk(f) if isinstance(c, ast.Call) and getattr(c.func, "id", None) == nama]  # noqa: E731
    guard, resolve = panggil(pen, "assert_deposit_within_order_total"), panggil(pen, "resolve_order_id_for_deposit")
    assert guard, "pagar tidak DIPANGGIL di penentu create"
    assert resolve, "SO acuan tidak diturunkan di penentu create"
    assert resolve[0].lineno < guard[0].lineno, "SO acuan harus diturunkan sebelum pagar"
    assert [ast.unparse(k.value) for k in guard[0].keywords if k.arg == "kunci"] == ["not pratinjau"], "penulis WAJIB mengunci SO"
    cr = fn["create_customer_deposit"]
    rb = panggil(cr, "rencana_buat_dp")
    ins = [c.lineno for c in ast.walk(cr) if isinstance(c, ast.Call) and c.args and isinstance(c.args[0], ast.Constant)
           and "INSERT INTO customer_deposits" in str(c.args[0].value)]
    assert rb and ins and rb[0].lineno < min(ins), "pagar harus dievaluasi SEBELUM INSERT"
    assert '_guard_order_id, pelanggan_dp, jenis_simpan = rb["order_id"]' in " ".join(ast.get_source_segment(ROUTER_SRC, cr).split())


async def test_sales_order_tidak_ada_ditolak():
    conn = _FakeConn([None])
    with pytest.raises(HTTPException) as ex:
        await cd.assert_deposit_within_order_total(conn, TENANT, SO, 1)
    assert ex.value.status_code == 400
