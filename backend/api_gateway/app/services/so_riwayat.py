"""Riwayat Pesanan Penjualan (SO) — SIAPA + KAPAN + APA untuk SO dan dokumen turunannya (26 Sep 2026).

Arahan pemilik (via MASTER): detail SO menampilkan log: siapa membuat SO + SETIAP kejadian
yang menyangkut SO itu (buat, ubah, konfirmasi, batal/tutup, uang muka, proforma, faktur dari
SO, Surat Jalan, pembayaran yang dialokasikan ke faktur SO), dengan aktor + waktu + ringkasan.

Diukur sebelum dibangun: audit_logs praktis KOSONG untuk SO/faktur (riwayat faktur di
sales_invoices.py membaca metadata->>'entity_type' — 1 baris di seluruh DB). Tetapi kolom
dokumen SUDAH menyimpan aktor+waktu (created_by/_at, posted_by/_at, voided_by/_at,
confirmed_by/_at). Maka riwayat = GABUNGAN:
  (1) turunan kolom dokumen — satu-satunya sumber untuk kejadian sebelum hari ini;
  (2) audit_logs — HANYA untuk kejadian yang tak punya kolom (ubah/batal/tutup SO, terbit/batal
      proforma, ...). Penulis baru mencatat lewat catat_riwayat() di transaksi YANG SAMA
      dengan perubahannya (Law 12: jejak audit tak terpisah dari kejadiannya).
Kejadian lama tanpa kolom aktor -> aktor None (FE: "—"); TIDAK ditebak.

Tenant: SETIAP kueri memuat tenant_id eksplisit (BYPASSRLS: set_config bukan pagar).
Izin: rute = R sales_order (middleware). Dokumen terkait disaring per izin BACA pemanggil
(boleh_baca, PolicyEngine yang sama, OWNER lolos, galat = tidak boleh); yang disaring
dilaporkan di `omitted` supaya FE jujur ("sebagian riwayat tak terlihat"), bukan diam.
"""
import json
from datetime import datetime
from typing import Awaitable, Callable, Optional

from .nama_pengguna import nama_untuk
from ..utils.tanggal_tenant import zona_tenant
from . import teks_galat as tg

# jenis dokumen -> modul izin BACA
MODUL = {
    "sales_order": "sales_order",
    "customer_deposit": "customer_deposit",
    "proforma": "proforma",
    "sales_invoice": "sales_invoice",
    "fulfillment": "sales_invoice",
    "receive_payment": "receive_payment",
}

# riwayat FAKTUR (30 Sep): modul MODUL + nota kredit. Terpisah supaya `omitted` riwayat SO tak berubah.
MODUL_FAKTUR = {**MODUL, "credit_note": "credit_note"}

# entity_type di audit_logs (lama memakai nama tabel, penulis baru juga) -> jenis dokumen
ENTITAS_AUDIT = {
    "quotes": "quote", "quote": "quote",
    "sales_orders": "sales_order", "sales_order": "sales_order",
    "proformas": "proforma", "proforma": "proforma",
    "sales_invoices": "sales_invoice", "sales_invoice": "sales_invoice",
    "customer_deposits": "customer_deposit", "customer_deposit": "customer_deposit",
    "invoice_fulfillments": "fulfillment",
    "receive_payments": "receive_payment",
    "credit_notes": "credit_note",
}

# Aktor kejadian yang ditulis fungsi DB (bukan pengguna): tampil "Sistem", beda dari null (= tak diketahui).
AKTOR_SISTEM = "__sistem__"

# Label manusia untuk metadata.fields kejadian "*_UPDATED" (3 Okt 2026, MASTER): dirender SAAT BACA supaya baris lama
# ikut membaik; ringkas tersimpan dibiarkan. Urutan tampil = urutan daftar ini; label ganda (diskon, uang muka, rekening,
# pelanggan, ...) dirapatkan; medan tak dikenal tampil dengan NAMA ASLINYA (tak disembunyikan).
LABEL_MEDAN = [
    ("customer_id", "pelanggan"), ("customer_name", "pelanggan"), ("customer_email", "pelanggan"),
    ("quote_number", "nomor"), ("order_number", "nomor"),
    ("quote_date", "tanggal"), ("order_date", "tanggal"), ("expiry_date", "berlaku sampai"),
    ("expected_ship_date", "tanggal kirim"), ("subject", "perihal"), ("reference", "referensi"),
    ("order_title", "judul"), ("items", "barang"),
    ("discount_type", "diskon"), ("discount_value", "diskon"), ("discount_amount", "diskon"),
    ("shipping_address", "alamat kirim"), ("shipping_method", "cara kirim"), ("shipping_amount", "ongkir"),
    ("shipping_tax_code_id", "ongkir"), ("dp_percent", "uang muka"), ("dp_amount", "uang muka"),
    ("payment_bank_name", "rekening"), ("payment_account_number", "rekening"), ("payment_account_holder", "rekening"),
    ("purpose", "tujuan"), ("percent_of_order", "nominal"), ("amount", "nominal"),
    ("proforma_date", "tanggal"), ("due_date", "jatuh tempo"),
    ("terms", "syarat pembayaran"), ("payment_terms", "syarat pembayaran"),
    ("opening_text", "teks pembuka"), ("closing_text", "teks penutup"),
    ("notes", "catatan"), ("internal_notes", "catatan internal"), ("footer", "catatan kaki"),
    ("deposit_date", "tanggal"), ("payment_method", "cara bayar"), ("account_id", "rekening"),
    ("bank_account_id", "rekening"),
]
_PERINGKAT = {m: i for i, (m, _) in enumerate(LABEL_MEDAN)}
_LABEL = dict(LABEL_MEDAN)


def label_medan(fields) -> str:
    """['notes', 'discount_value', 'dp_percent', 'items'] -> 'barang, diskon, uang muka, catatan'."""
    keluar = []
    for m in sorted(fields, key=lambda m: (_PERINGKAT.get(m, len(_PERINGKAT)), m)):
        lb = _LABEL.get(m, m)
        if lb not in keluar:
            keluar.append(lb)
    return ", ".join(keluar)


RINGKAS_AUDIT = {
    "SALES_ORDER_UPDATED": "Pesanan diubah",
    "SALES_ORDER_CANCELLED": "Pesanan dibatalkan",
    "SALES_ORDER_CLOSED": "Pesanan ditutup",
    "SALES_ORDER_FORCE_CLOSED": "Pesanan ditutup (sisa dibatalkan)",
    "SALES_ORDER_AUTO_COMPLETED": "Pesanan selesai otomatis",
    "SALES_ORDER_REOPENED": "Pesanan dibuka kembali",
    "SO_FASE_LOKASI_CHANGED": "Posisi lokasi pesanan diubah",
    "PROFORMA_ISSUED": "Proforma diterbitkan",
    "PROFORMA_CANCELLED": "Proforma dibatalkan",
    "PROFORMA_UPDATED": "Proforma diubah",
    "QUOTE_UPDATED": "Penawaran diubah",
    "SALES_INVOICE_LINKED_TO_ORDER": "Faktur ditautkan ke pesanan",
    "REVENUE_RECOGNIZED_NON_STOCK": "Pendapatan non-stok diakui",
    "SALES_INVOICE_VOIDED": "Faktur dibatalkan (void)",
    "FULFILLMENT_VOIDED": "Surat Jalan dibatalkan",
    "DOCUMENT_DELETED": "Dokumen dihapus",
    "DEPOSIT_VOIDED": "Uang muka dibatalkan",
    "DEPOSIT_APPLIED": "Uang muka diterapkan",
    "DEPOSIT_APPLICATION_REVERSED": "Penerapan uang muka dilepas",
    "DEPOSIT_REFUNDED": "Uang muka dikembalikan",
    "DEPOSIT_UPDATED": "Uang muka diubah",
}


