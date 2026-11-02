"""UMP-PO U-A (2 Nov 2026, rancangan disetujui MASTER): plafon uang muka pembelian ber-PO <= sisa PO (cermin plafon DP Penjualan),
applicable-bills mendahulukan FPB dari PO yang sama (same_po), journal_number di respons UMP.

SPEK (Law 1/16 turunan jurnal; 13 kunci PO; 23 satu transaksi; 9/25 Decimal; 27 peran AP_PREPAID):
- neto per UMP = terima (VENDOR_DEPOSIT) − lain (selain VENDOR_DEPOSIT/DEPOSIT_APPLICATION, mis. DEPOSIT_REFUND) pada akun AP_PREPAID,
  source_id = UMP, jurnal efektif; draf = nominal (menahan). Penerapan TIDAK melepas plafon (== DP).
- sisa = total PO − Σ(neto + draf) UMP LAIN; jumlah > sisa + 0,005 -> blok UMP_MELEBIHI_SISA_PO 400 (kalimat cermin DP).
- dipasang di rencana buat (+pratinjau tanpa kunci), ubah (PO/nominal berubah; aturan PO sama dengan buat), posting (cek ulang).
"""
import asyncio
import inspect
import uuid
from decimal import Decimal

from app.services import uang_muka_po as UPO
from app.services import uang_muka_vendor_rencana as R
from app.routers import vendor_deposits as RV
from app.schemas.vendor_deposits import VendorDepositResponse

T = "tenant-uji"


def _j(c):
    return asyncio.run(c)


class Conn:
    def __init__(self, po=None, baris=None):
        self.po, self.baris, self.q = po, baris or [], []

    async def fetchrow(self, sql, *a):
        self.q.append((" ".join(sql.split()), a))
        return self.po

    async def fetch(self, sql, *a):
        self.q.append((" ".join(sql.split()), a))
        return self.baris


def _akun(monkeypatch, akun=uuid.uuid4()):
    async def f(conn, tid, peran):
        assert peran == "AP_PREPAID"
        return akun
    monkeypatch.setattr(UPO, "resolve_account_id_by_role", f)
    return akun


def _baris():
    return [{"id": uuid.uuid4(), "status": "posted", "amount": Decimal("300"), "terima": Decimal("300"), "lain": Decimal("100")},
            {"id": uuid.uuid4(), "status": "partially_applied", "amount": Decimal("50"), "terima": Decimal("50"), "lain": Decimal("0")},
            {"id": uuid.uuid4(), "status": "draft", "amount": Decimal("150"), "terima": Decimal("0"), "lain": Decimal("0")}]


def test_rincian_po_turunan_jurnal_draf_menahan_dan_kunci(monkeypatch):
    akun = _akun(monkeypatch)
    pid, kecuali = uuid.uuid4(), uuid.uuid4()
    c = Conn({"id": pid, "po_number": "PO-1", "total_amount": Decimal("1000")}, _baris())
    r = _j(UPO.rincian_po(c, T, pid, exclude_id=kecuali, kunci=True))
    assert r == {"po_number": "PO-1", "total": Decimal("1000"), "neto": Decimal("250"), "draf": Decimal("150"),
                 "dibayar_dimuka": Decimal("400"), "sisa": Decimal("600")}
    sql_po, a_po = c.q[0]
    assert sql_po.endswith("FOR UPDATE") and a_po == (pid, T)
    sql, a = c.q[1]
    assert a == (T, pid, akun, str(kecuali))
    for potong in ("je.source_id = vd.id AND is_effective_journal(je.id)", "jl.account_id = $3", "vd.status <> 'void'",
                   "FILTER (WHERE je.source_type = 'VENDOR_DEPOSIT')", "NOT IN ('VENDOR_DEPOSIT', 'DEPOSIT_APPLICATION')",
                   "vd.purchase_order_id = $2", "vd.id <> $4::uuid"):
        assert potong in sql, potong
    c = Conn({"id": pid, "po_number": "PO-1", "total_amount": 1000}, [])
    _j(UPO.rincian_po(c, T, pid))
    assert not c.q[0][0].endswith("FOR UPDATE") and c.q[1][1][3] is None


def test_periksa_plafon_batas_persis_dan_kalimat(monkeypatch):
    _akun(monkeypatch)
    pid = uuid.uuid4()
    po = {"id": pid, "po_number": "PO-1", "total_amount": Decimal("1000")}
    assert _j(UPO.periksa_plafon(Conn(po, _baris()), T, pid, Decimal("600"), None, kunci=False)) is None
    assert _j(UPO.periksa_plafon(Conn(po, _baris()), T, pid, Decimal("600.004"), None, kunci=False)) is None
    b = _j(UPO.periksa_plafon(Conn(po, _baris()), T, pid, Decimal("600.01"), None, kunci=False))
    from app.services import teks_galat as tg
    assert b == {"code": "UMP_MELEBIHI_SISA_PO", "status": 400, "message": (
        f"Jumlah {tg.rp(Decimal('600.01'))} melebihi sisa yang masih dapat dibayar di muka. Nilai pesanan {tg.rp(Decimal('1000'))}, "
        f"sudah dibayar di muka {tg.rp(Decimal('400'))}, sisa yang masih dapat dibayar di muka {tg.rp(Decimal('600'))}.")}
    assert _j(UPO.periksa_plafon(Conn(po, []), T, None, Decimal("1"), None, kunci=False)) is None  # tanpa PO
    assert _j(UPO.periksa_plafon(Conn(None, []), T, pid, Decimal("1"), None, kunci=False)) is None  # PO tak ada: penjaga lain


