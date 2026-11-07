"""U-P4b-1 (2 Nov 2026; disetujui MASTER): pratinjau BUAT penerimaan READ ONLY tanpa kunci (POST /receive-payments/preview +
pratinjau terima pembayaran SI/SO). Pemeriksaan = _periksa_penerimaan (dipindah utuh dari buat_penerimaan, FOR UPDATE hanya
kunci=True); baris jurnal = fungsi MURNI yang dipakai _post_payment (teks SQL INSERT persis seperti sebelum ekstraksi)."""
import inspect
import pathlib
from decimal import Decimal

from app.routers import receive_payments as RP
from app.services import so_pelunasan as SP
from app.routers import sales_invoices as SI
from app.routers import sales_orders as SO

P = {"source_type": "cash", "bank_account_id": "kas", "total_amount": Decimal("950"), "discount_amount": Decimal("50"),
     "allocated_amount": Decimal("800"), "unapplied_amount": Decimal("200"), "payment_number": "RCV-1", "customer_name": "Toko"}


def test_baris_murni_arah_dan_memo():
    assert RP._baris_debit_penerimaan(P, "um") == [{"account_id": "kas", "debit": Decimal("950"), "credit": 0, "memo": "Terima Pembayaran - RCV-1"}]
    assert RP._baris_debit_penerimaan({**P, "source_type": "deposit"}, "um")[0]["account_id"] == "um"
    assert RP._baris_diskon_penerimaan(P, "dk") == [{"account_id": "dk", "debit": Decimal("50"), "credit": 0, "memo": "Potongan Penjualan - RCV-1"}]
    assert RP._baris_diskon_penerimaan(P, None) == []
    assert RP._baris_piutang_penerimaan(P, "ar") == [{"account_id": "ar", "debit": 0, "credit": Decimal("800"), "memo": "Pelunasan Piutang - Toko"}]
    assert RP._baris_kelebihan_penerimaan(P, "um") == [{"account_id": "um", "debit": 0, "credit": Decimal("200"), "memo": "Uang Muka dari Overpayment - Toko"}]
    assert RP._baris_piutang_penerimaan({**P, "allocated_amount": 0}, "ar") == [] and RP._baris_kelebihan_penerimaan({**P, "unapplied_amount": 0}, "um") == []


def test_teks_sql_baris_persis_seperti_sebelum_ekstraksi():
    assert RP._SQL_BARIS_DEBIT == ("\n            INSERT INTO journal_lines (\n                id, journal_id, line_number, account_id, debit, credit, memo"
                                   "\n            ) VALUES ($1, $2, $3, $4, $5, 0, $6)\n        ")
    assert RP._SQL_BARIS_KREDIT == ("\n            INSERT INTO journal_lines (\n                id, journal_id, line_number, account_id, debit, credit, memo"
                                    "\n            ) VALUES ($1, $2, $3, $4, 0, $5, $6)\n        ")
    assert RP._SQL_BARIS_DEBIT_DISKON == ("\n                INSERT INTO journal_lines (\n                    id, journal_id, line_number, account_id, debit, credit, memo"
                                          "\n                ) VALUES ($1, $2, $3, $4, $5, 0, $6)\n            ")


def test_penulis_memakai_pemeriksa_dan_baris_murni():
    b = inspect.getsource(RP.buat_penerimaan)
    assert "_cek = await _periksa_penerimaan(conn, ctx, body)" in b and "FOR UPDATE" not in b
    p = inspect.getsource(RP._post_payment)
    for f in ("_baris_debit_penerimaan(", "_baris_diskon_penerimaan(", "_baris_piutang_penerimaan(", "_baris_kelebihan_penerimaan("):
        assert f in p, f
    assert "INSERT INTO journal_lines" not in p  # teks SQL baris hanya di konstanta
    k = inspect.getsource(RP._periksa_penerimaan)
    assert k.count("_KUNCI(kunci)") == 3 and RP._KUNCI(True) == " FOR UPDATE" and RP._KUNCI(False) == ""


def test_pratinjau_baca_saja_tanpa_kunci_tanpa_penulis():
    r = inspect.getsource(RP.rencana_penerimaan_ro)
    assert "_periksa_penerimaan(conn, ctx, body, kunci=False)" in r and "buat_penerimaan(" not in r and "pg_advisory" not in r
    assert "INSERT" not in r and "UPDATE " not in r and "generate_receive_payment_number" not in r
    v = inspect.getsource(RP.preview_create_receive_payment)
    assert "conn.transaction(readonly=True)" in v and "rencana_penerimaan_ro(conn, ctx, body)" in v
    assert '"payment_number_estimated": True' in v
    j = inspect.getsource(SP._jalankan_inti)
    assert "rencana_penerimaan_ro(" in j and "buat_penerimaan(" not in j and "pg_advisory_xact_lock(" not in j  # panggilan, bukan docstring
    for mod, fn in ((SI, "preview_receive_payment_for_invoice"), (SO, "preview_receive_payment_from_order")):
        assert "conn.transaction(readonly=True)" in inspect.getsource(getattr(mod, fn)), fn


def test_nomor_perkiraan_rumus_sama_dengan_generator():
    r = inspect.getsource(RP.rencana_penerimaan_ro)
    assert "await nomor_rcv_pratinjau(conn, tid)" in r
    # BUG-098 (V483): RCV-YYMM-NNNN, penghitung per bulan -- cermin Python == fungsi DB di berkas migrasi
    n = inspect.getsource(RP.nomor_rcv_pratinjau)
    assert "(sq['last_number'] + 1) if (sq and sq['year_month'] == ym) else 1" in n and 'f"RCV-{yymm}-' in n
    mig = (pathlib.Path(__file__).resolve().parents[3] / "migrations" / "V483__nomor_rcv_yymm_bulanan.sql").read_text()
    assert "WHEN receive_payment_sequences.year_month = v_ym" in mig and "'RCV-' || TO_CHAR(v_tgl, 'YYMM')" in mig
    # U-P4b-2: rumus OVP pindah ke helper SATU sumber (pratinjau buat + pratinjau posting draf)
    assert "await _perkiraan_nomor_ovp(conn, tid)" in r
    assert "OVP-{bl['yymm']}-{((last or 0) + 1):04d}" in inspect.getsource(RP._perkiraan_nomor_ovp)


def test_faktur_sesudah_penerimaan_sisa_turun_sebesar_alokasi():
    alok = [{"invoice_id": "i1", "invoice_number": "INV-1", "invoice_amount": Decimal("1000"), "remaining_before": Decimal("1000"),
             "amount_applied": Decimal("400")},
            {"invoice_id": "i2", "invoice_number": "INV-2", "invoice_amount": Decimal("500"), "remaining_before": Decimal("500"),
             "amount_applied": Decimal("500")}]
    r = RP._faktur_sesudah_penerimaan(alok, False)
    assert [(x["remaining_after"], x["status_after"]) for x in r] == [(600.0, "partial"), (0.0, "paid")]
    d = RP._faktur_sesudah_penerimaan(alok, True)
    assert [(x["remaining_after"], x["status_after"]) for x in d] == [(1000.0, None), (500.0, None)]
    assert 'hasil["invoices"] = _faktur_sesudah_penerimaan(cek["validated_allocations"], body.save_as_draft)' in inspect.getsource(RP.rencana_penerimaan_ro)