EVENT_KODE_ORDER = ("ORDER_CODE_ISSUED", "ORDER_CODE_OVERRIDDEN", "ORDER_CODE_IMPORTED")


def teks_riwayat_kode(event: str, label: str, meta: dict, tersimpan: str) -> str:
    """Ringkas peristiwa kode order DIRENDER SAAT BACA dgn label tenant SEKARANG (U7, 5 Okt): baris lama ikut berganti
    ("Kode order …" -> "No. SPK …") karena teks tersimpan membeku di label saat kejadian. Sumber nilai = metadata old/new
    yang SUDAH tersimpan (tanpa 'new' -> teks tersimpan dipakai apa adanya, tak dikarang)."""
    baru = meta.get("new")
    if not baru:
        return tersimpan
    if event == "ORDER_CODE_ISSUED":
        return f"{label} {baru} terbit" + (" (manual)" if (tersimpan or "").rstrip().endswith("(manual)") else "")
    if event == "ORDER_CODE_OVERRIDDEN":
        return f"{label}: {meta.get('old') or '—'} → {baru}"
    return f"{label} {baru} diimpor"


async def catat_riwayat(conn, tenant_id: str, entity_type: str, entity_id, entity_number: Optional[str],
                        event: str, user_id, ringkas: str, meta: Optional[dict] = None,
                        source: str = "api") -> None:
    """Satu baris audit_logs untuk kejadian TANPA kolom dokumen. WAJIB dipanggil di transaksi
    yang sama dengan perubahannya: gagal mencatat = perubahan batal (bukan jejak yang hilang diam)."""
    isi = dict(meta or {})
    isi["ringkas"] = ringkas
    if user_id is not None:
        isi["user_id"] = str(user_id)
    await conn.execute(
        """INSERT INTO audit_logs (id, "userId", "eventType", entity_type, entity_id, entity_number,
                                   tenant_id, source, metadata, success, "createdAt")
           VALUES (gen_random_uuid()::text, $1, $2, $3, $4::uuid, $5, $6, $7, $8::jsonb, true, NOW())""",
        str(user_id) if user_id is not None else None, event, entity_type, str(entity_id),
        entity_number, tenant_id, source, json.dumps(isi, default=str),
    )


def _rp(x) -> str:
    """Rupiah baku (services/teks_galat.rp: Decimal, sen hanya bila != 0); masukan tak sah -> 'Rp—'."""
    try:
        return tg.rp(x)
    except ValueError:
        return "Rp—"
class _Kumpul:
    def __init__(self):
        self.ev = []

    def tambah(self, at, jenis, ringkas, aktor, dok_tipe=None, dok_id=None, dok_nomor=None, sumber="dokumen",
               kunci=None):
        """kunci = id BARIS anak (penerapan/refund uang muka) untuk padanan audit per baris; tak ikut keluaran."""
        if at is None:
            return
        self.ev.append({
            "at": at, "jenis": jenis, "ringkas": ringkas, "aktor_id": str(aktor) if aktor else None,
            "dokumen": ({"tipe": dok_tipe, "id": str(dok_id), "nomor": dok_nomor} if dok_tipe else None),
            "sumber": sumber, "kunci": str(kunci) if kunci else None,
        })


async def _bagian_faktur(conn, tenant_id: str, k: "_Kumpul", fakturs, lihat: dict, entitas_audit: dict,
                         akhiran_dibuat: str) -> list:
    """Kejadian faktur + Surat Jalan + pembayaran teralokasi — BERSAMA riwayat SO dan riwayat faktur (satu
    pemetaan, bukan salinan). `fakturs` = baris sales_invoices (id, invoice_number, total_amount, created_*,
    posted_*, voided_at, voided_reason). Kembali: id faktur."""
    id_faktur = [f["id"] for f in fakturs]
    if lihat["sales_invoice"]:
        entitas_audit["sales_invoice"] = id_faktur
        for f in fakturs:
            n = f["invoice_number"]
            k.tambah(f["created_at"], "FAKTUR_DIBUAT", f"Faktur {n} {_rp(f['total_amount'])} dibuat{akhiran_dibuat}", f["created_by"], "sales_invoice", f["id"], n)
            k.tambah(f["posted_at"], "FAKTUR_DITERBITKAN", f"Faktur {n} diterbitkan (diposting)", f["posted_by"], "sales_invoice", f["id"], n)
            alasan = f": {f['voided_reason']}" if f["voided_reason"] else ""
            k.tambah(f["voided_at"], "FAKTUR_DIBATALKAN", f"Faktur {n} dibatalkan (void){alasan}", None, "sales_invoice", f["id"], n)
        if id_faktur:
            sjs = await conn.fetch(
                """SELECT f.id, f.fulfillment_number, f.created_at, f.created_by, f.posted_at, f.posted_by,
                          f.voided_at, f.voided_reason, si.invoice_number
                   FROM invoice_fulfillments f
                   JOIN sales_invoices si ON si.id = f.invoice_id AND si.tenant_id = $1
                   WHERE f.tenant_id = $1 AND f.invoice_id = ANY($2::uuid[])""",
                tenant_id, id_faktur,
            )
            entitas_audit["fulfillment"] = [s["id"] for s in sjs]
            for s in sjs:
                n = s["fulfillment_number"]
                k.tambah(s["created_at"], "SURAT_JALAN_DIBUAT", f"Surat Jalan {n} dibuat (faktur {s['invoice_number']})", s["created_by"], "fulfillment", s["id"], n)
                alasan = f": {s['voided_reason']}" if s["voided_reason"] else ""
                k.tambah(s["voided_at"], "SURAT_JALAN_DIBATALKAN", f"Surat Jalan {n} dibatalkan{alasan}", None, "fulfillment", s["id"], n)

    if lihat["receive_payment"] and id_faktur:
        pays = await conn.fetch(
            """SELECT rp.id, rp.payment_number, a.invoice_number, a.amount_applied, rp.posted_at, rp.posted_by,
                      rp.voided_at, rp.voided_by, rp.void_reason, a.reversed_at, a.reversed_by, a.unapply_reason
               FROM receive_payment_allocations a
               JOIN receive_payments rp ON rp.id = a.payment_id AND rp.tenant_id = $1
               WHERE a.tenant_id = $1 AND a.invoice_id = ANY($2::uuid[])""",
            tenant_id, id_faktur,
        )
        for p in pays:
            n = p["payment_number"]
            k.tambah(p["posted_at"], "PEMBAYARAN_DITERIMA",
                     f"Pembayaran {n} {_rp(p['amount_applied'])} dialokasikan ke faktur {p['invoice_number']}",
                     p["posted_by"], "receive_payment", p["id"], n)
            alasan = f": {p['void_reason']}" if p["void_reason"] else ""
            k.tambah(p["voided_at"], "PEMBAYARAN_DIBATALKAN", f"Pembayaran {n} dibatalkan{alasan}", p["voided_by"], "receive_payment", p["id"], n)
            alasan = f": {p['unapply_reason']}" if p["unapply_reason"] else ""
            k.tambah(p["reversed_at"], "ALOKASI_DILEPAS", f"Alokasi {n} ke faktur {p['invoice_number']} dilepas{alasan}",
                     p["reversed_by"], "receive_payment", p["id"], n)

    return id_faktur


