"""L7 lanjutan (1 Nov 2026, MASTER GO): bagikan tunggal + massal untuk Penerimaan Barang (GRN), Pembayaran Keluar (PK, pola
remittance advice), Uang Muka Pembelian (UMP) dan Nota Debit (ND). Kontrak SAMA dengan L7 PO/FPB (test_bagikan_massal_beli).

Penjaga jenis BARU: (1) tabel/kolom nomor/status draf + tenant eksplisit; label == judul nav/daftar FE; (2) penentu per item per status
nyata (GRN tanpa draf, PK batal = 'voided', UMP 'applied' boleh); (3) izin per jenis == modul PDF tunggal jenis itu, eksplisit di atas
penampung sales_order, GET /shares eksplisit (READ /api/documents terbuka tanpa pola); (4) bundel ND memberi `sent` dari tautan."""
import asyncio
import uuid

import pytest
from fastapi import HTTPException

from app.middleware import permission_middleware as PM
from app.routers import bulk_dokumen as BD
from app.routers import dokumen as D
from app.services import nota_debit_daftar as ND

T = "t1"
CTX = {"tenant_id": T, "user_id": uuid.UUID("0bccdb25-fdf0-4e99-9024-b9a20846f76c")}
# kind -> (modul bulk, tabel, kolom nomor, status draf, label nav FE, modul izin == PDF tunggal)
BARU = {
    "goods_receipt": ("goods-receipts", "goods_receipts", "receipt_number", None, "Penerimaan Barang", "purchase_order"),
    "payment_out": ("bill-payments", "bill_payments_v2", "payment_number", "draft", "Pembayaran Keluar", "send_payment"),
    "vendor_deposit": ("vendor-deposits", "vendor_deposits", "deposit_number", "draft", "Uang Muka Pembelian", "vendor_deposit"),
    "debit_note": ("vendor-credits", "vendor_credits", "credit_number", "draft", "Nota Debit", "debit_note"),
}
# status nyata (CHECK DB / data prod 1 Nov) -> boleh | kode tolak
STATUS = {
    "goods_receipt": {"posted": None, "void": "DOKUMEN_BATAL"},
    "payment_out": {"posted": None, "draft": "DOKUMEN_DRAF", "voided": "DOKUMEN_BATAL"},
    "vendor_deposit": {"posted": None, "partial": None, "applied": None, "draft": "DOKUMEN_DRAF", "void": "DOKUMEN_BATAL"},
    "debit_note": {"posted": None, "partial": None, "applied": None, "draft": "DOKUMEN_DRAF", "void": "DOKUMEN_BATAL"},
}


def _jalan(c): return asyncio.run(c)


# ---------------- (1) tabel + label ----------------
@pytest.mark.parametrize("kind", sorted(BARU))
def test_tabel_kolom_draf_dan_label(kind):
    _, tabel, kolom, draf, label, _ = BARU[kind]
    sql, d = D._DOKUMEN_TABEL[kind]
    assert f"FROM {tabel} " in sql and f"{kolom} AS nomor" in sql and "WHERE id = $1 AND tenant_id = $2" in sql
    assert d == draf
    assert D.LABEL_JENIS[kind] == label
    assert kind in D.JENIS_BAGIKAN and BD.MODUL_KIND[BARU[kind][0]] == kind and BARU[kind][0] in BD.MODUL_BAGIKAN


def test_status_batal_pk_voided_tercakup_penentu_tunggal():
    assert "voided" in D.STATUS_BATAL_BAGIKAN and "void" in D.STATUS_BATAL_BAGIKAN
    assert D.dokumen_batal("payment_out", "voided") and not D.dokumen_batal("vendor_deposit", "applied")


# ---------------- (2) penentu per item ----------------
class _Conn:
    def __init__(self, baris): self.baris, self.sql = baris, []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        return self.baris


