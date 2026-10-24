"""SALDO-AWAL-VENDOR-A (25 Okt 2026; aturan "Saldo awal hutang vendor = tagihan pembuka" + V458 disetujui LANGSUNG pemilik): saldo awal hutang
vendor = tagihan pembuka (bills.is_opening_balance) diposting lewat post_bill YANG SAMA -> Dr Modal Saldo Awal / Cr Hutang Usaha, source BILL.
Menggantikan jurnal VENDOR_OPENING_BALANCE lama (Dr Hutang = terbalik) dan penahan SALDO-AWAL-VENDOR-C."""
import ast
import asyncio
import inspect
import textwrap
from decimal import Decimal as D
from pathlib import Path

from app.routers import vendors as RV
from app.services import bills_service as BS
from app.services import tagihan_aksi as TA

MIG = Path(__file__).resolve().parents[3] / "migrations" / "V458__bill_saldo_awal_vendor.sql"


def _tubuh(fn):
    s = textwrap.dedent(inspect.getsource(fn))
    f = ast.parse(s).body[0]
    isi = f.body[1:] if ast.get_docstring(f) is not None else f.body
    return "\n".join(ast.get_source_segment(s, n) for n in isi)


def test_v458_aditif_dan_satu_tagihan_pembuka_aktif_per_vendor():
    s = " ".join(MIG.read_text().split())
    assert "ALTER TABLE bills ADD COLUMN IF NOT EXISTS is_opening_balance boolean NOT NULL DEFAULT false" in s
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_bill_saldo_awal_vendor ON bills (tenant_id, vendor_id) WHERE is_opening_balance AND status_v2 <> 'void'" in s


class _K:
    """Koneksi palsu: is_opening_balance & subkontrak dijawab; fetch (baris tagihan) DILARANG di cabang pembuka."""

    def __init__(self, pembuka):
        self.pembuka = pembuka

    async def fetchval(self, sql, *a):
        if "is_opening_balance" in sql:
            return self.pembuka
        if "production_subcontracts" in sql:
            return False
        raise AssertionError(sql[:60])

    async def fetch(self, sql, *a):
        raise AssertionError("tagihan pembuka tak membaca baris produk")


def test_hitung_debit_tagihan_pembuka_satu_baris_modal():
    r = asyncio.run(TA.hitung_debit(_K(True), "t", "b1", D("7000"), "akun-modal"))
    assert r == [("akun-modal", D("7000"), TA.SALDO_AWAL)]
    assert TA.memo_debit("akun-modal", TA.SALDO_AWAL, "Vendor X", None) == "Saldo awal hutang - Vendor X"


def test_blok_posting_peran_debit_modal_dan_tanpa_pajak(monkeypatch):
    from app.services import role_resolver as RR
    peran = []

    async def res(conn, tid, role):
        peran.append(role)
        return f"akun-{role}"

    async def buka(conn, tid, tgl):
        return None
    async def vat(conn, tid, role):
        return "akun-ppn"
    monkeypatch.setattr(RR, "resolve_account_id_by_role", res)
    monkeypatch.setattr(RR, "resolve_account_id_by_role_if_pkp", vat)
    monkeypatch.setattr(TA, "periode_tertutup", buka)
    bill = {"id": "b1", "status_v2": "draft", "issue_date": None, "tax_amount": 0}
    bloks, akun = asyncio.run(TA.blok_posting(_K(True), "t", bill))
    assert not bloks and akun["debit_role"] == RR.AccountRole.EQUITY_OPENING_BALANCE and akun["debit"] == f"akun-{RR.AccountRole.EQUITY_OPENING_BALANCE}"
    bloks, _ = asyncio.run(TA.blok_posting(_K(True), "t", {**bill, "tax_amount": 1100}))
    assert [b["code"] for b in bloks] == ["SALDO_AWAL_BERPAJAK"]
    # kontrol: tagihan biasa tetap Persediaan
    _, akun = asyncio.run(TA.blok_posting(_K(False), "t", bill))
    assert akun["debit_role"] == RR.AccountRole.INVENTORY_MERCHANDISE


def test_post_bill_conn_luar_memakai_koneksi_pemanggil():
    src = inspect.getsource(BS.BillsService.post_bill)
    assert "conn_luar=None" in src and "_pinjam(conn_luar) if conn_luar is not None else self.pool.acquire()" in src

    class Kon:
        pass
    k = Kon()

    async def pakai():
        async with BS._pinjam(k) as c:
            return c
    assert asyncio.run(pakai()) is k


def test_endpoint_satu_transaksi_lewat_post_bill_tanpa_jurnal_tulisan_tangan():
    t = _tubuh(RV.set_vendor_opening_balance)
    urut = ['_ib.mulai(conn, ctx, _ib.kunci_dari(request), "VENDOR_OB"', 'f"VENDOR_OPENING:{vid}"', "FROM vendors WHERE id = $1",
            "AccountRole.EQUITY_OPENING_BALANCE", "is_opening_balance AND status_v2 <> 'void'", "INSERT INTO bills",
            "INSERT INTO bill_items", "svc.post_bill(ctx[\"tenant_id\"], bill_id, UUID(str(ctx[\"user_id\"])), conn_luar=conn)",
            '_ib.simpan(conn, ctx, kp, sidik, "VENDOR_OB"']
    pos = [t.index(x) for x in urut]
    assert pos == sorted(pos), list(zip(urut, pos))
    assert "INSERT INTO journal_entries" not in t and "INSERT INTO journal_lines" not in t  # jurnal HANYA lewat post_bill
    ins = t[t.index("INSERT INTO bills"):t.index("INSERT INTO bill_items")]
    assert "'draft'" in ins and "is_opening_balance" in ins and "$6, $6" in ins and ", 0, false, $5, $5, 0, $5, true)" in ins  # jatuh tempo = tanggal, tanpa PPN
    assert "KODE_SALDO_AWAL_VENDOR_SUDAH_ADA" in t and "KODE_AKUN_SALDO_AWAL_BELUM_DIPETAKAN" in t


def test_penahan_c_dicabut():
    assert not hasattr(RV, "SALDO_AWAL_VENDOR_DITAHAN") and not hasattr(RV, "PESAN_SALDO_AWAL_VENDOR_DITAHAN")
