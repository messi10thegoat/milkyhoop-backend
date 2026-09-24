"""PDF baris daftar Penerimaan ber-id JURNAL (24 Sep 2026).

Bug pemilik grapgrap: "Cetak Kwitansi" DA-2609-0026 -> "Gagal memuat PDF".
Daftar Penerimaan memuat jurnal DEPOSIT_APPLICATION / CREDIT_NOTE ber-id
JURNAL; DETAIL punya jalur cadangan (200) tapi PDF hanya membaca
receive_payments -> 404. Diukur: 38 DA + 2 CN (grapgrap+kaos) detail 200 / PDF 404.

Kontrak:
  * id jurnal DEPOSIT_APPLICATION -> "Bukti Penerapan Uang Muka" (BUKAN
    kwitansi: tanpa metode/akun bank), faktur dari customer_deposit_applications.
  * id jurnal CREDIT_NOTE (jurnal PENERBITAN) -> "Bukti Nota Kredit", faktur
    dari credit_note_applications atau credit_notes.original_invoice_number.
  * id jurnal RECEIVE_PAYMENT -> kwitansi pembayaran asalnya.
  * tenant lain / jenis lain -> 404.
  * templat kwitansi tanpa kunci baru = teks kwitansi LAMA persis.
"""
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import receive_payments as RP
from app.services.pdf_service import get_pdf_service

TENANT = "grapgrap-manado"
JID = uuid.UUID("3d094e1d-13c4-4628-95a8-a274898f888b")
DEP = uuid.UUID("0b8acfdc-b076-4f4e-843b-2f08b84cb0e8")
RPID = uuid.UUID("e79ff28f-22e9-4b7c-b2ab-3ef3cf9dc37b")


def render(receipt):
    svc = get_pdf_service()
    return svc.jinja_env.get_template("kwitansi.html").render(
        receipt=receipt, company={"name": "Toko"}, generated_at=date(2026, 9, 24), batal=None
    )


LAMA = {"receipt_number": "RCV-1", "receipt_date": "2026-09-24", "payer_name": "Budi",
        "amount": 1000, "amount_words": "Seribu Rupiah", "method": "Transfer Bank",
        "bank_name": "BCA", "purpose_label": "Pelunasan Faktur", "purpose_ref": "INV-1",
        "remaining": None, "notes": None, "status": "posted"}


def _elemen(html):
    """Teks tiap elemen kunci (bukan substring bebas: <title> juga memuat judul)."""
    import re

    def satu(pola):
        m = re.findall(pola, html, re.S)
        return [" ".join(x.split()) for x in m]

    return {
        "title_tag": satu(r"<title>(.*?)</title>"),
        "h1": satu(r'<h1 class="doc-title">(.*?)</h1>'),
        "sub": satu(r'<div class="doc-subtitle">(.*?)</div>'),
        "party": satu(r'<div class="party-label">(.*?)</div>'),
        "meta": satu(r'<td class="meta-label">(.*?)</td>'),
        "hero": satu(r'<div class="hero-amount">(.*?)</div>'),
        "label": satu(r'<td class="label">(.*?)</td>'),
    }


def test_templat_default_sama_dengan_kwitansi_lama():
    e = _elemen(render(LAMA))
    assert e["title_tag"] == ["Bukti Penerimaan RCV-1"]
    assert e["h1"] == ["Bukti Penerimaan"] and e["sub"] == ["Kwitansi"]
    assert e["party"] == ["Toko", "Telah Terima Dari", "Penerima,"]
    assert e["meta"] == ["No. Kwitansi", "Tanggal"]
    assert e["hero"] == ["Jumlah Diterima Rp 1.000"]
    assert e["label"] == ["Metode", "Bank / Rekening", "Untuk Pelunasan Faktur"]


def test_templat_penerapan_jujur():
    html = render(dict(LAMA, title="Bukti Penerapan Uang Muka", subtitle="Tanpa penerimaan uang baru",
                       party_label="Pelanggan", number_label="No. Penerapan", amount_label="Jumlah Diterapkan",
                       hide_method=True, method=None, bank_name=None, source_label="Dari Uang Muka",
                       source_ref="DEP-2609-0039", signature_label="Hormat kami,"))
    e = _elemen(html)
    assert e["h1"] == ["Bukti Penerapan Uang Muka"] and e["sub"] == ["Tanpa penerimaan uang baru"]
    assert e["party"] == ["Toko", "Pelanggan", "Hormat kami,"]
    assert e["meta"] == ["No. Penerapan", "Tanggal"]
    assert e["hero"] == ["Jumlah Diterapkan Rp 1.000"]
    assert e["label"] == ["Dari Uang Muka", "Untuk Pelunasan Faktur"]
    assert "DEP-2609-0039" in html
    for tak in ("Kwitansi", "Metode", "Telah Terima Dari", "Jumlah Diterima", "Penerima,"):
        assert tak not in html, tak


# ------------------------------------------------------------------ _pdf_dari_jurnal
class Conn:
    def __init__(self, je=None, dok=None, faktur=(), jumlah=2500000, pay=None):
        self.je, self.dok, self.faktur, self.jumlah, self.pay = je, dok, list(faktur), jumlah, pay
        self.sql = []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM journal_entries" in sql:
            assert "je.tenant_id = $2" in sql
            return self.je if (self.je and a[1] == TENANT) else None
        if "FROM customer_deposits" in sql or "FROM credit_notes" in sql:
            assert "tenant_id = $2" in sql and a[1] == TENANT
            return self.dok
        if "FROM receive_payments" in sql:
            assert "tenant_id = $2" in sql
            return self.pay
        raise AssertionError(sql)

    async def fetchval(self, sql, *a):
        assert "RECEIVABLE" in sql
        return self.jumlah

    async def fetch(self, sql, *a):
        assert "journal_id = $1 AND tenant_id = $2" in sql and a[1] == TENANT
        return [{"invoice_number": f} for f in self.faktur]


