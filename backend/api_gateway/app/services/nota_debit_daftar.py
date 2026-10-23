"""Data daftar/detail/bundel NOTA DEBIT seragam (U3c, 23 Okt 2026; KONTRAK-nota-debit-u3c-20261023.md). Kembar services/nota_kredit_daftar
(S2-NK; gold reference Penjualan). Server menghitung, FE memformat. Uang = JURNAL (U3a nota_debit.sql_sisa_nd; V441/V443), tak diubah di sini.

  sisa jurnal  = nota_debit.sql_sisa_nd (ND bebas terbit: Σ debit − Σ kredit Hutang jurnal efektif VENDOR_CREDIT/_REFUND; draf/void/bertaut = 0)
  aksi         = 'belum_terpakai' : terbit AND tak bertaut AND sisa jurnal > 0 (pill + ?aksi= + summary: predikat SAMA). SATU-SATUNYA pill (== NK).
  sisa_keadaan = draf | utuh | sebagian | terpakai | dikembalikan | sebagian_dikembalikan | batal (== NK)
  status_text  = teks_galat.status_id("vc", status_detail) -- kata SAMA dengan NK; ND bertaut 'posted' -> "Terpakai" (NetSuite "Fully Applied")
  remaining_amount = rumus LAMA router (void 0, selain itu total − applied − received) -- TIDAK diubah (preseden NK sisa_kredit_nk);
                 harness menggerbangnya == sisa jurnal di semua keadaan.
"""
from datetime import date
from decimal import Decimal
from typing import Optional

from .nota_debit import sql_sisa_nd

AKSI_BELUM_TERPAKAI = "belum_terpakai"
AKSI_ND = (AKSI_BELUM_TERPAKAI,)
KEADAAN_ND = ("draf", "utuh", "sebagian", "terpakai", "dikembalikan", "sebagian_dikembalikan", "batal")
_KEADAAN_DARI_DETAIL = {"draft": "draf", "posted": "utuh", "partial": "sebagian", "applied": "terpakai",
                        "refunded": "dikembalikan", "partially_refunded": "sebagian_dikembalikan", "void": "batal"}
# predikat jurnal efektif == nota_debit.sql_sisa_nd (U3a; satu definisi)
_EFEKTIF = "je.status = 'POSTED' AND je.reversed_by_id IS NULL AND je.reversal_of_id IS NULL"


def sql_aksi(aksi: str, a: str = "vendor_credits") -> str:
    if aksi != AKSI_BELUM_TERPAKAI:
        raise ValueError(aksi)
    return f"({a}.status NOT IN ('draft', 'void') AND {a}.original_bill_id IS NULL AND {sql_sisa_nd(a)} > 0)"


def aksi_nd(status: str, original_bill_id, sisa) -> Optional[str]:
    """Kembaran Python sql_aksi."""
    if status in ("draft", "void") or original_bill_id is not None or sisa is None:
        return None
    return AKSI_BELUM_TERPAKAI if Decimal(str(sisa)) > 0 else None


def status_detail_nd(status, amount_applied, amount_refunded) -> str:
    """== routers/credit_notes.status_detail_nk: 'partially_refunded' / 'refunded' bila habis/sebagian karena refund saja."""
    dipakai, kembali = (amount_applied or 0), (amount_refunded or 0)
    if dipakai == 0 and kembali > 0:
        if status == "partial":
            return "partially_refunded"
        if status == "applied":
            return "refunded"
    return status


def _detail_efektif(status: str, status_detail: str, original_bill_id) -> str:
    # ND bertaut berstatus 'posted' = nilainya sudah diatribusikan ke tagihan (sisa 0) -> tampil seperti 'applied' (== NK)
    return "applied" if status == "posted" and original_bill_id is not None else status_detail


def keadaan_nd(status: str, status_detail: str, original_bill_id) -> str:
    sd = _detail_efektif(status, status_detail, original_bill_id)
    return _KEADAAN_DARI_DETAIL.get(sd, sd)


def status_text_nd(status: str, status_detail: str, original_bill_id) -> str:
    from . import teks_galat as tg
    return tg.status_id("vc", _detail_efektif(status, status_detail, original_bill_id))


