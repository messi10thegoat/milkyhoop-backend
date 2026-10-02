"""Dokumen SO (P3 SO-dokumen, 02-DATA-DAN-API §Endpoint/§Render, 1 Okt 2026).

  GET /api/sales-orders/{order_id}/documents   bundel dokumen satu pesanan (skema contracts/so-documents.schema.json)
  GET /api/documents/{kind}/{id}/html           HTML panel -- string HTML YANG SAMA dengan PDF (pdf_service.html_layar)
  GET /api/documents/{kind}/{id}/pdf            PDF dari Render yang sama, di-cache per versi (Redis)
  GET /api/documents/receipts/pdf?so={id}       semua kwitansi pesanan, satu PDF A5 (satu per halaman)
  GET /api/public/fonts/{nama}                  font Inter untuk HTML layar (publik: unduhan font CSS tak membawa JWT)

SATU sumber: konteks dari PEMUAT yang sama dengan rute /pdf lama (muat_pdf_*), render dari pdf_service.render_*.
kind: rekap | quotation | proforma | receipt | delivery | invoice. Izin per kind dipetakan di permission_middleware;
kwitansi UANG MUKA juga wajib customer_deposit R (satu rute melayani dua sumber kwitansi).
"""
import base64
import hashlib
import logging
from datetime import date
from decimal import Decimal
from io import BytesIO
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from ..services.pdf_service import TEMPLATE_DIR, Render, get_pdf_service

logger = logging.getLogger(__name__)
router = APIRouter()

JENIS = ("rekap", "quotation", "proforma", "receipt", "delivery", "invoice")
FONT_SAH = {f"Inter-{b}.ttf" for b in ("Regular", "Medium", "SemiBold", "Bold", "Italic")}
VERSI_RENDER = "p3-1"  # naikkan bila mesin/aturan render berubah tanpa mengubah HTML
NOL = Decimal("0")
SUBTIPE = {"DP": "dp", "TERMIN": "termin", "PELUNASAN": "penuh"}


def _ctx(request: Request) -> dict:
    u = getattr(request.state, "user", None)
    if not u or not u.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Authentication required")
    uid = u.get("user_id") or u.get("id")
    return {"tenant_id": u["tenant_id"], "user_id": UUID(str(uid)) if uid else None}


async def _pool():
    from ..services.db_pool import get_db_pool
    return await get_db_pool()


def _uuid(v) -> UUID:
    try:
        return v if isinstance(v, UUID) else UUID(str(v))
    except ValueError:
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan")


async def _wajib_izin(request: Request, aksi: str, modul: str):
    from ..services.policy_engine_client import get_policy_engine
    u = request.state.user
    eng = get_policy_engine()
    uc = await eng.get_user_context(str(u.get("user_id")), u.get("tenant_id"), u.get("role", "USER"))
    if not await eng.can(uc, aksi, modul):
        raise HTTPException(status_code=403, detail="Aksi ini belum diberi izin untuk peran Anda. Hubungi pemilik usaha.")


async def _render(conn, ctx: dict, request: Request, jenis: str, did: str, cek_izin: bool = True) -> tuple:
    """-> (Render, nomor). Konteks dari pemuat rute /pdf lama (satu sumber). cek_izin=False HANYA untuk halaman
    publik bertoken (P5): otorisasinya = token tautan yang berlaku, bukan peran pengguna."""
    ps = get_pdf_service()
    uid = _uuid(did)
    if jenis == "rekap":
        from ..services.rekap_pesanan import muat_rekap_pesanan
        h = await muat_rekap_pesanan(conn, ctx, uid)
        return ps.render_rekap_pesanan(h["rekap"], h["tenant_info"]), f"Rekap {h['rekap']['order_number']}"
    if jenis == "quotation":
        from . import quotes as Q
        m = await Q.muat_pdf_penawaran(conn, ctx, str(uid))
        return ps.render_quote(m["quote_data"], m["tenant_info"]), m["quote_data"]["quote_number"]
    if jenis == "proforma":
        from . import proformas as PF
        m = await PF.muat_pdf_proforma_id(conn, ctx, uid)
        return ps.render_proforma(m["proforma_data"], m["tenant_info"]), m["proforma_data"]["proforma_number"]
    if jenis == "receipt":
        if await conn.fetchval("SELECT 1 FROM customer_deposits WHERE id = $1 AND tenant_id = $2", uid, ctx["tenant_id"]):
            if cek_izin:
                await _wajib_izin(request, "R", "customer_deposit")
            from . import customer_deposits as CD
            m = await CD.muat_pdf_kwitansi_uang_muka(conn, ctx, str(uid))
        else:
            from . import receive_payments as RP
            m = await RP.muat_pdf_kwitansi_penerimaan(conn, ctx, str(uid))
        return ps.render_receipt(m["receipt_data"], m["tenant_info"]), m["receipt_data"]["receipt_number"]
    if jenis == "delivery":
        from . import deliveries as DL
        m = await DL.muat_pdf_surat_jalan(conn, ctx, str(uid))
        return ps.render_delivery_note(m["delivery_data"]), m["delivery_data"]["delivery_number"]
    if jenis == "invoice":
        from . import sales_invoices as SI
        m = await SI.muat_pdf_faktur(conn, ctx, uid, None)
        return ps.render_sales_invoice(m["invoice_data"], m["tpl"]), m["invoice_data"]["invoice_number"]
    raise HTTPException(status_code=404, detail="Jenis dokumen tidak dikenal")


