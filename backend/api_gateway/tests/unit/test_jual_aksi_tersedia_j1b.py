"""aksi_tersedia DETAIL jual J1b (10 Nov 2026; kontrak backend/docs/KONTRAK-jualbeli-aksi-standar-so-20261109.md §8): Penawaran, Proforma,
Faktur, Surat Jalan, Penerimaan, Uang Muka, Nota Kredit. Diuji: kunci persis, penjaga yang diekstrak dipakai PENULIS (teks lama),
saklar kunci baca void penawaran, blok isian diabaikan, kirim faktur ber-SO = penentu SO, terbit faktur non-draf tanpa penentu berat,
detail memuat aksi_tersedia. Paritas nyata vs penulis + kunci RO = scripts/ops/uji-salinan-jual-aksi.sh."""
import asyncio
import inspect
import uuid

import pytest
from fastapi import HTTPException

from app.routers import credit_notes as NK
from app.routers import customer_deposits as CD
from app.routers import deliveries as DL
from app.routers import proformas as PF
from app.routers import quotes as QT
from app.routers import receive_payments as RP
from app.routers import sales_invoices as SI
from app.routers import sales_orders as SO
from app.services import nota_kredit_rencana as NKR
from app.services import pihak_helpers as PH

T = "kaos-biru-konveksi"
ID = uuid.uuid4()


def run(c):
    return asyncio.run(c)


class KonRO:
    def __init__(self, nilai=None, baris=()):
        self.tx, self.sql, self.nilai, self.baris = [], [], nilai, list(baris)

    def transaction(self, **kw):
        kon = self

        class Tx:
            async def __aenter__(self):
                kon.tx.append(kw)

            async def __aexit__(self, *e):
                return False
        return Tx()

    async def execute(self, sql, *a):
        self.sql.append(sql)

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        return self.nilai

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        return {"id": ID, "status": "sent", "quote_number": "Q-1", "total_amount": 1, "customer_name": "x",
                "converted_to_type": None, "converted_to_id": None, "amount": 5}

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        return self.baris


# ---------------------------------------------------------------- kunci persis (KUNCI-aksi-detail-per-modul §2–§8)
def test_kunci_persis():
    assert QT.AKSI_DETAIL_PN == ("kirim", "kirimdok", "terima", "pesanan", "tolak", "perpanjang", "batal", "hapus")
    assert PF.AKSI_DETAIL_PF == ("terbit", "ubah", "batal", "koreksi")
    assert SI.AKSI_DETAIL_SI == ("terbit", "hapusdraf", "bayar", "kirim", "nk", "void")
    assert DL.AKSI_DETAIL_SJ == ("sjbatal",)
    assert RP.AKSI_DETAIL_RP == ("rpposting", "rphapus", "batal")
    assert CD.AKSI_DETAIL_DP == ("terapkan", "lepas", "refund", "batal", "pindah")
    assert NK.AKSI_DETAIL_NK == ("terbit", "batal", "terapkan", "lepas", "refund")


# ---------------------------------------------------------------- penjaga diekstrak: teks lama + penulis memanggilnya
@pytest.mark.parametrize("status,pesan", [("void", "Faktur berstatus Batal tidak bisa dihapus."),
                                          ("posted", "Faktur yang sudah diterbitkan tidak bisa dihapus. Batalkan fakturnya."),
                                          ("draft", None)])
def test_penjaga_hapus_faktur(status, pesan):
    if pesan is None:
        SI.penjaga_hapus_faktur({"status": status})
        return
    with pytest.raises(HTTPException) as e:
        SI.penjaga_hapus_faktur({"status": status})
    assert (e.value.status_code, e.value.detail) == (400, pesan)


def test_penjaga_kirim_dan_terbit_faktur():
    with pytest.raises(HTTPException) as e:
        SI.penjaga_kirim_faktur({"status": "draft", "fulfillment_status": "pending"})
    assert e.value.detail == "Hanya faktur terbit (belum lunas, sebagian, atau lunas) yang bisa dikirim."
    with pytest.raises(HTTPException) as e:
        SI.penjaga_kirim_faktur({"status": "posted", "fulfillment_status": "fulfilled"})
    assert e.value.detail == "Status pengiriman: fulfilled, tidak bisa dikirim lagi"
    SI.penjaga_kirim_faktur({"status": "paid", "fulfillment_status": "partial"})
    with pytest.raises(HTTPException) as e:
        SI.penjaga_terbit_faktur({"status": "posted"})
    assert e.value.detail == SI.SI_BUKAN_DRAF == SO.SI_BUKAN_DRAF
    SI.penjaga_terbit_faktur({"status": "draft"})