async def _selesaikan(conn, tenant_id: str, k: "_Kumpul", entitas_audit: dict, lihat: dict, limit: int):
    """Ekor BERSAMA: gabung audit_logs dokumen yang boleh dilihat, buang kolom tanpa aktor yang punya
    padanan audit, nama aktor (satu kueri), zona tenant, terbaru dulu. Kembali: (events, total)."""
    # ---- audit_logs untuk SO + dokumen terkait yang BOLEH dilihat ----
    tipe_ent = {j: [e for e, jj in ENTITAS_AUDIT.items() if jj == j] for j in set(ENTITAS_AUDIT.values())}
    kueri_ent, kueri_id = [], []
    for jenis, ids in entitas_audit.items():
        if not lihat.get(jenis) or not ids:
            continue
        for e in tipe_ent.get(jenis, []):
            for i in ids:
                kueri_ent.append(e)
                kueri_id.append(str(i))
    audit_ada, audit_baris = set(), set()
    label_kode = None  # label kode order tenant: dibaca SEKALI, hanya bila ada peristiwa kode order (U7)
    if kueri_ent:
        for r in await conn.fetch(
            """SELECT a.id, a."createdAt", a."eventType", a."userId", a.entity_type, a.entity_id,
                      a.entity_number, a.metadata, a.source
               FROM audit_logs a
               JOIN unnest($2::text[], $3::uuid[]) AS x(et, eid) ON a.entity_type = x.et AND a.entity_id = x.eid
               WHERE a.tenant_id = $1""",
            tenant_id, kueri_ent, kueri_id,
        ):
            meta = r["metadata"] or {}
            if isinstance(meta, str):
                meta = json.loads(meta)
            jenis = ENTITAS_AUDIT.get(r["entity_type"])
            ringkas = meta.get("ringkas") or RINGKAS_AUDIT.get(r["eventType"], r["eventType"])
            _medan = meta.get("fields")
            if (r["eventType"].endswith("_UPDATED") and r["eventType"] in RINGKAS_AUDIT and isinstance(_medan, list)
                    and _medan):
                ringkas = f"{RINGKAS_AUDIT[r['eventType']]}: {label_medan(_medan)}"
            if r["eventType"] in EVENT_KODE_ORDER or r["eventType"] == "ORDER_TITLE_CHANGED":
                if label_kode is None:
                    from .kode_order import muat_setelan as _muat_setelan_kode
                    _st_kode = await _muat_setelan_kode(conn, tenant_id)
                    label_kode, label_judul = _st_kode["label"], _st_kode["title_label"]
                if r["eventType"] == "ORDER_TITLE_CHANGED":
                    # V390: label judul tenant SEKARANG (pola U7 kode): "Judul order: a → b" -> "Judul SPK: a → b"
                    if "new" in meta:
                        ringkas = f"{label_judul}: {meta.get('old') or '—'} → {meta.get('new') or '—'}"
                else:
                    ringkas = teks_riwayat_kode(r["eventType"], label_kode, meta, ringkas)
            aktor = r["userId"] or meta.get("user_id")
            if not aktor and str(r["source"] or "").startswith("db:"):
                aktor = AKTOR_SISTEM  # kejadian ditulis fungsi DB (V315 selesai otomatis / dibuka kembali)
            k.tambah(r["createdAt"], r["eventType"], ringkas, aktor, jenis, r["entity_id"], r["entity_number"], sumber="audit")
            audit_ada.add((r["eventType"], str(r["entity_id"])))
            if meta.get("kunci"):
                audit_baris.add((r["eventType"], str(r["entity_id"]), str(meta["kunci"])))

    # kolom tanpa aktor yang punya padanan audit (beraktor) -> pakai yang audit saja
    PADANAN = {"PROFORMA_DITERBITKAN": "PROFORMA_ISSUED", "PROFORMA_DIBATALKAN": "PROFORMA_CANCELLED",
               "PENAWARAN_DIKIRIM": "QUOTE_SENT", "PENAWARAN_DISETUJUI": "QUOTE_ACCEPTED",
               "PENAWARAN_DITOLAK": "QUOTE_DECLINED", "PENAWARAN_DIBATALKAN": "QUOTE_VOIDED",
               "PENAWARAN_JADI_PESANAN": "QUOTE_CONVERTED",
               "FAKTUR_DIBATALKAN": "SALES_INVOICE_VOIDED", "SURAT_JALAN_DIBATALKAN": "FULFILLMENT_VOIDED",
               "UANG_MUKA_DIBATALKAN": "DEPOSIT_VOIDED"}
    # Padanan PER BARIS (kejadian yang bisa berkali-kali per dokumen): kolom hanya dibuang bila audit dengan KUNCI
    # baris yang sama ada -- padanan per dokumen akan membuang SEMUA penerapan lama karena satu audit baru.
    PADANAN_BARIS = {"UANG_MUKA_DITERAPKAN": "DEPOSIT_APPLIED", "UANG_MUKA_DILEPAS": "DEPOSIT_APPLICATION_REVERSED",
                     "UANG_MUKA_DIKEMBALIKAN": "DEPOSIT_REFUNDED"}
    ev = [e for e in k.ev if not (e["jenis"] in PADANAN and e["dokumen"]
                                  and (PADANAN[e["jenis"]], e["dokumen"]["id"]) in audit_ada)
          and not (e["jenis"] in PADANAN_BARIS and e["dokumen"] and e.get("kunci")
                   and (PADANAN_BARIS[e["jenis"]], e["dokumen"]["id"], e["kunci"]) in audit_baris)]

    # nama aktor (satu kueri)
    ids = sorted({e["aktor_id"] for e in ev if e["aktor_id"] and e["aktor_id"] != AKTOR_SISTEM})
    nama = {}
    if ids:
        nama.update(await nama_untuk(conn, ids))  # 7 Okt 2026: rantai SATU (profil -> fullname -> name -> surel)

    zona = await zona_tenant(conn, tenant_id)
    # terbaru dulu; waktu SAMA (buat+posting satu transaksi) -> yang dicatat belakangan di atas
    ev = [e for _, e in sorted(enumerate(ev), key=lambda x: (x[1]["at"], x[0]), reverse=True)]
    keluar = []
    for e in ev[:limit]:
        at: datetime = e["at"]
        keluar.append({
            "at": at.astimezone(zona).isoformat(),
            "jenis": e["jenis"],
            "ringkas": e["ringkas"],
            "aktor": ({"id": None, "nama": "Sistem"} if e["aktor_id"] == AKTOR_SISTEM
                      else {"id": e["aktor_id"], "nama": nama.get(e["aktor_id"])} if e["aktor_id"] else None),
            "dokumen": e["dokumen"],
            "sumber": e["sumber"],
        })
    return keluar, len(ev)


