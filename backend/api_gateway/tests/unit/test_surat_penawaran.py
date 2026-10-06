"""Surat Penawaran (6 Okt 2026, MASTER): isian SNAPSHOT + sumber per medan + bagian PDF hanya bila berisi."""
import inspect
import pathlib

import pytest
from fastapi import HTTPException

from app.services import penawaran_surat as PS
from app.routers import quotes as Q
from app.routers import accounting_settings as AS
from app.schemas.quotes import CreateQuoteRequest, UpdateQuoteRequest, QuoteDetail

BAWAAN = {"opening_text": {"value": "Dengan hormat,", "source": "company"}, "closing_text": None,
          "notes": {"value": "Harga belum termasuk ongkir", "source": "company"}, "terms": None,
          "attention_name": {"value": "Bu Rina", "source": "customer"}, "attention_title": None,
          "signer": {"user_id": "u1", "name": "Anton", "title": "Direktur", "phone": "0812", "email": "a@x.id",
                     "source": "company"}}


def test_create_tanpa_medan_diisi_bawaan_bersumber_default():
    n, s = PS.gabung({}, {}, BAWAAN, buat=True)
    assert n["opening_text"] == "Dengan hormat," and n["notes"] == "Harga belum termasuk ongkir"
    assert n["closing_text"] is None and n["attention_name"] == "Bu Rina" and n["attention_title"] is None
    assert (n["signer_name"], n["signer_title"], n["signer_phone"], n["signer_email"]) == ("Anton", "Direktur", "0812", "a@x.id")
    assert set(s.values()) == {"default"} and set(s) == set(PS.MEDAN)


def test_sumber_eksplisit_disimpan_apa_adanya_dan_tanpa_sumber_manual():
    n, s = PS.gabung({"opening_text": "Halo", "notes": "Catatan X"}, {"opening_text": "default"}, BAWAAN, buat=False)
    assert n == {"opening_text": "Halo", "notes": "Catatan X"}
    assert s == {"opening_text": "default", "notes": "manual"}


def test_kosong_disengaja_dan_patch_tanpa_medan_tak_berubah():
    n, s = PS.gabung({"terms": "   "}, {}, BAWAAN, buat=False)
    assert n == {"terms": None} and s == {"terms": "manual"}
    assert PS.gabung({}, {}, BAWAAN, buat=False) == ({}, {})


def test_sumber_default_tanpa_nilai_salin_bawaan_saat_ini():
    n, s = PS.gabung({}, {"signer_title": "default"}, BAWAAN, buat=False)
    assert n == {"signer_title": "Direktur"} and s == {"signer_title": "default"}


def test_batas_panjang_422():
    with pytest.raises(HTTPException) as e:
        PS.gabung({"signer_phone": "1" * 51}, {}, BAWAAN, buat=False)
    assert e.value.status_code == 422


def test_skema_menerima_dan_mengeluarkan_medan_sumber():
    for M in (CreateQuoteRequest, UpdateQuoteRequest, QuoteDetail):
        for m in PS.MEDAN:
            assert m in M.model_fields and m + "_source" in M.model_fields, (M.__name__, m)
        assert "signer_user_id" in M.model_fields
    assert "total_in_words" in QuoteDetail.model_fields
    with pytest.raises(Exception):
        UpdateQuoteRequest(notes_source="live")


def test_update_tak_menulis_medan_surat_lewat_update_data():
    src = inspect.getsource(Q.update_quote)
    assert "*_surat.MEDAN, *(m + \"_source\" for m in _surat.MEDAN)" in src and "_surat.terapkan(" in src
    assert "_surat.terapkan(" in inspect.getsource(Q.create_quote) and "buat=True" in inspect.getsource(Q.create_quote)
    assert "field_sources = s.field_sources" in inspect.getsource(Q.duplicate_quote)


def test_setelan_bawaan_surat_terdeklarasi():
    for k in AS._BARU_SURAT:
        assert k in AS.AccountingSettingsResponse.model_fields and k in AS.UpdateAccountingSettingsRequest.model_fields


def _html(**q):
    from app.services.pdf_service import get_pdf_service
    ps = get_pdf_service()
    dasar = {"quote_number": "QUO-1", "quote_date": "2026-10-06", "customer_name": "PT Contoh", "total_amount": 1500000,
             "subtotal": 1500000, "items": [{"description": "Kaos", "quantity": 3, "unit": "pcs", "unit_price": 500000,
                                              "line_total": 1500000}], "has_cents": False}
    dasar.update(q)
    return ps.render_quote(dasar, {"name": "Kaos Biru Konveksi"}).html


