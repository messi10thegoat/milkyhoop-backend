"""AUDIT-DOKUMEN (1 Nov 2026, GO MASTER): SATU penentu jejak audit siklus hidup Pembelian + posting Faktur Penjualan (Law 12).

Penjaga: (1) tabel (jenis, aksi) -> eventType = SATU-SATUNYA sumber nama event Pembelian (tak ada string event di luar tabel);
(2) metadata HANYA {jenis, aksi, ringkas, user_id} -- tanpa nominal/baris; (3) tiap fungsi siklus hidup memanggil penentu TEPAT SEKALI,
SESUDAH perubahan status, di transaksi pemanggil; core yang dipakai jalur massal ikut (massal N = N baris + 1 BULK_*);
(4) event baru = jejak audit saja, TIDAK digabung ke riwayat dokumen (tanpa baris ganda); (5) hapus PO: aktor lewat GUC di core."""
import asyncio
import inspect
import json
import re
import uuid
from pathlib import Path

import pytest

from app.routers import bill_payments as BP
from app.routers import bulk_hapus_beli as BHB
from app.routers import bulk_po as BPO
from app.routers import goods_receipts as GR
from app.routers import purchase_orders as PO
from app.routers import sales_invoices as SI
from app.routers import vendor_credits as VC
from app.routers import vendor_deposits as VD
from app.services import audit_dokumen as AD
from app.services import bills_service as BS
from app.services import grn_daftar as GD
from app.services import nota_debit as ND
from app.services import so_riwayat as SR
from app.services import uang_muka_vendor_lepas as UVL

APP = Path(AD.__file__).resolve().parents[1]


def _jalan(c): return asyncio.run(c)


# ---------------- (1) satu sumber nama ----------------
def test_tabel_event_unik_dan_entitas_dikenal_riwayat():
    nama = [e.nama for e in AD.EVENT.values()]
    assert len(nama) == len(set(nama))
    assert all(e.entitas in SR.ENTITAS_AUDIT for e in AD.EVENT.values())
    # 1 Nov 2026 tiket (a) (OK MASTER): VD_UNAPPLIED -> riwayat=False (riwayat UMP sudah punya kolom UM_DILEPAS beraktor; tanpa ganda)
    lama = {"PO_UPDATED", "PO_RECEIVED", "VD_VOIDED", "GRN_DIBATALKAN"}
    assert {e.nama for e in AD.EVENT.values() if e.riwayat} == lama  # hanya event lama yang tampil di riwayat
    assert AD.TANPA_RIWAYAT == frozenset(nama) - lama and "VD_UNAPPLIED" in AD.TANPA_RIWAYAT
    assert all(SR.RINGKAS_AUDIT[n] == AD.EVENT[k].ringkas for k, n in ((k, e.nama) for k, e in AD.EVENT.items()) if n in lama)
    assert SR.RINGKAS_AUDIT["PO_UPDATED"] == "Pesanan pembelian diubah" and SR.RINGKAS_AUDIT["PO_RECEIVED"] == "Barang diterima"


def test_tak_ada_string_event_pembelian_di_luar_tabel():
    pola = re.compile(r"""["'](%s)["']""" % "|".join(re.escape(e.nama) for e in AD.EVENT.values()))
    luar = [f"{p.relative_to(APP)}:{i}" for p in APP.rglob("*.py") if p.name != "audit_dokumen.py"
            for i, baris in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if pola.search(baris)]
    assert luar == []


def test_pemanggil_lama_mengambil_nama_dari_tabel():
    assert '_ad.nama("vendor_deposit", "voided")' in inspect.getsource(VD.void_vendor_deposit)
    assert '_ad.nama("vendor_deposit", "unapplied")' in inspect.getsource(UVL)
    assert '_ad.nama("goods_receipt", "voided")' in inspect.getsource(GR.void_goods_receipt)
    assert '_ad.nama("goods_receipt", "voided")' in inspect.getsource(GD)
    assert '_ad.nama("purchase_order", "updated")' in inspect.getsource(PO.update_purchase_order)
    assert '_ad.nama("purchase_order", "received")' in inspect.getsource(PO.receive_goods)
    with pytest.raises(KeyError):
        AD.nama("bill", "tak_ada")