def _kunci_cache(tenant_id: str, r: Render) -> str:
    h = hashlib.sha256()
    for bagian in (VERSI_RENDER, tenant_id, r.html, *[(TEMPLATE_DIR / n).read_text(encoding="utf-8") for n in r.css
                                                     if (TEMPLATE_DIR / n).exists()], str(r.font)):
        h.update(bagian.encode("utf-8"))
        h.update(b"\0")
    return f"dokpdf:{tenant_id}:{h.hexdigest()}"


async def pdf_tercache(tenant_id: str, r: Render) -> bytes:
    """PDF per VERSI (02 §Render): kunci = hash isi HTML + CSS + versi render (+ tenant). Data/template berubah = kunci
    baru -> tak perlu pembatalan. Redis mati -> render langsung (tak gagal). Render di threadpool (WeasyPrint CPU)."""
    from ..services.redis_client import get_redis
    kunci = _kunci_cache(tenant_id, r)
    redis = await get_redis()
    if redis is not None:
        try:
            v = await redis.get(kunci)
            if v:
                return base64.b64decode(v)
        except Exception as e:  # cache tak boleh menjatuhkan unduhan
            logger.warning("[DOKUMEN] cache baca gagal (%s)", type(e).__name__)
    pdf = await run_in_threadpool(get_pdf_service().tulis_pdf, r)
    if redis is not None:
        try:
            await redis.set(kunci, base64.b64encode(pdf).decode("ascii"), ex=7 * 86400)
        except Exception as e:
            logger.warning("[DOKUMEN] cache tulis gagal (%s)", type(e).__name__)
    return pdf


def _respons_pdf(pdf: bytes, nomor: str) -> StreamingResponse:
    from ..utils.content_disposition import pdf_content_disposition
    return StreamingResponse(BytesIO(pdf), media_type="application/pdf",
                             headers={"Content-Disposition": pdf_content_disposition(nomor or "dokumen"),
                                      "Cache-Control": "no-store"})


@router.get("/public/fonts/{nama}")
async def font_publik(nama: str):
    """Font Inter untuk HTML layar. Publik (CSS @font-face tak membawa Authorization) dan HANYA 5 berkas daftar putih."""
    if nama not in FONT_SAH:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(TEMPLATE_DIR / "fonts" / nama, media_type="font/ttf",
                        # ACAO *: dokumen di iframe ber-sandbox (asal opak) memuat font lintas-asal (P5, 1 Okt 2026).
                        headers={"Cache-Control": "public, max-age=31536000, immutable", "Access-Control-Allow-Origin": "*"})


@router.get("/documents/receipts/pdf")
async def pdf_kwitansi_pesanan(request: Request, so: str = Query(..., description="id Sales Order")):
    """Semua kwitansi pesanan dalam SATU PDF A5 melintang, satu kwitansi per halaman ("Cetak N kwitansi")."""
    ctx = _ctx(request)
    pool = await _pool()
    ps = get_pdf_service()
    async with pool.acquire() as conn:
        b = await susun_dokumen(conn, ctx, _uuid(so))
        kw = next((g["docs"] for g in b["groups"] if g["key"] == "kw"), [])
        if not kw:
            raise HTTPException(status_code=404, detail="Pesanan ini belum punya kwitansi")
        renders = [(await _render(conn, ctx, request, "receipt", d["id"]))[0] for d in kw]

    def _gabung():
        docs = [ps.dokumen(r) for r in renders]
        return docs[0].copy([p for d in docs for p in d.pages]).write_pdf()
    pdf = await run_in_threadpool(_gabung)
    return _respons_pdf(pdf, f"Kwitansi {b['so']['number']}")