def sisa_lama(status, total_amount, amount_applied, amount_received):
    """remaining_amount rumus LAMA router (daftar :259 / detail :436) -- SATU tempat, perilaku tak diubah."""
    if status == "void":
        return 0
    return total_amount - (amount_applied or 0) - (amount_received or 0)


SQL_RINCIAN_ND = f"""
SELECT vendor_credits.id,
       COALESCE((SELECT SUM(jl.debit) FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id
                 JOIN chart_of_accounts coa ON coa.id = jl.account_id AND coa.account_type = 'PAYABLE'
                 WHERE je.tenant_id = $1 AND je.source_id = vendor_credits.id AND je.source_type = 'VENDOR_CREDIT' AND {_EFEKTIF}), 0) AS debit_hutang,
       COALESCE((SELECT SUM(jl.credit) FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id
                 JOIN chart_of_accounts coa ON coa.id = jl.account_id AND coa.account_type = 'PAYABLE'
                 WHERE je.tenant_id = $1 AND je.source_id = vendor_credits.id AND je.source_type = 'VENDOR_CREDIT_REFUND' AND {_EFEKTIF}), 0) AS dikembalikan,
       {{sisa}} AS sisa
FROM vendor_credits WHERE vendor_credits.tenant_id = $1 AND vendor_credits.id = ANY($2::uuid[])
"""


async def rincian_nd_many(conn, tenant_id: str, ids: list) -> dict:
    """{str(id): {terpakai, dikembalikan, sisa, debit_hutang}} turunan JURNAL (kembar rincian_nk_many): dikembalikan = kredit Hutang jurnal
    VENDOR_CREDIT_REFUND efektif; terpakai = debit Hutang jurnal VENDOR_CREDIT efektif − dikembalikan − sisa (ND bertaut = seluruhnya)."""
    if not ids:
        return {}
    out = {}
    for r in await conn.fetch(SQL_RINCIAN_ND.replace("{sisa}", sql_sisa_nd()), tenant_id, list(ids)):
        dh, rf, s = (Decimal(str(r[k] or 0)) for k in ("debit_hutang", "dikembalikan", "sisa"))
        out[str(r["id"])] = {"terpakai": dh - rf - s, "dikembalikan": rf, "sisa": s, "debit_hutang": dh}
    return out


def medan_rincian(r) -> dict:
    r = r or {}
    return {"terpakai": float(r.get("terpakai", 0)), "dikembalikan": float(r.get("dikembalikan", 0))}


def syarat_cari(search: str, mulai: int, a: str = "vendor_credits") -> tuple:
    """?search= per kata (SATU predikat daftar & /summary): nomor, nama vendor, ref_no, nomor tagihan asal. Hanya spasi = TRUE."""
    kata = (search or "").strip().split()
    if not kata:
        return "TRUE", []
    syarat = [f"({a}.credit_number ILIKE ${mulai + i} OR {a}.vendor_name ILIKE ${mulai + i} OR {a}.ref_no ILIKE ${mulai + i} "
              f"OR {a}.original_bill_number ILIKE ${mulai + i})" for i in range(len(kata))]
    return (syarat[0] if len(syarat) == 1 else f"({' AND '.join(syarat)})"), [f"%{w}%" for w in kata]


def _tgl(v) -> Optional[str]:
    return v.isoformat() if isinstance(v, date) else (str(v)[:10] if v else None)


def dok_nota_debit(v) -> dict:
    """Dokumen nota debit di bundel: status draf | batal | diposting (== dokumen ND di bundel tagihan)."""
    st = {"draft": "draf", "void": "batal"}.get(v["status"], "diposting")
    return {"kind": "debit_note", "id": str(v["id"]), "number": v["credit_number"], "date": _tgl(v["credit_date"]),
            "amount": f"{Decimal(str(v['total_amount'] or 0)):.2f}", "paper": "A4",
            "sent": {"state": "none", "sent_at": None, "viewed_at": None}, "batal": v["status"] == "void", "status": st}