@pytest.mark.parametrize("kind,status", [(k, s) for k in sorted(STATUS) for s in sorted(STATUS[k])])
def test_periksa_per_status_nyata(kind, status):
    uid, harap = uuid.uuid4(), STATUS[kind][status]
    c = _Conn({"nomor": "X-1", "status": status})
    if harap is None:
        assert _jalan(BD._periksa_bagikan(c, CTX, kind, uid))["status"] == status
    else:
        with pytest.raises(HTTPException) as e:
            _jalan(BD._periksa_bagikan(c, CTX, kind, uid))
        assert e.value.status_code == 409 and e.value.detail["code"] == harap
        if harap == "DOKUMEN_BATAL":
            assert e.value.detail["message"].startswith(f"{BARU[kind][4]} X-1 ")
    assert c.sql[0][1] == (uid, T)  # tenant eksplisit


@pytest.mark.parametrize("kind,status", [(k, s) for k in sorted(STATUS) for s in sorted(STATUS[k])])
def test_jalur_tunggal_dokumen_ada_draf_ditolak_sama_dengan_massal(kind, status):
    """Share/pratinjau TUNGGAL (dokumen._dokumen_ada lewat _DOKUMEN_TABEL) menolak draf PERSIS seperti massal (massal punya cek
    status 'draft' sendiri, jadi tanpa tes ini draf jalur tunggal bisa lolos diam)."""
    uid = uuid.uuid4()
    c = _Conn({"nomor": "X-1", "status": status})
    if STATUS[kind][status] == "DOKUMEN_DRAF":
        with pytest.raises(HTTPException) as e:
            _jalan(D._dokumen_ada(c, T, kind, uid))
        assert e.value.status_code == 409 and e.value.detail["code"] == "DOKUMEN_DRAF"
    else:
        assert _jalan(D._dokumen_ada(c, T, kind, uid))["status"] == status
    assert c.sql[0][1] == (uid, T)


@pytest.mark.parametrize("kind", sorted(BARU))
def test_periksa_tak_ada_404(kind):
    with pytest.raises(HTTPException) as e:
        _jalan(BD._periksa_bagikan(_Conn(None), CTX, kind, uuid.uuid4()))
    assert e.value.status_code == 404 and e.value.detail["code"] == "DOKUMEN_TAK_ADA"


# ---------------- (3) izin ----------------
def _izin(metode, jalur):
    return PM.PermissionMiddleware(lambda *a: None, False)._find_permission(jalur, metode)


@pytest.mark.parametrize("kind", sorted(BARU))
def test_izin_modul_sama_dengan_pdf_tunggal(kind):
    prefiks, modul = BARU[kind][0], BARU[kind][5]
    assert _izin("GET", f"/api/documents/{kind}/X/pdf") == (modul, "R")  # modul == PDF tunggal yang SUDAH ada
    for akhir in ("share", "share/preview", "shares/Y/revoke"):
        assert _izin("POST", f"/api/documents/{kind}/X/{akhir}") == (modul, "E"), akhir
    assert _izin("POST", f"/api/{prefiks}/bulk/share") == (modul, "E") == _izin("POST", f"/api/{prefiks}/bulk/share/preview")
    assert _izin("GET", f"/api/documents/{kind}/X/shares") == (modul, "R")  # tanpa pola = READ terbuka bagi semua anggota


# ---------------- (4) bundel ND: sent dari tautan ----------------
class _ConnNd:
    def __init__(self, vid):
        self.vid, self.share_args = vid, None

    async def fetch(self, sql, *a):
        if "FROM vendor_credits" in sql:
            return [{"id": self.vid, "credit_number": "ND-1", "credit_date": None, "total_amount": 1000, "status": "posted",
                     "original_bill_id": None}]
        assert "FROM document_shares WHERE tenant_id = $1" in sql
        self.share_args = a
        from datetime import datetime, timezone
        t = datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)
        return [{"kind": "debit_note", "doc_id": str(self.vid), "sent_at": t, "viewed_at": t}]


def test_bundel_nota_debit_sent_dari_tautan():
    vid = uuid.uuid4()
    c = _ConnNd(vid)
    out = _jalan(ND.kelompok_nd(c, T, [{"id": vid}]))
    dok = out[vid][-1]["docs"][0]
    assert dok["kind"] == "debit_note" and dok["sent"]["state"] == "viewed" and dok["sent"]["sent_at"].startswith("2026-11-01")
    assert c.share_args[0] == T and c.share_args[1] == ["debit_note"]