@router.get("/documents/{kind}/{doc_id}/html", response_class=HTMLResponse)
async def html_dokumen(request: Request, kind: str, doc_id: str):
    if kind not in JENIS:
        raise HTTPException(status_code=404, detail="Jenis dokumen tidak dikenal")
    ctx = _ctx(request)
    pool = await _pool()
    async with pool.acquire() as conn:
        r, nomor = await _render(conn, ctx, request, kind, doc_id)
    html = await run_in_threadpool(get_pdf_service().html_layar, r)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@router.get("/documents/{kind}/{doc_id}/pdf")
async def pdf_dokumen(request: Request, kind: str, doc_id: str):
    if kind not in JENIS:
        raise HTTPException(status_code=404, detail="Jenis dokumen tidak dikenal")
    ctx = _ctx(request)
    pool = await _pool()
    async with pool.acquire() as conn:
        r, nomor = await _render(conn, ctx, request, kind, doc_id)
    return _respons_pdf(await pdf_tercache(ctx["tenant_id"], r), nomor)


# ── bundel dokumen satu pesanan ────────────────────────────────────────────────────────────────────────────────

def _uang(v) -> str:
    return f"{Decimal(str(v or 0)):.2f}"


def _tgl(v) -> Optional[str]:
    return v.isoformat() if isinstance(v, date) else (str(v)[:10] if v else None)