async def riwayat_so(conn, tenant_id: str, so_id, boleh: Callable[[str], Awaitable[bool]],
                     limit: int = 200) -> Optional[dict]:
    """None = SO tak ada di tenant ini (pemanggil -> 404)."""
    so = await conn.fetchrow(
        """SELECT id, order_number, created_at, created_by, confirmed_at, confirmed_by
           FROM sales_orders WHERE id = $1 AND tenant_id = $2""",
        so_id, tenant_id,
    )
    if not so:
        return None

    izin = {}
    for jenis, modul in MODUL.items():
        if modul not in izin:
            izin[modul] = bool(await boleh(modul))
    lihat = {j: izin[m] for j, m in MODUL.items()}
    omitted = sorted({m for j, m in MODUL.items() if not izin[m]})

    k = _Kumpul()
    nomor_so = so["order_number"]
    k.tambah(so["created_at"], "SO_DIBUAT", f"Pesanan {nomor_so} dibuat", so["created_by"], "sales_order", so["id"], nomor_so)
    k.tambah(so["confirmed_at"], "SO_DIKONFIRMASI", f"Pesanan {nomor_so} dikonfirmasi", so["confirmed_by"], "sales_order", so["id"], nomor_so)

    entitas_audit = {"sales_order": [so["id"]]}

    # ---- uang muka + aplikasinya ----
    if lihat["customer_deposit"]:
        dps = await conn.fetch(
            """SELECT id, deposit_number, amount, status, created_at, created_by, posted_at, posted_by,
                      voided_at, voided_by, voided_reason
               FROM customer_deposits WHERE tenant_id = $1 AND sales_order_id = $2""",
            tenant_id, so_id,
        )
        entitas_audit["customer_deposit"] = [d["id"] for d in dps]
        for d in dps:
            n = d["deposit_number"]
            k.tambah(d["created_at"], "UANG_MUKA_DIBUAT", f"Uang muka {n} {_rp(d['amount'])} dibuat", d["created_by"], "customer_deposit", d["id"], n)
            k.tambah(d["posted_at"], "UANG_MUKA_DITERIMA", f"Uang muka {n} {_rp(d['amount'])} diterima (diposting)", d["posted_by"], "customer_deposit", d["id"], n)
            alasan = f": {d['voided_reason']}" if d["voided_reason"] else ""
            k.tambah(d["voided_at"], "UANG_MUKA_DIBATALKAN", f"Uang muka {n} dibatalkan{alasan}", d["voided_by"], "customer_deposit", d["id"], n)
        if dps:
            for a in await conn.fetch(
                """SELECT a.id, a.deposit_id, d.deposit_number, a.invoice_number, a.amount_applied,
                          a.created_at, a.created_by, a.reversed_at
                   FROM customer_deposit_applications a
                   JOIN customer_deposits d ON d.id = a.deposit_id AND d.tenant_id = $1
                   WHERE a.tenant_id = $1 AND a.deposit_id = ANY($2::uuid[])""",
                tenant_id, [d["id"] for d in dps],
            ):
                n = a["deposit_number"]
                k.tambah(a["created_at"], "UANG_MUKA_DITERAPKAN",
                         f"Uang muka {n} {_rp(a['amount_applied'])} diterapkan ke faktur {a['invoice_number']}",
                         a["created_by"], "customer_deposit", a["deposit_id"], n, kunci=a["id"])
                k.tambah(a["reversed_at"], "UANG_MUKA_DILEPAS",
                         f"Penerapan uang muka {n} ke faktur {a['invoice_number']} dilepas",
                         None, "customer_deposit", a["deposit_id"], n, kunci=a["id"])

    # ---- proforma ----
    if lihat["proforma"]:
        pfs = await conn.fetch(
            """SELECT id, proforma_number, amount, created_at, created_by, issued_at, cancelled_at, cancelled_reason
               FROM proformas WHERE tenant_id = $1 AND sales_order_id = $2""",
            tenant_id, so_id,
        )
        entitas_audit["proforma"] = [p["id"] for p in pfs]
        for p in pfs:
            n = p["proforma_number"] or "Proforma"
            k.tambah(p["created_at"], "PROFORMA_DIBUAT", f"Proforma {n} {_rp(p['amount'])} dibuat", p["created_by"], "proforma", p["id"], p["proforma_number"])
            # aktor terbit/batal ada di audit_logs (penulis baru); baris kolom tanpa aktor
            # hanya dipakai bila audit tak memuatnya (lihat penggabungan di bawah)
            k.tambah(p["issued_at"], "PROFORMA_DITERBITKAN", f"Proforma {n} diterbitkan", None, "proforma", p["id"], p["proforma_number"])
            alasan = f": {p['cancelled_reason']}" if p["cancelled_reason"] else ""
            k.tambah(p["cancelled_at"], "PROFORMA_DIBATALKAN", f"Proforma {n} dibatalkan{alasan}", None, "proforma", p["id"], p["proforma_number"])

    # ---- faktur dari SO + Surat Jalan + pembayaran ----
    fakturs = await conn.fetch(
        """SELECT id, invoice_number, total_amount, created_at, created_by, posted_at, posted_by,
                  voided_at, voided_reason
           FROM sales_invoices WHERE tenant_id = $1 AND sales_order_id = $2""",
        tenant_id, so_id,
    )
    await _bagian_faktur(conn, tenant_id, k, fakturs, lihat, entitas_audit, " dari pesanan")

    keluar, total = await _selesaikan(conn, tenant_id, k, entitas_audit, lihat, limit)
    return {
        "order_id": str(so["id"]),
        "order_number": nomor_so,
        "events": keluar,
        "total": total,
        "omitted": omitted,
    }


