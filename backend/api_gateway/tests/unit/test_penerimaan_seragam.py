"""PENJUALAN-SERAGAM / Penerimaan (20 Okt 2026): belum_dialokasikan TURUNAN alokasi (bukan cache wrapper), aksi, alokasi_keadaan/teks,
penerimaan_saja server, with_totals, summary ?search=+hitungan, bundel /receive-payments/{id}/documents.
Nyata (kaos+grapgrap, baca-saja): scripts/ops/uji-nyata-penerimaan-seragam.sh."""
import asyncio
import inspect
import itertools
import uuid
from decimal import Decimal as D

from app.middleware.permission_middleware import izin_untuk_jalur
from app.routers import dokumen as DK
from app.routers import receive_payments as RP
from app.schemas.receive_payments import ReceivePaymentListItem, ReceivePaymentListResponse
from app.services import dokumen_bundel as DB
from app.services import penerimaan_daftar as PD


def test_kisi_alokasi_fakta():
    for st, wrapper, bruto, dial in itertools.product(["draft", "posted", "voided"], (True, False), (D("1000"), D("0"), None),
                                                       (D("0"), D("400"), D("999.99"), D("1000"), D("1000.004"), D("1200"))):
        f = PD.alokasi_fakta(wrapper, st, bruto, dial)
        assert f["alokasi_keadaan"] in ("belum", "sebagian", "penuh", "tanpa")
        assert f["alokasi_teks"] == PD.TEKS[f["alokasi_keadaan"]]
        if not wrapper or st != "posted" or bruto is None:
            assert f["alokasi_keadaan"] == "tanpa" and f["belum_dialokasikan"] == 0.0 and f["aksi"] is None and f["dialokasikan"] == 0.0
            continue
        belum = max(bruto - dial, D(0))
        assert D(str(f["dialokasikan"])) == dial                                            # "Ke faktur" = Σ alokasi aktif (bukan cache)
        if dial <= bruto:
            assert abs(D(str(f["dialokasikan"])) + D(str(f["belum_dialokasikan"])) - bruto) < D("0.0001")  # dialokasikan + belum == total
        assert abs(D(str(f["belum_dialokasikan"])) - belum) < D("0.0001")
        assert (f["aksi"] == "belum_dialokasikan") == (belum > D("0.005"))
        assert f["alokasi_keadaan"] == ("penuh" if belum <= D("0.005") else "belum" if belum >= bruto - D("0.005") else "sebagian")


def test_teks_persis_putusan_master():
    assert PD.TEKS == {"belum": "Belum dialokasikan", "sebagian": "Sebagian dialokasikan", "penuh": "Teralokasi penuh", "tanpa": "—"}
    f = PD.alokasi_fakta(True, "posted", D("16284600"), D("0"))   # kasus nyata kaos: alokasi dilepas, cache wrapper masih 'penuh'
    assert f == {"dialokasikan": 0.0, "belum_dialokasikan": 16284600.0, "aksi": "belum_dialokasikan", "alokasi_keadaan": "belum", "alokasi_teks": "Belum dialokasikan"}
    assert PD.alokasi_fakta(True, "posted", D("500"), D("200"))["alokasi_keadaan"] == "sebagian"
    assert PD.alokasi_fakta(True, "posted", D("500"), D("500"))["alokasi_keadaan"] == "penuh"


def test_sql_belum_dari_alokasi_aktif_bukan_cache():
    q = PD.sql_belum("w")
    assert "receive_payment_allocations" in q and "a.status = 'active'" in q and "w.total_amount" in q
    assert "unapplied_amount" not in q and "allocated_amount" not in q
    cari, par = RP._syarat_cari("kaos", "rp", 7)
    assert "rp.payment_number ILIKE $7" in cari and "rp.customer_name ILIKE $7" in cari and "c.tenant_id = $1" in cari and par == ["%kaos%"]
    assert RP._syarat_cari(None, "q", 2) == (None, []) and RP._syarat_cari("", "q", 2) == (None, [])
    assert "q.payment_number ILIKE $2" in RP._syarat_cari("x", "q", 2)[0]


def test_documents_baris_tanpa_rekap():
    kel = [{"key": "rekap", "docs": [{"kind": "rekap"}]}, {"key": "pnw", "docs": [{"kind": "quotation"}]}, {"key": "kw", "docs": [{"kind": "receipt"}]},
           {"key": "ki", "docs": [{"kind": "delivery"}, {"kind": "invoice"}]}, {"key": "nk", "docs": [{"kind": "credit_note"}]}]
    assert [d["type"] for d in PD.documents_baris(kel)] == ["quotation", "receipt", "delivery", "invoice", "credit_note"]


