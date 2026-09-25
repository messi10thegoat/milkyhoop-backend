"""Terbayar proforma = turunan dari SO (BUG-006, putusan MASTER/Anton 25 Sep 2026).

Syarat MASTER: sisa DP journal-derived (bukan cache amount_applied); DP 3,42 jt diterapkan +
pelunasan 2,88 jt = tertutup 6,3 jt BUKAN 9,72 jt; kelebihan bayar tak terhitung ganda;
paid_breakdown di respons; pagar batal tetap uang muka eksplisit.
"""
import ast
import uuid
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest

from app.routers import proformas as PF
from app.services import proforma_terbayar as PT

TENANT = "grapgrap-manado"
SO = uuid.uuid4()
P1, P2, P3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
T0 = datetime(2026, 9, 15, 6, 37)


def _p(id_, amount, status="issued", menit=0, nomor=None):
    return {"id": id_, "amount": D(amount), "status": status,
            "issued_at": T0 + timedelta(minutes=menit) if status == "issued" else None,
            "proforma_number": nomor or str(id_)}


RAHAYU = [_p(P1, "3420000", menit=0, nomor="PRO-2609-0001"),
          _p(P2, "3420000", status="cancelled", nomor="PRO-2609-0002"),
          _p(P3, "2880000", menit=57, nomor="PRO-2609-0003")]


# ---------- alokasi (murni) ----------

def test_rahayu_sesudah_ditautkan_keduanya_lunas_dari_pesanan():
    a = PT.alokasikan(RAHAYU, D("6300000"), {})
    assert (a[P1]["paid"], a[P1]["dari_pesanan"]) == (D("3420000"), D("3420000"))
    assert a[P2]["paid"] == 0                       # batal: tak menerima alokasi
    assert (a[P3]["paid"], a[P3]["dari_pesanan"]) == (D("2880000"), D("2880000"))


def test_rahayu_sebelum_ditautkan_tetap_nol():
    a = PT.alokasikan(RAHAYU, D("0"), {})
    assert [a[p]["paid"] for p in (P1, P2, P3)] == [0, 0, 0]


def test_eksplisit_menang_lalu_sisa_kolam_berurutan():
    # DP 3,42 menunjuk PRO-1 (eksplisit) dan SUDAH diterapkan; faktur SO tertutup 6,3 total
    a = PT.alokasikan(RAHAYU, D("6300000"), {P1: D("3420000")})
    assert (a[P1]["paid"], a[P1]["dari_uang_muka_langsung"], a[P1]["dari_pesanan"]) == (D("3420000"), D("3420000"), 0)
    assert (a[P3]["paid"], a[P3]["dari_pesanan"]) == (D("2880000"), D("2880000"))
    assert sum(a[p]["paid"] for p in (P1, P2, P3)) == D("6300000")   # bukan 9,72 jt


def test_dp_eksplisit_tak_dihitung_lagi_lewat_kolam():
    # DP 3,42 menunjuk PRO-1, belum ada faktur/pelunasan: tertutup SO = 3,42 (sisa DP) saja.
    # Kalau eksplisit tak dikurangi dari kolam, PRO-3 "terbayar" 2,88 padahal belum sepeser pun.
    a = PT.alokasikan(RAHAYU, D("3420000"), {P1: D("3420000")})
    assert (a[P1]["paid"], a[P3]["paid"]) == (D("3420000"), 0)


def test_urutan_issued_at_bukan_urutan_daftar():
    pros = [_p(P3, "100", menit=10), _p(P1, "100", menit=0)]
    a = PT.alokasikan(pros, D("150"), {})
    assert (a[P1]["paid"], a[P3]["paid"]) == (D("100"), D("50"))


def test_draf_dan_kedaluwarsa_hanya_eksplisit():
    pros = [_p(P1, "100", status="draft"), _p(P2, "100", status="expired"), _p(P3, "100")]
    a = PT.alokasikan(pros, D("500"), {P1: D("30")})
    assert (a[P1]["paid"], a[P2]["paid"], a[P3]["paid"]) == (D("30"), 0, D("100"))


def test_kolam_berlebih_tak_menggelembungkan_proforma():
    a = PT.alokasikan([_p(P1, "100")], D("1000"), {})
    assert a[P1]["paid"] == D("100")


# ---------- tertutup_pesanan: journal-derived, tanpa hitung ganda ----------

class DB:
    def __init__(self, fakturs, outstanding, deps):
        self.fakturs, self.outstanding, self.deps = fakturs, outstanding, deps
        self.sql = []

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        if "FROM sales_invoices" in sql:
            return self.fakturs
        if "compute_ar_outstanding" in sql:
            return [{"invoice_id": k, "outstanding": v} for k, v in self.outstanding.items()]
        if "FROM customer_deposits cd" in sql:
            return [{"id": d["id"], "so_id": d["so_id"]} for d in self.deps]
        raise AssertionError(sql)


@pytest.fixture
def sisa_jurnal(monkeypatch):
    from app.routers import customer_deposits as CD
    tabel = {}

    async def palsu(conn, tenant_id, ids):
        return {str(i): tabel[str(i)] for i in ids}
    monkeypatch.setattr(CD, "compute_deposit_remaining_many", palsu)
    return tabel