def test_pdf_bagian_tercetak_hanya_bila_berisi():
    lama = _html(status="sent")  # bukan draf (status kosong = draft -> stempel DRAF)
    for kata in ("Up. ", "Catatan khusus", "Syarat & ketentuan", "Kontak:", "Terbilang:", "DRAF"):
        assert kata not in lama, kata
    assert "Hormat kami," in lama and "Kaos Biru Konveksi" in lama
    isi = _html(attention_name="Bu Rina", attention_title="Purchasing", notes="N1", terms="T1", signer_name="Anton",
                signer_title="Direktur", signer_phone="0812", signer_email="a@x.id",
                total_in_words="Satu Juta Lima Ratus Ribu Rupiah", opening_text="Dengan hormat,", closing_text="Terima kasih.")
    for kata in ("Kepada Yth.", "Up. Bu Rina, Purchasing", "Catatan khusus", "Syarat & ketentuan",
                 "Kontak: HP 0812 · <!--email_off-->a@x.id<!--/email_off-->", "Terbilang: Satu Juta Lima Ratus Ribu Rupiah",
                 "Dengan hormat,", "Terima kasih."):
        assert kata in isi, kata
    # 6 Okt 2026 (pemilik, mirip Accurate): pembuka < tabel < terbilang < catatan < S&K < PENUTUP < Hormat kami < nama < jabatan
    # < baris Kontak (tanpa mengulang nama)
    urut = ["Dengan hormat,", "items-table", "Terbilang:", "Catatan khusus", "Syarat & ketentuan", "Terima kasih.",
            "Hormat kami,", '<div class="nama">Anton</div>', "<div>Direktur</div>", "Kontak:"]
    pos = [isi.index(k) for k in urut]
    assert pos == sorted(pos)


def test_detail_tak_mengoper_medan_dobel():
    src = inspect.getsource(Q.get_quote_detail)
    i = src.index("QuoteDetail(")
    eksplisit = {m for m in Q._TEKS_LAMA if f"{m}=quote[" in src[i:]}
    assert eksplisit == set(Q._TEKS_LAMA)  # dioper eksplisit...
    assert "if k not in _TEKS_LAMA" in src  # ...dan disaring dari **_surat_keluaran


def test_terapkan_tolak_penanda_tangan_bukan_anggota():
    import asyncio

    class C:
        def __init__(s): s.tulis = []
        async def fetchval(s, sql, *a):
            assert "user_tenant_roles" in sql and a[0] == "t-uji"
            return None  # bukan anggota aktif
        async def execute(s, sql, *a): s.tulis.append(sql)
    body = UpdateQuoteRequest(signer_user_id="11111111-1111-1111-1111-111111111111")
    c = C()
    with pytest.raises(HTTPException) as e:
        asyncio.run(PS.terapkan(c, "t-uji", "q1", body, {}, buat=False))
    assert e.value.status_code == 422 and c.tulis == []  # ditolak SEBELUM menulis



def test_kontak_tak_mengulang_nama_dan_baris_baru_dipertahankan():
    isi = _html(signer_name="Anton", signer_phone="0812", opening_text="Dengan hormat,\n\nBaris dua")
    kontak = isi[isi.index("Kontak:"):isi.index("</div>", isi.index("Kontak:"))]
    assert "Anton" not in kontak
    assert "white-space: pre-line" in isi and "Dengan hormat,\n\nBaris dua" in isi


def test_stempel_draf_hanya_untuk_draft():
    assert "DRAF" in _html(status="draft") and "bukan penawaran resmi" in _html(status="draft")
    assert "DRAF" not in _html(status="sent")


def test_semua_email_cetak_terbungkus_email_off():
    import re
    app = pathlib.Path(Q.__file__).resolve().parents[1] / "templates" / "pdf"
    telanjang = [(f.name, m.group(0)) for f in app.rglob("*.html")
                 for m in re.finditer(r"\{\{ [a-z_.]*email \}\}", f.read_text())
                 if "<!--email_off-->" + m.group(0) not in f.read_text()]
    assert not telanjang, telanjang


def test_dicetak_di_margin_halaman_bukan_badan():
    isi = _html()
    assert "@bottom-left" in isi and 'class="footer-line"' not in isi
