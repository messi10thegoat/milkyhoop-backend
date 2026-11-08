"""U-P6 (2 Nov 2026): pratinjau Nota Kredit membawa kunci bentuk Nota Debit secara ADITIF. Kunci lama tetap ada; alias dihitung
dari nilai YANG SAMA (bukan penentu kedua). Acuan bentuk: vendor_credits /preview + aksi (nota_debit_rencana)."""
import ast
import inspect
import uuid
from decimal import Decimal

from app.routers import credit_notes as CN
from app.services import nota_kredit_rencana as NKR

PID = uuid.uuid4()
RESTOCK = [{"product_id": PID, "nama": "Kaos Biru", "qty": Decimal("2"), "unit_cost": Decimal("12500.50"), "line_cost": Decimal("25001.00"),
            "inv": None, "cogs": None}]


def test_stok_bentuk_nd_dari_restock_yang_sama():
    assert NKR.stok_bentuk_nd(RESTOCK) == [{"product_id": str(PID), "product": "Kaos Biru", "quantity": 2.0,
                                            "unit_cost": 12500.5, "total_cost": 25001.0, "unit": None}]  # BUG-138: unit aditif (restock tanpa satuan -> None)
    assert NKR.stok_bentuk_nd([]) == []


def test_bank_bentuk_nd_dari_bank_nk():
    bank = {"account_id": "a", "bank_transactions": [{"bank_account_id": "b1", "bank_account_name": "BCA", "transaction_type": "withdrawal",
                                                       "amount": -50000.0}]}
    assert NKR.bank_bentuk_nd(bank) == [{"bank_account": "BCA", "bank_account_id": "b1", "amount": -50000.0, "transaction_type": "withdrawal"}]
    assert NKR.bank_bentuk_nd(None) == [] and NKR.bank_bentuk_nd({"bank_transactions": []}) == []


def test_alias_aksi_aditif_kunci_lama_utuh():
    inv = {"invoice_id": "f1", "remaining_before": 100.0, "remaining_after": 40.0}
    lama = {"credit_note_id": "c", "action": "refund", "can_proceed": True, "blocks": [], "before": {}, "after": {}, "journals": [{"x": 1}],
            "bank": {"bank_transactions": [{"bank_account_id": "b1", "bank_account_name": "BCA", "transaction_type": "withdrawal", "amount": -1.0}]},
            "invoice": inv, "payload": {}, "preview": True}
    salin = dict(lama)
    d = NKR.alias_aksi_nd(dict(lama), RESTOCK)
    assert all(d[k] == v for k, v in salin.items())  # kunci lama tak berubah
    assert d["invoices"] == [inv] and d["stock"] == NKR.stok_bentuk_nd(RESTOCK) and d["bank_transactions"] == NKR.bank_bentuk_nd(lama["bank"])
    kosong = NKR.alias_aksi_nd({"invoice": None, "journals": []}, [])
    assert kosong["invoices"] == [] and kosong["stock"] == [] and kosong["bank_transactions"] == []


def _buat(ok):
    return {"ok": ok, "can_save": ok, "blocks": [] if ok else [{"code": "X", "message": "m"}], "gross_subtotal": 100000.0, "doc_discount": 5000.0,
            "journals_on_post": [{"source_type": "CREDIT_NOTE"}] if ok else [], "stock_returned_on_post": [],
            "invoice": {"id": "f1", "invoice_number": "INV-1", "remaining": 300000.0, "remaining_after_post": 205000.0}}


def test_alias_buat_dari_kunci_lama():
    d = NKR.alias_buat_nd(_buat(True), NKR.stok_bentuk_nd(RESTOCK))
    assert d["action"] == "create" and d["preview"] is True and d["can_proceed"] is d["can_save"] is True
    assert d["subtotal"] == d["gross_subtotal"] and d["discount_amount"] == d["doc_discount"]
    assert d["journals"] is d["journals_on_post"] and d["bank_transactions"] == []
    assert d["stock"] == NKR.stok_bentuk_nd(RESTOCK)
    assert d["invoices"] == [{"invoice_id": "f1", "invoice_number": "INV-1", "remaining_before": 300000.0, "remaining_after": 205000.0}]
    b = NKR.alias_buat_nd(_buat(False), NKR.stok_bentuk_nd(RESTOCK))
    assert b["can_proceed"] is False and b["stock"] == [] and b["journals"] == []
    t = _buat(True)
    t["invoice"] = None
    assert NKR.alias_buat_nd(t, [])["invoices"] == []


def _return_memanggil(fn, nama):
    pohon = ast.parse(inspect.getsource(fn).lstrip())
    hasil = [n.value for n in ast.walk(pohon) if isinstance(n, ast.Return) and n.value is not None]
    return [r for r in hasil if isinstance(r, ast.Call) and ast.unparse(r.func).endswith(nama)], hasil


def test_semua_pratinjau_nk_lewat_alias():
    for fn, nama in ((NKR.pratinjau_aksi_nk, "alias_aksi_nd"), (NKR.pratinjau_post_void_nk, "alias_aksi_nd"),
                     (CN.pratinjau_nota_kredit, "alias_buat_nd")):
        cocok, semua = _return_memanggil(fn, nama)
        assert cocok and len(cocok) == len([r for r in semua if isinstance(r, (ast.Dict, ast.Call))]), fn.__name__


def test_post_membawa_restock_penentu_yang_sama_void_tidak():
    cocok, _ = _return_memanggil(NKR.pratinjau_post_void_nk, "alias_aksi_nd")
    assert ast.unparse(cocok[0].args[1]) == "r['restock'] if bisa and aksi == 'post' else []"
    src = inspect.getsource(CN.pratinjau_nota_kredit)
    assert "stok_nd = NKR.stok_bentuk_nd(rp['restock'])".replace("'", '"') in src


def test_cabang_dari_faktur_alias_sesudah_blok_rakit():
    src = inspect.getsource(CN)
    i = src.index('data["ok"] = data["can_save"] = not data["blocks"]')
    j = src.index("NKR.alias_buat_nd(data, data.get(\"stock\") or [])")
    assert i < j < src.index('data["lines"] = rakit["lines"]')
