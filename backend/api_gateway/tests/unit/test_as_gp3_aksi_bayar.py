"""AS-GP3 (24 Okt 2026): Pembayaran Keluar void / posting draf / hapus draf = penentu BERSAMA (services/bayar_keluar_rencana) yang
dipanggil penulis SESUDAH kunci advisory dan oleh pratinjau READ ONLY tanpa kunci; idempotensi aksi = kunci klien saja (idem_buat)."""
import asyncio
import inspect
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services import bayar_keluar_rencana as BKR

APP = Path(__file__).resolve().parents[2] / "app"
BP = (APP / "routers/bill_payments.py").read_text()
PID = str(uuid4())


def _fn(src, mulai, akhir):
    return src[src.index(mulai):src.index(akhir)]


class _K:
    """Koneksi tiruan: mencatat SQL; jawaban per kata kunci."""

    def __init__(self, bayar=None, periode=None, rekon=None, baris=(1,), alokasi=(), sisa=Decimal("0"), rincian=None, bagian=()):
        self.sql, self.bayar, self.periode, self.rekon, self.baris, self.alokasi, self.sisa = [], bayar, periode, rekon, baris, alokasi, sisa
        self.rincian, self.bagian = rincian, bagian  # rincian: baris compute_ap_rincian (None = tagihan tidak aktif)

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        if "FROM bill_payments_v2" in sql:
            return self.bayar
        if "fiscal_periods" in sql:
            return self.periode
        if "bank_transactions" in sql and "reconcil" in sql.lower():
            return self.rekon
        if "compute_ap_rincian" in sql:
            return self.rincian
        if "FROM journal_entries" in sql:
            return {"id": uuid4(), "journal_number": "JV-1", "total_debit": Decimal("100")}
        return None

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        if sql is BKR.SQL_BAGIAN_BAYAR:
            return list(self.bagian)
        if "journal_lines" in sql:
            return [r for _ in self.baris for r in ({"account_id": uuid4(), "debit": Decimal("100"), "credit": 0, "memo": "m"},
                                                     {"account_id": uuid4(), "debit": 0, "credit": Decimal("100"), "memo": "n"})]
        if "bill_payment_allocations" in sql:
            return list(self.alokasi)
        return []

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        return 0


@pytest.fixture(autouse=True)
def _tambal(monkeypatch):
    async def sisa(conn, tid, bid):
        return conn.sisa
    monkeypatch.setattr(BKR, "sisa_tagihan", sisa)
    import app.utils.tanggal_tenant as TT

    async def hari(conn, tid):
        return date(2026, 10, 24)
    monkeypatch.setattr(TT, "tanggal_dokumen", hari)
    import app.services.jaga_rekonsiliasi as JR

    async def rekon(conn, tid, sid):
        if getattr(conn, "rekon", None):
            raise HTTPException(status_code=409, detail={"code": "BANK_TX_RECONCILED", "message": "sudah direkonsiliasi"})
    monkeypatch.setattr(JR, "tolak_void_bila_terekonsiliasi", rekon)


def _bayar(status="posted", **k):
    return {"id": uuid4(), "status": status, "payment_number": "PAY-1", "payment_date": date(2026, 10, 1), "journal_id": uuid4(),
            "bank_account_id": None, "vendor_name": "V", "total_amount": Decimal("100"), **k}


def _jalan(c):
    return asyncio.get_event_loop().run_until_complete(c) if False else asyncio.run(c)


