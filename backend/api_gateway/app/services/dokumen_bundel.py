"""Pembentuk dokumen PER KIND untuk bundel panel /documents — jangkar GENERIK (S1-U2, 20 Okt 2026, MASTER).

Satu sumber bentuk dokumen untuk SEMUA bundel: pesanan (routers/dokumen.susun_dokumen), faktur
(susun_dokumen_faktur) dan modul lain (BACKEND2: Penawaran/Penerimaan/Pengiriman/Proforma/UM/NK). Pemanggil (=
"jangkar") hanya MEMILIH id; pembentuk di sini memuat baris, membentuk medan, dan mengurutkan. Medan & urutan IDENTIK
dengan bundel SO sebelum ekstraksi (dibuktikan harness scripts/ops/uji-nyata-dokumen-faktur.py: SO lama == baru).

  rekap(so_id)                                   Rekap pesanan
  penawaran(conn, tid, quote_id)                 Penawaran (atau None)
  proforma_so(conn, tid, so_id, so_batal, ids, termasuk_semua)  Proforma SO (term & lunas RELATIF ke SO-nya); ids = saring hasil; termasuk_semua = jangkar proforma (draf & batal ikut)
  kwitansi_uang_muka(conn, tid, ids, jumlah)     uang muka ber-jurnal tak void; jumlah {id: Decimal} = nominal per jangkar
  kwitansi_penerimaan(conn, tid, invoice_ids)    penerimaan posted beralokasi AKTIF ke faktur itu; amount = Σ alokasi ke faktur itu
  kwitansi_penerimaan_sendiri(conn, tid, ids)    kwitansi penerimaan ITU SENDIRI (posted/voided; amount = total_amount) -- jangkar penerimaan
  surat_jalan(conn, tid, ids)                    Surat jalan aktif
  faktur(conn, tid, ids)                         Faktur; paid = terbit AND sisa jurnal <= 0
  nota_kredit(conn, tid, ids)                    Nota kredit
  rampungkan(conn, tid, kelompok)                sent (document_shares) + batal (penentu 409) + status per dokumen

Uang = string "1234.00" (_uang). Filter tenant EKSPLISIT di tiap kueri.
"""
from datetime import date
from decimal import Decimal
from typing import Optional

NOL = Decimal("0")
SUBTIPE = {"DP": "dp", "TERMIN": "termin", "PELUNASAN": "penuh"}
# deposit_jenis.JENIS -> receipt_kind kwitansi bundel. BUKAN subtype: pembaca ketat FE live (bacaDokumen.ts) menolak subtype
# di luar dp/termin/penuh/cicilan untuk SEMUA kind -> panel dokumen gagal (MASTER/WORKSPACE 17 Okt 2026).
SUBTIPE_KWITANSI = {"DP": "uang_muka", "PELUNASAN": "pelunasan"}


DRAF_SEBAGAI_DIRINYA = ("invoice", "proforma", "receipt", "credit_note")  # proforma: jangkar proforma BACKEND2 (21 Okt)


def _uang(v) -> str:
    return f"{Decimal(str(v or 0)):.2f}"


def _tgl(v) -> Optional[str]:
    return v.isoformat() if isinstance(v, date) else (str(v)[:10] if v else None)


def _belum_kirim() -> dict:
    return {"state": "none", "sent_at": None, "viewed_at": None}


def rekap(so_id) -> dict:
    return {"kind": "rekap", "id": str(so_id), "paper": "A4"}


async def penawaran(conn, tid: str, quote_id) -> Optional[dict]:
    q = await conn.fetchrow("""SELECT id, quote_number, quote_date, expiry_date, total_amount, sent_at FROM quotes
                               WHERE id = $1 AND tenant_id = $2""", quote_id, tid)
    if not q:
        return None
    return {"kind": "quotation", "id": str(q["id"]), "number": q["quote_number"], "date": _tgl(q["quote_date"]),
            "amount": _uang(q["total_amount"]), "paper": "A4",
            "valid_until": _tgl(q["expiry_date"]),  # P5 pesan Kirim "berlaku sampai {tgl}"
            "sent": {"state": "sent" if q["sent_at"] else "none",
                     "sent_at": q["sent_at"].isoformat() if q["sent_at"] else None, "viewed_at": None}}


