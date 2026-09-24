"""#29 — metode pembayaran penerimaan DITURUNKAN dari jenis akun Kas/Bank.

Akar (diukur 24 Sep 2026): jalur "Catat pembayaran" faktur menerima 'transfer'
(hardcode FE) lalu memetakan semua selain 'cash' ke 'bank_transfer' -> 16/16
RCV grapgrap ke akun kas/petty_cash tercatat "Transfer Bank".

Kontrak (GO MASTER): cash|bank_transfer|e_wallet sah (V301); kosong -> turunan
akun; nilai sah klien -> dihormati; kosakata lama jalur faktur -> turunan.
Tes endpoint memanggil HANDLER sungguhan dengan conn palsu yang merekam
parameter INSERT/UPDATE (nilai yang benar-benar dikirim ke Postgres).
"""

import re
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.routers import receive_payments as rp
from app.routers import sales_invoices as si
from app.schemas.receive_payments import (
    CreateReceivePaymentRequest,
    UpdateReceivePaymentRequest,
)
from app.schemas.sales_invoices import InvoicePaymentCreate
from app.utils import metode_pembayaran as mp

TENANT = "kaos-biru-konveksi"
APP = Path(rp.__file__).resolve().parents[1]


# ─────────────────────────── fungsi sumber tunggal ───────────────────────────
@pytest.mark.parametrize(
    "jenis,harap",
    [
        ("cash", "cash"),
        ("petty_cash", "cash"),
        ("bank", "bank_transfer"),
        ("e_wallet", "e_wallet"),
        ("credit_card", "bank_transfer"),
        (None, "bank_transfer"),  # akun CoA tanpa baris bank_accounts
        ("tak_dikenal", "bank_transfer"),
    ],
)
def test_metode_dari_jenis_akun(jenis, harap):
    assert mp.metode_dari_jenis_akun(jenis) == harap


def test_label_metode_tiga_nilai_dan_data_lama():
    assert mp.label_metode("cash") == "Tunai"
    assert mp.label_metode("bank_transfer") == "Transfer Bank"
    assert mp.label_metode("e_wallet") == "E-Wallet"
    assert mp.label_metode(None) == "Transfer Bank"  # perilaku lama dipertahankan


def test_metode_klien_sah_hanya_tiga_nilai():
    for v in ("cash", "bank_transfer", "e_wallet"):
        assert mp.metode_klien_sah(v) == v
    for v in (None, "", "transfer", "check", "other"):
        assert mp.metode_klien_sah(v) is None


def test_kelebihan_bayar_e_wallet_jadi_other_di_uang_muka():
    assert rp._deposit_payment_method("e_wallet") == "other"
    assert rp._deposit_payment_method("cash") == "cash"
    assert rp._deposit_payment_method("bank_transfer") == "transfer"


def test_v301_check_melebarkan_ke_e_wallet():
    mig = APP.parents[1] / "migrations" / "V301__receive_payments_metode_e_wallet.sql"
    sql = mig.read_text()
    m = re.search(r"ADD CONSTRAINT chk_rcv_payment_method\s+CHECK \((.*)\);", sql, re.S)
    assert m, "CHECK chk_rcv_payment_method tak ditemukan"
    nilai = set(re.findall(r"'([a-z_]+)'", m.group(1)))
    assert nilai == {"cash", "bank_transfer", "e_wallet"}
    assert set(mp.METODE_SAH) == nilai  # kode dan DB satu himpunan


# ─────────────────────────── skema ───────────────────────────
def _rp_body(**kw):
    d = dict(
        customer_id=str(uuid.uuid4()),
        payment_date=date(2026, 9, 24),
        bank_account_id=str(uuid.uuid4()),
        total_amount=Decimal("100000"),
        save_as_draft=True,
    )
    d.update(kw)
    return CreateReceivePaymentRequest(**d)


def test_skema_rp_metode_opsional_dan_check_422():
    assert _rp_body().payment_method is None
    assert _rp_body(payment_method="e_wallet").payment_method == "e_wallet"
    with pytest.raises(ValidationError):
        _rp_body(payment_method="check")
    with pytest.raises(ValidationError):
        UpdateReceivePaymentRequest(payment_method="giro")


def test_skema_faktur_menerima_kosakata_lama_dan_kosong():
    base = dict(amount=Decimal("1"), payment_date=date(2026, 9, 24), account_id=str(uuid.uuid4()))
    assert InvoicePaymentCreate(**base).payment_method is None
    for v in ("transfer", "check", "other", "cash", "bank_transfer", "e_wallet"):
        assert InvoicePaymentCreate(**base, payment_method=v).payment_method == v


# ─────────────────────────── conn palsu ───────────────────────────
class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *e):
        return False