def test_tanda_tangan_dan_skema():
    p = inspect.signature(RP.list_receive_payments).parameters
    assert {"penerimaan_saja", "aksi", "with_totals"} <= set(p)
    assert "search" in inspect.signature(RP.get_receive_payments_summary).parameters
    for k in ("belum_dialokasikan", "aksi", "alokasi_keadaan", "alokasi_teks", "documents", "doc_count"):
        assert k in ReceivePaymentListItem.model_fields
    assert {"sum_total", "sum_sisa"} <= set(ReceivePaymentListResponse.model_fields)
    # default = bentuk lama
    assert p["penerimaan_saja"].default.default is False and p["with_totals"].default.default is False


def test_rute_dokumen_penerimaan_dipetakan_izin():
    assert izin_untuk_jalur("/api/receive-payments/0b3c/documents", "GET") == ("receive_payment", "R")
    assert izin_untuk_jalur("/api/receive-payments/0b3c", "GET") == ("receive_payment", "R")
    assert hasattr(DK, "susun_dokumen_penerimaan") and any(getattr(r, "path", "") == "/receive-payments/{payment_id}/documents" for r in DK.router.routes)


def test_kwitansi_penerimaan_sendiri_posted_dan_voided_nominal_total(monkeypatch):
    pid = uuid.uuid4()
    dipanggil = {}

    class Conn:
        async def fetch(self, sql, *a):
            dipanggil["sql"], dipanggil["args"] = sql, a
            return [{"id": pid, "payment_number": "RCV-1", "payment_date": None, "payment_method": "cash", "jumlah": D("16284600"),
                     "tenant_id": "t", "bank_account_id": None, "bank_name": None, "account_number": None, "account_holder_name": None,
                     "account_name": None, "coa_name": None}]
    from app.services import faktur_cetak, proforma_atribusi
    monkeypatch.setattr(faktur_cetak, "label_metode", lambda m, b: "Tunai")
    monkeypatch.setattr(proforma_atribusi, "label_rekening", lambda p, pemilik=False: "")
    hasil = asyncio.run(DB.kwitansi_penerimaan_sendiri(Conn(), "t", [pid]))
    assert "rp.status IN ('posted', 'voided')" in dipanggil["sql"] and "rp.total_amount AS jumlah" in dipanggil["sql"]
    assert "receive_payment_allocations" not in dipanggil["sql"]       # amount = total, bukan Σ alokasi
    assert hasil[pid]["kind"] == "receipt" and hasil[pid]["amount"] == "16284600.00" and hasil[pid]["receipt_kind"] == "pelunasan"
    assert asyncio.run(DB.kwitansi_penerimaan_sendiri(Conn(), "t", [])) == {}


def test_koreksi_cache_turunan_hanya_posted_wrapper():
    f = PD.alokasi_fakta(True, "posted", D("16284600"), D("0"))
    t = PD.dengan_turunan(f)
    assert t["allocated_amount"] == 0.0 and t["unapplied_amount"] == 16284600.0 and t["dialokasikan"] == 0.0     # kasus nyata kaos: cache 'penuh' basi
    f2 = PD.alokasi_fakta(True, "posted", D("10000"), D("10000"))
    assert PD.dengan_turunan(f2)["allocated_amount"] == 10000.0 and PD.dengan_turunan(f2)["unapplied_amount"] == 0.0
    for wrapper, st in ((True, "voided"), (True, "draft"), (False, "posted")):
        g = PD.dengan_turunan(PD.alokasi_fakta(wrapper, st, D("5"), D("5")))
        assert "allocated_amount" not in g and "unapplied_amount" not in g                                        # cache apa adanya


def test_bruto_efektif_memuat_potongan():
    assert "discount_amount" in PD.sql_bruto("w") and "w.total_amount" in PD.sql_bruto("w")
    # potongan Rp50: bayar 1000 + potongan 50, teralokasi 900 -> belum 150 (== rumus tulis unapplied = (total+diskon) - alokasi)
    assert PD.alokasi_fakta(True, "posted", D("1050"), D("900"))["belum_dialokasikan"] == 150.0
    src = inspect.getsource(RP.list_receive_payments) + inspect.getsource(RP.get_receive_payment)
    assert src.count("dengan_turunan(") == 2
    from app.services import rp_periode as RPE
    assert "__DIAL__" not in RPE.SQL_RINGKASAN and "receive_payment_allocations" in RPE.SQL_RINGKASAN and "discount_amount" in RPE.SQL_RINGKASAN