async def proforma_so(conn, tid: str, so_id, so_batal: bool, ids: Optional[set] = None, termasuk_semua: bool = False) -> list:
    """Proforma SO (bukan draf; batal hanya bila SO batal; `termasuk_semua=True` = jangkar Proforma: draf & batal ikut, `status` lewat rampungkan). term = urutan TERMIN di SO; paid = terbayar_proforma SO.
    ids (set str) = saring HASIL (term & lunas tetap dihitung relatif ke seluruh SO -> medan identik dengan bundel SO)."""
    from .proforma_terbayar import terbayar_proforma
    pros = await conn.fetch(
        """SELECT id, proforma_number, proforma_date, purpose, amount, status, issued_at FROM proformas
           WHERE tenant_id = $1 AND sales_order_id = $2 AND ($3 OR status <> 'draft')
           ORDER BY issued_at NULLS LAST, proforma_number""", tid, so_id, termasuk_semua)
    pros = [p for p in pros if termasuk_semua or p["status"] != "cancelled" or so_batal]
    terbayar = await terbayar_proforma(conn, tid, [so_id]) if pros else {}
    termin = [p["id"] for p in pros if p["purpose"] == "TERMIN" and p["status"] != "cancelled"]
    docs = []
    for p in pros:
        lunas = (not so_batal and p["status"] == "issued"
                 and terbayar.get(p["id"], {}).get("paid", NOL) >= Decimal(str(p["amount"] or 0)))
        docs.append({"kind": "proforma", "id": str(p["id"]), "number": p["proforma_number"],
                     "date": _tgl(p["proforma_date"]), "amount": _uang(p["amount"]),
                     "subtype": SUBTIPE.get(p["purpose"]),
                     "term": (termin.index(p["id"]) + 1) if p["id"] in termin else None,
                     "installments": None, "paid": lunas, "paper": "A4", "sent": _belum_kirim()})
    return docs if ids is None else [d for d in docs if d["id"] in ids]


async def kwitansi_uang_muka(conn, tid: str, ids: list, jumlah: Optional[dict] = None, sendiri: bool = False) -> dict:
    """{deposit_id: dok} uang muka ber-jurnal tak void di `ids`. amount = cd.amount, atau jumlah[id] bila jangkar memberi
    nominal sendiri (mis. faktur: bagian yang DITERAPKAN ke faktur itu). sendiri=True (S2-UM b, jangkar uang muka): kwitansi
    uang muka ITU SENDIRI apa pun statusnya (draf/void tampil sebagai dirinya; status lewat rampungkan)."""
    from .faktur_cetak import label_metode  # SATU format metode (riwayat faktur / Rekap / bundel)
    from .deposit_jenis import sql_jenis_efektif  # jenis efektif kwitansi (sumber label_deposit)
    from .proforma_atribusi import label_rekening  # format rekening SATU (pemilik=False -> '{bank} {nomor}')
    if not ids:
        return {}
    out = {}
    for d in await conn.fetch(
        f"""SELECT cd.id, cd.deposit_number, cd.deposit_date, cd.amount, cd.payment_method, ba.bank_name,
                  ba.account_number, ba.account_holder_name, ba.account_name, coa.name AS coa_name,
                  {sql_jenis_efektif("cd")} AS jenis
           FROM customer_deposits cd
           LEFT JOIN proformas p ON p.id = cd.proforma_id AND p.tenant_id = cd.tenant_id
           LEFT JOIN LATERAL (
               SELECT b.bank_name, b.account_number, b.account_holder_name, b.account_name FROM bank_accounts b
               WHERE b.tenant_id = cd.tenant_id AND (b.id = cd.bank_account_id OR b.coa_id = cd.account_id)
               ORDER BY (b.id = cd.bank_account_id) DESC, b.is_default DESC LIMIT 1
           ) ba ON true
           LEFT JOIN chart_of_accounts coa ON coa.id = cd.account_id AND coa.tenant_id = cd.tenant_id
           WHERE cd.tenant_id = $1 AND cd.id = ANY($2::uuid[])"""
        + ("" if sendiri else " AND cd.status <> 'void' AND cd.journal_id IS NOT NULL"),
            tid, list(ids)):
        out[d["id"]] = {"kind": "receipt", "id": str(d["id"]), "number": d["deposit_number"],
                        "date": _tgl(d["deposit_date"]),
                        "amount": _uang(jumlah[d["id"]] if jumlah is not None else d["amount"]),
                        "method": label_metode(d["payment_method"], d["bank_name"]),
                        # detailpage (17 Okt 2026): jenis efektif (deposit_jenis, sumber label_deposit) + rekening '{bank} {nomor}'
                        "receipt_kind": SUBTIPE_KWITANSI.get(d["jenis"], "uang_muka"),
                        "account_label": label_rekening(d, pemilik=False),
                        "installment": None, "paper": "A5-landscape", "sent": _belum_kirim()}
    return out