def test_penulis_faktur_memakai_penjaga():
    assert "penjaga_hapus_faktur(invoice)" in inspect.getsource(SI.delete_invoice)
    assert "penjaga_kirim_faktur(invoice)" in inspect.getsource(SI.fulfill_invoice)
    assert "penjaga_terbit_faktur(invoice)" in inspect.getsource(SI.post_invoice)
    assert "penjaga_faktur_nk(faktur)" in inspect.getsource(PH.pastikan_cn_muat_faktur)


@pytest.mark.parametrize("faktur,boleh", [({"status": "posted", "journal_id": ID}, True), ({"status": "draft", "journal_id": None}, False),
                                          ({"status": "void", "journal_id": ID}, False), ({"status": "paid", "journal_id": None}, False)])
def test_penjaga_faktur_nk(faktur, boleh):
    if boleh:
        PH.penjaga_faktur_nk(faktur)
    else:
        with pytest.raises(HTTPException) as e:
            PH.penjaga_faktur_nk(faktur)
        assert e.value.detail == "Faktur belum dibukukan atau sudah dibatalkan."


def test_penjaga_penawaran():
    with pytest.raises(HTTPException) as e:
        QT.penjaga_ubah_penawaran("accepted")
    assert e.value.detail.endswith("tidak bisa diubah. Duplikat untuk membuat penawaran baru.")
    for s in QT.STATUS_BOLEH_UBAH:
        QT.penjaga_ubah_penawaran(s)
    with pytest.raises(HTTPException):
        QT.penjaga_konversi_penawaran({"status": "draft", "quote_number": "Q-1"})
    QT.penjaga_konversi_penawaran({"status": "viewed", "quote_number": "Q-1"})
    src = inspect.getsource(QT)
    assert src.count("penjaga_konversi_penawaran(quote)") == 3  # definisi + to-order + to-invoice
    assert "penjaga_ubah_penawaran(quote[\"status\"])" in inspect.getsource(QT.update_quote)


@pytest.mark.parametrize("kunci,harap", [(True, 2), (False, 0)])
def test_void_penawaran_saklar_kunci(kunci, harap):
    k = KonRO()
    run(QT._ambil_penawaran_void(k, T, str(ID), kunci=kunci))
    assert sum("pg_advisory_xact_lock" in s or "FOR UPDATE" in s for s in k.sql) == harap
    assert inspect.signature(QT._ambil_penawaran_void).parameters["kunci"].default is True


def test_blok_refund_nk(monkeypatch):
    async def bertaut(conn, tid, cn):
        if cn["original_invoice_id"]:
            raise HTTPException(409, {"code": "CN_TERTAUT", "message": "tertaut"})
    monkeypatch.setattr(NK, "tolak_nk_bertaut", bertaut)
    cn = {"status": "applied", "amount_applied": 10, "amount_refunded": 0, "original_invoice_id": None}
    st, det = run(NKR.blok_refund_nk(None, T, cn))
    assert st == 400 and "dikembalikan dananya" in det
    assert run(NKR.blok_refund_nk(None, T, {**cn, "status": "posted", "amount_applied": 0, "original_invoice_id": ID})) == \
        (409, {"code": "CN_TERTAUT", "message": "tertaut"})
    assert run(NKR.blok_refund_nk(None, T, {**cn, "status": "posted", "amount_applied": 0})) is None
    assert "blok_refund_nk(conn, tid, cn)" in inspect.getsource(NKR.rencana_refund_nk)


def test_blok_status_dp(monkeypatch):
    async def label(conn, tid, did):
        return "Uang muka"
    monkeypatch.setattr(CD, "label_deposit", label)
    dep = {"status": "applied", "amount_applied": 5, "amount_refunded": 0}
    b = run(CD.blok_status_dp(None, T, ID, dep, "refund"))
    assert b["code"] == "DEPOSIT_NOT_REFUNDABLE" and b["message"].endswith("tidak bisa dikembalikan.")
    assert run(CD.blok_status_dp(None, T, ID, dep, "apply"))["code"] == "DEPOSIT_NOT_APPLYABLE"
    assert run(CD.blok_status_dp(None, T, ID, {**dep, "status": "partial"}, "apply")) is None
    assert "blok_status_dp(conn, tid, deposit_id, dep, aksi)" in inspect.getsource(CD._rencana_dp)


