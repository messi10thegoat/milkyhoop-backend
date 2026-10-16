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

from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.routers import customer_deposits as cd
from app.services import uang_muka_pesanan as ump
from app.schemas.customer_deposits import UpdateCustomerDepositRequest

TENANT = "kaos-biru-konveksi"
SO = UUID("6552e5bc-641c-48d0-b99d-893d8b5adcc6")
DEP = UUID("11111111-2222-3333-4444-555555555555")
# Sejak 40299673 (If-Match opt-in) handler PATCH membaca request.headers. object()
# tak punya .headers -> 500 SEBELUM pagar plafon tercapai: 3 hari 9 tes ini merah
# dan pagar plafon-PATCH tanpa satu pun tes yang lulus. headers={} = tanpa If-Match.
_REQ = SimpleNamespace(headers={})


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
        if sql.startswith("SELECT deposit_number, "):  # nilai lama untuk audit DEPOSIT_UPDATED (5 Okt)
            from collections import defaultdict
            return defaultdict(lambda: None, deposit_number="DP-T204")
        return self._rows.pop(0) if self._rows else None

    async def execute(self, sql, *args):
        self.sqls.append(sql)
        self.args.append(args)
        return "UPDATE 1"

    async def fetchval(self, sql, *args):  # PELUNASAN-TEKS: label jenis untuk ringkas audit (None -> "Uang muka")
        assert "FROM customer_deposits cdl" in sql, sql[:60]
        return None


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
    # KONTRAK-dp-neto (16 Okt 2026): akumulasi = services/uang_muka_pesanan (jurnal, neto). Baris jurnal per uang muka
    # dipalsukan: uang muka LAIN bernilai `sudah_lain` + uang muka INI (DEP) sendiri bernilai besar, supaya exclude_id
    # benar-benar diuji (tanpa exclude, DEP ikut terhitung dan PATCH ditolak keliru).
    um = [r for r in rows if "__uang_muka__" in r]
    rows = [r for r in rows if "__uang_muka__" not in r]
    conn = _Conn(rows)
    sudah_lain = um[0]["__uang_muka__"] if um else 0

    async def _per_uang_muka(c, tenant_id, so_ids):
        conn.panggil_ump = (tenant_id, list(so_ids))
        baris = [{"id": DEP, "so_id": so_ids[0], "status": "posted", "amount": 4_999_999, "proforma_id": None,
                  "terima": ump._d(4_999_999), "terap": ump.NOL, "lain": ump.NOL, "neto": ump._d(4_999_999)}]
        if sudah_lain:
            baris.append({"id": "dp-lain", "so_id": so_ids[0], "status": "posted", "amount": sudah_lain,
                          "proforma_id": None, "terima": ump._d(sudah_lain), "terap": ump.NOL, "lain": ump.NOL,
                          "neto": ump._d(sudah_lain)})
        return baris
    monkeypatch.setattr(ump, "per_uang_muka", _per_uang_muka)

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
        {"order_number": "SO-2608-0001", "total_amount": order_total, "status": "confirmed"},  # C7: pagar kini membaca status SO
        {"__uang_muka__": sudah_lain},
    ]


async def _patch(monkeypatch, rows, **fields):
    conn = _wire(monkeypatch, rows)
    body = UpdateCustomerDepositRequest(**fields)
    res = await cd.update_customer_deposit(_REQ, DEP, body)
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
    assert "Rp 5.000.000" in detail, detail
    assert "Rp 1.600.000" in detail, detail
    assert "Rp 3.400.000." in detail, detail


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
    res = await cd.update_customer_deposit(_REQ, DEP, body)
    assert res["success"] is True   # DEP sendiri (4.999.999) dikecualikan; tanpa exclude -> ditolak
    assert conn.panggil_ump == (TENANT, [SO])


async def test_query_akumulasi_punya_klausa_exclude(monkeypatch):
    # exclude kini di jumlahkan() (murni): uang muka ber-id itu tak dihitung
    b = [{"id": DEP, "so_id": SO, "status": "posted", "amount": 1, "proforma_id": None, "terima": ump._d(7),
          "terap": ump.NOL, "lain": ump.NOL, "neto": ump._d(7)}]
    assert ump.jumlahkan(b, [SO])[SO]["neto"] == 7
    assert ump.jumlahkan(b, [SO], exclude_id=DEP)[SO]["neto"] == 0


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
        _REQ, DEP, UpdateCustomerDepositRequest(amount=999_999_999)
    )
    assert res["success"] is True
    assert not any("FROM sales_orders" in s for s in conn.sqls), conn.sqls


# -------------------------------------------------------- VOID & TENANT_ID


async def test_akumulasi_patch_mengecualikan_void(monkeypatch):
    conn = _wire(monkeypatch, _rows(5_000_000, 0))
    await cd.update_customer_deposit(
        _REQ, DEP, UpdateCustomerDepositRequest(amount=1)
    )
    assert "cd.status <> 'void'" in ump.SQL_PER_UANG_MUKA
    assert conn.panggil_ump == (TENANT, [SO])


async def test_query_pagar_patch_menyaring_tenant_id(monkeypatch):
    conn = _wire(monkeypatch, _rows(5_000_000, 0))
    await cd.update_customer_deposit(
        _REQ, DEP, UpdateCustomerDepositRequest(amount=1)
    )
    so_i = [i for i, s in enumerate(conn.sqls) if "FROM sales_orders" in s][0]
    assert "tenant_id = $2" in conn.sqls[so_i]
    assert conn.args[so_i] == (SO, TENANT)
    assert "cd.tenant_id = $1" in ump.SQL_PER_UANG_MUKA
    assert conn.panggil_ump == (TENANT, [SO])


# --------------------------------------------- PATCH TANPA amount TAK KENA


async def test_patch_tanpa_amount_tidak_memicu_pagar(monkeypatch):
    conn = _wire(monkeypatch, _rows(5_000_000, 0))
    res = await cd.update_customer_deposit(
        _REQ, DEP, UpdateCustomerDepositRequest(notes="ubah catatan")
    )
    assert res["success"] is True
    assert not any("FROM sales_orders" in s for s in conn.sqls), conn.sqls