_SQL_REKENING_KWITANSI = """
           LEFT JOIN LATERAL (
               SELECT b.bank_name, b.account_number, b.account_holder_name, b.account_name FROM bank_accounts b
               WHERE b.tenant_id = x.tenant_id AND (b.id = x.bank_account_id OR b.coa_id = x.bank_account_id)
               ORDER BY (b.id = x.bank_account_id) DESC, b.is_default DESC LIMIT 1
           ) ba ON true
           LEFT JOIN chart_of_accounts coa ON coa.id = x.bank_account_id AND coa.tenant_id = x.tenant_id"""


def _dok_kwitansi_penerimaan(p) -> dict:
    """Bentuk dokumen kwitansi penerimaan (SATU tempat): dipakai kwitansi_penerimaan (per faktur) dan kwitansi_penerimaan_sendiri."""
    from .faktur_cetak import label_metode
    from .proforma_atribusi import label_rekening
    return {"kind": "receipt", "id": str(p["id"]), "number": p["payment_number"],
            "date": _tgl(p["payment_date"]), "amount": _uang(p["jumlah"]),
            "method": label_metode(p["payment_method"], p["bank_name"]),
            "receipt_kind": "pelunasan",   # penerimaan atas faktur = pelunasan
            "account_label": label_rekening(p, pemilik=False),
            "installment": None, "paper": "A5-landscape", "sent": _belum_kirim()}


async def kwitansi_penerimaan(conn, tid: str, invoice_ids: list) -> dict:
    """{payment_id: dok} penerimaan posted beralokasi AKTIF ke `invoice_ids`; amount = Σ alokasi ke faktur-faktur itu."""
    if not invoice_ids:
        return {}
    out = {}
    for p in await conn.fetch(
        """SELECT x.*, ba.bank_name, ba.account_number, ba.account_holder_name, ba.account_name, coa.name AS coa_name
           FROM (
               SELECT rp.id, rp.payment_number, rp.payment_date, rp.payment_method, SUM(rpa.amount_applied) AS jumlah,
                      rp.tenant_id, rp.bank_account_id
               FROM receive_payment_allocations rpa
               JOIN receive_payments rp ON rp.id = rpa.payment_id AND rp.tenant_id = $1 AND rp.status = 'posted'
               JOIN sales_invoices si ON si.id = rpa.invoice_id AND si.tenant_id = $1
               WHERE rpa.status = 'active' AND rpa.invoice_id = ANY($2::uuid[])
               GROUP BY 1, 2, 3, 4, rp.tenant_id, rp.bank_account_id
           ) x""" + _SQL_REKENING_KWITANSI,
            tid, list(invoice_ids)):
        out[p["id"]] = _dok_kwitansi_penerimaan(p)
    return out


async def kwitansi_penerimaan_sendiri(conn, tid: str, payment_ids: list) -> dict:
    """{payment_id: dok} kwitansi penerimaan ITU SENDIRI (jangkar penerimaan, PENJUALAN-SERAGAM 20 Okt 2026): penerimaan posted ATAU
    voided (voided tampil sebagai dirinya, `batal` lewat rampungkan); draf tak punya kwitansi. amount = total_amount penerimaan
    (bukan Σ alokasi) -- tetap tampil walau semua alokasinya dilepas."""
    if not payment_ids:
        return {}
    out = {}
    for p in await conn.fetch(
        """SELECT x.*, ba.bank_name, ba.account_number, ba.account_holder_name, ba.account_name, coa.name AS coa_name
           FROM (
               SELECT rp.id, rp.payment_number, rp.payment_date, rp.payment_method, rp.total_amount AS jumlah,
                      rp.tenant_id, rp.bank_account_id
               FROM receive_payments rp
               WHERE rp.tenant_id = $1 AND rp.id = ANY($2::uuid[]) AND rp.status IN ('posted', 'voided')
           ) x""" + _SQL_REKENING_KWITANSI,
            tid, list(payment_ids)):
        out[p["id"]] = _dok_kwitansi_penerimaan(p)
    return out


def urut_kwitansi(kw: dict) -> list:
    return sorted(kw.values(), key=lambda x: (x["date"] or "", x["number"] or ""))