# ---------------------------------------------------------------- perakitan (penentu dipalsukan)
class Blk:
    def __init__(self, daftar):
        self.daftar = daftar


def test_nk_isian_diabaikan(monkeypatch):
    async def posting(c, ctx, cid, pratinjau):
        assert pratinjau is True
        return {"blocks": Blk([])}

    async def batal(c, ctx, cid, alasan, pratinjau):
        return {"blocks": Blk([{"code": "VOID_REASON_REQUIRED", "message": "alasan"}])}

    async def terapkan(c, tid, cid, body, pratinjau):
        assert body.applications == []
        return {"blocks": Blk([{"code": "CN_APPLY_SATU_FAKTUR", "message": "isian"}])}

    async def lepas(c, tid, cid, alasan, pratinjau):
        return {"blocks": Blk([{"code": "CN_ALASAN_WAJIB", "message": "isian"},
                               {"code": "CN_BELUM_DITERAPKAN", "message": "belum diterapkan"}])}

    async def refund(c, tid, cn):
        return None
    for n, f in (("rencana_posting_nk", posting), ("rencana_batal_nk", batal), ("rencana_terapkan_nk", terapkan),
                 ("rencana_lepas_nk", lepas), ("blok_refund_nk", refund)):
        monkeypatch.setattr(NKR, n, f)
    k = KonRO()
    h = run(NK.aksi_tersedia_nk(k, {"tenant_id": T}, {"id": ID}))
    assert k.tx == [{"readonly": True}] and list(h) == list(NK.AKSI_DETAIL_NK)
    assert [h[x]["tersedia"] for x in NK.AKSI_DETAIL_NK] == [True, True, True, False, True]
    assert h["lepas"]["alasan"] == "belum diterapkan"


@pytest.mark.parametrize("apps,blok,harap", [([], {}, "tak_ada"), ([{"id": 1}, {"id": 2}], {1: [{"code": "X", "message": "x"}], 2: []}, None),
                                             ([{"id": 1}], {1: [{"code": "APPLICATION_JOURNAL_REVERSED", "message": "dibalik"}]}, "dibalik")])
def test_dp_lepas_penerapan_mana_pun(monkeypatch, apps, blok, harap):
    async def rencana(c, ctx, did, aksi, body):
        if aksi == "reverse":
            return blok[body.application_id]
        return []

    async def pindah(c, tid, did, body, kunci):
        assert kunci is False and body.target_proforma_id is None
        return {"blocks": [{"code": "PROFORMA_TIDAK_VALID", "message": "isian"}]}

    async def status(c, tid, did, dep, aksi):
        return None
    monkeypatch.setattr(CD, "_rencana_dp", rencana)
    monkeypatch.setattr(CD, "_rencana_pindah", pindah)
    monkeypatch.setattr(CD, "blok_status_dp", status)
    h = run(CD.aksi_tersedia_dp(KonRO(baris=apps), {"tenant_id": T}, {"id": ID}))
    assert h["pindah"]["tersedia"] is True
    assert h["lepas"]["alasan"] == {"tak_ada": CD.PENERAPAN_DP_TAK_ADA, None: None, "dibalik": "dibalik"}[harap]


@pytest.mark.parametrize("so_id", [None, uuid.uuid4()])
def test_si_kirim_ber_so_pakai_penentu_so(monkeypatch, so_id):
    dipanggil = []

    async def kirim_so(c, ctx, oid):
        dipanggil.append(oid)
        return "dari SO"

    async def berat(*a, **k):
        raise AssertionError("penentu terbit penuh tak boleh jalan untuk faktur non-draf")

    async def void(c, ctx, iid):
        return {"blocks": []}

    async def bayar(c, ctx, iid, body):
        return {"blocks": [{"code": "RP_ACCOUNT_REQUIRED", "message": "isian"}]}
    monkeypatch.setattr(SO, "alasan_kirim_so", kirim_so)
    monkeypatch.setattr("app.services.faktur_terbit_rencana.rencana_terbit_ro", berat)
    monkeypatch.setattr(SI, "_rencana_void_faktur", void)
    monkeypatch.setattr("app.services.so_pelunasan.rencana_pelunasan_faktur", bayar)
    inv = {"id": ID, "status": "posted", "fulfillment_status": "fulfilled", "journal_id": ID, "sales_order_id": so_id}
    h = run(SI.aksi_tersedia_si(KonRO(), {"tenant_id": T}, inv))
    assert h["terbit"] == {"tersedia": False, "alasan": SI.SI_BUKAN_DRAF}
    assert h["bayar"]["tersedia"] is True and h["void"]["tersedia"] is True and h["nk"]["tersedia"] is True
    if so_id:
        assert dipanggil == [so_id] and h["kirim"]["alasan"] == "dari SO"
    else:
        assert dipanggil == [] and h["kirim"]["alasan"] == "Status pengiriman: fulfilled, tidak bisa dikirim lagi"


