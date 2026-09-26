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

from ..utils.tanggal_tenant import zona_tenant

# jenis dokumen -> modul izin BACA
MODUL = {
    "sales_order": "sales_order",
    "customer_deposit": "customer_deposit",
    "proforma": "proforma",
    "sales_invoice": "sales_invoice",
    "fulfillment": "sales_invoice",
    "receive_payment": "receive_payment",
}

# entity_type di audit_logs (lama memakai nama tabel, penulis baru juga) -> jenis dokumen
ENTITAS_AUDIT = {
    "sales_orders": "sales_order", "sales_order": "sales_order",
    "proformas": "proforma", "proforma": "proforma",
    "sales_invoices": "sales_invoice", "sales_invoice": "sales_invoice",
    "customer_deposits": "customer_deposit", "customer_deposit": "customer_deposit",
    "invoice_fulfillments": "fulfillment",
    "receive_payments": "receive_payment",
}

RINGKAS_AUDIT = {
    "SALES_ORDER_UPDATED": "Pesanan diubah",
    "SALES_ORDER_CANCELLED": "Pesanan dibatalkan",
    "SALES_ORDER_CLOSED": "Pesanan ditutup",
    "SALES_ORDER_FORCE_CLOSED": "Pesanan ditutup (sisa dibatalkan)",
    "PROFORMA_ISSUED": "Proforma diterbitkan",
    "PROFORMA_CANCELLED": "Proforma dibatalkan",
    "PROFORMA_UPDATED": "Proforma diubah",
    "SALES_INVOICE_LINKED_TO_ORDER": "Faktur ditautkan ke pesanan",
    "REVENUE_RECOGNIZED_NON_STOCK": "Pendapatan non-stok diakui",
    "SALES_INVOICE_VOIDED": "Faktur dibatalkan (void)",
    "FULFILLMENT_VOIDED": "Surat Jalan dibatalkan",
    "DOCUMENT_DELETED": "Dokumen dihapus",
}


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
    try:
        v = float(x or 0)
    except (TypeError, ValueError):
        return "Rp—"
    s = f"{v:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".")
    return "Rp" + s.removesuffix(",00")


class _Kumpul:
    def __init__(self):
        self.ev = []

    def tambah(self, at, jenis, ringkas, aktor, dok_tipe=None, dok_id=None, dok_nomor=None, sumber="dokumen"):
        if at is None:
            return
        self.ev.append({
            "at": at, "jenis": jenis, "ringkas": ringkas, "aktor_id": str(aktor) if aktor else None,
            "dokumen": ({"tipe": dok_tipe, "id": str(dok_id), "nomor": dok_nomor} if dok_tipe else None),
            "sumber": sumber,
        })


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
                         a["created_by"], "customer_deposit", a["deposit_id"], n)
                k.tambah(a["reversed_at"], "UANG_MUKA_DILEPAS",
                         f"Penerapan uang muka {n} ke faktur {a['invoice_number']} dilepas",
                         None, "customer_deposit", a["deposit_id"], n)

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
    id_faktur = [f["id"] for f in fakturs]
    if lihat["sales_invoice"]:
        entitas_audit["sales_invoice"] = id_faktur
        for f in fakturs:
            n = f["invoice_number"]
            k.tambah(f["created_at"], "FAKTUR_DIBUAT", f"Faktur {n} {_rp(f['total_amount'])} dibuat dari pesanan", f["created_by"], "sales_invoice", f["id"], n)
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
    audit_ada = set()
    if kueri_ent:
        for r in await conn.fetch(
            """SELECT a.id, a."createdAt", a."eventType", a."userId", a.entity_type, a.entity_id,
                      a.entity_number, a.metadata
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
            aktor = r["userId"] or meta.get("user_id")
            k.tambah(r["createdAt"], r["eventType"], ringkas, aktor, jenis, r["entity_id"], r["entity_number"], sumber="audit")
            audit_ada.add((r["eventType"], str(r["entity_id"])))

    # kolom tanpa aktor yang punya padanan audit (beraktor) -> pakai yang audit saja
    PADANAN = {"PROFORMA_DITERBITKAN": "PROFORMA_ISSUED", "PROFORMA_DIBATALKAN": "PROFORMA_CANCELLED",
               "FAKTUR_DIBATALKAN": "SALES_INVOICE_VOIDED", "SURAT_JALAN_DIBATALKAN": "FULFILLMENT_VOIDED"}
    ev = [e for e in k.ev if not (e["jenis"] in PADANAN and e["dokumen"]
                                  and (PADANAN[e["jenis"]], e["dokumen"]["id"]) in audit_ada)]

    # nama aktor (satu kueri)
    ids = sorted({e["aktor_id"] for e in ev if e["aktor_id"]})
    nama = {}
    if ids:
        for u in await conn.fetch(
            """SELECT id, COALESCE(NULLIF(fullname, ''), NULLIF(name, ''), email) AS nama
               FROM "User" WHERE id = ANY($1::text[])""", ids,
        ):
            nama[u["id"]] = u["nama"]

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
            "aktor": ({"id": e["aktor_id"], "nama": nama.get(e["aktor_id"])} if e["aktor_id"] else None),
            "dokumen": e["dokumen"],
            "sumber": e["sumber"],
        })
    return {
        "order_id": str(so["id"]),
        "order_number": nomor_so,
        "events": keluar,
        "total": len(ev),
        "omitted": omitted,
    }