async def susun_dokumen(conn, ctx: dict, so_id: UUID) -> dict:
    """Bundel per skema so-documents. Kelompok hanya yang berisi (Rekap selalu). Angka dari sumber yang sama dengan
    Rekap/Posisi (ringkasan_pesanan, terbayar_proforma, compute_ar_outstanding). Filter tenant eksplisit."""
    from ..services.proforma_terbayar import ringkasan_pesanan, terbayar_proforma
    from ..services.so_posisi import fakta_daftar
    from ..services.faktur_cetak import label_metode  # SATU format metode (riwayat faktur / Rekap / bundel)
    from ..utils.tanggal_tenant import tanggal_dokumen
    tid = ctx["tenant_id"]
    so = await conn.fetchrow(
        """SELECT id, order_number, customer_name, customer_id, status, total_amount, expected_ship_date, quote_id,
                  order_code, order_title
           FROM sales_orders WHERE id = $1 AND tenant_id = $2""", so_id, tid)
    if not so:
        raise HTTPException(status_code=404, detail="Sales order not found")
    sid = so["id"]
    r = (await ringkasan_pesanan(conn, tid, [sid]))[sid]
    total = Decimal(str(so["total_amount"] or 0))
    batal = so["status"] == "cancelled"
    sisa = NOL if batal else max(total - r["tertutup"], NOL)
    posisi = (await fakta_daftar(conn, tid, [so], await tanggal_dokumen(conn, tid)))[str(sid)]
    kelompok = [{"key": "rekap", "label": "Rekap", "docs": [{"kind": "rekap", "id": str(sid), "paper": "A4"}]}]

    if so["quote_id"]:
        q = await conn.fetchrow("""SELECT id, quote_number, quote_date, expiry_date, total_amount, sent_at FROM quotes
                                   WHERE id = $1 AND tenant_id = $2""", so["quote_id"], tid)
        if q:
            kelompok.append({"key": "pnw", "label": "Penawaran", "docs": [{
                "kind": "quotation", "id": str(q["id"]), "number": q["quote_number"], "date": _tgl(q["quote_date"]),
                "amount": _uang(q["total_amount"]), "paper": "A4",
                "valid_until": _tgl(q["expiry_date"]),  # P5 pesan Kirim "berlaku sampai {tgl}"
                "sent": {"state": "sent" if q["sent_at"] else "none",
                         "sent_at": q["sent_at"].isoformat() if q["sent_at"] else None, "viewed_at": None}}]})

    pros = await conn.fetch(
        """SELECT id, proforma_number, proforma_date, purpose, amount, status, issued_at FROM proformas
           WHERE tenant_id = $1 AND sales_order_id = $2 AND status <> 'draft'
           ORDER BY issued_at NULLS LAST, proforma_number""", tid, sid)
    pros = [p for p in pros if p["status"] != "cancelled" or batal]
    terbayar = await terbayar_proforma(conn, tid, [sid]) if pros else {}
    termin = [p["id"] for p in pros if p["purpose"] == "TERMIN" and p["status"] != "cancelled"]
    docs_pro = []
    for p in pros:
        lunas = (not batal and p["status"] == "issued"
                 and terbayar.get(p["id"], {}).get("paid", NOL) >= Decimal(str(p["amount"] or 0)))
        docs_pro.append({"kind": "proforma", "id": str(p["id"]), "number": p["proforma_number"],
                         "date": _tgl(p["proforma_date"]), "amount": _uang(p["amount"]),
                         "subtype": SUBTIPE.get(p["purpose"]),
                         "term": (termin.index(p["id"]) + 1) if p["id"] in termin else None,
                         "installments": None, "paid": lunas, "paper": "A4",
                         "sent": {"state": "none", "sent_at": None, "viewed_at": None}})
    if docs_pro:
        kelompok.append({"key": "pro", "label": "Tagihan", "docs": docs_pro})

    # Kwitansi: uang muka tertaut SO + penerimaan atas faktur SO + uang muka LEPAS yang diterapkan ke faktur SO
    # (himpunan yang sama dengan baris PEMBAYARAN Rekap; satu dokumen kwitansi per nomor).
    kw = {}
    for d in await conn.fetch(
        """SELECT cd.id, cd.deposit_number, cd.deposit_date, cd.amount, cd.payment_method, ba.bank_name
           FROM customer_deposits cd
           LEFT JOIN proformas p ON p.id = cd.proforma_id AND p.tenant_id = cd.tenant_id
           LEFT JOIN LATERAL (
               SELECT b.bank_name FROM bank_accounts b
               WHERE b.tenant_id = cd.tenant_id AND (b.id = cd.bank_account_id OR b.coa_id = cd.account_id)
               ORDER BY (b.id = cd.bank_account_id) DESC, b.is_default DESC LIMIT 1
           ) ba ON true
           WHERE cd.tenant_id = $1 AND cd.status <> 'void' AND cd.journal_id IS NOT NULL
             AND (COALESCE(cd.sales_order_id, p.sales_order_id) = $2
                  OR cd.id IN (SELECT cda.deposit_id FROM customer_deposit_applications cda
                               JOIN sales_invoices si ON si.id = cda.invoice_id AND si.tenant_id = $1
                               WHERE cda.tenant_id = $1 AND cda.status = 'active' AND si.sales_order_id = $2))""",
            tid, sid):
        kw[d["id"]] = {"kind": "receipt", "id": str(d["id"]), "number": d["deposit_number"],
                       "date": _tgl(d["deposit_date"]), "amount": _uang(d["amount"]),
                       "method": label_metode(d["payment_method"], d["bank_name"]),
                       "installment": None, "paper": "A5-landscape",
                       "sent": {"state": "none", "sent_at": None, "viewed_at": None}}
    for p in await conn.fetch(
        """SELECT rp.id, rp.payment_number, rp.payment_date, rp.payment_method, SUM(rpa.amount_applied) AS jumlah,
                  (SELECT b.bank_name FROM bank_accounts b
                   WHERE b.tenant_id = rp.tenant_id AND (b.id = rp.bank_account_id OR b.coa_id = rp.bank_account_id)
                   ORDER BY (b.id = rp.bank_account_id) DESC, b.is_default DESC LIMIT 1) AS bank_name
           FROM receive_payment_allocations rpa
           JOIN receive_payments rp ON rp.id = rpa.payment_id AND rp.tenant_id = $1 AND rp.status = 'posted'
           JOIN sales_invoices si ON si.id = rpa.invoice_id AND si.tenant_id = $1 AND si.sales_order_id = $2
           WHERE rpa.status = 'active' GROUP BY 1, 2, 3, 4, rp.tenant_id, rp.bank_account_id""", tid, sid):
        kw[p["id"]] = {"kind": "receipt", "id": str(p["id"]), "number": p["payment_number"],
                       "date": _tgl(p["payment_date"]), "amount": _uang(p["jumlah"]),
                       "method": label_metode(p["payment_method"], p["bank_name"]),
                       "installment": None, "paper": "A5-landscape",
                       "sent": {"state": "none", "sent_at": None, "viewed_at": None}}
    if kw:
        kelompok.append({"key": "kw", "label": "Pembayaran", "planned": None,
                         "docs": sorted(kw.values(), key=lambda x: (x["date"] or "", x["number"] or ""))})

    from ..services.so_kirim import _AKTIF
    ki = [{"kind": "delivery", "id": str(f["id"]), "number": f["fulfillment_number"], "date": _tgl(f["fulfillment_date"]),
           "paper": "A4", "sent": {"state": "none", "sent_at": None, "viewed_at": None}}
          for f in await conn.fetch(
              f"""SELECT f.id, f.fulfillment_number, f.fulfillment_date FROM invoice_fulfillments f
                  JOIN sales_invoices si ON si.id = f.invoice_id AND si.tenant_id = f.tenant_id
                  WHERE si.tenant_id = $1 AND si.sales_order_id = $2 AND {_AKTIF}
                  ORDER BY f.fulfillment_date, f.fulfillment_number""", tid, sid)]
    fak = await conn.fetch(
        """SELECT id, invoice_number, invoice_date, total_amount FROM sales_invoices
           WHERE tenant_id = $1 AND sales_order_id = $2 AND status NOT IN ('draft', 'void')
           ORDER BY invoice_date, invoice_number""", tid, sid)
    sisa_f = {}
    if fak:
        sisa_f = {x["invoice_id"]: Decimal(str(x["outstanding"])) for x in await conn.fetch(
            "SELECT invoice_id, outstanding FROM compute_ar_outstanding($1) WHERE invoice_id = ANY($2::uuid[])",
            tid, [f["id"] for f in fak])}
    ki += [{"kind": "invoice", "id": str(f["id"]), "number": f["invoice_number"], "date": _tgl(f["invoice_date"]),
            "amount": _uang(f["total_amount"]), "paid": sisa_f.get(f["id"], NOL) <= NOL, "paper": "A4",
            "sent": {"state": "none", "sent_at": None, "viewed_at": None}} for f in fak]
    if ki:
        kelompok.append({"key": "ki", "label": "Kirim & Faktur", "docs": ki})

    # P5: status kirim dari document_shares (tautan publik); penawaran tanpa tautan tetap memakai quotes.sent_at.
    from ..services.dokumen_bagikan import keadaan_kirim
    kirim = await keadaan_kirim(conn, tid, [(d["kind"], d["id"]) for gr in kelompok for d in gr["docs"]])
    for gr in kelompok:
        for d in gr["docs"]:
            k = kirim.get((d["kind"], d["id"]))
            if k:
                d["sent"] = k
            elif "sent" not in d:
                d["sent"] = {"state": "none", "sent_at": None, "viewed_at": None}
    kontak = await conn.fetchrow(
        """SELECT COALESCE(mobile_phone, phone, telepon) AS telepon, email FROM customers
           WHERE id = $1 AND tenant_id = $2""", so["customer_id"], tid) if so["customer_id"] else None

    # Dibuka pertama = proforma yang belum dibayar (01 §C), kalau tidak ada -> Rekap.
    bawaan = {"group": "rekap", "index": 0}
    for i, d in enumerate(docs_pro):
        if not d["paid"] and not batal:
            bawaan = {"group": "pro", "index": i}
            break
    from ..services.kode_order import muat_setelan as _setelan_kode
    _label_kode = (await _setelan_kode(conn, tid))["label"]
    return {
        "so": {"id": str(sid), "number": so["order_number"], "customer": so["customer_name"] or "",
               "order_code": so["order_code"], "order_title": so["order_title"],  # V359 kode order
               "order_code_label": _label_kode,
               "position_text": posisi["position_text"], "total": _uang(total),
               "paid": _uang(r["tertutup"] - r["credit_note"]), "balance": _uang(sisa),
               # P5: untuk wa.me / email di FE (tambahan; skema so tak membatasi medan tambahan)
               "customer_phone": (kontak["telepon"] if kontak else None) or None,
               "customer_email": (kontak["email"] if kontak else None) or None,
               # P5: "— {nama usaha}" di pesan Kirim. Aturan nama SAMA dgn kop (rekap_pesanan.muat_kop:
               # display_name, kalau kosong id) tanpa membaca berkas logo.
               "business_name": (await conn.fetchval(
                   'SELECT display_name FROM "Tenant" WHERE id = $1', tid)) or tid},
        "default": bawaan,
        "groups": kelompok,
    }