# ---------------------------------------------------------------- void
def test_void_tulis_melempar_galat_lama_persis():
    for bayar, harap in ((None, (404, "Pembayaran tidak ditemukan.")),
                         (_bayar("draft"), (400, "Pembayaran draf tidak dibatalkan, hapus saja.")),
                         (_bayar("cancelled"), (400, "Hanya pembayaran berstatus Dibayar yang bisa dibatalkan."))):
        with pytest.raises(HTTPException) as e:
            _jalan(BKR.rencana_batal_bayar(_K(bayar=bayar), "t", PID))
        assert (e.value.status_code, e.value.detail) == harap
    with pytest.raises(HTTPException) as e:
        _jalan(BKR.rencana_batal_bayar(_K(bayar=_bayar(), periode={"period_name": "Okt", "status": "CLOSED"}), "t", PID))
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        _jalan(BKR.rencana_batal_bayar(_K(bayar=_bayar(), baris=()), "t", PID))
    assert (e.value.status_code, e.value.detail) == (500, "Baris jurnal asal tidak ditemukan.")
    with pytest.raises(HTTPException) as e:
        _jalan(BKR.rencana_batal_bayar(_K(bayar=_bayar(), rekon=True), "t", PID))
    assert e.value.status_code == 409 and e.value.detail["code"] == "BANK_TX_RECONCILED"


def test_void_sudah_voided_tulis_ulang_tanpa_galat_pratinjau_blok():
    r = _jalan(BKR.rencana_batal_bayar(_K(bayar=_bayar("voided")), "t", PID))
    assert r["ulang"] and not r["blocks"]
    p = _jalan(BKR.pratinjau_batal_bayar(_K(bayar=_bayar("voided")), "t", PID))
    assert [b["code"] for b in p["blocks"]] == ["SUDAH_DIBATALKAN"] and not p["can_proceed"] and p["after"] is None


def test_void_pratinjau_mengumpulkan_semua_blok_dan_jurnal_tukar():
    k = _K(bayar=_bayar(), periode={"period_name": "Okt", "status": "LOCKED"}, rekon=True)
    p = _jalan(BKR.pratinjau_batal_bayar(k, "t", PID, "salah"))
    assert [b["code"] for b in p["blocks"]] == ["BANK_TX_RECONCILED", "PERIODE_TERTUTUP", "PERIODE_TERTUTUP"]
    assert not p["can_proceed"] and p["after"]["status"] == "voided"
    j = p["journals"][0]
    assert j["journal_number"] is None and j["reversal_of"] == "JV-1" and j["lines"][0]["credit"] == 100.0 and j["lines"][0]["debit"] == 0.0
    assert not any("FOR UPDATE" in s for s in k.sql)


def test_void_tulis_mengunci_baris_pembayaran():
    k = _K(bayar=_bayar())
    _jalan(BKR.rencana_batal_bayar(k, "t", PID))
    assert any("FOR UPDATE OF bp" in s for s in k.sql)


# ---------------------------------------------------------------- posting draf
def test_posting_tulis_galat_lama_dan_sisa_di_bawah_kunci():
    for bayar, harap in ((None, (404, "Pembayaran tidak ditemukan.")), (_bayar("posted"), (400, "Hanya pembayaran berstatus Draf yang bisa diposting."))):
        with pytest.raises(HTTPException) as e:
            _jalan(BKR.rencana_posting_draf_bayar(_K(bayar=bayar), "t", PID))
        assert (e.value.status_code, e.value.detail) == harap
    bid = uuid4()
    k = _K(bayar=_bayar("draft"), alokasi=[{"bill_id": bid, "amount_applied": Decimal("60"), "invoice_number": "B1"},
                                           {"bill_id": bid, "amount_applied": Decimal("50"), "invoice_number": "B1"}], sisa=Decimal("100"))
    with pytest.raises(HTTPException) as e:  # Σ per tagihan 110 > sisa 100 (alokasi ke tagihan sama dijumlah)
        _jalan(BKR.rencana_posting_draf_bayar(k, "t", PID))
    assert (e.value.status_code, e.value.detail) == (400, "Nominal melebihi sisa faktur pembelian.")
    assert any("FROM bills" in s and "FOR UPDATE" in s for s in k.sql)


def test_posting_pratinjau_tanpa_kunci_mengumpulkan():
    bid = uuid4()
    k = _K(bayar=_bayar("draft"), periode={"period_name": "Okt", "status": "CLOSED"},
           alokasi=[{"bill_id": bid, "amount_applied": Decimal("200"), "invoice_number": "B1"}], sisa=Decimal("100"))
    r = _jalan(BKR.rencana_posting_draf_bayar(k, "t", PID, pratinjau=True))
    assert [b["code"] for b in r["blocks"]] == ["PERIODE_TERTUTUP", "MELEBIHI_SISA_TAGIHAN"]
    assert not any("FOR UPDATE" in s for s in k.sql)