class _Conn:
    """jenis = bank_accounts.account_type untuk akun yang dipakai (None = tak ada baris).
    akun_coa = True bila id yang dikirim adalah CoA (bukan bank_accounts.id)."""

    def __init__(self, jenis, akun_coa=False, status="draft", akun_tersimpan=None):
        self.jenis = jenis
        self.akun_coa = akun_coa
        self.status = status
        self.akun_tersimpan = akun_tersimpan
        self.insert_rp = None
        self.update_rp = None
        self.kueri_jenis = []

    def transaction(self):
        return _Tx()

    def is_in_transaction(self):
        return True

    async def execute(self, q, *a):
        if "INSERT INTO receive_payments" in q:
            self.insert_rp = (q, a)
            raise _Berhenti()  # jalur faktur: cukup sampai nilai dikirim ke DB
        if "UPDATE receive_payments" in q:
            self.update_rp = (q, a)
        return None

    async def fetchval(self, q, *a):
        if "SELECT account_type FROM bank_accounts" in q:
            self.kueri_jenis.append(a)
            return self.jenis
        if "generate_receive_payment_number" in q:
            return "RCV-UJI-0001"
        if "INSERT INTO receive_payments" in q:
            self.insert_rp = (q, a)
            return uuid.uuid4()
        if "SELECT bank_account_id FROM receive_payments" in q:
            return self.akun_tersimpan
        if "journal_lines" in q:
            return Decimal("1000000")
        return None

    async def fetchrow(self, q, *a):
        if "FROM chart_of_accounts" in q and "account_code" in q:
            if self.akun_coa:
                return {"id": a[0], "account_code": "1-10100", "name": "Kas", "account_type": "ASSET"}
            return None
        if "FROM bank_accounts ba" in q:
            return {"coa_id": uuid.uuid4(), "id": uuid.uuid4(), "account_code": "1-10101",
                    "name": "Kas Toko", "account_type": "ASSET"}
        if "FROM bank_accounts WHERE id" in q:
            return {"id": a[0], "coa_id": uuid.uuid4(), "account_name": "Kas Kecil"}
        if "FROM customers" in q:
            return {"id": a[0], "nama": "Pelanggan Uji"}
        if "FROM sales_invoices" in q:
            return {"id": a[0], "invoice_number": "INV-UJI", "customer_id": uuid.uuid4(),
                    "customer_name": "Pelanggan Uji", "status": "posted",
                    "total_amount": Decimal("1000000"), "amount_paid": 0, "ar_id": None}
        if "FROM receive_payments" in q and "status" in q:
            return {"id": a[0], "status": self.status}
        return None  # idempotency_keys: miss


class _Berhenti(Exception):
    pass


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        c = self.c

        class _A:
            async def __aenter__(self_):
                return c

            async def __aexit__(self_, *e):
                return False

        return _A()


class _Req:
    headers = {}


async def _aw(x):
    return x


async def _nop(*a, **k):
    return None


def _pasang(monkeypatch, mod, conn):
    monkeypatch.setattr(mod, "get_pool", lambda: _aw(_Pool(conn)))
    monkeypatch.setattr(mod, "get_user_context", lambda r: {"tenant_id": TENANT, "user_id": str(uuid.uuid4())})


# ─────────────────────────── POST /api/receive-payments ───────────────────────────
async def _buat_rp(monkeypatch, conn, **kw):
    _pasang(monkeypatch, rp, conn)
    monkeypatch.setattr(rp, "_ensure_receive_payments_role_preconditions", _nop)
    return await rp.create_receive_payment(_Req(), _rp_body(**kw))


def _metode_insert_rp(conn):
    q, a = conn.insert_rp
    kolom = re.search(r"INSERT INTO receive_payments \((.*?)\)", q, re.S).group(1)
    kolom = [k.strip() for k in kolom.split(",")]
    return a[kolom.index("payment_method")]


@pytest.mark.asyncio
async def test_rp_tanpa_metode_akun_kas_jadi_cash_dan_respons_tersimpan(monkeypatch):
    conn = _Conn("cash")
    res = await _buat_rp(monkeypatch, conn)
    assert _metode_insert_rp(conn) == "cash"
    assert res["data"]["payment_method"] == "cash"
    # helper menerima id yang DIKIRIM klien (bank_accounts.id) + tenant
    assert conn.kueri_jenis and conn.kueri_jenis[0][0] == TENANT


@pytest.mark.asyncio
async def test_rp_override_bank_transfer_ke_akun_kas_dihormati(monkeypatch):
    conn = _Conn("cash")
    res = await _buat_rp(monkeypatch, conn, payment_method="bank_transfer")
    assert _metode_insert_rp(conn) == "bank_transfer"
    assert res["data"]["payment_method"] == "bank_transfer"


@pytest.mark.asyncio
async def test_rp_akun_coa_tanpa_baris_bank_accounts_jadi_bank_transfer(monkeypatch):
    conn = _Conn(None, akun_coa=True)
    res = await _buat_rp(monkeypatch, conn)
    assert _metode_insert_rp(conn) == "bank_transfer"
    assert res["data"]["payment_method"] == "bank_transfer"


@pytest.mark.asyncio
async def test_rp_akun_e_wallet_jadi_e_wallet(monkeypatch):
    conn = _Conn("e_wallet")
    res = await _buat_rp(monkeypatch, conn)
    assert _metode_insert_rp(conn) == "e_wallet"
    assert res["data"]["payment_method"] == "e_wallet"