@router.get("/sales-orders/{order_id}/documents")
async def dokumen_pesanan(request: Request, order_id: str):
    ctx = _ctx(request)
    pool = await _pool()
    async with pool.acquire() as conn:
        return await susun_dokumen(conn, ctx, _uuid(order_id))


# ── P5: tautan publik + lacak (02-DATA-DAN-API §Kirim & lacak) ─────────────────────────────────────────────────

_DOKUMEN_TABEL = {
    # kind -> (SQL keberadaan + status, kolom nomor). Tenant SELALU eksplisit.
    "rekap": ("SELECT order_number AS nomor, status FROM sales_orders WHERE id = $1 AND tenant_id = $2", None),
    "quotation": ("SELECT quote_number AS nomor, status FROM quotes WHERE id = $1 AND tenant_id = $2", None),
    "proforma": ("SELECT proforma_number AS nomor, status FROM proformas WHERE id = $1 AND tenant_id = $2", "draft"),
    "delivery": ("SELECT fulfillment_number AS nomor, status FROM invoice_fulfillments WHERE id = $1 AND tenant_id = $2", None),
    "invoice": ("SELECT invoice_number AS nomor, status FROM sales_invoices WHERE id = $1 AND tenant_id = $2", "draft"),
}
LABEL_JENIS = {"rekap": "Rekap Pesanan", "quotation": "Penawaran", "proforma": "Proforma", "receipt": "Kwitansi",
               "delivery": "Surat Jalan", "invoice": "Faktur"}