async def kelompok_nd(conn, tid: str, nota: list) -> dict:
    """{vendor_credit_id: [kelompok]} bundel NOTA DEBIT (kamus FE pembelian): po · faktur (dari tagihan asal; dokumen SAMA dengan bundel
    tagihan = tagihan_daftar.kelompok_tagihan) · nota_debit (ITU SENDIRI, apa pun statusnya, TERAKHIR). Dipakai bundel DAN documents[] baris."""
    from uuid import UUID
    from .tagihan_daftar import kelompok_tagihan
    ids = [n["id"] for n in nota]
    if not ids:
        return {}
    kanon = {i: (i if isinstance(i, UUID) else UUID(str(i))) for i in ids}  # kunci hasil = id masukan apa adanya (str/UUID)
    rows = {r["id"]: r for r in await conn.fetch(
        """SELECT id, credit_number, credit_date, total_amount, status, original_bill_id FROM vendor_credits
           WHERE tenant_id = $1 AND id = ANY($2::uuid[])""", tid, list(kanon.values()))}
    bill_ids = sorted({r["original_bill_id"] for r in rows.values() if r["original_bill_id"]})
    kt = await kelompok_tagihan(conn, tid, bill_ids) if bill_ids else {}
    hasil = {}
    for vid in ids:
        r, kel = rows.get(kanon[vid]), []
        if r and r["original_bill_id"]:
            kel += [g for g in kt.get(r["original_bill_id"], []) if g["key"] in ("po", "faktur")]
        if r:
            kel.append({"key": "nota_debit", "label": "Nota Debit", "docs": [dok_nota_debit(r)]})
        hasil[vid] = kel
    return hasil


async def po_asal(conn, tid: str, bill_ids: list) -> dict:
    """{bill_id: (po_id, po_number)} PO asal tagihan (bills.purchase_order_id)."""
    if not bill_ids:
        return {}
    return {r["id"]: (r["po_id"], r["po_number"]) for r in await conn.fetch(
        """SELECT b.id, po.id AS po_id, po.po_number FROM bills b JOIN purchase_orders po ON po.id = b.purchase_order_id AND po.tenant_id = $1
           WHERE b.tenant_id = $1 AND b.id = ANY($2::uuid[])""", tid, list(bill_ids))}


def medan_baris(v, sisa_jurnal, rincian, kelompok, po) -> dict:
    """SEMUA medan turunan U3c untuk satu nota debit -- SATU fungsi untuk baris daftar DAN detail (paritas)."""
    from .tagihan_daftar import documents_baris
    sd = status_detail_nd(v["status"], v["amount_applied"], v["amount_received"])
    ak = aksi_nd(v["status"], v["original_bill_id"], sisa_jurnal)
    docs = documents_baris(kelompok or [])
    po_id, po_no = po or (None, None)
    return {"aksi": ak, "aksi_semua": [ak] if ak else [],
            "sisa_keadaan": keadaan_nd(v["status"], sd, v["original_bill_id"]),
            "status_text": status_text_nd(v["status"], sd, v["original_bill_id"]),
            "original_bill_id": str(v["original_bill_id"]) if v["original_bill_id"] else None,
            "original_bill_number": v["original_bill_number"],
            "purchase_order_id": str(po_id) if po_id else None, "purchase_order_number": po_no,
            "documents": docs, "doc_count": len(docs), **medan_rincian(rincian)}


async def medan_banyak(conn, tid: str, rows: list) -> dict:
    """{id: medan_baris} batch satu halaman (kueri ber-batch, tanpa N+1)."""
    ids = [r["id"] for r in rows]
    if not ids:
        return {}
    sisa = {str(r["id"]): r["sisa"] for r in await conn.fetch(
        f"SELECT vendor_credits.id, {sql_sisa_nd()} AS sisa FROM vendor_credits WHERE vendor_credits.tenant_id = $1 AND vendor_credits.id = ANY($2::uuid[])",
        tid, ids)}
    rinc = await rincian_nd_many(conn, tid, ids)
    kel = await kelompok_nd(conn, tid, [{"id": i} for i in ids])
    po = await po_asal(conn, tid, sorted({r["original_bill_id"] for r in rows if r["original_bill_id"]}))
    return {r["id"]: medan_baris(r, sisa.get(str(r["id"])), rinc.get(str(r["id"])), kel.get(r["id"]),
                                 po.get(r["original_bill_id"]) if r["original_bill_id"] else None) for r in rows}