# ─────────────────────────── PUT /api/receive-payments/{id} ───────────────────────────
def _metode_update(conn):
    q, a = conn.update_rp
    sets = re.search(r"SET (.*?), updated_at", q, re.S).group(1)
    for bagian in sets.split(","):
        kol, ph = [x.strip() for x in bagian.split("=")]
        if kol == "payment_method":
            return a[int(ph.lstrip("$")) - 1]
    return "TAK-DIUBAH"


@pytest.mark.asyncio
async def test_put_ganti_akun_tanpa_metode_diturunkan(monkeypatch):
    conn = _Conn("petty_cash")
    _pasang(monkeypatch, rp, conn)
    body = UpdateReceivePaymentRequest(bank_account_id=str(uuid.uuid4()))
    await rp.update_receive_payment(_Req(), uuid.uuid4(), body)
    assert _metode_update(conn) == "cash"


@pytest.mark.asyncio
async def test_put_metode_null_eksplisit_diturunkan_dari_akun_tersimpan(monkeypatch):
    conn = _Conn("bank", akun_tersimpan=uuid.uuid4())
    _pasang(monkeypatch, rp, conn)
    body = UpdateReceivePaymentRequest(payment_method=None)
    await rp.update_receive_payment(_Req(), uuid.uuid4(), body)
    assert _metode_update(conn) == "bank_transfer"


@pytest.mark.asyncio
async def test_put_tanpa_metode_dan_akun_tak_mengubah_metode(monkeypatch):
    """Kontrol: PATCH absen = jangan ubah."""
    conn = _Conn("cash")
    _pasang(monkeypatch, rp, conn)
    body = UpdateReceivePaymentRequest(notes="catatan")
    await rp.update_receive_payment(_Req(), uuid.uuid4(), body)
    assert _metode_update(conn) == "TAK-DIUBAH"
    assert conn.kueri_jenis == []


# ─────────────────────────── POST /api/sales-invoices/{id}/payments ───────────────────────────
async def _bayar_faktur(monkeypatch, conn, metode=None):
    _pasang(monkeypatch, si, conn)
    monkeypatch.setattr(si, "_ensure_role_preconditions", _nop)
    monkeypatch.setattr(si, "resolve_account_id_by_role", lambda *a, **k: _aw(uuid.uuid4()))
    kw = dict(amount=Decimal("500000"), payment_date=date(2026, 9, 24),
              account_id=str(uuid.uuid4()), bank_account_id=str(uuid.uuid4()))
    if metode is not None:
        kw["payment_method"] = metode
    with pytest.raises(HTTPException):  # _Berhenti di INSERT -> handler membungkus 500
        await si.record_payment(_Req(), uuid.uuid4(), InvoicePaymentCreate(**kw))
    assert conn.insert_rp, "INSERT receive_payments tak tercapai — stimulus tak sampai"
    return _metode_insert_rp(conn)


@pytest.mark.asyncio
async def test_faktur_tanpa_metode_akun_petty_cash_jadi_cash(monkeypatch):
    assert await _bayar_faktur(monkeypatch, _Conn("petty_cash")) == "cash"


@pytest.mark.asyncio
async def test_faktur_transfer_hardcode_ke_akun_kas_jadi_cash(monkeypatch):
    """Kasus grapgrap: FE mengirim 'transfer' ke akun kas -> dulu bank_transfer."""
    assert await _bayar_faktur(monkeypatch, _Conn("cash"), "transfer") == "cash"


@pytest.mark.asyncio
async def test_faktur_override_sah_dihormati(monkeypatch):
    assert await _bayar_faktur(monkeypatch, _Conn("cash"), "bank_transfer") == "bank_transfer"
    assert await _bayar_faktur(monkeypatch, _Conn("bank"), "e_wallet") == "e_wallet"


@pytest.mark.asyncio
async def test_faktur_akun_bank_tanpa_metode_jadi_bank_transfer(monkeypatch):
    """Kontrol: akun bank tetap bank_transfer (perilaku lama benar untuk bank)."""
    assert await _bayar_faktur(monkeypatch, _Conn("bank"), "other") == "bank_transfer"


def test_faktur_respons_membawa_metode_tersimpan():
    """Variabel yang di-INSERT (pm) = variabel yang dipantulkan di respons."""
    src = Path(si.__file__).read_text()
    blok = src[src.index("async def record_payment("):]
    blok = blok[: blok.index("\n@router.")]
    assert '"payment_method": pm,' in blok
    assert re.search(r"body\.payment_date,\s*\n\s*pm,", blok), "INSERT tak memakai pm"


def test_kwitansi_dan_aktivitas_memakai_label_metode():
    rp_src = Path(rp.__file__).read_text()
    assert 'method_label = label_metode(pay["payment_method"])' in rp_src
    si_src = Path(si.__file__).read_text()
    assert "label_metode(p['payment_method'])" in si_src
    assert "'tunai' if p['payment_method'] == 'cash' else 'transfer'" not in si_src