async def riwayat_faktur(conn, tenant_id: str, invoice_id, boleh: Callable[[str], Awaitable[bool]],
                         limit: int = 200) -> Optional[dict]:
    """Riwayat FAKTUR PENJUALAN, bentuk SAMA dengan riwayat_so (30 Sep, halaman CW detail faktur).
    Pemetaan faktur/Surat Jalan/pembayaran = _bagian_faktur yang SAMA dengan riwayat SO; ekor = _selesaikan.
    Tambahan khas faktur: uang muka yang DITERAPKAN ke faktur ini, nota kredit atas faktur ini, SO asal.
    None = faktur tak ada di tenant ini (pemanggil -> 404)."""
    f = await conn.fetchrow(
        """SELECT si.id, si.invoice_number, si.total_amount, si.created_at, si.created_by, si.posted_at, si.posted_by,
                  si.voided_at, si.voided_reason, si.sales_order_id, so.order_number
           FROM sales_invoices si
           LEFT JOIN sales_orders so ON so.id = si.sales_order_id AND so.tenant_id = si.tenant_id
           WHERE si.id = $1 AND si.tenant_id = $2""",
        invoice_id, tenant_id,
    )
    if not f:
        return None

    izin = {}
    for jenis, modul in MODUL_FAKTUR.items():
        if modul not in izin:
            izin[modul] = bool(await boleh(modul))
    lihat = {j: izin[m] for j, m in MODUL_FAKTUR.items()}
    omitted = sorted({m for j, m in MODUL_FAKTUR.items() if not izin[m]})

    k = _Kumpul()
    entitas_audit = {}
    akhiran = f" dari pesanan {f['order_number']}" if f["order_number"] and lihat["sales_order"] else ""
    await _bagian_faktur(conn, tenant_id, k, [f], lihat, entitas_audit, akhiran)

    # ---- uang muka yang diterapkan ke faktur INI ----
    if lihat["customer_deposit"]:
        for a in await conn.fetch(
            """SELECT a.id, a.deposit_id, d.deposit_number, a.amount_applied, a.created_at, a.created_by, a.reversed_at
               FROM customer_deposit_applications a
               JOIN customer_deposits d ON d.id = a.deposit_id AND d.tenant_id = $1
               WHERE a.tenant_id = $1 AND a.invoice_id = $2""",
            tenant_id, f["id"],
        ):
            n = a["deposit_number"]
            k.tambah(a["created_at"], "UANG_MUKA_DITERAPKAN", f"Uang muka {n} {_rp(a['amount_applied'])} diterapkan ke faktur ini",
                     a["created_by"], "customer_deposit", a["deposit_id"], n, kunci=a["id"])
            k.tambah(a["reversed_at"], "UANG_MUKA_DILEPAS", f"Penerapan uang muka {n} dilepas", None,
                     "customer_deposit", a["deposit_id"], n, kunci=a["id"])

    # ---- nota kredit atas faktur INI ----
    if lihat["credit_note"]:
        cns = await conn.fetch(
            """SELECT id, credit_note_number, total_amount, created_at, created_by, posted_at, posted_by,
                      voided_at, voided_by, voided_reason
               FROM credit_notes WHERE tenant_id = $1 AND original_invoice_id = $2""",
            tenant_id, f["id"],
        )
        entitas_audit["credit_note"] = [c["id"] for c in cns]
        for c in cns:
            n = c["credit_note_number"]
            k.tambah(c["created_at"], "NOTA_KREDIT_DIBUAT", f"Nota kredit {n} {_rp(c['total_amount'])} dibuat", c["created_by"], "credit_note", c["id"], n)
            k.tambah(c["posted_at"], "NOTA_KREDIT_DITERBITKAN", f"Nota kredit {n} diterbitkan", c["posted_by"], "credit_note", c["id"], n)
            alasan = f": {c['voided_reason']}" if c["voided_reason"] else ""
            k.tambah(c["voided_at"], "NOTA_KREDIT_DIBATALKAN", f"Nota kredit {n} dibatalkan{alasan}", c["voided_by"], "credit_note", c["id"], n)

    keluar, total = await _selesaikan(conn, tenant_id, k, entitas_audit, lihat, limit)
    return {
        "invoice_id": str(f["id"]),
        "invoice_number": f["invoice_number"],
        "sales_order": ({"id": str(f["sales_order_id"]), "order_number": f["order_number"]}
                        if f["sales_order_id"] and lihat["sales_order"] else None),
        "events": keluar,
        "total": total,
        "omitted": omitted,
    }


MODUL_PENAWARAN = {"quote": "quote", "sales_order": "sales_order"}


MODUL_UANG_MUKA = {"customer_deposit": "customer_deposit", "sales_order": "sales_order", "proforma": "proforma",
                   "sales_invoice": "sales_invoice"}


async def riwayat_uang_muka(conn, tenant_id: str, deposit_id, boleh: Callable[[str], Awaitable[bool]],
                            limit: int = 200) -> Optional[dict]:
    """Riwayat UANG MUKA (U3a CW, 4 Okt 2026), bentuk SAMA dengan riwayat_so. Kolom siklus (dibuat, diterima/diposting,
    dibatalkan) + penerapan ke faktur (diterapkan/dilepas) + pengembalian dana + audit_logs beraktor.
    None = tak ada di tenant ini."""
    d = await conn.fetchrow(
        """SELECT d.id, d.deposit_number, d.amount, d.created_at, d.created_by, d.posted_at, d.posted_by, d.voided_at,
                  d.voided_by, d.voided_reason, d.sales_order_id, d.proforma_id, so.order_number, p.proforma_number
           FROM customer_deposits d
           LEFT JOIN sales_orders so ON so.id = d.sales_order_id AND so.tenant_id = d.tenant_id
           LEFT JOIN proformas p ON p.id = d.proforma_id AND p.tenant_id = d.tenant_id
           WHERE d.id = $1 AND d.tenant_id = $2""", deposit_id, tenant_id)
    if not d:
        return None
    izin = {}
    for jenis, modul in MODUL_UANG_MUKA.items():
        if modul not in izin:
            izin[modul] = bool(await boleh(modul))
    lihat = {j: izin[m] for j, m in MODUL_UANG_MUKA.items()}
    omitted = sorted({m for m in izin if not izin[m]})
    k = _Kumpul()
    n = d["deposit_number"]
    k.tambah(d["created_at"], "UANG_MUKA_DIBUAT", f"Uang muka {n} {_rp(d['amount'])} dibuat", d["created_by"],
             "customer_deposit", d["id"], n)
    k.tambah(d["posted_at"], "UANG_MUKA_DITERIMA", f"Uang muka {n} {_rp(d['amount'])} diterima (diposting)",
             d["posted_by"], "customer_deposit", d["id"], n)
    al = f": {d['voided_reason']}" if d["voided_reason"] else ""
    k.tambah(d["voided_at"], "UANG_MUKA_DIBATALKAN", f"Uang muka {n} dibatalkan{al}", d["voided_by"],
             "customer_deposit", d["id"], n)
    for a in await conn.fetch(
            """SELECT id, invoice_number, amount_applied, created_at, created_by, reversed_at FROM customer_deposit_applications
               WHERE tenant_id = $1 AND deposit_id = $2""", tenant_id, d["id"]):
        fk = f" ke faktur {a['invoice_number']}" if lihat["sales_invoice"] else ""
        k.tambah(a["created_at"], "UANG_MUKA_DITERAPKAN", f"Uang muka {n} {_rp(a['amount_applied'])} diterapkan{fk}",
                 a["created_by"], "customer_deposit", d["id"], n, kunci=a["id"])
        k.tambah(a["reversed_at"], "UANG_MUKA_DILEPAS", f"Penerapan uang muka {n}{fk} dilepas", None,
                 "customer_deposit", d["id"], n, kunci=a["id"])
    for r in await conn.fetch(
            """SELECT id, amount, refund_date, created_at, created_by FROM customer_deposit_refunds
               WHERE tenant_id = $1 AND deposit_id = $2""", tenant_id, d["id"]):
        k.tambah(r["created_at"], "UANG_MUKA_DIKEMBALIKAN", f"Uang muka {n} {_rp(r['amount'])} dikembalikan ke pelanggan",
                 r["created_by"], "customer_deposit", d["id"], n, kunci=r["id"])
    keluar, total = await _selesaikan(conn, tenant_id, k, {"customer_deposit": [d["id"]]}, lihat, limit)
    return {
        "deposit_id": str(d["id"]), "deposit_number": n,
        "sales_order": ({"id": str(d["sales_order_id"]), "order_number": d["order_number"]}
                        if lihat["sales_order"] and d["sales_order_id"] else None),
        "proforma": ({"id": str(d["proforma_id"]), "proforma_number": d["proforma_number"]}
                     if lihat["proforma"] and d["proforma_id"] else None),
        "events": keluar, "total": total, "omitted": omitted,
    }


