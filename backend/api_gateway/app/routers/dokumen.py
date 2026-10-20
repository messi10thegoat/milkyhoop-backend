"""Dokumen SO (P3 SO-dokumen, 02-DATA-DAN-API §Endpoint/§Render, 1 Okt 2026).

  GET /api/sales-orders/{order_id}/documents   bundel dokumen satu pesanan (skema contracts/so-documents.schema.json)
  GET /api/documents/{kind}/{id}/html           HTML panel -- string HTML YANG SAMA dengan PDF (pdf_service.html_layar)
  GET /api/documents/{kind}/{id}/pdf            PDF dari Render yang sama, di-cache per versi (Redis)
  GET /api/documents/receipts/pdf?so={id}       semua kwitansi pesanan, satu PDF A5 (satu per halaman)
  GET /api/public/fonts/{nama}                  font Inter untuk HTML layar (publik: unduhan font CSS tak membawa JWT)

SATU sumber: konteks dari PEMUAT yang sama dengan rute /pdf lama (muat_pdf_*), render dari pdf_service.render_*.
kind: rekap | quotation | proforma | receipt | delivery | invoice | credit_note (U5-B, 4 Okt). Izin per kind dipetakan di permission_middleware;
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

JENIS = ("rekap", "quotation", "proforma", "receipt", "delivery", "invoice", "credit_note")
FONT_SAH = {f"Inter-{b}.ttf" for b in ("Regular", "Medium", "SemiBold", "Bold", "Italic")}
VERSI_RENDER = "p3-1"  # naikkan bila mesin/aturan render berubah tanpa mengubah HTML
NOL = Decimal("0")
SUBTIPE = {"DP": "dp", "TERMIN": "termin", "PELUNASAN": "penuh"}


def _ctx(request: Request) -> dict:
    u = getattr(request.state, "user", None)
    if not u or not u.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Perlu masuk (login).")
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
    if jenis == "credit_note":
        from ..services.nota_kredit_dokumen import muat_pdf_nota_kredit
        m = await muat_pdf_nota_kredit(conn, ctx, uid)
        return (ps.render_credit_note(m["credit_note_data"], m["tenant_info"]),
                m["credit_note_data"]["credit_note_number"])
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
        raise HTTPException(status_code=404, detail="Tidak ditemukan.")
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


async def susun_dokumen(conn, ctx: dict, so_id: UUID, sertakan_nk: bool = False) -> dict:
    """Bundel per skema so-documents. Kelompok hanya yang berisi (Rekap selalu). Angka dari sumber yang sama dengan
    Rekap/Posisi (ringkasan_pesanan, terbayar_proforma, compute_ar_outstanding). Filter tenant eksplisit."""
    from ..services.proforma_terbayar import ringkasan_pesanan
    from ..services.so_posisi import fakta_daftar
    from ..utils.tanggal_tenant import tanggal_dokumen
    tid = ctx["tenant_id"]
    so = await conn.fetchrow(
        """SELECT id, order_number, customer_name, customer_id, status, total_amount, expected_ship_date, quote_id,
                  order_code, order_title, fase_lokasi, fase_gudang_id
           FROM sales_orders WHERE id = $1 AND tenant_id = $2""", so_id, tid)
    if not so:
        raise HTTPException(status_code=404, detail="Pesanan penjualan tidak ditemukan.")
    sid = so["id"]
    r = (await ringkasan_pesanan(conn, tid, [sid]))[sid]
    total = Decimal(str(so["total_amount"] or 0))
    batal = so["status"] == "cancelled"
    sisa = NOL if batal else max(total - r["tertutup"], NOL)
    posisi = (await fakta_daftar(conn, tid, [so], await tanggal_dokumen(conn, tid)))[str(sid)]
    kelompok = [{"key": "rekap", "label": "Rekap", "docs": [{"kind": "rekap", "id": str(sid), "paper": "A4"}]}]

    # S1-U2 (20 Okt 2026): bentuk dokumen dari pembentuk per-kind BERSAMA (services/dokumen_bundel); di sini hanya PEMILIHAN id
    # milik pesanan ini. Medan & urutan identik dengan sebelum ekstraksi (harness uji-nyata-dokumen-faktur: SO lama == baru).
    from ..services import dokumen_bundel as DB
    if so["quote_id"]:
        q = await DB.penawaran(conn, tid, so["quote_id"])
        if q:
            kelompok.append({"key": "pnw", "label": "Penawaran", "docs": [q]})

    docs_pro = await DB.proforma_so(conn, tid, sid, batal)
    if docs_pro:
        kelompok.append({"key": "pro", "label": "Tagihan", "docs": docs_pro})

    # Kwitansi: uang muka tertaut SO + penerimaan atas faktur SO + uang muka LEPAS yang diterapkan ke faktur SO
    # (himpunan yang sama dengan baris PEMBAYARAN Rekap; satu dokumen kwitansi per nomor).
    dep_ids = [r["id"] for r in await conn.fetch(
        """SELECT cd.id FROM customer_deposits cd
           LEFT JOIN proformas p ON p.id = cd.proforma_id AND p.tenant_id = cd.tenant_id
           WHERE cd.tenant_id = $1 AND cd.status <> 'void' AND cd.journal_id IS NOT NULL
             AND (COALESCE(cd.sales_order_id, p.sales_order_id) = $2
                  OR cd.id IN (SELECT cda.deposit_id FROM customer_deposit_applications cda
                               JOIN sales_invoices si ON si.id = cda.invoice_id AND si.tenant_id = $1
                               WHERE cda.tenant_id = $1 AND cda.status = 'active' AND si.sales_order_id = $2))""",
        tid, sid)]
    fak_so = [r["id"] for r in await conn.fetch(
        "SELECT id FROM sales_invoices WHERE tenant_id = $1 AND sales_order_id = $2", tid, sid)]
    kw = {**await DB.kwitansi_uang_muka(conn, tid, dep_ids), **await DB.kwitansi_penerimaan(conn, tid, fak_so)}
    if kw:
        kelompok.append({"key": "kw", "label": "Pembayaran", "planned": None, "docs": DB.urut_kwitansi(kw)})

    ki = await DB.surat_jalan(conn, tid, [r["id"] for r in await conn.fetch(
        """SELECT f.id FROM invoice_fulfillments f
           LEFT JOIN sales_invoices si ON si.id = f.invoice_id AND si.tenant_id = f.tenant_id
           WHERE f.tenant_id = $1 AND COALESCE(f.sales_order_id, si.sales_order_id) = $2""", tid, sid)])
    ki += await DB.faktur(conn, tid, [r["id"] for r in await conn.fetch(
        """SELECT id FROM sales_invoices WHERE tenant_id = $1 AND sales_order_id = $2
           AND status NOT IN ('draft', 'void')""", tid, sid)])
    if ki:
        kelompok.append({"key": "ki", "label": "Kirim & Faktur", "docs": ki})
    if sertakan_nk:
        # Nota kredit atas faktur SO ini (lewat faktur asal); draf & void tak masuk panel (sama dengan faktur).
        nk = await DB.nota_kredit(conn, tid, [r["id"] for r in await conn.fetch(
            """SELECT cn.id FROM credit_notes cn
               JOIN sales_invoices si ON si.id = cn.original_invoice_id AND si.tenant_id = cn.tenant_id
               WHERE cn.tenant_id = $1 AND si.sales_order_id = $2 AND cn.status NOT IN ('draft', 'void')""", tid, sid)])
        if nk:
            kelompok.append({"key": "nk", "label": "Nota Kredit", "docs": nk})

    # P5 status kirim + DOKUMEN-BATAL-FIELD + status per dokumen: satu penyelesai bersama.
    await DB.rampungkan(conn, tid, kelompok)
    kontak = await _kontak(conn, tid, so["customer_id"])

    # Dibuka pertama = proforma yang belum dibayar (01 §C), kalau tidak ada -> Rekap.
    bawaan = {"group": "rekap", "index": 0}
    for i, d in enumerate(docs_pro):
        if not d["paid"] and not batal:
            bawaan = {"group": "pro", "index": i}
            break
    from ..services.kode_order import muat_setelan as _setelan_kode
    _st_kode = await _setelan_kode(conn, tid)
    _label_kode, _label_judul = _st_kode["label"], _st_kode["title_label"]
    return {
        "so": {"id": str(sid), "number": so["order_number"], "customer": so["customer_name"] or "",
               "order_code": so["order_code"], "order_title": so["order_title"],  # V359 kode order
               "order_code_label": _label_kode, "order_title_label": _label_judul,
               "position_text": posisi["position_text"], "total": _uang(total),
               "paid": _uang(r["tertutup"] - r["credit_note"]), "balance": _uang(sisa),
               # P5: untuk wa.me / email di FE (tambahan; skema so tak membatasi medan tambahan)
               "customer_phone": (kontak["telepon"] if kontak else None) or None,
               "customer_email": (kontak["email"] if kontak else None) or None,
               # P5: "— {nama usaha}" di pesan Kirim. Aturan nama SAMA dgn kop (rekap_pesanan.muat_kop:
               # display_name, kalau kosong id) tanpa membaca berkas logo.
               "business_name": await _nama_usaha(conn, tid)},
        "default": bawaan,
        "groups": kelompok,
    }


async def _kontak(conn, tid: str, customer_id) -> Optional[dict]:
    """P5: telepon/email pelanggan untuk wa.me / email di FE."""
    if not customer_id:
        return None
    return await conn.fetchrow(
        """SELECT COALESCE(mobile_phone, phone, telepon) AS telepon, email FROM customers
           WHERE id = $1 AND tenant_id = $2""", customer_id, tid)


async def _nama_usaha(conn, tid: str) -> str:
    # P5: "— {nama usaha}" di pesan Kirim. Aturan nama SAMA dgn kop (rekap_pesanan.muat_kop: display_name, kalau kosong id).
    return (await conn.fetchval('SELECT display_name FROM "Tenant" WHERE id = $1', tid)) or tid


async def susun_dokumen_faktur(conn, ctx: dict, invoice_id: UUID) -> dict:
    """S1-U2 (20 Okt 2026, MASTER): bundel panel berjangkar FAKTUR, skema dokumen SAMA dengan bundel SO (pembentuk bersama
    services/dokumen_bundel). Kelompok (urutan SO): rekap + pnw (bila ber-SO) · pro = proforma ASAL (milik uang muka yang
    diterapkan ke faktur ini) · kw = kwitansi faktur (himpunan SAMA dengan documents[] daftar, faktur_daftar.SQL_KWITANSI_FAKTUR;
    amount = bagian yang diterapkan ke faktur INI) · ki = surat jalan faktur + faktur sendiri (selalu, juga draf/batal) ·
    nk = nota kredit asal. Kepala `anchor` (bukan `so`): position_text/paid/balance = detail faktur (sisa jurnal)."""
    from ..services import dokumen_bundel as DB
    from ..services.faktur_daftar import posisi_faktur, sisa_baris
    from .sales_invoices import _t43_dibayar_dan_sisa
    tid = ctx["tenant_id"]
    si = await conn.fetchrow(
        """SELECT id, invoice_number, customer_name, customer_id, status, total_amount, sales_order_id
           FROM sales_invoices WHERE id = $1 AND tenant_id = $2""", invoice_id, tid)
    if not si:
        raise HTTPException(status_code=404, detail="Faktur tidak ditemukan.")
    so = await conn.fetchrow(
        """SELECT id, order_number, status, quote_id, order_code, order_title FROM sales_orders
           WHERE id = $1 AND tenant_id = $2""", si["sales_order_id"], tid) if si["sales_order_id"] else None
    kelompok = []
    if so:
        kelompok.append({"key": "rekap", "label": "Rekap", "docs": [DB.rekap(so["id"])]})
        if so["quote_id"]:
            q = await DB.penawaran(conn, tid, so["quote_id"])
            if q:
                kelompok.append({"key": "pnw", "label": "Penawaran", "docs": [q]})
    jumlah = {r["deposit_id"]: r["jumlah"] for r in await conn.fetch(
        """SELECT deposit_id, SUM(amount_applied) AS jumlah FROM customer_deposit_applications
           WHERE tenant_id = $1 AND invoice_id = $2 AND status = 'active' GROUP BY 1""", tid, si["id"])}
    kw_dp = await DB.kwitansi_uang_muka(conn, tid, list(jumlah), jumlah)
    if so and kw_dp:
        asal = {str(r["proforma_id"]) for r in await conn.fetch(
            """SELECT proforma_id FROM customer_deposits WHERE tenant_id = $1 AND id = ANY($2::uuid[])
               AND proforma_id IS NOT NULL""", tid, list(kw_dp))}
        pro = await DB.proforma_so(conn, tid, so["id"], so["status"] == "cancelled", asal) if asal else []
        if pro:
            kelompok.append({"key": "pro", "label": "Tagihan", "docs": pro})
    kw = {**kw_dp, **await DB.kwitansi_penerimaan(conn, tid, [si["id"]])}
    if kw:
        kelompok.append({"key": "kw", "label": "Pembayaran", "planned": None, "docs": DB.urut_kwitansi(kw)})
    ki = await DB.surat_jalan(conn, tid, [r["id"] for r in await conn.fetch(
        "SELECT id FROM invoice_fulfillments WHERE tenant_id = $1 AND invoice_id = $2", tid, si["id"])])
    ki += await DB.faktur(conn, tid, [si["id"]])
    kelompok.append({"key": "ki", "label": "Kirim & Faktur", "docs": ki})
    nk = await DB.nota_kredit(conn, tid, [r["id"] for r in await conn.fetch(
        """SELECT id FROM credit_notes WHERE tenant_id = $1 AND original_invoice_id = $2
           AND status NOT IN ('draft', 'void')""", tid, si["id"])])
    if nk:
        kelompok.append({"key": "nk", "label": "Nota Kredit", "docs": nk})
    await DB.rampungkan(conn, tid, kelompok)

    ar = await conn.fetchval(
        "SELECT outstanding FROM compute_ar_outstanding($1) WHERE invoice_id = $2", tid, si["id"])
    dibayar, sisa = _t43_dibayar_dan_sisa(si["status"], si["total_amount"], ar)
    kontak = await _kontak(conn, tid, si["customer_id"])
    from ..services.kode_order import muat_setelan as _setelan_kode
    _st_kode = await _setelan_kode(conn, tid)
    i_ki = next(i for i, g in enumerate(kelompok) if g["key"] == "ki")
    return {
        "anchor": {"kind": "invoice", "id": str(si["id"]), "number": si["invoice_number"],
                   "customer": si["customer_name"] or "",
                   "position_text": posisi_faktur(si["status"], si["total_amount"], sisa_baris(si["status"], ar)),
                   "total": DB._uang(si["total_amount"]), "paid": DB._uang(dibayar), "balance": DB._uang(sisa),
                   "sales_order_id": str(so["id"]) if so else None, "sales_order_number": so["order_number"] if so else None,
                   "order_code": so["order_code"] if so else None, "order_title": so["order_title"] if so else None,
                   "order_code_label": _st_kode["label"], "order_title_label": _st_kode["title_label"],
                   "customer_phone": (kontak["telepon"] if kontak else None) or None,
                   "customer_email": (kontak["email"] if kontak else None) or None,
                   "business_name": await _nama_usaha(conn, tid)},
        # dibuka pertama = faktur ini (dokumen terakhir kelompok ki)
        "default": {"group": "ki", "index": len(kelompok[i_ki]["docs"]) - 1},
        "groups": kelompok,
    }


async def susun_dokumen_penerimaan(conn, ctx: dict, payment_id: UUID) -> dict:
    """PENJUALAN-SERAGAM (20 Okt 2026, MASTER): bundel panel berjangkar PENERIMAAN PEMBAYARAN, skema dokumen SAMA dengan bundel SO/faktur
    (pembentuk bersama services/dokumen_bundel; PEMILIHAN id di services/penerimaan_daftar.kelompok_untuk -- fungsi YANG SAMA dengan
    kolom documents[] daftar). Kelompok: rekap + pnw (bila semua faktur teralokasi satu SO) · kw = kwitansi penerimaan ITU SENDIRI
    (posted/voided; amount = total_amount) · ki = surat jalan + faktur teralokasi · nk = nota kredit asal. Kepala `anchor` (bukan `so`):
    position_text/paid/balance = teks alokasi / Σ alokasi aktif / belum dialokasikan (turunan alokasi, bukan cache wrapper)."""
    from ..services import dokumen_bundel as DB
    from ..services import penerimaan_daftar as PD
    tid = ctx["tenant_id"]
    rp = await conn.fetchrow(
        """SELECT id, payment_number, customer_name, customer_id, status, total_amount FROM receive_payments
           WHERE id = $1 AND tenant_id = $2""", payment_id, tid)
    if not rp:
        raise HTTPException(status_code=404, detail="Penerimaan tidak ditemukan.")
    kelompok = (await PD.kelompok_untuk(conn, tid, [dict(rp)]))[rp["id"]]
    await DB.rampungkan(conn, tid, kelompok)
    dialokasi = await conn.fetchval(f"SELECT {PD.sql_dialokasi('rp')} FROM receive_payments rp WHERE rp.id = $1 AND rp.tenant_id = $2",
                                    rp["id"], tid)
    f = PD.alokasi_fakta(True, rp["status"], rp["total_amount"], dialokasi)
    so = None
    rekap = next((g for g in kelompok if g["key"] == "rekap"), None)
    if rekap:
        so = await conn.fetchrow("SELECT id, order_number, order_code, order_title FROM sales_orders WHERE id = $1 AND tenant_id = $2",
                                 UUID(rekap["docs"][0]["id"]), tid)
    kontak = await _kontak(conn, tid, rp["customer_id"])
    from ..services.kode_order import muat_setelan as _setelan_kode
    _st_kode = await _setelan_kode(conn, tid)
    i_kw = next((i for i, g in enumerate(kelompok) if g["key"] == "kw"), None)
    return {
        "anchor": {"kind": "receipt", "id": str(rp["id"]), "number": rp["payment_number"], "customer": rp["customer_name"] or "",
                   "position_text": f["alokasi_teks"], "total": DB._uang(rp["total_amount"]), "paid": DB._uang(dialokasi),
                   "balance": DB._uang(f["belum_dialokasikan"]),
                   "sales_order_id": str(so["id"]) if so else None, "sales_order_number": so["order_number"] if so else None,
                   "order_code": so["order_code"] if so else None, "order_title": so["order_title"] if so else None,
                   "order_code_label": _st_kode["label"], "order_title_label": _st_kode["title_label"],
                   "customer_phone": (kontak["telepon"] if kontak else None) or None,
                   "customer_email": (kontak["email"] if kontak else None) or None,
                   "business_name": await _nama_usaha(conn, tid)},
        # dibuka pertama = kwitansi penerimaan ini (jangkar tampil sebagai dirinya); draf tak punya kwitansi -> tanpa default
        "default": {"group": "kw", "index": 0} if i_kw is not None else None,
        "groups": kelompok,
    }


async def susun_dokumen_pengiriman(conn, ctx: dict, delivery_id: UUID) -> dict:
    """PENJUALAN-SERAGAM (20 Okt 2026, MASTER): bundel panel berjangkar SURAT JALAN, skema dokumen SAMA dengan bundel SO/faktur (pembentuk
    bersama services/dokumen_bundel; PEMILIHAN id di services/pengiriman_daftar.kelompok_untuk -- fungsi YANG SAMA dengan kolom documents[]
    daftar). Kelompok: rekap + pnw (bila ber-SO) · ki = Surat Jalan ITU SENDIRI (juga voided -> batal) + faktur asal. Kepala `anchor`
    (kind=delivery): position_text = teks keadaan serah; Pengiriman tanpa kolom uang -> total/paid/balance = null."""
    from ..services import dokumen_bundel as DB
    from ..services import pengiriman_daftar as PG
    from ..services import pengiriman_serah as SERAH
    tid = ctx["tenant_id"]
    r = await conn.fetchrow(
        """SELECT f.id, f.fulfillment_number, f.status, f.invoice_id,
                  COALESCE(f.sales_order_id, si.sales_order_id) AS sales_order_id,
                  COALESCE(si.customer_id, so.customer_id) AS customer_id, c.nama AS customer_name
           FROM invoice_fulfillments f
           LEFT JOIN sales_invoices si ON si.id = f.invoice_id
           LEFT JOIN sales_orders so ON so.id = COALESCE(f.sales_order_id, si.sales_order_id) AND so.tenant_id = f.tenant_id
           LEFT JOIN customers c ON c.id = COALESCE(si.customer_id, so.customer_id)
           WHERE f.id = $1 AND f.tenant_id = $2""", delivery_id, tid)
    if not r:
        raise HTTPException(status_code=404, detail="Surat Jalan tidak ditemukan.")
    kelompok = (await PG.kelompok_untuk(conn, tid, [dict(r)]))[r["id"]]
    await DB.rampungkan(conn, tid, kelompok)
    aktif = await SERAH.aktif(conn, tid)
    fs = await PG.fakta_serah(conn, tid, [r["id"]]) if aktif else {}
    fakta = PG.serah_fakta(aktif, r["status"], *fs.get(r["id"], (None, None, 0)))
    so = None
    if any(g["key"] == "rekap" for g in kelompok):
        so = await conn.fetchrow("SELECT id, order_number, order_code, order_title FROM sales_orders WHERE id = $1 AND tenant_id = $2",
                                 r["sales_order_id"], tid)
    kontak = await _kontak(conn, tid, r["customer_id"])
    from ..services.kode_order import muat_setelan as _setelan_kode
    _st_kode = await _setelan_kode(conn, tid)
    i_ki = next((i for i, g in enumerate(kelompok) if g["key"] == "ki"), None)
    return {
        "anchor": {"kind": "delivery", "id": str(r["id"]), "number": r["fulfillment_number"], "customer": r["customer_name"] or "",
                   "position_text": fakta["serah_teks"], "total": None, "paid": None, "balance": None,
                   "sales_order_id": str(so["id"]) if so else None, "sales_order_number": so["order_number"] if so else None,
                   "order_code": so["order_code"] if so else None, "order_title": so["order_title"] if so else None,
                   "order_code_label": _st_kode["label"], "order_title_label": _st_kode["title_label"],
                   "customer_phone": (kontak["telepon"] if kontak else None) or None,
                   "customer_email": (kontak["email"] if kontak else None) or None,
                   "business_name": await _nama_usaha(conn, tid)},
        # dibuka pertama = Surat Jalan ini (dokumen pertama kelompok ki: jangkar tampil sebagai dirinya)
        "default": {"group": "ki", "index": 0} if i_ki is not None else None,
        "groups": kelompok,
    }


async def susun_dokumen_proforma(conn, ctx: dict, proforma_id: UUID) -> dict:
    """PENJUALAN-SERAGAM (21 Okt 2026, MASTER): bundel panel berjangkar PROFORMA, skema dokumen SAMA dengan bundel SO/faktur (pembentuk bersama
    services/dokumen_bundel; PEMILIHAN id di services/proforma_daftar.kelompok_untuk -- fungsi YANG SAMA dgn kolom documents[] daftar).
    Kelompok: rekap + pnw (penawaran SO) · pro = proforma ITU SENDIRI (draf -> "draf", batal -> "batal") · kw = kwitansi uang muka bertaut.
    Kepala `anchor` (kind=proforma): position_text = keadaan bayar; total = nominal, paid = terbayar, balance = sisa (terbit saja)."""
    from ..services import dokumen_bundel as DB
    from ..services import proforma_daftar as PD
    from ..services.proforma_terbayar import terbayar_proforma
    from ..services.proforma_aksi import proforma_lunas
    tid = ctx["tenant_id"]
    p = await conn.fetchrow(
        """SELECT p.id, p.proforma_number, p.status, p.amount, p.sales_order_id, p.customer_id, p.customer_name, so.status AS so_status,
                  so.order_number, so.order_code, so.order_title
           FROM proformas p LEFT JOIN sales_orders so ON so.id = p.sales_order_id AND so.tenant_id = p.tenant_id
           WHERE p.id = $1 AND p.tenant_id = $2""", proforma_id, tid)
    if not p:
        raise HTTPException(status_code=404, detail="Proforma tidak ditemukan.")
    kelompok = (await PD.kelompok_untuk(conn, tid, [dict(p)]))[p["id"]]
    await DB.rampungkan(conn, tid, kelompok)
    tb = await terbayar_proforma(conn, tid, [p["sales_order_id"]])
    paid = Decimal(str(tb[p["id"]]["paid"])) if p["id"] in tb else Decimal("0")
    lunas = proforma_lunas(p["amount"], paid)
    terbit = p["status"] == "issued"
    teks = {"draft": "Draf", "cancelled": "Dibatalkan", "expired": "Kedaluwarsa"}.get(p["status"]) or (
        "Lunas" if lunas else ("Dibayar sebagian" if paid > 0 else "Menunggu bayar"))
    kontak = await _kontak(conn, tid, p["customer_id"])
    from ..services.kode_order import muat_setelan as _setelan_kode
    _st_kode = await _setelan_kode(conn, tid)
    i_pro = next((i for i, g in enumerate(kelompok) if g["key"] == "pro"), None)
    return {
        "anchor": {"kind": "proforma", "id": str(p["id"]), "number": p["proforma_number"], "customer": p["customer_name"] or "",
                   "position_text": teks, "total": DB._uang(p["amount"]), "paid": DB._uang(paid) if terbit else None,
                   "balance": DB._uang(max(Decimal(str(p["amount"] or 0)) - paid, Decimal("0"))) if terbit else None,
                   "sales_order_id": str(p["sales_order_id"]), "sales_order_number": p["order_number"],
                   "order_code": p["order_code"], "order_title": p["order_title"],
                   "order_code_label": _st_kode["label"], "order_title_label": _st_kode["title_label"],
                   "customer_phone": (kontak["telepon"] if kontak else None) or None,
                   "customer_email": (kontak["email"] if kontak else None) or None,
                   "business_name": await _nama_usaha(conn, tid)},
        "default": {"group": "pro", "index": 0} if i_pro is not None else None,
        "groups": kelompok,
    }


@router.get("/sales-invoices/{invoice_id}/documents")
async def dokumen_faktur(request: Request, invoice_id: str):
    ctx = _ctx(request)
    pool = await _pool()
    async with pool.acquire() as conn:
        return await susun_dokumen_faktur(conn, ctx, _uuid(invoice_id))


@router.get("/proformas/{proforma_id}/documents")
async def dokumen_proforma(request: Request, proforma_id: str):
    ctx = _ctx(request)
    pool = await _pool()
    async with pool.acquire() as conn:
        return await susun_dokumen_proforma(conn, ctx, _uuid(proforma_id))


@router.get("/deliveries/{delivery_id}/documents")
async def dokumen_pengiriman(request: Request, delivery_id: str):
    ctx = _ctx(request)
    pool = await _pool()
    async with pool.acquire() as conn:
        return await susun_dokumen_pengiriman(conn, ctx, _uuid(delivery_id))


@router.get("/receive-payments/{payment_id}/documents")
async def dokumen_penerimaan(request: Request, payment_id: str):
    ctx = _ctx(request)
    pool = await _pool()
    async with pool.acquire() as conn:
        return await susun_dokumen_penerimaan(conn, ctx, _uuid(payment_id))


async def susun_dokumen_uang_muka(conn, ctx: dict, deposit_id: UUID) -> dict:
    """S2-UM b (20 Okt 2026, MASTER): bundel panel berjangkar UANG MUKA, skema dokumen SAMA (pembentuk bersama services/dokumen_bundel;
    PEMILIHAN id di services/uang_muka_daftar.kelompok_untuk -- fungsi YANG SAMA dengan documents[] daftar). Jangkar = kwitansi uang muka
    ITU SENDIRI (selalu, juga draf/void). Kepala `anchor`: position_text = status_text; total = nominal; balance = sisa JURNAL
    (compute_deposit_remaining_many; draf = null); paid = total - balance."""
    from ..services import dokumen_bundel as DB
    from ..services import uang_muka_daftar as UMD
    from ..services.status_uang_muka import status_detail_dp
    from .customer_deposits import compute_deposit_remaining_many
    tid = ctx["tenant_id"]
    d = await conn.fetchrow(
        """SELECT cd.id, cd.deposit_number, cd.customer_name, cd.customer_id, cd.status, cd.amount, cd.amount_applied,
                  cd.amount_refunded, COALESCE(cd.sales_order_id, p.sales_order_id) AS so_id
           FROM customer_deposits cd LEFT JOIN proformas p ON p.id = cd.proforma_id AND p.tenant_id = cd.tenant_id
           WHERE cd.id = $1 AND cd.tenant_id = $2""", deposit_id, tid)
    if not d:
        raise HTTPException(status_code=404, detail="Uang muka tidak ditemukan.")
    kelompok = (await UMD.kelompok_untuk(conn, tid, [dict(d)]))[d["id"]]
    await DB.rampungkan(conn, tid, kelompok)
    sisa = None if d["status"] == "draft" else (await compute_deposit_remaining_many(conn, tid, [d["id"]]))[str(d["id"])]
    return await _kepala_jangkar(conn, tid, kelompok, "kw", "receipt", d["id"], d["deposit_number"], d["customer_name"],
                                 d["customer_id"], d["so_id"],
                                 UMD.status_text_um(status_detail_dp(d["status"], d["amount_applied"], d["amount_refunded"])),
                                 d["amount"], sisa)


async def susun_dokumen_nota_kredit(conn, ctx: dict, credit_note_id: UUID) -> dict:
    """S2-NK b (20 Okt 2026, MASTER): bundel panel berjangkar NOTA KREDIT (pola uang muka). Jangkar = NK ITU SENDIRI (selalu, juga
    draf/void). Kepala `anchor`: position_text = status_text NK (bertaut posted = Terpakai); total = total_amount; balance = sisa
    JURNAL NK (nota_kredit_daftar; draf = null); paid = total - balance."""
    from ..services import dokumen_bundel as DB
    from ..services import nota_kredit_daftar as NKD
    from .credit_notes import status_detail_nk
    tid = ctx["tenant_id"]
    c = await conn.fetchrow(
        """SELECT cn.id, cn.credit_note_number, cn.customer_name, cn.customer_id, cn.status, cn.total_amount, cn.amount_applied,
                  cn.amount_refunded, cn.original_invoice_id, si.sales_order_id AS so_id
           FROM credit_notes cn LEFT JOIN sales_invoices si ON si.id = cn.original_invoice_id AND si.tenant_id = cn.tenant_id
           WHERE cn.id = $1 AND cn.tenant_id = $2""", credit_note_id, tid)
    if not c:
        raise HTTPException(status_code=404, detail="Nota kredit tidak ditemukan.")
    kelompok = (await NKD.kelompok_untuk(conn, tid, [dict(c)]))[c["id"]]
    await DB.rampungkan(conn, tid, kelompok)
    sisa = None if c["status"] == "draft" else (await NKD.sisa_nk_jurnal_many(conn, tid, [c["id"]]))[str(c["id"])]
    return await _kepala_jangkar(conn, tid, kelompok, "nk", "credit_note", c["id"], c["credit_note_number"], c["customer_name"],
                                 c["customer_id"], c["so_id"],
                                 NKD.status_text_nk(c["status"], status_detail_nk(c["status"], c["amount_applied"], c["amount_refunded"]),
                                                    c["original_invoice_id"]),
                                 c["total_amount"], sisa)


async def _kepala_jangkar(conn, tid, kelompok, grup, kind, did, nomor, pelanggan, customer_id, so_id, posisi, total, sisa) -> dict:
    """Kepala `anchor` + default (dokumen jangkar di kelompok `grup`) untuk bundel UM/NK (S2 b). sisa None (draf) -> paid/balance null."""
    from ..services import dokumen_bundel as DB
    so = await conn.fetchrow("SELECT id, order_number, order_code, order_title FROM sales_orders WHERE id = $1 AND tenant_id = $2",
                             so_id, tid) if so_id else None
    kontak = await _kontak(conn, tid, customer_id)
    from ..services.kode_order import muat_setelan as _setelan_kode
    _st_kode = await _setelan_kode(conn, tid)
    gi = next((i for i, g in enumerate(kelompok) if g["key"] == grup), None)
    di = next((k for k, d in enumerate(kelompok[gi]["docs"]) if d["id"] == str(did)), None) if gi is not None else None
    return {
        "anchor": {"kind": kind, "id": str(did), "number": nomor, "customer": pelanggan or "", "position_text": posisi,
                   "total": DB._uang(total), "paid": None if sisa is None else DB._uang(Decimal(str(total or 0)) - sisa),
                   "balance": None if sisa is None else DB._uang(sisa),
                   "sales_order_id": str(so["id"]) if so else None, "sales_order_number": so["order_number"] if so else None,
                   "order_code": so["order_code"] if so else None, "order_title": so["order_title"] if so else None,
                   "order_code_label": _st_kode["label"], "order_title_label": _st_kode["title_label"],
                   "customer_phone": (kontak["telepon"] if kontak else None) or None,
                   "customer_email": (kontak["email"] if kontak else None) or None,
                   "business_name": await _nama_usaha(conn, tid)},
        # dibuka pertama = dokumen jangkar sendiri
        "default": {"group": grup, "index": di} if di is not None else None,
        "groups": kelompok,
    }


@router.get("/customer-deposits/{deposit_id}/documents")
async def dokumen_uang_muka(request: Request, deposit_id: str):
    ctx = _ctx(request)
    pool = await _pool()
    async with pool.acquire() as conn:
        return await susun_dokumen_uang_muka(conn, ctx, _uuid(deposit_id))


@router.get("/credit-notes/{credit_note_id}/documents")
async def dokumen_nota_kredit(request: Request, credit_note_id: str):
    ctx = _ctx(request)
    pool = await _pool()
    async with pool.acquire() as conn:
        return await susun_dokumen_nota_kredit(conn, ctx, _uuid(credit_note_id))


@router.get("/sales-orders/{order_id}/documents")
async def dokumen_pesanan(request: Request, order_id: str, sertakan: Optional[str] = Query(None),
                          bentuk: Optional[str] = Query(None)):
    ctx = _ctx(request)
    pool = await _pool()
    async with pool.acquire() as conn:
        if bentuk == "terkait":  # 5 Okt 2026: bentuk BARU aditif (WORKSPACE D0); bundel lama di bawah tak berubah
            from ..services.so_terkait import susun_terkait
            return {"data": await susun_terkait(conn, ctx["tenant_id"], _uuid(order_id),
                                                sertakan_order=(sertakan == "order"))}
        return await susun_dokumen(conn, ctx, _uuid(order_id), sertakan_nk=(sertakan == "nota_kredit"))


# ── P5: tautan publik + lacak (02-DATA-DAN-API §Kirim & lacak) ─────────────────────────────────────────────────

_DOKUMEN_TABEL = {
    # kind -> (SQL keberadaan + status, kolom nomor). Tenant SELALU eksplisit.
    "rekap": ("SELECT order_number AS nomor, status FROM sales_orders WHERE id = $1 AND tenant_id = $2", None),
    "quotation": ("SELECT quote_number AS nomor, status FROM quotes WHERE id = $1 AND tenant_id = $2", None),
    "proforma": ("SELECT proforma_number AS nomor, status FROM proformas WHERE id = $1 AND tenant_id = $2", "draft"),
    "delivery": ("SELECT fulfillment_number AS nomor, status FROM invoice_fulfillments WHERE id = $1 AND tenant_id = $2", None),
    "invoice": ("SELECT invoice_number AS nomor, status FROM sales_invoices WHERE id = $1 AND tenant_id = $2", "draft"),
    "credit_note": ("SELECT credit_note_number AS nomor, status FROM credit_notes WHERE id = $1 AND tenant_id = $2", "draft"),
}
LABEL_JENIS = {"rekap": "Rekap Pesanan", "quotation": "Penawaran", "proforma": "Proforma", "receipt": "Kwitansi",
               "delivery": "Surat Jalan", "invoice": "Faktur", "credit_note": "Nota Kredit"}


# Dokumen DIBATALKAN tak boleh dibagikan -- jalur TUNGGAL == jalur MASSAL (W-20261015-BAGIKAN-BATAL, putusan MASTER; pola NetSuite:
# record void tak dibagikan). Satu penentu dipakai _bagikan (share + pratinjau) dan bulk_dokumen._periksa_bagikan.
STATUS_BATAL_BAGIKAN = frozenset({"void", "voided", "cancelled", "batal"})


# deposit_jenis.JENIS -> receipt_kind kwitansi bundel. BUKAN subtype: pembaca ketat FE live (bacaDokumen.ts) menolak subtype
# di luar dp/termin/penuh/cicilan untuk SEMUA kind -> panel dokumen gagal (MASTER/WORKSPACE 17 Okt 2026).
from ..services.dokumen_bundel import SUBTIPE_KWITANSI  # noqa: E402  satu definisi


def status_dokumen(kind: str, batal: bool, paid, draf: bool = False) -> str:
    """detailpage (17 Okt 2026): kata status per dokumen bundel /documents, turunan penentu YANG SAMA (dokumen_batal -> medan batal,
    medan paid panel). batal menang; proforma: lunas|terbit; faktur: lunas|diposting; kwitansi: diterima;
    surat jalan & penawaran: terbit; nota kredit: diposting."""
    if batal:
        return "batal"
    if draf:  # S1-U2: hanya bundel FAKTUR yang memuat dokumen draf (faktur dirinya); bundel SO tak pernah
        return "draf"
    if kind in ("proforma", "invoice"):
        return "lunas" if paid else ("terbit" if kind == "proforma" else "diposting")
    return {"receipt": "diterima", "credit_note": "diposting"}.get(kind, "terbit")


def dokumen_batal(kind: str, status) -> bool:
    """Penentu TUNGGAL "dibatalkan untuk dibagikan": dipakai tolak_batal (409) dan medan `batal` bundel /documents.
    Penawaran memakai penentunya sendiri (QUOTE_NOT_SHAREABLE) -> selalu False di sini."""
    return kind != "quotation" and str(status or "").lower() in STATUS_BATAL_BAGIKAN


def tolak_batal(kind: str, dok: dict) -> None:
    if dokumen_batal(kind, dok.get("status")):
        raise HTTPException(status_code=409, detail={
            "code": "DOKUMEN_BATAL",
            "message": f"{LABEL_JENIS.get(kind, 'Dokumen')} {dok['nomor']} sudah dibatalkan; tak bisa dibagikan."})


async def _baris_dokumen(conn, tid: str, kind: str, uid: UUID):
    """(baris {nomor, status} | None, status_draf_jenis). Satu pembaca untuk _dokumen_ada dan medan `batal` bundel."""
    if kind == "receipt":
        row = await conn.fetchrow("SELECT deposit_number AS nomor, status FROM customer_deposits WHERE id = $1 AND tenant_id = $2", uid, tid) \
            or await conn.fetchrow("SELECT payment_number AS nomor, status FROM receive_payments WHERE id = $1 AND tenant_id = $2", uid, tid)
        return row, None
    sql, draf = _DOKUMEN_TABEL[kind]
    return await conn.fetchrow(sql, uid, tid), draf


async def _dokumen_ada(conn, tid: str, kind: str, uid: UUID) -> dict:
    """{nomor, status} atau 404; dokumen DRAF (proforma/faktur) belum boleh dibagikan -> 409."""
    row, draf = await _baris_dokumen(conn, tid, kind, uid)
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
    async with pool.acquire() as conn, conn.transaction():
        hasil, info = await _bagikan(conn, request, ctx, kind, uid, channel)
    hasil["document"] = {"kind": kind, "id": str(uid), "number": info["number"]}
    if kind == "quotation":
        hasil["marked_sent"] = info["akan_ditandai_terkirim"]  # Q5: draf -> ditandai terkirim di transaksi ini
    return hasil


async def _bagikan(conn, request: Request, ctx: dict, kind: str, uid: UUID, channel: str) -> tuple:
    """SATU jalur share (dipakai /share dan /share/preview yang di-rollback). Pemanggil membuka transaksi.
    Penawaran (Q5, 3 Okt): kunci QUOTE + FOR UPDATE; penentu penawaran_bagikan; draf -> tandai terkirim (status, sent_at,
    audit QUOTE_SENT) di transaksi YANG SAMA dengan tautannya -- satu gagal, keduanya batal."""
    from ..services import dokumen_bagikan as DB
    if kind == "receipt" and await conn.fetchval(
            "SELECT 1 FROM customer_deposits WHERE id = $1 AND tenant_id = $2", uid, ctx["tenant_id"]):
        await _wajib_izin(request, "E", "customer_deposit")
    if kind == "quotation":
        from ..services import penawaran_bagikan as PB
        from ..utils import tanggal_tenant as TT
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"QUOTE:{ctx['tenant_id']}:{uid}")
        q = await conn.fetchrow("""SELECT id, quote_number, status, expiry_date FROM quotes
                                   WHERE id = $1 AND tenant_id = $2 FOR UPDATE""", uid, ctx["tenant_id"])
        if not q:
            raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan")
        p = PB.penentu(q["status"], q["quote_number"], q["expiry_date"], await TT.tanggal_dokumen(conn, ctx["tenant_id"]))
        info = {"status_now": q["status"], "number": q["quote_number"], **p}
        if not p["boleh"]:
            raise HTTPException(status_code=409, detail={"code": p["code"], "message": p["message"]})
        if p["akan_ditandai_terkirim"]:
            await PB.tandai_terkirim(conn, ctx["tenant_id"], q["id"], q["quote_number"], ctx["user_id"],
                                     "api:dokumen.share")
    else:
        dok = await _dokumen_ada(conn, ctx["tenant_id"], kind, uid)
        tolak_batal(kind, dok)  # BAGIKAN-BATAL: sama dengan jalur massal
        info = {"status_now": dok["status"], "number": dok["nomor"], "boleh": True, "akan_ditandai_terkirim": False,
                "code": None, "message": None}
    hasil = await DB.buat_tautan(conn, ctx["tenant_id"], kind, uid, channel, ctx["user_id"])
    return hasil, info


class _BatalkanBagikan(Exception):
    def __init__(self, info):
        self.info = info


@router.post("/documents/{kind}/{doc_id}/share/preview")
async def pratinjau_bagikan_dokumen(request: Request, kind: str, doc_id: str):
    """Kartu Tinjau Kirim (Q5): jalur _bagikan YANG SAMA lalu ROLLBACK -> nol tulis (tautan, status, audit). 200 selalu
    untuk dokumen yang ada: boleh=false + code/message bila share akan ditolak 409. Izin = rute share."""
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
    info = None
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                _, info = await _bagikan(conn, request, ctx, kind, uid, channel)
                raise _BatalkanBagikan(info)
        except _BatalkanBagikan as b:
            info = b.info
        except HTTPException as e:
            if e.status_code != 409 or not isinstance(e.detail, dict):
                raise
            # 409 = dokumen ADA tapi tak boleh dibagikan (QUOTE_NOT_SHAREABLE / DOKUMEN_DRAF): baca status untuk kartu
            row = await conn.fetchrow(_DOKUMEN_TABEL[kind][0], uid, ctx["tenant_id"]) if kind in _DOKUMEN_TABEL else None
            info = {"status_now": row["status"] if row else None, "number": row["nomor"] if row else None,
                    "boleh": False, "akan_ditandai_terkirim": False,
                    "code": e.detail.get("code"), "message": e.detail.get("message")}
    return {"success": True, "data": {k: info[k] for k in ("status_now", "akan_ditandai_terkirim", "boleh", "code",
                                                           "message", "number")}}


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
