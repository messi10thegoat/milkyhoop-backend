"""KB-1 (25 Nov): label tipe/status rekening + field *_text di skema daftar & detail (FE tak merangkai teks uang)."""
from app.services import kasbank_daftar as KD
from app.schemas.bank_accounts import BankAccountListItem, BankAccountDetail


def test_tipe_text_semua_tipe():
    assert [KD.tipe_text(t) for t in ("bank", "cash", "petty_cash", "e_wallet", "credit_card")] == [
        "Bank", "Kas", "Kas kecil", "Dompet digital", "Kartu kredit"]


def test_status_text():
    assert KD.status_text_rekening(False, True) == "Nonaktif"
    assert KD.status_text_rekening(True, True) == "Aktif · utama"
    assert KD.status_text_rekening(True, False) == "Aktif"


def test_skema_memuat_field_text():
    d = set(BankAccountDetail.model_fields)
    assert {"opening_balance_text", "saldo_text", "status_text", "account_type_text"} <= d
    assert {"jumlah_text", "account_type_text"} <= set(BankAccountListItem.model_fields)


def test_router_pakai_format_rupiah_id():
    import inspect
    from app.routers import bank_accounts as R
    src = inspect.getsource(R)
    for k in ('"jumlah_text": tg.rp(', '"opening_balance_text": tg.rp(', '"saldo_text": tg.rp('):
        assert k in src, k


def test_baris_manual_teks_uang_arah_status():
    from datetime import date
    from decimal import Decimal
    from app.services import kasbank_daftar as KD
    r = {"id": "i", "nomor": "BT-1", "tgl": date(2026, 11, 1), "net": Decimal("-1234567"), "ba_id": "b", "account_name": "BCA", "account_type": "bank",
         "lawan_id": None, "pihak": None, "description": "x", "batal": False, "void_reason": None, "journal_id": None, "journal_number": None}
    b = KD.baris_manual(r)
    assert (b["jumlah_text"], b["arah_text"], b["status_text"]) == ("Rp 1.234.567", "Uang keluar", "Tercatat")
    b = KD.baris_manual({**r, "net": Decimal("500"), "batal": True})
    assert (b["jumlah_text"], b["arah_text"], b["status_text"]) == ("Rp 500", "Uang masuk", "Dibatalkan")
