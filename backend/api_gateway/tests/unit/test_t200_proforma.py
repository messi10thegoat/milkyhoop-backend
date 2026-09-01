"""T200 — Proforma (tagihan uang muka atas Sales Order), Tahap 3.

Setiap tes di berkas ini sudah dibuktikan BISA MERAH lewat sabotase pada
KODE PRODUKSI (bukan pada tes), lalu dipulihkan. Lihat laporan tiket.
"""

import inspect
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers import proformas as pf

ROUTER_SRC = Path(pf.__file__).read_text()


def _kode_saja(src: str) -> str:
    """Buang docstring dan komentar — kita menguji KODE, bukan prosa."""
    tanpa_doc = re.sub(r'"""(?:.|\n)*?"""', "", src)
    return "\n".join(
        baris for baris in tanpa_doc.splitlines() if not baris.strip().startswith("#")
    )


ROUTER_KODE = _kode_saja(ROUTER_SRC)


def _migration_text() -> str:
    here = Path(__file__).resolve()
    for base in here.parents:
        kandidat = base / "migrations" / "V225__proformas.sql"
        if kandidat.exists():
            return kandidat.read_text()
        kandidat = base / "backend" / "migrations" / "V225__proformas.sql"
        if kandidat.exists():
            return kandidat.read_text()
    raise AssertionError(
        "V225__proformas.sql tidak ditemukan — mount direktori migrations "
        "(-v /root/mh-t200/backend/migrations:/app/backend/migrations)"
    )


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


# ---------------------------------------------------------------- PAGAR TOTAL


async def test_pagar_total_menolak_dan_menyebut_sisa():
    """Melebihi sisa -> 400, dan pesannya MENYEBUT angka sisa."""
    conn = _FakeConn([{"total": 6_000_000}])
    with pytest.raises(HTTPException) as ex:
        await pf.assert_within_order_total(
            conn, "kaos-biru-konveksi", "so-1", 10_000_000.0, 5_000_000.0
        )
    assert ex.value.status_code == 400
    assert "4,000,000.00" in ex.value.detail, ex.value.detail


async def test_pagar_total_meloloskan_tepat_sisa():
    """Persis sebesar sisa -> lolos (tidak boleh off-by-one menolak)."""
    conn = _FakeConn([{"total": 6_000_000}])
    await pf.assert_within_order_total(
        conn, "kaos-biru-konveksi", "so-1", 10_000_000.0, 4_000_000.0
    )


async def test_pagar_hanya_menghitung_issued():
    """'draft'/'cancelled'/'expired' DIKECUALIKAN. Kalau 'expired' ikut dihitung,
    SO terkunci dari penagihan ulang."""
    conn = _FakeConn([{"total": 0}])
    await pf.issued_total_for_order(conn, "kaos-biru-konveksi", "so-1")
    sql = conn.sqls[0]
    assert "status = 'issued'" in sql
    for terlarang in ("'expired'", "'draft'", "'cancelled'"):
        assert terlarang not in sql, f"{terlarang} tidak boleh ikut dihitung: {sql}"


# ------------------------------------------------------- TERBAYAR = TURUNAN


async def test_terbayar_dihitung_dari_customer_deposits_lewat_proforma_id():
    """Atribusi lewat proforma_id, BUKAN tanggal (tanggal pecah pada cicilan)."""
    conn = _FakeConn([{"paid": 3_000_000, "n": 1}])
    paid = await pf.compute_paid_amount(conn, "kaos-biru-konveksi", "pf-1")
    assert paid == 3_000_000.0
    sql = conn.sqls[0]
    assert "customer_deposits" in sql
    assert "proforma_id = $1" in sql
    assert "tenant_id = $2" in sql
    for tanggal in ("deposit_date", "proforma_date", "issued_at"):
        assert tanggal not in sql, f"atribusi TIDAK BOLEH memakai {tanggal}: {sql}"


def test_tabel_proformas_tidak_menyimpan_angka_terbayar():
    """Terbayar adalah TURUNAN: tidak boleh ada kolom tersimpan."""
    ddl = _migration_text()
    head = ddl.split("CREATE INDEX")[0]
    for kolom in ("paid_amount", "amount_paid", "amount_received", "paid_at"):
        assert kolom not in head, f"kolom tersimpan '{kolom}' melanggar 'terbayar = turunan'"


# ------------------------------------------------------------- NON-POSTING