MODUL_PROFORMA = {"proforma": "proforma", "customer_deposit": "customer_deposit", "sales_order": "sales_order"}


async def riwayat_proforma(conn, tenant_id: str, proforma_id, boleh: Callable[[str], Awaitable[bool]],
                           limit: int = 200) -> Optional[dict]:
    """Riwayat PROFORMA (U2 CW, 4 Okt 2026), bentuk SAMA dengan riwayat_so / riwayat_penawaran. Kolom siklus (dibuat,
    diterbitkan, dibatalkan) + uang muka yang MENAUT proforma ini (proforma_id eksplisit; pencocokan nominal = atribusi
    tampilan, tak ditampilkan sebagai kejadian) + audit_logs beraktor (PROFORMA_*). None = tak ada di tenant ini."""
    p = await conn.fetchrow(
        """SELECT p.id, p.proforma_number, p.amount, p.created_at, p.created_by, p.issued_at, p.cancelled_at,
                  p.cancelled_reason, p.sales_order_id, so.order_number
           FROM proformas p
           LEFT JOIN sales_orders so ON so.id = p.sales_order_id AND so.tenant_id = p.tenant_id
           WHERE p.id = $1 AND p.tenant_id = $2""", proforma_id, tenant_id)
    if not p:
        return None
    izin = {}
    for jenis, modul in MODUL_PROFORMA.items():
        if modul not in izin:
            izin[modul] = bool(await boleh(modul))
    lihat = {j: izin[m] for j, m in MODUL_PROFORMA.items()}
    omitted = sorted({m for m in izin if not izin[m]})
    k = _Kumpul()
    n = p["proforma_number"] or "Proforma"
    k.tambah(p["created_at"], "PROFORMA_DIBUAT", f"Proforma {n} {_rp(p['amount'])} dibuat", p["created_by"],
             "proforma", p["id"], p["proforma_number"])
    k.tambah(p["issued_at"], "PROFORMA_DITERBITKAN", f"Proforma {n} diterbitkan", None, "proforma", p["id"],
             p["proforma_number"])
    alasan = f": {p['cancelled_reason']}" if p["cancelled_reason"] else ""
    k.tambah(p["cancelled_at"], "PROFORMA_DIBATALKAN", f"Proforma {n} dibatalkan{alasan}", None, "proforma", p["id"],
             p["proforma_number"])
    entitas_audit = {"proforma": [p["id"]]}
    if lihat["customer_deposit"]:
        dps = await conn.fetch(
            """SELECT id, deposit_number, amount, created_at, created_by, posted_at, posted_by, voided_at, voided_by,
                      voided_reason
               FROM customer_deposits WHERE tenant_id = $1 AND proforma_id = $2""", tenant_id, p["id"])
        entitas_audit["customer_deposit"] = [d["id"] for d in dps]
        for d in dps:
            dn = d["deposit_number"]
            k.tambah(d["created_at"], "UANG_MUKA_DIBUAT", f"Uang muka {dn} {_rp(d['amount'])} dibuat untuk proforma {n}",
                     d["created_by"], "customer_deposit", d["id"], dn)
            k.tambah(d["posted_at"], "UANG_MUKA_DITERIMA", f"Uang muka {dn} {_rp(d['amount'])} diterima (diposting)",
                     d["posted_by"], "customer_deposit", d["id"], dn)
            al = f": {d['voided_reason']}" if d["voided_reason"] else ""
            k.tambah(d["voided_at"], "UANG_MUKA_DIBATALKAN", f"Uang muka {dn} dibatalkan{al}", d["voided_by"],
                     "customer_deposit", d["id"], dn)
    keluar, total = await _selesaikan(conn, tenant_id, k, entitas_audit, lihat, limit)
    return {
        "proforma_id": str(p["id"]),
        "proforma_number": p["proforma_number"],
        "sales_order": ({"id": str(p["sales_order_id"]), "order_number": p["order_number"]}
                        if lihat["sales_order"] and p["sales_order_id"] else None),
        "events": keluar,
        "total": total,
        "omitted": omitted,
    }


async def riwayat_penawaran(conn, tenant_id: str, quote_id, boleh: Callable[[str], Awaitable[bool]],
                            limit: int = 200) -> Optional[dict]:
    """Riwayat PENAWARAN, bentuk SAMA dengan riwayat_so / riwayat_faktur (2 Okt 2026, Penawaran CW).
    Kolom siklus (dibuat/terkirim/dilihat/disetujui/ditolak/jadi pesanan) + audit_logs beraktor (QUOTE_* sejak
    2 Okt). Kolom tanpa aktor yang punya padanan audit -> pakai yang audit (PADANAN di _selesaikan).
    Pesanan hasil konversi tampil bila izin baca sales_order. None = penawaran tak ada di tenant ini (-> 404)."""
    q = await conn.fetchrow(
        """SELECT q.id, q.quote_number, q.total_amount, q.status, q.created_at, q.created_by, q.sent_at, q.viewed_at,
                  q.accepted_at, q.declined_at, q.declined_reason, q.converted_at, q.converted_to_type,
                  q.converted_to_id, q.updated_at, so.order_number
           FROM quotes q
           LEFT JOIN sales_orders so ON q.converted_to_type = 'sales_order' AND so.id = q.converted_to_id
                                    AND so.tenant_id = q.tenant_id
           WHERE q.id = $1 AND q.tenant_id = $2""",
        quote_id, tenant_id,
    )
    if not q:
        return None
    izin = {}
    for jenis, modul in MODUL_PENAWARAN.items():
        if modul not in izin:
            izin[modul] = bool(await boleh(modul))
    lihat = {j: izin[m] for j, m in MODUL_PENAWARAN.items()}
    omitted = sorted({m for m in izin if not izin[m]})

    k = _Kumpul()
    n = q["quote_number"]
    k.tambah(q["created_at"], "PENAWARAN_DIBUAT", f"Penawaran {n} {_rp(q['total_amount'])} dibuat", q["created_by"],
             "quote", q["id"], n)
    k.tambah(q["sent_at"], "PENAWARAN_DIKIRIM", f"Penawaran {n} ditandai terkirim", None, "quote", q["id"], n)
    k.tambah(q["viewed_at"], "PENAWARAN_DILIHAT", f"Penawaran {n} dibuka pelanggan", None, "quote", q["id"], n)
    k.tambah(q["accepted_at"], "PENAWARAN_DISETUJUI", f"Penawaran {n} disetujui pelanggan", None, "quote", q["id"], n)
    alasan = f": {q['declined_reason']}" if q["declined_reason"] else ""
    k.tambah(q["declined_at"], "PENAWARAN_DITOLAK", f"Penawaran {n} ditolak{alasan}", None, "quote", q["id"], n)
    if q["status"] == "void":
        # tak ada kolom waktu batal: updated_at (penawaran batal tak bisa disunting lagi) -- audit menimpa bila ada
        k.tambah(q["updated_at"], "PENAWARAN_DIBATALKAN", f"Penawaran {n} dibatalkan", None, "quote", q["id"], n)
    if q["converted_at"] and q["converted_to_type"] == "sales_order":
        # dokumen = penawaran (supaya padanan audit QUOTE_CONVERTED menimpanya); tautan SO ada di "sales_order" atas
        so_teks = f" {q['order_number']}" if lihat["sales_order"] and q["order_number"] else ""
        k.tambah(q["converted_at"], "PENAWARAN_JADI_PESANAN", f"Penawaran {n} dijadikan pesanan{so_teks}", None,
                 "quote", q["id"], n)
    entitas_audit = {"quote": [q["id"]]}
    keluar, total = await _selesaikan(conn, tenant_id, k, entitas_audit, lihat, limit)
    return {
        "quote_id": str(q["id"]),
        "quote_number": n,
        "sales_order": ({"id": str(q["converted_to_id"]), "order_number": q["order_number"]}
                        if q["converted_to_type"] == "sales_order" and q["converted_to_id"] and lihat["sales_order"]
                        else None),
        "events": keluar,
        "total": total,
        "omitted": omitted,
    }


