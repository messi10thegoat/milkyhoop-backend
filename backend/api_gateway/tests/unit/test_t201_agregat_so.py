"""T201 — panel agregat per Sales Order (nilai · ditagih · diterima · sisa).

Setiap tes di berkas ini DIBUKTIKAN BISA MERAH lewat sabotase pada KODE
PRODUKSI (bukan pada tes), lalu dipulihkan. Lihat laporan tiket.
"""

import re
from pathlib import Path

from app.routers import proformas as pf

ROUTER_SRC = Path(pf.__file__).read_text()


def _kode_saja(src: str) -> str:
    """Buang docstring dan komentar — kita menguji KODE, bukan prosa."""
    tanpa_doc = re.sub(r'"""(?:.|\n)*?"""', "", src)
    return "\n".join(
        b for b in tanpa_doc.splitlines() if not b.strip().startswith("#")
    )


ROUTER_KODE = _kode_saja(ROUTER_SRC)


class _FakeConn:
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


# ------------------------------------------------------- received_total


async def test_received_total_mengecualikan_void():
    """`void` HARUS disaring di SQL — deposit batal bukan uang yang diterima."""
    conn = _FakeConn([{"received": 162000, "unbilled": 0}])
    await pf.deposit_totals_for_order(conn, "kaos-biru-konveksi", "so-1")
    sql = conn.sqls[0]
    assert "customer_deposits" in sql
    assert "status <> 'void'" in sql, (
        "received_total tidak menyaring status void — deposit yang dibatalkan "
        f"akan ikut dihitung. SQL:\n{sql}"
    )


async def test_received_total_float_bukan_decimal_atau_string():
    """pydantic v2 menyerialkan Decimal jadi STRING → merusak matematika FE."""
    from decimal import Decimal

    conn = _FakeConn([{"received": Decimal("162000.00"), "unbilled": Decimal("0")}])
    hasil = await pf.deposit_totals_for_order(conn, "t", "so-1")
    assert type(hasil["received_total"]) is float, type(hasil["received_total"])
    assert type(hasil["unbilled_received"]) is float
    assert hasil["received_total"] == 162000.0


async def test_received_total_nol_saat_tidak_ada_deposit():
    """Nol, bukan None — kontrol negatif tidak boleh mengirim null ke FE."""
    conn = _FakeConn([{"received": None, "unbilled": None}])
    hasil = await pf.deposit_totals_for_order(conn, "t", "so-1")
    assert hasil == {"received_total": 0.0, "unbilled_received": 0.0}


# ---------------------------------------------------- unbilled_received


async def test_unbilled_hanya_deposit_tanpa_proforma_id():
    """`unbilled_received` = FILTER proforma_id IS NULL, bukan seluruh jumlah."""
    conn = _FakeConn([{"received": 100, "unbilled": 100}])
    await pf.deposit_totals_for_order(conn, "t", "so-1")
    sql = " ".join(conn.sqls[0].split())
    assert "FILTER (WHERE proforma_id IS NULL)" in sql, (
        "unbilled_received tidak difilter pada proforma_id IS NULL — deposit "
        f"yang SUDAH bertaut tagihan ikut terhitung. SQL:\n{sql}"
    )


async def test_unbilled_terpisah_dari_received():
    """Dua angka berbeda: sebagian bertaut proforma, sebagian tidak."""
    conn = _FakeConn([{"received": 1600000, "unbilled": 100000}])
    hasil = await pf.deposit_totals_for_order(conn, "t", "so-1")
    assert hasil["received_total"] == 1600000.0
    assert hasil["unbilled_received"] == 100000.0


# ------------------------------------------------------------- ISOLASI


async def test_query_menyaring_tenant_id():
    """Gateway BYPASSRLS — RLS TIDAK melindungi jalur ini. tenant_id wajib."""
    conn = _FakeConn([{"received": 0, "unbilled": 0}])
    await pf.deposit_totals_for_order(conn, "kaos-biru-konveksi", "so-1")
    sql = " ".join(conn.sqls[0].split())
    assert "tenant_id = $1" in sql, f"tenant_id tidak disaring. SQL:\n{sql}"
    assert conn.args[0][0] == "kaos-biru-konveksi"


async def test_atribusi_lewat_sales_order_id_bukan_tanggal():
    """Atribusi lewat kunci relasi. Tanggal TIDAK BOLEH muncul di query ini."""
    conn = _FakeConn([{"received": 0, "unbilled": 0}])
    await pf.deposit_totals_for_order(conn, "t", "so-1")
    sql = " ".join(conn.sqls[0].split()).lower()
    assert "sales_order_id = $2" in sql
    for kata in ("deposit_date", "created_at", "date_trunc", "between", "::date"):
        assert kata not in sql, (
            f"query agregat memakai tanggal ('{kata}') — atribusi harus lewat "
            f"sales_order_id/proforma_id. SQL:\n{sql}"
        )


# ------------------------------------------------- respons endpoint SO


async def test_respons_endpoint_membawa_kelima_agregat():
    """Panel butuh kelimanya di AKAR respons daftar (bukan di `.data`)."""
    blok = ROUTER_KODE.split("async def list_proformas_for_order")[1]
    for kunci in (
        '"order_total"',
        '"issued_total"',
        '"billable_remaining"',
        '"received_total"',
        '"unbilled_received"',
    ):
        assert kunci in blok, f"{kunci} tidak dikembalikan endpoint SO-proformas"


async def test_endpoint_memanggil_helper_diterima():
    """Angka DIHITUNG saat baca — bukan dibaca dari kolom ringkasan."""
    blok = ROUTER_KODE.split("async def list_proformas_for_order")[1]
    assert "deposit_totals_for_order(conn," in blok


async def test_tak_ada_kolom_ringkasan_disimpan():
    """Nol kolom ringkasan: tak ada UPDATE ke kolom agregat mana pun."""
    for terlarang in (
        "SET received_total",
        "SET unbilled_received",
        "received_total =",
        "unbilled_received =",
    ):
        assert terlarang not in ROUTER_KODE, (
            f"'{terlarang}' — agregat mulai DISIMPAN, bukan dihitung"
        )