# ---------------------------------------------------------------- hapus draf
def test_hapus_galat_lama_dan_kunci():
    for bayar, harap in ((None, (404, "Pembayaran tidak ditemukan.")), (_bayar("posted"), (400, "Hanya pembayaran berstatus Draf yang bisa dihapus."))):
        with pytest.raises(HTTPException) as e:
            _jalan(BKR.rencana_hapus_draf_bayar(_K(bayar=bayar), "t", PID))
        assert (e.value.status_code, e.value.detail) == harap
    k = _K(bayar=_bayar("draft"))
    _jalan(BKR.rencana_hapus_draf_bayar(k, "t", PID))
    assert any("FOR UPDATE" in s for s in k.sql)
    k2 = _K(bayar=_bayar("posted"))
    p = _jalan(BKR.pratinjau_hapus_draf_bayar(k2, "t", PID))
    assert [b["code"] for b in p["blocks"]] == ["STATUS_BUKAN_DRAF"] and not any("FOR UPDATE" in s for s in k2.sql)


# ---------------------------------------------------------------- penulis & rute
@pytest.mark.parametrize("mulai,akhir,penentu,awalan", [
    ("async def delete_bill_payment(", "async def post_bill_payment(", "BKR.rencana_hapus_draf_bayar(", "BP_DELETE"),
    ("async def post_bill_payment(", "async def void_bill_payment(", "BKR.rencana_posting_draf_bayar(", "BP_POST"),
    ("async def void_bill_payment(", "@router.get(\"/vendors/{vendor_id}/open-bills\"", "BKR.rencana_batal_bayar(", "BP_VOID"),
])
def test_penulis_idem_lalu_kunci_lalu_penentu_lalu_simpan(mulai, akhir, penentu, awalan):
    f = _fn(BP, mulai, akhir)
    i_idem = f.index(f'idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "{awalan}"')
    assert i_idem < f.index("pg_advisory_xact_lock") < f.index(penentu)
    assert f.count(f'idem_buat.simpan(conn, ctx, kp, sidik, "{awalan}"') >= 1
    assert "return BillPaymentResponse(" not in f  # setiap jawaban sukses lewat simpan (replay = bentuk sama)
    assert "response: Response = None" in f


def test_void_penulis_tak_lagi_punya_cek_sendiri():
    f = _fn(BP, "async def void_bill_payment(", "@router.get(\"/vendors/{vendor_id}/open-bills\"")
    for lama in ("tolak_void_bila_terekonsiliasi(", "Cannot void draft payment", 'detail="Baris jurnal asal tidak ditemukan."', "check_period_is_open("):
        assert lama not in f, lama
    assert 'original_btxn = r["cermin_asli"]' in f


def test_pratinjau_aksi_read_only_tanpa_kunci():
    pv = _fn(BP, "async def _pratinjau_aksi(", '@router.get("", response_model=BillPaymentListResponse)')
    assert "transaction(readonly=True)" in pv and "tr.rollback()" in pv
    assert "pg_advisory" not in pv and "FOR UPDATE" not in pv and "mulai_aksi" not in pv
    for nama in ("pratinjau_batal_bayar", "pratinjau_posting_draf_bayar", "pratinjau_hapus_draf_bayar"):
        assert f"BKR.{nama}" in pv
        assert "pratinjau=True" in inspect.getsource(getattr(BKR, nama))


def test_izin_pratinjau_sama_huruf_penulis():
    m = (APP / "middleware/permission_middleware.py").read_text()
    for aksi, huruf in (("void", "V"), ("post", "P"), ("delete", "D")):
        assert f'(r"^/api/bill-payments/[^/]+/{aksi}/preview$", ["POST"], "send_payment", "{huruf}")' in m