def je(src="DEPOSIT_APPLICATION", status="POSTED"):
    return {"id": JID, "journal_number": "DA-2609-0026", "journal_date": date(2026, 9, 24),
            "description": "Apply Deposit", "source_type": src, "source_id": DEP, "status": status}


@pytest.mark.asyncio
async def test_jurnal_da_jadi_bukti_penerapan():
    conn = Conn(je=je(), dok={"nomor": "DEP-2609-0039", "customer_name": "Nutrindo", "faktur_asal": None},
                faktur=["INV-2609-0028"])
    pay, d = await RP._pdf_dari_jurnal(conn, str(JID), TENANT)
    assert pay is None
    assert d["title"] == "Bukti Penerapan Uang Muka" and d["hide_method"] is True
    assert (d["receipt_number"], d["payer_name"], d["amount"]) == ("DA-2609-0026", "Nutrindo", 2500000.0)
    assert (d["purpose_ref"], d["source_ref"]) == ("INV-2609-0028", "DEP-2609-0039")
    assert d["method"] is None and d["bank_name"] is None
    assert d["amount_words"].startswith("Dua Juta Lima Ratus Ribu")


@pytest.mark.asyncio
async def test_jurnal_cn_faktur_dari_nota_kredit_bila_tanpa_penerapan():
    conn = Conn(je=je("CREDIT_NOTE"), dok={"nomor": "CN-2609-0001", "customer_name": "Nathanael",
                                           "faktur_asal": "INV-2609-0004"}, faktur=[], jumlah=255000)
    _, d = await RP._pdf_dari_jurnal(conn, str(JID), TENANT)
    assert d["title"] == "Bukti Nota Kredit"
    assert (d["purpose_ref"], d["source_ref"], d["amount"]) == ("INV-2609-0004", "CN-2609-0001", 255000.0)


@pytest.mark.asyncio
async def test_jurnal_void_bertanda_batal():
    conn = Conn(je=je(status="VOID"), dok=None)
    _, d = await RP._pdf_dari_jurnal(conn, str(JID), TENANT)
    assert d["status"] == "void" and d["payer_name"] is None and d["source_ref"] is None


@pytest.mark.asyncio
async def test_jurnal_receive_payment_ke_pembayaran_asal():
    asal = {"id": RPID}
    conn = Conn(je=je("RECEIVE_PAYMENT"), pay=asal)
    pay, d = await RP._pdf_dari_jurnal(conn, str(JID), TENANT)
    assert pay is asal and d is None


@pytest.mark.asyncio
async def test_tenant_lain_atau_id_rusak_404():
    assert await RP._pdf_dari_jurnal(Conn(je=je()), str(JID), "kaos-biru-konveksi") == (None, None)
    assert await RP._pdf_dari_jurnal(Conn(je=je()), "bukan-uuid", TENANT) == (None, None)
    assert await RP._pdf_dari_jurnal(Conn(je=None), str(JID), TENANT) == (None, None)


# ------------------------------------------------------------------ handler
class HConn(Conn):
    async def fetchrow(self, sql, *a):
        if sql.startswith("SELECT * FROM receive_payments WHERE id = $1") and a[0] == JID:
            return None  # id jurnal: bukan baris receive_payments
        if 'FROM "Tenant"' in sql:
            return None
        return await super().fetchrow(sql, *a)


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


def pasang(monkeypatch, conn):
    async def _pool():
        return SimpleNamespace(acquire=lambda: _Acq(conn))

    tangkap = {}

    class Svc:
        def generate_receipt_pdf(self, receipt_data, tenant_info):
            tangkap["data"] = receipt_data
            return b"%PDF-1.7 palsu"

    monkeypatch.setattr(RP, "get_pool", _pool)
    monkeypatch.setattr(RP, "_get_pdf_service", lambda: Svc())
    return tangkap


def req():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": str(uuid.uuid4())}))


@pytest.mark.asyncio
async def test_handler_pdf_id_jurnal_da_200():
    """Sabotase 'cabang jurnal dibuang' -> 404 -> MERAH."""
    import pytest as _p

    mp = _p.MonkeyPatch()
    try:
        conn = HConn(je=je(), dok={"nomor": "DEP-2609-0039", "customer_name": "Nutrindo", "faktur_asal": None},
                     faktur=["INV-2609-0028"])
        tangkap = pasang(mp, conn)
        r = await RP.get_receive_payment_pdf(req(), str(JID), "inline")
        assert r.media_type == "application/pdf"
        assert tangkap["data"]["title"] == "Bukti Penerapan Uang Muka"
        assert "DA-2609-0026" in r.headers["content-disposition"]
    finally:
        mp.undo()


@pytest.mark.asyncio
async def test_handler_pdf_id_tak_dikenal_404(monkeypatch):
    pasang(monkeypatch, HConn(je=None))
    with pytest.raises(HTTPException) as ei:
        await RP.get_receive_payment_pdf(req(), str(JID), "inline")
    assert ei.value.status_code == 404