async def _dokumen_ada(conn, tid: str, kind: str, uid: UUID) -> dict:
    """{nomor, status} atau 404; dokumen DRAF (proforma/faktur) belum boleh dibagikan -> 409."""
    if kind == "receipt":
        row = await conn.fetchrow("SELECT deposit_number AS nomor, status FROM customer_deposits WHERE id = $1 AND tenant_id = $2", uid, tid) \
            or await conn.fetchrow("SELECT payment_number AS nomor, status FROM receive_payments WHERE id = $1 AND tenant_id = $2", uid, tid)
        draf = None
    else:
        sql, draf = _DOKUMEN_TABEL[kind]
        row = await conn.fetchrow(sql, uid, tid)
    if not row:
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan")
    if draf and row["status"] == draf:
        raise HTTPException(status_code=409, detail={"code": "DOKUMEN_DRAF",
                                                     "message": "Dokumen draf belum bisa dibagikan. Terbitkan dulu."})
    return dict(row)


@router.post("/documents/{kind}/{doc_id}/share", status_code=201)
async def bagikan_dokumen(request: Request, kind: str, doc_id: str):
    """Buat tautan publik (satu kiriman = satu baris; kanal wa|email|link). Token hanya muncul di respons ini."""
    from ..services import dokumen_bagikan as DB
    if kind not in JENIS:
        raise HTTPException(status_code=404, detail="Jenis dokumen tidak dikenal")
    ctx = _ctx(request)
    try:
        badan = await request.json()
    except Exception:
        badan = {}
    channel = (badan or {}).get("channel", "link")
    if channel not in DB.KANAL:
        raise HTTPException(status_code=422, detail=f"channel harus salah satu dari {list(DB.KANAL)}")
    uid = _uuid(doc_id)
    pool = await _pool()
    async with pool.acquire() as conn:
        if kind == "receipt" and await conn.fetchval(
                "SELECT 1 FROM customer_deposits WHERE id = $1 AND tenant_id = $2", uid, ctx["tenant_id"]):
            await _wajib_izin(request, "E", "customer_deposit")
        dok = await _dokumen_ada(conn, ctx["tenant_id"], kind, uid)
        hasil = await DB.buat_tautan(conn, ctx["tenant_id"], kind, uid, channel, ctx["user_id"])
    hasil["document"] = {"kind": kind, "id": str(uid), "number": dok["nomor"]}
    return hasil


@router.get("/documents/{kind}/{doc_id}/shares")
async def daftar_tautan_dokumen(request: Request, kind: str, doc_id: str):
    from ..services import dokumen_bagikan as DB
    if kind not in JENIS:
        raise HTTPException(status_code=404, detail="Jenis dokumen tidak dikenal")
    ctx = _ctx(request)
    pool = await _pool()
    async with pool.acquire() as conn:
        return {"shares": await DB.daftar_tautan(conn, ctx["tenant_id"], kind, _uuid(doc_id))}