# ---------------- (2) metadata saja ----------------
class _Conn:
    def __init__(self): self.exec = []

    async def execute(self, sql, *a): self.exec.append((" ".join(sql.split()), a))


@pytest.mark.parametrize("kunci", list(AD.EVENT))
def test_catat_dokumen_satu_baris_metadata_aksi_saja(kunci):
    c, did, uid = _Conn(), uuid.uuid4(), uuid.uuid4()
    _jalan(AD.catat_dokumen(c, "t1", kunci[0], kunci[1], did, "DOK-001", uid))
    assert len(c.exec) == 1
    sql, a = c.exec[0]
    assert sql.startswith("INSERT INTO audit_logs")
    ev = AD.EVENT[kunci]
    assert a[:6] == (str(uid), ev.nama, ev.entitas, str(did), "DOK-001", "t1")
    assert a[6] == f"api:{kunci[0]}.{kunci[1]}"
    meta = json.loads(a[7])
    assert meta == {"jenis": kunci[0], "aksi": kunci[1], "ringkas": f"{ev.ringkas} DOK-001", "user_id": str(uid)}


def test_catat_dokumen_ke_dan_kunci_hanya_bila_ada():
    """Terap/lepas/refund: metadata + "ke" (nomor sasaran) + "kunci" (id baris) -- identitas, tanpa nominal; aksi tanpa sasaran: field TIDAK ada."""
    c, did, uid, kid = _Conn(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    _jalan(AD.catat_dokumen(c, "t1", "vendor_credit", "applied", did, "VC-1", uid, ke="BILL-9", kunci=kid))
    _jalan(AD.catat_dokumen(c, "t1", "vendor_deposit", "refunded", did, "VD-1", uid, kunci=kid))
    m1, m2 = (json.loads(x[1][7]) for x in c.exec)
    assert m1 == {"jenis": "vendor_credit", "aksi": "applied", "ke": "BILL-9", "kunci": str(kid), "ringkas": "Nota debit diterapkan VC-1",
                  "user_id": str(uid)}
    assert m2 == {"jenis": "vendor_deposit", "aksi": "refunded", "kunci": str(kid), "ringkas": "Uang muka pembelian dikembalikan vendor VD-1",
                  "user_id": str(uid)} and "ke" not in m2


TERAP = [
    (ND.terapkan_nota_debit, '"vendor_credit", "applied"', "INSERT INTO vendor_credit_applications", 'ke=bill["invoice_number"], kunci=app_id'),
    (ND.lepas_nota_debit, '"vendor_credit", "unapplied"', "UPDATE vendor_credit_applications SET status = 'reversed'", 'ke=app["bill_number"], kunci=app["id"]'),
    (VC.receive_refund, '"vendor_credit", "refunded"', "INSERT INTO vendor_credit_refunds", "kunci=refund_id"),
    (VD.apply_vendor_deposit, '"vendor_deposit", "applied"', "INSERT INTO vendor_deposit_applications", 'ke=bill["invoice_number"], kunci=app["id"]'),
    (VD.refund_vendor_deposit, '"vendor_deposit", "refunded"', "INSERT INTO vendor_deposit_refunds", 'kunci=refund["id"]'),
]


@pytest.mark.parametrize("fn,pasangan,ubah,identitas", TERAP, ids=[t[1].replace('"', "").replace(", ", ".") for t in TERAP])
def test_terap_lepas_refund_sekali_sesudah_tulis_baris_dengan_identitas(fn, pasangan, ubah, identitas):
    src = inspect.getsource(fn)
    assert src.count("_ad.catat_dokumen(") == 1 and src.count(pasangan) == 1, fn.__name__
    i = src.index("_ad.catat_dokumen(")
    assert i > src.index(ubah) and identitas in src[i:i + 300], fn.__name__


def test_lepas_ump_tetap_satu_penulis_nama_dari_tabel():
    src = inspect.getsource(UVL.lepas_penerapan)
    assert src.count('_ad.nama("vendor_deposit", "unapplied")') == 1 and "_ad.catat_dokumen(" not in src


def test_riwayat_ump_lepas_tanpa_ganda_dari_audit():
    did = uuid.uuid4()
    import datetime as _dt
    t = _dt.datetime(2026, 11, 1, 3, 0, tzinfo=_dt.timezone.utc)
    rows = [{"id": str(uuid.uuid4()), "createdAt": t, "eventType": ev, "userId": None, "entity_type": "vendor_deposit", "entity_id": did,
             "entity_number": "VD-1", "metadata": {"ringkas": ev}, "source": "api"} for ev in ("VD_UNAPPLIED", "VD_APPLIED", "VD_REFUNDED", "VD_VOIDED")]
    ev, _ = _jalan(SR._selesaikan(_ConnRiwayat(rows), "t1", SR._Kumpul(), {"vendor_deposit": [did]}, {"vendor_deposit": True}, 200))
    assert [e["jenis"] for e in ev] == ["VD_VOIDED"]  # terap/lepas/refund tampil dari kolom beraktor; void (tanpa kolom) dari audit


def test_catat_dokumen_tanpa_aktor_tetap_tercatat_userid_null():
    c = _Conn()
    _jalan(AD.catat_dokumen(c, "t1", "bill", "posted", uuid.uuid4(), None, None))
    a = c.exec[0][1]
    assert a[0] is None and a[4] is None and json.loads(a[7]) == {"jenis": "bill", "aksi": "posted", "ringkas": "Faktur pembelian diposting"}


# ---------------- (3) titik pasang: sekali, sesudah perubahan status ----------------
TITIK = [
    (BS.BillsService.create_bill, "bill", "posted", "status = 'posted', status_v2 = 'posted'"),
    (BS.BillsService.create_bill_v2, "bill", "posted", "status = 'posted', status_v2 = 'posted'"),
    (BS.BillsService.post_bill, "bill", "posted", "SET status_v2 = 'posted'"),
    (BS.BillsService.void_bill, "bill", "voided", "status_v2 = 'void'"),
    (BP.efek_posting_bayar, "payment_out", "posted", "INSERT INTO bank_transactions"),
    (BP.void_bill_payment, "payment_out", "voided", "SET status = 'voided'"),
    (BP.hapus_draf_bayar_core, "payment_out", "deleted", "DELETE FROM bill_payments_v2"),
    (VD._posting_inti, "vendor_deposit", "posted", "SET status = 'posted'"),
    (VD.hapus_draf_umv_core, "vendor_deposit", "deleted", "DELETE FROM vendor_deposits"),
    (VC.post_vendor_credit, "vendor_credit", "posted", "SET status = 'posted'"),
    (VC.void_vendor_credit, "vendor_credit", "voided", "SET status = 'void'"),
    (VC.hapus_draf_nd_core, "vendor_credit", "deleted", "DELETE FROM vendor_credits"),
    (PO.kirim_po_core, "purchase_order", "sent", "SET status = 'sent'"),
    (PO.batal_po_core, "purchase_order", "cancelled", "SET status = 'cancelled'"),
    (PO.close_purchase_order, "purchase_order", "closed", "SET status = 'closed'"),
    (SI._internal_post_invoice, "sales_invoice", "posted", "SET status = 'posted'"),
]


@pytest.mark.parametrize("fn,jenis,aksi,ubah", TITIK, ids=[f"{t[1]}.{t[2]}" for t in TITIK])
def test_titik_pasang_sekali_sesudah_ubah_status(fn, jenis, aksi, ubah):
    src = inspect.getsource(fn)
    tenant = "tenant_id" if fn.__qualname__.startswith("BillsService") else 'ctx["tenant_id"]'
    pola = f'_ad.catat_dokumen(conn, {tenant}, "{jenis}", "{aksi}"'
    assert src.count(pola) == 1, (fn.__qualname__, pola)
    assert src.index(pola) > src.index(ubah), fn.__qualname__  # SESUDAH status berubah (transaksi sama; gagal catat = batal)


def test_tiap_jalur_posting_tagihan_tepat_sekali():
    for fn in (BS.BillsService.create_bill, BS.BillsService.create_bill_v2, BS.BillsService.post_bill):
        assert inspect.getsource(fn).count('"bill", "posted"') == 1, fn.__qualname__
    assert inspect.getsource(BS.BillsService.void_bill).count('"bill", "voided"') == 1


def test_pk_posting_satu_penulis_untuk_buat_posted_dan_draf():
    assert inspect.getsource(BP.create_bill_payment).count("await efek_posting_bayar(") == 1
    assert inspect.getsource(BP.post_bill_payment).count("await efek_posting_bayar(") == 1
    assert inspect.getsource(BP.efek_posting_bayar).count("_ad.catat_dokumen(") == 1


def test_faktur_penjualan_posting_satu_titik_untuk_buat_dan_post():
    src = inspect.getsource(SI)
    assert src.count("await _internal_post_invoice(") == 2  # buat-posted + POST /{id}/post
    assert inspect.getsource(SI._internal_post_invoice).count("_ad.catat_dokumen(") == 1
    assert src.count('"sales_invoice", "posted"') == 1


def test_massal_lewat_core_yang_sama_n_baris_plus_satu_ringkasan():
    """Massal = core per dokumen (masing-masing SATU catat_dokumen) + SATU baris BULK_* (bulk.catat_audit) -- tanpa ganda."""
    hb = inspect.getsource(BHB)
    for core in ("hapus_draf_bayar_core", "hapus_draf_umv_core", "hapus_draf_nd_core"):
        assert core in hb, core
    bp = inspect.getsource(BPO)
    for core in ("kirim_po_core", "batal_po_core", "hapus_po_draf_core"):
        assert core in bp, core
    for core in (BP.hapus_draf_bayar_core, VD.hapus_draf_umv_core, VC.hapus_draf_nd_core, PO.kirim_po_core, PO.batal_po_core):
        assert inspect.getsource(core).count("_ad.catat_dokumen(") == 1, core.__name__
    # core hapus PO / tagihan TIDAK menulis catat_dokumen: trigger trg_log_deletion yang mencatat (tanpa ganda)
    assert "_ad.catat_dokumen(" not in inspect.getsource(PO.hapus_po_draf_core)
    assert "_ad.catat_dokumen(" not in inspect.getsource(BS.hapus_draf_tagihan_core)
    assert "catat_dokumen" not in hb and "catat_dokumen" not in bp  # router massal tak menulis baris per dokumen sendiri


# ---------------- (4) riwayat tanpa ganda ----------------
def test_riwayat_melewati_event_jejak_audit_saja_sebelum_digabung():
    src = inspect.getsource(SR._selesaikan)
    i = src.index('if r["eventType"] in _AD.TANPA_RIWAYAT')
    assert i < src.index("k.tambah(r[\"createdAt\"]") and "continue" in src[i:i + 200]


class _ConnRiwayat:
    """fetch audit_logs -> baris; kueri lain (nama aktor, zona) -> kosong/None."""
    def __init__(self, baris): self.baris = baris

    async def fetch(self, sql, *a):
        return self.baris if "FROM audit_logs" in sql else []

    async def fetchrow(self, sql, *a): return None

    async def fetchval(self, sql, *a): return None


def test_riwayat_perilaku_event_baru_tak_tampil_event_lama_tampil():
    did = uuid.uuid4()
    import datetime as _dt
    t = _dt.datetime(2026, 11, 1, 3, 0, tzinfo=_dt.timezone.utc)

    def r(ev): return {"id": str(uuid.uuid4()), "createdAt": t, "eventType": ev, "userId": None, "entity_type": "purchase_order",
                       "entity_id": did, "entity_number": "PO-1", "metadata": {"ringkas": ev}, "source": "api"}
    k = SR._Kumpul()
    ev, _ = _jalan(SR._selesaikan(_ConnRiwayat([r("PO_SENT"), r("PO_CANCELLED"), r("PO_UPDATED")]), "t1", k,
                                  {"purchase_order": [did]}, {"purchase_order": True}, 200))
    jenis = [e["jenis"] for e in ev]
    assert "PO_UPDATED" in jenis and "PO_SENT" not in jenis and "PO_CANCELLED" not in jenis


# ---------------- (5) hapus PO: aktor lewat GUC di core (di transaksi pemanggil) ----------------
def test_hapus_po_aktor_guc_sebelum_delete_di_transaksi_pemanggil():
    src = inspect.getsource(PO.hapus_po_draf_core)
    assert src.index("set_config('app.user_id', $1, true)") < src.index("DELETE FROM purchase_orders")
    rute = inspect.getsource(PO.delete_purchase_order)
    assert rute.index("conn.transaction()") < rute.index("hapus_po_draf_core(")
    assert "hapus_po_draf_core" in inspect.getsource(BPO)