def test_pn_pesanan_idempoten_dan_batal_isian(monkeypatch):
    async def void(c, tid, q, alasan):
        return [{"code": "ALASAN_WAJIB", "message": "isian"}]

    async def hapus(c, ctx, qid):
        return [{"code": "QUOTE_BUKAN_DRAF", "message": "bukan draf"}]

    async def rencana(*a, **k):
        return None

    async def hari(c, tid):
        import datetime
        return datetime.date(2026, 11, 10)
    monkeypatch.setattr(QT, "_rencana_void_penawaran", void)
    monkeypatch.setattr(QT, "rencana_hapus_penawaran", hapus)
    monkeypatch.setattr(QT, "rencana_kirim_penawaran", rencana)
    monkeypatch.setattr(QT, "rencana_terima_tolak", rencana)
    monkeypatch.setattr("app.utils.tanggal_tenant.tanggal_dokumen", hari)
    q = {"id": ID, "status": "converted", "quote_number": "Q-1", "expiry_date": None, "converted_to_type": "sales_order",
         "converted_to_id": uuid.uuid4()}
    h = run(QT.aksi_tersedia_pn(KonRO(nilai=1), {"tenant_id": T}, q))
    assert h["pesanan"]["tersedia"] is True            # SO hasil konversi ada -> already_converted (penulis 200)
    assert h["batal"]["tersedia"] is True              # ALASAN_WAJIB = isian
    assert h["hapus"] == {"tersedia": False, "alasan": "bukan draf"}
    assert h["perpanjang"]["tersedia"] is False        # converted bukan STATUS_BOLEH_UBAH
    h2 = run(QT.aksi_tersedia_pn(KonRO(nilai=None), {"tenant_id": T}, q))
    assert h2["pesanan"]["tersedia"] is False          # SO hasil konversi tak ada -> penjaga status


def test_sj_nonaktif_jadi_alasan(monkeypatch):
    async def void(c, tid, fid, alasan, kunci=False):
        assert kunci is False
        raise HTTPException(409, {"code": "SJ_SO_NONAKTIF", "message": "Pengiriman sebelum faktur belum tersedia untuk usaha ini."})
    monkeypatch.setattr("app.services.sj_so.rencana_void", void)
    h = run(DL.aksi_tersedia_sj(KonRO(), {"tenant_id": T}, ID))
    assert h == {"sjbatal": {"tersedia": False, "alasan": "Pengiriman sebelum faktur belum tersedia untuk usaha ini."}}


# ---------------------------------------------------------------- detail memuat aksi_tersedia di dalam data
def test_detail_memuat_aksi_tersedia():
    from app.schemas.aksi_tersedia import AksiTersediaPeta
    from app.schemas.credit_notes import CreditNoteDetail
    from app.schemas.customer_deposits import CustomerDepositDetail
    from app.schemas.quotes import QuoteDetail
    from app.schemas.receive_payments import ReceivePaymentDetail
    for kelas in (QuoteDetail, ReceivePaymentDetail, CustomerDepositDetail, CreditNoteDetail):
        assert kelas.model_fields["aksi_tersedia"].annotation == AksiTersediaPeta
    for f, nama in ((QT.get_quote_detail, "aksi_tersedia_pn"), (PF.get_proforma_detail, "aksi_tersedia_pf"),
                    (SI.get_invoice, "aksi_tersedia_si"), (DL.get_delivery_detail, "aksi_tersedia_sj"),
                    (RP.get_receive_payment, "aksi_tersedia_rp"), (CD.get_customer_deposit, "aksi_tersedia_dp"),
                    (NK.get_credit_note, "aksi_tersedia_nk")):
        assert f"await {nama}(" in inspect.getsource(f), nama
