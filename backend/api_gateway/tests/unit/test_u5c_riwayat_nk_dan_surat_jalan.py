"""U5-C / U4 (4 Okt 2026): riwayat Nota Kredit + riwayat Surat Jalan, bentuk SAMA dengan riwayat SO.

Penjaga: label manusia + urutan terbaru-dulu, aktor, `omitted` per izin (nomor faktur/pesanan tak bocor tanpa izin), None bila
tak ada di tenant, tiap kueri berfilter tenant, de-dup kolom-tanpa-aktor dengan audit beraktor (SURAT_JALAN_DIBATALKAN),
pemetaan izin rute (credit_note R / sales_invoice R) -- tanpa baris /deliveries/{id}/history rute jatuh ke tak-terpetakan."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.middleware import permission_middleware as PM
from app.routers import credit_notes as CNR
from app.routers import deliveries as DLR
from app.services import so_riwayat as SR

T0 = datetime(2026, 10, 4, 3, 0, tzinfo=timezone.utc)


def _jalan(c):
    return asyncio.run(c)


class _Conn:
    """Koneksi tiruan: jawaban per potongan SQL; semua SQL tercatat untuk uji filter tenant."""

    def __init__(self, utama, apps=(), refunds=(), audit=(), users=()):
        self.utama, self.apps, self.refunds, self.audit, self.users, self.sql = utama, apps, refunds, audit, users, []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        return self.utama if a[1] == "t1" else None

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM credit_note_applications" in sql:
            return list(self.apps)
        if "FROM credit_note_refunds" in sql:
            return list(self.refunds)
        if "FROM audit_logs" in sql:
            return list(self.audit)
        if 'FROM "User"' in sql:
            return list(self.users)
        return []

    async def fetchval(self, sql, *a):
        self.sql.append((sql, a))
        return "Asia/Jakarta"


async def _semua(m):
    return True


def _hanya(*izin):
    async def b(m):
        return m in izin
    return b


def _nk(**u):
    d = {"id": uuid.uuid4(), "credit_note_number": "CN-2610-0001", "total_amount": 100000, "created_at": T0,
         "created_by": "u1", "posted_at": T0 + timedelta(minutes=1), "posted_by": "u1", "voided_at": None, "voided_by": None,
         "voided_reason": None, "original_invoice_id": uuid.uuid4(), "invoice_number": "INV-2610-0007",
         "sales_order_id": uuid.uuid4(), "order_number": "SO-2610-0003"}
    d.update(u)
    return d


def test_riwayat_nk_siklus_lengkap_terbaru_dulu_dan_berlabel():
    app = [{"invoice_number": "INV-2610-0008", "amount_applied": 40000, "created_at": T0 + timedelta(minutes=5),
            "created_by": "u1", "reversed_at": T0 + timedelta(minutes=9), "reversed_by": "u1", "reversal_reason": "salah faktur"}]
    ref = [{"amount": 60000, "created_at": T0 + timedelta(minutes=12), "created_by": "u1"}]
    nk = _nk(voided_at=T0 + timedelta(minutes=20), voided_by="u1", voided_reason="batal")
    c = _Conn(nk, app, ref, users=[{"id": "u1", "nama": "Anton"}])
    d = _jalan(SR.riwayat_nota_kredit(c, "t1", nk["id"], _semua))
    jenis = [e["jenis"] for e in d["events"]]
    assert jenis == ["NOTA_KREDIT_DIBATALKAN", "NOTA_KREDIT_DIKEMBALIKAN", "NOTA_KREDIT_DILEPAS", "NOTA_KREDIT_DITERAPKAN",
                     "NOTA_KREDIT_DITERBITKAN", "NOTA_KREDIT_DIBUAT"]
    teks = {e["jenis"]: e["ringkas"] for e in d["events"]}
    assert teks["NOTA_KREDIT_DIBUAT"] == "Nota kredit CN-2610-0001 Rp 100.000 dibuat atas faktur INV-2610-0007"
    assert teks["NOTA_KREDIT_DITERAPKAN"] == "Nota kredit CN-2610-0001 Rp 40.000 diterapkan ke faktur INV-2610-0008"
    assert teks["NOTA_KREDIT_DILEPAS"].endswith("dilepas: salah faktur")
    assert teks["NOTA_KREDIT_DIKEMBALIKAN"] == "Nota kredit CN-2610-0001 Rp 60.000 dikembalikan ke pelanggan"
    assert teks["NOTA_KREDIT_DIBATALKAN"].endswith("dibatalkan: batal")
    assert d["events"][0]["aktor"] == {"id": "u1", "nama": "Anton"}
    assert d["total"] == 6 and d["omitted"] == []
    assert d["sales_invoice"]["invoice_number"] == "INV-2610-0007" and d["sales_order"]["order_number"] == "SO-2610-0003"


def test_riwayat_nk_tanpa_izin_faktur_dan_pesanan_tak_membocorkan_nomor():
    app = [{"invoice_number": "INV-RAHASIA", "amount_applied": 1, "created_at": T0, "created_by": None,
            "reversed_at": None, "reversed_by": None, "reversal_reason": None}]
    nk = _nk()
    d = _jalan(SR.riwayat_nota_kredit(_Conn(nk, app), "t1", nk["id"], _hanya("credit_note")))
    semua = " ".join(e["ringkas"] for e in d["events"])
    assert "INV-" not in semua and "SO-" not in semua
    assert d["sales_invoice"] is None and d["sales_order"] is None
    assert d["omitted"] == ["sales_invoice", "sales_order"]


def test_riwayat_nk_tak_ada_atau_tenant_lain_none():
    nk = _nk()
    assert _jalan(SR.riwayat_nota_kredit(_Conn(nk), "t2", nk["id"], _semua)) is None


def test_riwayat_nk_setiap_kueri_berfilter_tenant():
    nk = _nk()
    c = _Conn(nk, [{"invoice_number": "X", "amount_applied": 1, "created_at": T0, "created_by": None, "reversed_at": None,
                    "reversed_by": None, "reversal_reason": None}], [{"amount": 1, "created_at": T0, "created_by": None}])
    _jalan(SR.riwayat_nota_kredit(c, "t1", nk["id"], _semua))
    isi = " ".join(" ".join(s.split()) for s, a in c.sql)
    assert "WHERE cn.id = $1 AND cn.tenant_id = $2" in isi
    assert "FROM credit_note_applications WHERE tenant_id = $1 AND credit_note_id = $2" in isi
    assert "FROM credit_note_refunds WHERE tenant_id = $1 AND credit_note_id = $2" in isi


def _sj(**u):
    d = {"id": uuid.uuid4(), "fulfillment_number": "SJ-2610-0004", "created_at": T0, "created_by": "u1", "posted_at": T0,
         "posted_by": "u1", "voided_at": None, "voided_reason": None, "invoice_id": uuid.uuid4(),
         "invoice_number": "INV-2610-0007", "sales_order_id": uuid.uuid4(), "order_number": "SO-2610-0003"}
    d.update(u)
    return d


def test_riwayat_sj_posting_bersamaan_dengan_dibuat_tak_digandakan():
    sj = _sj()
    d = _jalan(SR.riwayat_surat_jalan(_Conn(sj), "t1", sj["id"], _semua))
    assert [e["jenis"] for e in d["events"]] == ["SURAT_JALAN_DIBUAT"]
    assert d["events"][0]["ringkas"] == "Surat Jalan SJ-2610-0004 dibuat (faktur INV-2610-0007)"
    assert d["delivery_number"] == "SJ-2610-0004" and d["sales_invoice"]["invoice_number"] == "INV-2610-0007"


def test_riwayat_sj_posting_terpisah_dan_batal_dengan_alasan():
    sj = _sj(posted_at=T0 + timedelta(minutes=3), voided_at=T0 + timedelta(minutes=9), voided_reason="salah gudang")
    d = _jalan(SR.riwayat_surat_jalan(_Conn(sj), "t1", sj["id"], _semua))
    assert [e["jenis"] for e in d["events"]] == ["SURAT_JALAN_DIBATALKAN", "SURAT_JALAN_DIPOSTING", "SURAT_JALAN_DIBUAT"]
    assert d["events"][0]["ringkas"] == "Surat Jalan SJ-2610-0004 dibatalkan: salah gudang"
    assert d["events"][1]["ringkas"].endswith("diposting (barang keluar)")


def test_riwayat_sj_audit_beraktor_menggantikan_kolom_batal_tanpa_aktor():
    sj = _sj(voided_at=T0 + timedelta(minutes=9), voided_reason="x")
    audit = [{"id": "a1", "createdAt": T0 + timedelta(minutes=9), "eventType": "FULFILLMENT_VOIDED", "userId": "u1",
              "entity_type": "invoice_fulfillments", "entity_id": sj["id"], "entity_number": "SJ-2610-0004",
              "metadata": {"ringkas": "Surat Jalan SJ-2610-0004 dibatalkan: x"}, "source": "api:sales_invoices.void"}]
    d = _jalan(SR.riwayat_surat_jalan(_Conn(sj, audit=audit, users=[{"id": "u1", "nama": "Anton"}]), "t1", sj["id"], _semua))
    batal = [e for e in d["events"] if "DIBATALKAN" in e["jenis"] or e["jenis"] == "FULFILLMENT_VOIDED"]
    assert len(batal) == 1 and batal[0]["sumber"] == "audit" and batal[0]["aktor"]["nama"] == "Anton"


def test_riwayat_sj_tanpa_izin_faktur_pesanan_tak_membocorkan():
    sj = _sj()
    d = _jalan(SR.riwayat_surat_jalan(_Conn(sj), "t1", sj["id"], _hanya()))
    assert "INV-" not in d["events"][0]["ringkas"] and d["sales_invoice"] is None and d["sales_order"] is None
    assert d["omitted"] == ["sales_invoice", "sales_order"]


def test_riwayat_sj_tak_ada_none_dan_filter_tenant():
    sj = _sj()
    assert _jalan(SR.riwayat_surat_jalan(_Conn(sj), "t2", sj["id"], _semua)) is None
    c = _Conn(sj)
    _jalan(SR.riwayat_surat_jalan(c, "t1", sj["id"], _semua))
    isi = " ".join(" ".join(s.split()) for s, a in c.sql)
    assert "WHERE f.id = $1 AND f.tenant_id = $2" in isi


@pytest.mark.parametrize("path,izin", [
    ("/api/credit-notes/X/history", ("credit_note", "R")),
    ("/api/deliveries/X/history", ("sales_invoice", "R")),
    ("/api/deliveries/X", ("sales_invoice", "R")),
])
def test_izin_rute_riwayat(path, izin):
    assert PM.PermissionMiddleware(lambda *a: None, False)._find_permission(path, "GET") == izin


def test_rute_riwayat_terdaftar_dan_memakai_pembaca_bersama():
    import inspect
    rute_nk = {r.path for r in CNR.router.routes}
    rute_sj = {r.path for r in DLR.router.routes}
    assert "/{credit_note_id}/history" in rute_nk and "/{delivery_id}/history" in rute_sj
    assert "riwayat_nota_kredit" in inspect.getsource(CNR.get_credit_note_history)
    assert "riwayat_surat_jalan" in inspect.getsource(DLR.get_delivery_history)
    # urutan rute: /history tak boleh tertelan rute parameter tunggal yang menangkap segmen pertama saja
    assert "/{credit_note_id}" in rute_nk and "/{delivery_id}" in rute_sj