def test_peran_belum_dipetakan_neto_nol_draf_tetap(monkeypatch):
    async def f(conn, tid, peran):
        raise UPO.AccountRoleUnmappedError("x")
    monkeypatch.setattr(UPO, "resolve_account_id_by_role", f)
    pid = uuid.uuid4()
    c = Conn({"id": pid, "po_number": "PO-1", "total_amount": Decimal("100")}, [_baris()[2]])
    r = _j(UPO.rincian_po(c, T, pid))
    assert r["draf"] == Decimal("150") and r["sisa"] == Decimal("-50") and c.q[1][1][2] is None


# ---- pemasangan di penentu ----
def _src(fn):
    return inspect.getsource(fn)


def test_dipasang_di_buat_ubah_posting_dan_pratinjau_tanpa_kunci():
    b = _src(R.rencana_buat)
    assert "async def rencana_buat(conn, ctx, body, pratinjau: bool = False)" in b
    assert "UPO.periksa_plafon(conn, tid, body.purchase_order_id, body.amount, None, kunci=not pratinjau)" in b
    p = _src(R.rencana_posting)
    assert 'UPO.periksa_plafon(conn, tid, vd["purchase_order_id"], jumlah, vd["id"], kunci=not pratinjau)' in p
    u = _src(R.rencana_ubah)
    assert 'UPO.periksa_plafon(conn, tid, po_id, perubahan.get("amount", vd["amount"]), vd["id"], kunci=not pratinjau)' in u
    assert 'bloks.append(blok("PO_BEDA_VENDOR"' in u and 'bloks.append(blok("PO_DIBATALKAN"' in u
    pv = _src(RV.preview_create_vendor_deposit)
    assert "R.rencana_buat(conn, ctx, data, pratinjau=True)" in pv


class ConnUbah:
    def __init__(self, po):
        self.po = po

    async def fetchrow(self, sql, *a):
        return self.po


def test_ubah_po_lain_vendor_dan_plafon(monkeypatch):
    vend, pid = uuid.uuid4(), uuid.uuid4()
    vd = {"id": uuid.uuid4(), "status": "draft", "vendor_id": vend, "purchase_order_id": None, "amount": Decimal("10")}
    r = _j(R.rencana_ubah(ConnUbah({"vendor_id": uuid.uuid4(), "status": "sent"}), {"tenant_id": T}, vd, {"purchase_order_id": pid}))
    assert [b["code"] for b in r["blocks"]] == ["PO_BEDA_VENDOR"]
    dipanggil = []

    async def plafon(conn, tid, po_id, jumlah, kecuali, kunci):
        dipanggil.append((po_id, jumlah, kecuali, kunci))
        return {"code": "UMP_MELEBIHI_SISA_PO", "message": "m", "status": 400}
    monkeypatch.setattr(UPO, "periksa_plafon", plafon)
    r = _j(R.rencana_ubah(ConnUbah({"vendor_id": vend, "status": "sent"}), {"tenant_id": T}, {**vd, "purchase_order_id": pid},
                          {"amount": Decimal("99")}))
    assert [b["code"] for b in r["blocks"]] == ["UMP_MELEBIHI_SISA_PO"] and dipanggil == [(pid, Decimal("99"), vd["id"], True)]
    dipanggil.clear()
    r = _j(R.rencana_ubah(ConnUbah(None), {"tenant_id": T}, vd, {"notes": "x"}))
    assert r["blocks"] == [] and dipanggil == []  # medan bukan uang/PO: tak memeriksa plafon


# ---- applicable-bills + journal_number ----
def test_applicable_bills_same_po_dulu():
    s = _src(RV.get_applicable_bills)
    assert "ORDER BY same_po DESC, b.due_date NULLS LAST, b.invoice_number" in s
    assert "b.purchase_order_id IS NOT DISTINCT FROM $3::uuid) AS same_po" in s and 'vd["purchase_order_id"]' in s
    assert '"same_po": bool(r["same_po"])' in s


def test_journal_number_di_respons_ump():
    assert "journal_number" in VendorDepositResponse.model_fields
    c = _src(RV.create_vendor_deposit)
    assert "journal_number=nomor_jurnal" in c and "SELECT journal_number FROM journal_entries WHERE id = $1 AND tenant_id = $2" in c
    sub = "(SELECT je.journal_number FROM journal_entries je WHERE je.id = vd.journal_id AND je.tenant_id = vd.tenant_id) AS journal_number"
    assert sub in _src(RV.list_vendor_deposits) and sub in _src(RV.update_vendor_deposit)