MODUL_PEMBAYARAN = {"receive_payment": "receive_payment", "sales_invoice": "sales_invoice",
                    "customer_deposit": "customer_deposit"}


async def riwayat_pembayaran(conn, tenant_id: str, payment_id, boleh: Callable[[str], Awaitable[bool]],
                             limit: int = 200) -> Optional[dict]:
    """Riwayat PEMBAYARAN (penerimaan) — U3b, 4 Okt 2026, bentuk SAMA dengan riwayat_so / riwayat_proforma.
    Turunan kolom (dibuat, diposting, void: aktor+waktu sudah ada di kolom) + alokasi yang dilepas + uang muka kelebihan
    bayar + audit_logs beraktor. None = tak ada di tenant ini."""
    from ..utils.metode_pembayaran import label_metode
    p = await conn.fetchrow(
        """SELECT id, payment_number, customer_name, total_amount, payment_method, bank_account_name, status,
                  created_at, created_by, posted_at, posted_by, voided_at, voided_by, void_reason, created_deposit_id
           FROM receive_payments WHERE id = $1 AND tenant_id = $2""", payment_id, tenant_id)
    if not p:
        return None
    izin = {}
    for jenis, modul in MODUL_PEMBAYARAN.items():
        if modul not in izin:
            izin[modul] = bool(await boleh(modul))
    lihat = {j: izin[m] for j, m in MODUL_PEMBAYARAN.items()}
    omitted = sorted({m for m in izin if not izin[m]})
    k = _Kumpul()
    n = p["payment_number"] or "Pembayaran"
    nomor = p["payment_number"]
    k.tambah(p["created_at"], "PEMBAYARAN_DIBUAT", f"Pembayaran {n} {_rp(p['total_amount'])} dibuat", p["created_by"],
             "receive_payment", p["id"], nomor)
    alok = await conn.fetch(
        """SELECT invoice_number, amount_applied, reversed_at, reversed_by, unapply_reason
           FROM receive_payment_allocations WHERE payment_id = $1 AND tenant_id = $2 ORDER BY created_at""",
        p["id"], tenant_id)
    via = label_metode(p["payment_method"]) + (f" ({p['bank_account_name']})" if p["bank_account_name"] else "")
    if lihat["sales_invoice"] and alok:
        nomor_f = [a["invoice_number"] for a in alok if a["invoice_number"]]
        untuk = ", ".join(nomor_f[:3]) + (f" dan {len(nomor_f) - 3} lainnya" if len(nomor_f) > 3 else "")
        untuk = f", untuk faktur {untuk}" if untuk else ""
    else:
        untuk = f", untuk {len(alok)} faktur" if alok else ""
    k.tambah(p["posted_at"], "PEMBAYARAN_DITERIMA", f"Pembayaran {n} {_rp(p['total_amount'])} diterima via {via}{untuk}",
             p["posted_by"], "receive_payment", p["id"], nomor)
    for a in alok:
        al = f": {a['unapply_reason']}" if a["unapply_reason"] else ""
        k.tambah(a["reversed_at"], "ALOKASI_DILEPAS",
                 f"Alokasi {_rp(a['amount_applied'])} ke faktur {a['invoice_number']} dilepas{al}", a["reversed_by"],
                 "receive_payment", p["id"], nomor)
    entitas_audit = {"receive_payment": [p["id"]]}
    if lihat["customer_deposit"] and p["created_deposit_id"]:
        d = await conn.fetchrow(
            "SELECT id, deposit_number, amount FROM customer_deposits WHERE id = $1 AND tenant_id = $2",
            p["created_deposit_id"], tenant_id)
        if d:
            k.tambah(p["posted_at"], "UANG_MUKA_DARI_KELEBIHAN",
                     f"Kelebihan bayar {_rp(d['amount'])} dicatat sebagai uang muka {d['deposit_number']}",
                     p["posted_by"], "customer_deposit", d["id"], d["deposit_number"])
    al = f": {p['void_reason']}" if p["void_reason"] else ""
    k.tambah(p["voided_at"], "PEMBAYARAN_DIBATALKAN", f"Pembayaran {n} dibatalkan{al}", p["voided_by"],
             "receive_payment", p["id"], nomor)
    keluar, total = await _selesaikan(conn, tenant_id, k, entitas_audit, lihat, limit)
    return {
        "payment_id": str(p["id"]),
        "payment_number": p["payment_number"],
        "customer_name": p["customer_name"],
        "status": p["status"],
        "events": keluar,
        "total": total,
        "omitted": omitted,
    }


MODUL_NOTA_KREDIT = {"credit_note": "credit_note", "sales_invoice": "sales_invoice", "sales_order": "sales_order"}