def test_router_nol_sentuhan_jurnal():
    """MUTLAK NON-POSTING."""
    for terlarang in ("journal_entries", "journal_lines", "journal_id"):
        assert terlarang not in ROUTER_KODE, f"proformas.py menyentuh {terlarang}"


def test_status_hanya_empat_dan_bukan_status_pembayaran():
    """'paid'/'partial' BUKAN status — terbayar itu turunan."""
    ddl = _migration_text()
    m = re.search(r"chk_proformas_status CHECK \((.*?)\n    \)", ddl, re.S)
    assert m, "CHECK constraint status tidak ditemukan"
    body = m.group(1)
    for s in ("draft", "issued", "cancelled", "expired"):
        assert f"'{s}'" in body
    for s in ("paid", "partial"):
        assert f"'{s}'" not in body, f"'{s}' bukan status proforma"


# ------------------------------------------------------------- PAGAR TENANT


def _sql_literals(src: str):
    """Setiap literal SQL secara TERPISAH: blok tiga-kutip DAN string satu baris.
    Memeriksa per-literal, bukan per-blok, supaya satu query bocor tetap ketahuan."""
    out = []
    out += re.findall(r'"""(.*?)"""', src, re.S)
    out += [m for m in re.findall(r'"([^"\n]{20,})"', src) if re.search(r"\b(SELECT|UPDATE|INSERT|DELETE)\b", m)]
    return out


def test_setiap_query_proformas_menyaring_tenant_id():
    """Gateway konek BYPASSRLS -> RLS tidak melindungi jalur ini.
    WHERE tenant_id = $N adalah penjaga yang sebenarnya."""
    pelanggar = []
    for sql in _sql_literals(ROUTER_SRC):
        if not re.search(r"\b(FROM|UPDATE|INTO|JOIN)\s+proformas\b", sql):
            continue
        if "tenant_id" not in sql:
            pelanggar.append(" ".join(sql.split())[:140])
    assert not pelanggar, f"query proformas tanpa saringan tenant: {pelanggar}"


def test_detektor_tenant_bisa_melihat_query_bocor():
    """Kontrol positif: detektornya sendiri harus bisa MENOLAK."""
    bocor = _sql_literals('x = "SELECT * FROM proformas WHERE id = $1"\n')
    assert bocor and "tenant_id" not in bocor[0]


def test_so_yang_boleh_ditagih_tidak_termasuk_draft_atau_cancelled():
    assert "draft" not in pf.SO_BILLABLE_STATUSES
    assert "cancelled" not in pf.SO_BILLABLE_STATUSES
    assert "confirmed" in pf.SO_BILLABLE_STATUSES


# --------------------------------------------------------------- SERIALISASI


def test_respons_numerik_float_bukan_decimal_atau_string():
    """pydantic v2 menyerialkan Decimal sebagai STRING -> merusak matematika FE."""
    from decimal import Decimal
    from datetime import date, datetime

    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "proforma_number": "PRO-2609-0001",
        "proforma_date": date(2026, 9, 1),
        "due_date": None,
        "sales_order_id": "22222222-2222-2222-2222-222222222222",
        "customer_id": "33333333-3333-3333-3333-333333333333",
        "customer_name": "PT Uji",
        "purpose": "DP",
        "percent_of_order": Decimal("60.00"),
        "amount": Decimal("30000.00"),
        "currency": "IDR",
        "terms": None,
        "notes": None,
        "payment_bank_name": None,
        "payment_account_number": None,
        "payment_account_holder": None,
        "status": "issued",
        "issued_at": datetime(2026, 9, 1, 1, 0, 0),
        "cancelled_at": None,
        "cancelled_reason": None,
        "created_at": datetime(2026, 9, 1, 1, 0, 0),
        "updated_at": datetime(2026, 9, 1, 1, 0, 0),
    }
    out = pf.serialize_proforma(row, "SO-2609-0001", 10_000.0)
    assert isinstance(out["amount"], float) and out["amount"] == 30000.0
    assert isinstance(out["percent_of_order"], float)
    assert out["outstanding_amount"] == 20000.0
    assert out["is_fully_paid"] is False
    assert out["sales_order_number"] == "SO-2609-0001"


def test_pembatalan_ditolak_bila_ada_deposit():
    """Ada uang masuk -> batal ditolak, diarahkan ke refund."""
    src = inspect.getsource(pf.cancel_proforma)
    assert "compute_paid_amount" in src
    assert "refund" in src.lower()