@pytest.mark.asyncio
async def test_dp_diterapkan_plus_pelunasan_6_3_bukan_9_72(sisa_jurnal):
    inv, dep = uuid.uuid4(), uuid.uuid4()
    sisa_jurnal[str(dep)] = D("0")                      # DP sudah diterapkan -> sisa jurnal 0
    db = DB([{"id": inv, "sales_order_id": SO, "total_amount": D("6300000"), "status": "paid"}], {}, [{"id": dep, "so_id": SO}])
    t = await PT.tertutup_pesanan(db, TENANT, [SO])
    assert t[SO] == {"faktur": D("6300000"), "uang_muka_sisa": D("0"), "total": D("6300000")}


@pytest.mark.asyncio
async def test_dp_belum_diterapkan_dihitung_sekali(sisa_jurnal):
    dep = uuid.uuid4()
    sisa_jurnal[str(dep)] = D("3420000")
    db = DB([], {}, [{"id": dep, "so_id": SO}])
    t = await PT.tertutup_pesanan(db, TENANT, [SO])
    assert t[SO]["total"] == D("3420000")


@pytest.mark.asyncio
async def test_faktur_sebagian_dibayar_pakai_outstanding(sisa_jurnal):
    inv = uuid.uuid4()
    db = DB([{"id": inv, "sales_order_id": SO, "total_amount": D("6300000"), "status": "partial"}], {inv: D("2880000")}, [])
    t = await PT.tertutup_pesanan(db, TENANT, [SO])
    assert t[SO]["total"] == D("3420000")


@pytest.mark.asyncio
async def test_sql_sumber_journal_derived_dan_saringan(sisa_jurnal):
    db = DB([{"id": uuid.uuid4(), "sales_order_id": SO, "total_amount": D("1"), "status": "paid"}], {}, [])
    await PT.tertutup_pesanan(db, TENANT, [SO])
    semua = "\n".join(db.sql)
    assert "amount_applied" not in semua                     # bukan cache
    faktur = next(s for s in db.sql if "FROM sales_invoices" in s)
    assert "status NOT IN ('void', 'draft')" in faktur and "tenant_id = $1" in faktur
    dep = next(s for s in db.sql if "FROM customer_deposits cd" in s)
    # deposit TANPA sales_order_id/proforma_id (OVP/LPS kelebihan bayar) tak bisa lolos saringan ini
    assert "cd.sales_order_id = ANY($2::uuid[]) OR p.sales_order_id = ANY($2::uuid[])" in dep
    assert "cd.status <> 'void'" in dep


def test_kelebihan_bayar_dibuat_tanpa_tautan_so():
    # Penjaga premis: deposit OVP/LPS dari receive_payments tak membawa sales_order_id/proforma_id.
    src = (Path(PF.__file__).resolve().parent / "receive_payments.py").read_text()
    blok = [b for b in src.split("INSERT INTO customer_deposits (")[1:]]
    assert len(blok) >= 2
    for b in blok:
        kolom = b.split(")")[0]
        assert "sales_order_id" not in kolom and "proforma_id" not in kolom


# ---------- penyambungan di proformas.py ----------

def _pemanggil(nama_fungsi_dipanggil):
    pohon = ast.parse(Path(PF.__file__).read_text())
    hasil = set()
    for f in pohon.body:
        if isinstance(f, ast.AsyncFunctionDef):
            for n in ast.walk(f):
                if isinstance(n, ast.Call) and getattr(n.func, "id", None) == nama_fungsi_dipanggil:
                    hasil.add(f.name)
    return hasil


def test_pagar_batal_tetap_eksplisit_tampilan_pakai_turunan():
    assert _pemanggil("compute_paid_amount") == {"cancel_proforma"}
    tampil = _pemanggil("terbayar_satu") | _pemanggil("terbayar_proforma")
    assert {"list_proformas", "get_proforma_detail", "list_proformas_for_order", "update_proforma",
            "issue_proforma", "get_proforma_pdf"} <= tampil, tampil
    assert "cancel_proforma" not in tampil
    assert "SUM(cd.amount) FROM customer_deposits cd" not in Path(PF.__file__).read_text()


def test_serializer_membawa_breakdown():
    row = {k: None for k in ("id", "proforma_number", "proforma_date", "due_date", "sales_order_id", "customer_id",
                             "customer_name", "purpose", "percent_of_order", "currency", "terms", "notes",
                             "payment_bank_name", "payment_account_number", "payment_account_holder", "status",
                             "issued_at", "cancelled_at", "cancelled_reason", "created_at", "updated_at")}
    row.update(amount=D("2880000"), sales_order_id=SO, id=P3)
    bd = {"dari_uang_muka_langsung": 0.0, "dari_pesanan": 2880000.0}
    d = PF.serialize_proforma(row, "001-09-26", 2880000.0, bd)
    assert d["is_fully_paid"] is True and d["outstanding_amount"] == 0 and d["paid_breakdown"] == bd