async def riwayat_nota_kredit(conn, tenant_id: str, cn_id, boleh: Callable[[str], Awaitable[bool]],
                              limit: int = 200) -> Optional[dict]:
    """Riwayat NOTA KREDIT (U5-C, 4 Okt 2026), bentuk SAMA dengan riwayat_so. Siklus (dibuat, diterbitkan, dibatalkan) +
    penerapan ke faktur (diterapkan/dilepas) + pengembalian dana + audit_logs beraktor. Nomor faktur asal/pesanan hanya bila
    pemanggil boleh membaca modulnya (`omitted`). None = tak ada di tenant ini (draf yang di-void sudah DIHAPUS -> 404)."""
    c = await conn.fetchrow(
        """SELECT cn.id, cn.credit_note_number, cn.total_amount, cn.created_at, cn.created_by, cn.posted_at, cn.posted_by,
                  cn.voided_at, cn.voided_by, cn.voided_reason, cn.original_invoice_id, si.invoice_number,
                  si.sales_order_id, so.order_number
           FROM credit_notes cn
           LEFT JOIN sales_invoices si ON si.id = cn.original_invoice_id AND si.tenant_id = cn.tenant_id
           LEFT JOIN sales_orders so ON so.id = si.sales_order_id AND so.tenant_id = cn.tenant_id
           WHERE cn.id = $1 AND cn.tenant_id = $2""", cn_id, tenant_id)
    if not c:
        return None
    izin = {}
    for jenis, modul in MODUL_NOTA_KREDIT.items():
        if modul not in izin:
            izin[modul] = bool(await boleh(modul))
    lihat = {j: izin[m] for j, m in MODUL_NOTA_KREDIT.items()}
    omitted = sorted({m for m in izin if not izin[m]})
    k = _Kumpul()
    n = c["credit_note_number"]
    asal = f" atas faktur {c['invoice_number']}" if c["invoice_number"] and lihat["sales_invoice"] else ""
    k.tambah(c["created_at"], "NOTA_KREDIT_DIBUAT", f"Nota kredit {n} {_rp(c['total_amount'])} dibuat{asal}", c["created_by"],
             "credit_note", c["id"], n)
    k.tambah(c["posted_at"], "NOTA_KREDIT_DITERBITKAN", f"Nota kredit {n} diterbitkan (diposting)", c["posted_by"],
             "credit_note", c["id"], n)
    al = f": {c['voided_reason']}" if c["voided_reason"] else ""
    k.tambah(c["voided_at"], "NOTA_KREDIT_DIBATALKAN", f"Nota kredit {n} dibatalkan{al}", c["voided_by"],
             "credit_note", c["id"], n)
    for a in await conn.fetch(
            """SELECT invoice_number, amount_applied, created_at, created_by, reversed_at, reversed_by, reversal_reason
               FROM credit_note_applications WHERE tenant_id = $1 AND credit_note_id = $2""", tenant_id, c["id"]):
        fk = f" ke faktur {a['invoice_number']}" if a["invoice_number"] and lihat["sales_invoice"] else ""
        k.tambah(a["created_at"], "NOTA_KREDIT_DITERAPKAN", f"Nota kredit {n} {_rp(a['amount_applied'])} diterapkan{fk}",
                 a["created_by"], "credit_note", c["id"], n)
        al = f": {a['reversal_reason']}" if a["reversal_reason"] else ""
        k.tambah(a["reversed_at"], "NOTA_KREDIT_DILEPAS", f"Penerapan nota kredit {n}{fk} dilepas{al}", a["reversed_by"],
                 "credit_note", c["id"], n)
    for r in await conn.fetch(
            """SELECT amount, created_at, created_by FROM credit_note_refunds
               WHERE tenant_id = $1 AND credit_note_id = $2""", tenant_id, c["id"]):
        k.tambah(r["created_at"], "NOTA_KREDIT_DIKEMBALIKAN", f"Nota kredit {n} {_rp(r['amount'])} dikembalikan ke pelanggan",
                 r["created_by"], "credit_note", c["id"], n)
    keluar, total = await _selesaikan(conn, tenant_id, k, {"credit_note": [c["id"]]}, lihat, limit)
    return {
        "credit_note_id": str(c["id"]), "credit_note_number": n,
        "sales_invoice": ({"id": str(c["original_invoice_id"]), "invoice_number": c["invoice_number"]}
                          if lihat["sales_invoice"] and c["original_invoice_id"] else None),
        "sales_order": ({"id": str(c["sales_order_id"]), "order_number": c["order_number"]}
                        if lihat["sales_order"] and c["sales_order_id"] else None),
        "events": keluar, "total": total, "omitted": omitted,
    }


MODUL_SURAT_JALAN = {"fulfillment": "sales_invoice", "sales_invoice": "sales_invoice", "sales_order": "sales_order"}


async def riwayat_surat_jalan(conn, tenant_id: str, delivery_id, boleh: Callable[[str], Awaitable[bool]],
                              limit: int = 200) -> Optional[dict]:
    """Riwayat SURAT JALAN / pengiriman (U4, 4 Okt 2026), bentuk SAMA dengan riwayat_so. Dibuat, barang keluar (diposting,
    hanya bila beda dari saat dibuat), dibatalkan (alasan; aktor dari audit FULFILLMENT_VOIDED). Surat Jalan tak punya void
    mandiri -- pembatalan = turunan void faktur. None = tak ada di tenant ini."""
    f = await conn.fetchrow(
        """SELECT f.id, f.fulfillment_number, f.created_at, f.created_by, f.posted_at, f.posted_by, f.voided_at,
                  f.voided_reason, si.id AS invoice_id, si.invoice_number, si.sales_order_id, so.order_number
           FROM invoice_fulfillments f
           JOIN sales_invoices si ON si.id = f.invoice_id AND si.tenant_id = f.tenant_id
           LEFT JOIN sales_orders so ON so.id = si.sales_order_id AND so.tenant_id = f.tenant_id
           WHERE f.id = $1 AND f.tenant_id = $2""", delivery_id, tenant_id)
    if not f:
        return None
    izin = {}
    for jenis, modul in MODUL_SURAT_JALAN.items():
        if modul not in izin:
            izin[modul] = bool(await boleh(modul))
    lihat = {j: izin[m] for j, m in MODUL_SURAT_JALAN.items()}
    omitted = sorted({m for m in izin if not izin[m]})
    k = _Kumpul()
    n = f["fulfillment_number"]
    asal = f" (faktur {f['invoice_number']})" if lihat["sales_invoice"] else ""
    k.tambah(f["created_at"], "SURAT_JALAN_DIBUAT", f"Surat Jalan {n} dibuat{asal}", f["created_by"], "fulfillment", f["id"], n)
    if f["posted_at"] and f["created_at"] and abs((f["posted_at"] - f["created_at"]).total_seconds()) > 1:
        k.tambah(f["posted_at"], "SURAT_JALAN_DIPOSTING", f"Surat Jalan {n} diposting (barang keluar)", f["posted_by"],
                 "fulfillment", f["id"], n)
    al = f": {f['voided_reason']}" if f["voided_reason"] else ""
    k.tambah(f["voided_at"], "SURAT_JALAN_DIBATALKAN", f"Surat Jalan {n} dibatalkan{al}", None, "fulfillment", f["id"], n)
    keluar, total = await _selesaikan(conn, tenant_id, k, {"fulfillment": [f["id"]]}, lihat, limit)
    return {
        "delivery_id": str(f["id"]), "delivery_number": n,
        "sales_invoice": ({"id": str(f["invoice_id"]), "invoice_number": f["invoice_number"]}
                          if lihat["sales_invoice"] else None),
        "sales_order": ({"id": str(f["sales_order_id"]), "order_number": f["order_number"]}
                        if lihat["sales_order"] and f["sales_order_id"] else None),
        "events": keluar, "total": total, "omitted": omitted,
    }