def test_kontrol_merah_penulis_tanpa_penentu_tertangkap():
    palsu = BP.replace("BKR.rencana_batal_bayar(", "BKR.xx(")
    f = _fn(palsu, "async def void_bill_payment(", "@router.get(\"/vendors/{vendor_id}/open-bills\"")
    with pytest.raises(ValueError):
        f.index("BKR.rencana_batal_bayar(")


# ---------------------------------------------------------------- sisa sesudah void (harness 24 Okt: tagihan void + bayar posted)
def test_void_pratinjau_sisa_sesudah_ikut_rincian_bukan_amount_applied():
    bid = uuid4()
    al = [{"bill_id": bid, "amount_applied": Decimal("100000"), "invoice_number": "B1"}]
    # tagihan TIDAK aktif (status_v2 void -> tak ada di compute_ap_rincian): sisa 0 sebelum DAN sesudah (dulu 100000 palsu)
    p = _jalan(BKR.pratinjau_batal_bayar(_K(bayar=_bayar(), alokasi=al, rincian=None,
                                                bagian=[{"bill_id": bid, "bagian": Decimal("100000")}]), "t", PID))
    assert p["bills"] == [{"bill_id": str(bid), "bill_number": "B1", "bill_active": False, "remaining_before": 0.0,
                           "amount_applied": -100000.0, "remaining_after": 0.0}]
    # tagihan aktif: sisa sesudah = outstanding + BAGIAN debit AP (bukan amount_applied; mis. alokasi > debit AP)
    p = _jalan(BKR.pratinjau_batal_bayar(_K(bayar=_bayar(), alokasi=al, rincian={"bill_total": Decimal("500000"), "outstanding": Decimal("10")},
                                                bagian=[{"bill_id": bid, "bagian": Decimal("90000")}]), "t", PID))
    assert (p["bills"][0]["bill_active"], p["bills"][0]["remaining_before"], p["bills"][0]["remaining_after"]) == (True, 10.0, 90010.0)


def test_rumus_bagian_sama_dgn_compute_ap_rincian():
    """SQL_BAGIAN_BAYAR = CTE pd_bayar/pd_bagian/c_payment V440 yang dibatasi SATU pembayaran: unsur kunci wajib ada."""
    s = " ".join(BKR.SQL_BAGIAN_BAYAR.split())
    for u in ("je.status = 'POSTED' AND je.reversed_by_id IS NULL", "coa.account_type = 'PAYABLE' AND jl.debit > 0",
              "ROUND(pb.ap_debit * a.amount_applied / SUM(a.amount_applied) OVER (), 2)", "ROW_NUMBER() OVER (ORDER BY a.id DESC)",
              "x.urut_akhir = 1 AND x.jumlah > 0 THEN x.ap_debit - x.jumlah"):
        assert u in s, u


def test_salinan_rumus_terikat_ke_definisi_fungsi():
    """SQL_BAGIAN_BAYAR menyalin blok pd_bayar..c_payment compute_ap_rincian (V441). Blok berubah ATAU migrasi baru mendefinisikan ulang
    fungsi -> MERAH: periksa paritas (harness PARITAS) lalu perbarui PIN + SQL_BAGIAN_BAYAR bersama-sama."""
    import hashlib
    mig = APP.parents[1] / "migrations"
    definisi = sorted((p for p in mig.glob("V*__*.sql") if not p.name.endswith("_ROLLBACK.sql")
                       and "compute_ap_rincian" in (s := p.read_text()) and "BADAN-MULAI" in s),
                      key=lambda p: int(p.name[1:p.name.index("__")]))
    assert [p.name for p in definisi][-1] == "V441__ap_atribusi_nota_debit.sql", [p.name for p in definisi]
    t = definisi[-1].read_text()
    b = t[t.index("-- BADAN-MULAI"):t.index("-- BADAN-SELESAI")]
    blok = b[b.index("pd_bayar AS ("):b.index("c_vendor_credit AS (")]
    assert hashlib.sha256(" ".join(blok.split()).encode()).hexdigest()[:16] == "892e0de7cc824eca"
