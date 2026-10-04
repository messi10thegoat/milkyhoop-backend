"""PATCH /proformas/{id} menulis PROFORMA_UPDATED (4 Okt 2026, MASTER GO): medan yang BERUBAH lama->baru + nominal
lama->baru, di transaksi yang sama dengan UPDATE, di bawah kunci PROFORMA_SO (dulu tanpa transaksi/kunci). Label manusia
saat baca. Perilaku nyata = harness kaos rollback (+1 baris riwayat, tanpa perubahan -> 0, label, 400 terbit)."""
import inspect
from datetime import date
from decimal import Decimal

from app.routers import proformas as PF
from app.services.so_riwayat import label_medan


def test_satu_transaksi_kunci_lalu_update_lalu_riwayat():
    s = inspect.getsource(PF.update_proforma)
    tx, kunci = s.index("async with conn.transaction():"), s.index("_kunci_proforma(")
    upd, riw = s.index("UPDATE proformas SET"), s.index('"PROFORMA_UPDATED"')
    assert tx < kunci < upd < riw
    assert "if ubah:" in s, "tanpa perubahan nyata -> tanpa baris riwayat"


def test_medan_ubah_sama_dengan_kolom_update():
    s = inspect.getsource(PF.update_proforma)
    for m in PF.MEDAN_UBAH_PROFORMA:
        assert f"{m} = " in s, m


def test_label_manusia():
    assert label_medan(["amount", "due_date"]) == "nominal, jatuh tempo"
    assert label_medan(["percent_of_order", "amount", "purpose", "proforma_date"]) == "tujuan, nominal, tanggal"


def test_nilai_json():
    assert PF._js_pf(Decimal("400000.00")) == 400000.0 and PF._js_pf(date(2026, 12, 31)) == "2026-12-31"
    assert PF._js_pf(None) is None and PF._js_pf("x") == "x"