@router.post("/documents/{kind}/{doc_id}/shares/{share_id}/revoke")
async def cabut_tautan_dokumen(request: Request, kind: str, doc_id: str, share_id: str):
    from ..services import dokumen_bagikan as DB
    if kind not in JENIS:
        raise HTTPException(status_code=404, detail="Jenis dokumen tidak dikenal")
    ctx = _ctx(request)
    pool = await _pool()
    async with pool.acquire() as conn:
        ok = await DB.cabut(conn, ctx["tenant_id"], kind, _uuid(doc_id), _uuid(share_id), ctx["user_id"])
    if not ok:
        raise HTTPException(status_code=404, detail="Tautan tidak ditemukan atau sudah dicabut")
    return {"revoked": True}


_KEAMANAN_PUBLIK = {
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def _halaman_publik(judul: str, isi: str, status: int, nonce: str = "", skrip: str = "") -> HTMLResponse:
    from html import escape
    csp = ("default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; font-src 'self' https://milkyhoop.com; connect-src 'self'; "
           f"frame-src 'self'; script-src 'nonce-{nonce}'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    halaman = f"""<!DOCTYPE html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex, nofollow">
<title>{escape(judul)}</title>
<style>
  html, body {{ margin: 0; background: #F1EFEA; font-family: Inter, system-ui, sans-serif; color: #1a1a1a; }}
  .bilah {{ position: sticky; top: 0; z-index: 2; display: flex; align-items: center; justify-content: space-between;
           gap: 12px; padding: 12px 16px; background: #fff; border-bottom: 1px solid #E6E3DC; }}
  .bilah h1 {{ font-size: 15px; font-weight: 600; margin: 0; }}
  .bilah a {{ font-size: 14px; font-weight: 600; color: #fff; background: #1a1a1a; border-radius: 8px;
             padding: 8px 14px; text-decoration: none; white-space: nowrap; }}
  .kertas {{ display: flex; justify-content: center; padding: 16px 0 32px; overflow: hidden; }}
  /* flex: none -- tanpa ini item flex MENYUSUT ke lebar layar (390px) lalu DISKALA lagi -> kertas terpotong &
     kecil di HP (diukur Chromium 390px, 1 Okt). Lebar tata letak tetap = lebar kertas; skala yang mengecilkan. */
  iframe {{ flex: none; border: 0; background: #fff; box-shadow: 0 1px 4px rgba(0,0,0,.08); transform-origin: top center; }}
  .pesan {{ max-width: 520px; margin: 18vh auto 0; padding: 0 16px; text-align: center; font-size: 16px; line-height: 1.5; }}
</style></head><body>{isi}{skrip}</body></html>"""
    return HTMLResponse(halaman, status_code=status, headers={**_KEAMANAN_PUBLIK, "Content-Security-Policy": csp})


def _tak_berlaku(usaha: Optional[str], status: int) -> HTMLResponse:
    from html import escape
    teks = (f"Tautan tidak berlaku. Hubungi {escape(usaha)} untuk tautan baru." if usaha else "Tautan tidak berlaku.")
    return _halaman_publik("Tautan tidak berlaku", f'<div class="pesan">{teks}</div>', status)


async def _tautan_dari_token(conn, token: str):
    """-> (tautan|None, usaha|None). Token berbentuk salah / tak dikenal -> (None, None)."""
    import re
    from ..services import dokumen_bagikan as DB
    if not re.fullmatch(DB.POLA_TOKEN, token or ""):
        return None, None
    t = await DB.cari_tautan(conn, token)
    if not t:
        return None, None
    from ..services.rekap_pesanan import muat_kop
    return t, (await muat_kop(conn, t["tenant_id"]))["name"]


@router.get("/public/d/{token}", response_class=HTMLResponse)
async def halaman_dokumen_publik(request: Request, token: str):
    """Halaman publik /d/{token} (lewat nginx) -- HTML dokumen YANG SAMA + Unduh PDF, tanpa login. TIDAK mencatat
    "dibuka" (bot pratinjau tautan); pencatatan lewat suar JS ke /view."""
    import json
    import secrets as _secrets
    from html import escape
    from ..services import dokumen_bagikan as DB
    pool = await _pool()
    async with pool.acquire() as conn:
        t, usaha = await _tautan_dari_token(conn, token)
        if not t:
            return _tak_berlaku(None, 404)
        if not DB.berlaku(t):
            return _tak_berlaku(usaha, 410)
        r, nomor = await _render(conn, {"tenant_id": t["tenant_id"]}, request, t["kind"], str(t["doc_id"]), cek_izin=False)
    html_dok = await run_in_threadpool(get_pdf_service().html_layar, r)
    lebar, tinggi_hal = get_pdf_service().geometri(r)[:2]
    # Iframe ber-sandbox (tanpa allow-scripts / allow-same-origin): srcdoc SEASAL milkyhoop.com (localStorage token
    # aplikasi) -> injeksi apa pun di HTML dokumen tak bisa membaca token/cookie. Akibatnya skrip induk tak bisa
    # mengukur tinggi isi -> tinggi = jumlah halaman PDF x tinggi halaman (dari tata letak yang sama, server).
    n_hal = len((await run_in_threadpool(get_pdf_service().dokumen, r)).pages)
    judul = f"{LABEL_JENIS.get(t['kind'], 'Dokumen')} {nomor} dari {usaha}"
    nonce = _secrets.token_urlsafe(16)
    dasar = f"/api/public/d/{token}"
    isi = (f'<div class="bilah"><h1>{escape(judul)}</h1><a href="{dasar}/pdf">Unduh PDF</a></div>'
           f'<div class="kertas"><iframe id="dok" sandbox="" title="{escape(judul)}" width="{lebar:.0f}" '
           f'height="{tinggi_hal * n_hal:.0f}" srcdoc="{escape(html_dok, quote=True)}"></iframe></div>')
    skrip = f"""<script nonce="{nonce}">
(function () {{
  var f = document.getElementById('dok'), w = {lebar:.0f}, h = {tinggi_hal * n_hal:.0f};
  function pas() {{
    var s = Math.min(1, (window.innerWidth - 16) / w);
    f.style.transform = 'scale(' + s + ')';
    f.parentNode.style.height = (h * s + 48) + 'px';
  }}
  pas(); window.addEventListener('resize', pas);
  var hd = {{}}; try {{ var t = localStorage.getItem('access_token'); if (t) hd['Authorization'] = 'Bearer ' + t; }} catch (e) {{}}
  fetch({json.dumps(dasar + "/view")}, {{ method: 'POST', headers: hd, keepalive: true, credentials: 'omit' }}).catch(function () {{}});
}})();
</script>"""
    return _halaman_publik(judul, isi, 200, nonce, skrip)


@router.get("/public/d/{token}/pdf")
async def pdf_dokumen_publik(request: Request, token: str):
    from ..services import dokumen_bagikan as DB
    from ..utils.content_disposition import pdf_content_disposition
    pool = await _pool()
    async with pool.acquire() as conn:
        t, usaha = await _tautan_dari_token(conn, token)
        if not t:
            return _tak_berlaku(None, 404)
        if not DB.berlaku(t):
            return _tak_berlaku(usaha, 410)
        r, nomor = await _render(conn, {"tenant_id": t["tenant_id"]}, request, t["kind"], str(t["doc_id"]), cek_izin=False)
    pdf = await pdf_tercache(t["tenant_id"], r)
    return StreamingResponse(BytesIO(pdf), media_type="application/pdf",
                             headers={**_KEAMANAN_PUBLIK, "Content-Disposition": pdf_content_disposition(nomor or "dokumen", "attachment")})


@router.post("/public/d/{token}/view")
async def suar_dibuka(request: Request, token: str):
    """Suar "dibuka" dari JS halaman publik. Pengunjung ber-token aplikasi SAH dari tenant yang sama = INTERNAL ->
    tidak dihitung (spek 05). Jawaban seragam {ok:true} (tanpa oracle keadaan)."""
    from ..services import dokumen_bagikan as DB
    pool = await _pool()
    async with pool.acquire() as conn:
        t, _ = await _tautan_dari_token(conn, token)
        if t and DB.berlaku(t) and DB.tenant_dari_jwt(request.headers.get("authorization")) != t["tenant_id"]:
            await DB.catat_dibuka(conn, t)
    return JSONResponse({"ok": True}, headers=_KEAMANAN_PUBLIK)