async def surat_jalan(conn, tid: str, ids: list, termasuk_batal: bool = False) -> list:
    """Surat jalan aktif; `termasuk_batal=True` (jangkar Pengiriman: Surat Jalan voided tetap tampil sebagai dirinya, `batal` lewat rampungkan)."""
    from .so_kirim import _AKTIF
    if not ids:
        return []
    return [{"kind": "delivery", "id": str(f["id"]), "number": f["fulfillment_number"], "date": _tgl(f["fulfillment_date"]),
             "paper": "A4", "sent": _belum_kirim()}
            for f in await conn.fetch(
                f"""SELECT f.id, f.fulfillment_number, f.fulfillment_date FROM invoice_fulfillments f
                    WHERE f.tenant_id = $1 AND f.id = ANY($2::uuid[]){'' if termasuk_batal else f' AND {_AKTIF}'}
                    ORDER BY f.fulfillment_date, f.fulfillment_number""", tid, list(ids))]


async def faktur(conn, tid: str, ids: list) -> list:
    """Faktur `ids` urut tanggal, nomor. paid = terbit (bukan draf/void) DAN sisa jurnal (compute_ar_outstanding) <= 0."""
    if not ids:
        return []
    fak = await conn.fetch(
        """SELECT id, invoice_number, invoice_date, total_amount, status FROM sales_invoices
           WHERE tenant_id = $1 AND id = ANY($2::uuid[]) ORDER BY invoice_date, invoice_number""", tid, list(ids))
    terbit = [f["id"] for f in fak if f["status"] not in ("draft", "void")]
    sisa_f = {x["invoice_id"]: Decimal(str(x["outstanding"])) for x in await conn.fetch(
        "SELECT invoice_id, outstanding FROM compute_ar_outstanding($1) WHERE invoice_id = ANY($2::uuid[])",
        tid, terbit)} if terbit else {}
    return [{"kind": "invoice", "id": str(f["id"]), "number": f["invoice_number"], "date": _tgl(f["invoice_date"]),
             "amount": _uang(f["total_amount"]),
             "paid": f["status"] not in ("draft", "void") and sisa_f.get(f["id"], NOL) <= NOL, "paper": "A4",
             "sent": _belum_kirim()} for f in fak]


async def nota_kredit(conn, tid: str, ids: list) -> list:
    if not ids:
        return []
    return [{"kind": "credit_note", "id": str(c["id"]), "number": c["credit_note_number"],
             "date": _tgl(c["credit_note_date"]), "amount": _uang(c["total_amount"]), "paper": "A4", "sent": _belum_kirim()}
            for c in await conn.fetch(
                """SELECT id, credit_note_number, credit_note_date, total_amount FROM credit_notes
                   WHERE tenant_id = $1 AND id = ANY($2::uuid[])
                   ORDER BY credit_note_date, credit_note_number""", tid, list(ids))]


async def rampungkan(conn, tid: str, kelompok: list) -> list:
    """sent (document_shares; penawaran tanpa tautan tetap quotes.sent_at), batal (penentu SAMA dengan 409 DOKUMEN_BATAL),
    status per dokumen. Mengubah `kelompok` di tempat dan mengembalikannya."""
    from uuid import UUID
    from .dokumen_bagikan import keadaan_kirim
    from ..routers.dokumen import JENIS, _baris_dokumen, dokumen_batal, status_dokumen
    kirim = await keadaan_kirim(conn, tid, [(d["kind"], d["id"]) for gr in kelompok for d in gr["docs"]])
    for gr in kelompok:
        for d in gr["docs"]:
            k = kirim.get((d["kind"], d["id"]))
            if k:
                d["sent"] = k
            elif "sent" not in d:
                d["sent"] = _belum_kirim()
    # DOKUMEN-BATAL-FIELD (15 Okt 2026, MASTER): `batal` per dokumen = penentu YANG SAMA dengan 409 DOKUMEN_BATAL share.
    for gr in kelompok:
        for d in gr["docs"]:
            row, _ = await _baris_dokumen(conn, tid, d["kind"], UUID(d["id"])) if d["kind"] in JENIS else (None, None)
            d["batal"] = bool(row) and dokumen_batal(d["kind"], row["status"])
            # draf HANYA untuk dokumen yang tampil sebagai DIRINYA (jangkar): faktur, proforma (BACKEND2), kwitansi uang muka, NK (S2 b).
            # Rekap SO draf tetap "terbit" (bundel SO tak berubah -- harness SO lama == baru); bundel SO tak memuat kwitansi/NK draf.
            d["status"] = status_dokumen(d["kind"], d["batal"], d.get("paid"),
                                         draf=d["kind"] in DRAF_SEBAGAI_DIRINYA and bool(row) and str(row["status"] or "").lower() == "draft")
    return kelompok
