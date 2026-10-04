"""Kirim barang dari Pesanan Penjualan (30 Sep 2026, MASTER/WORKSPACE, halaman CW).

POST /api/sales-orders/{id}/fulfill/preview  -> rencana + SEMUA blok + tulis-sungguhan-lalu-ROLLBACK.
POST /api/sales-orders/{id}/fulfill          -> tulis ATOMIK: SEMUA faktur dalam SATU transaksi.

Jalur penyerahan TETAP jalur tunggal K2: invoice_fulfillments lewat sales_invoices._execute_fulfillment (inti
yang SAMA dengan POST /sales-invoices/{id}/fulfill). Rute ini hanya MENGORKESTRASI: baris SO -> baris faktur
terbit yang pengirimannya masih terbuka, TERTUA-DULU (tanggal faktur, dibuat, nomor; sama dengan
so_kirim.SQL_FAKTUR_TERTUNDA_PER_SO), lalu satu Surat Jalan per faktur. /ship tetap 409 (K2).

Dua lapis (pola so_pelunasan):
  1. RENCANA: alokasi + SEMUA penghalang yang bisa dibaca tanpa menulis (qty, bisa-dikirim, NK penuh, stok
     per barang DIJUMLAH lintas faktur, WAC, gudang, periode). Inti berhenti di galat pertama; rencana tidak.
  2. Bila rencana bersih: inti dijalankan per faktur di SAVEPOINT; galat inti -> blok SO_FULFILL_REJECTED
     (pesan inti apa adanya). Pratinjau: pemanggil SELALU me-ROLLBACK. Tulis: galat apa pun -> seluruhnya batal.
Kunci: INVOICE_FULFILL per faktur (kunci yang sama dengan /fulfill), diambil urut invoice_id -> tanpa deadlock,
SEBELUM rencana membaca sisa.
"""
from datetime import date
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field

from . import teks_galat as tg
from . import so_kirim

_NOL = Decimal("0")


class SOFulfillLine(BaseModel):
    sales_order_item_id: str
    quantity: Decimal


class SOFulfillRequest(BaseModel):
    items: Optional[List[SOFulfillLine]] = None   # absen/null -> semua sisa yang bisa dikirim; [] -> tidak ada
    warehouse_id: Optional[str] = None            # kosong -> gudang SO -> gudang bawaan tenant
    fulfillment_date: Optional[date] = None       # kosong -> tanggal bisnis tenant
    notes: Optional[str] = Field(None, max_length=1000)


def _f(v):
    return float(v) if v is not None else None


def _q(v) -> str:
    s = format(Decimal(str(v)).normalize(), "f")
    return s


def _pesan(e: HTTPException) -> str:
    d = e.detail
    return d if isinstance(d, str) else (d.get("message") if isinstance(d, dict) else str(d))


SQL_FAKTUR_KIRIM = """
    SELECT si.id, si.invoice_number, si.invoice_date, si.created_at, si.status, si.fulfillment_status,
           si.customer_name, si.total_amount
    FROM sales_invoices si
    WHERE si.tenant_id = $1 AND si.sales_order_id = $2
      AND si.status = ANY($3::text[]) AND si.fulfillment_status = ANY($4::text[])
    ORDER BY si.invoice_date, si.created_at, si.invoice_number, si.id
"""


async def kunci_faktur_so(conn, tenant_id: str, so_id: UUID) -> None:
    """INVOICE_FULFILL per faktur kandidat (kunci SAMA dengan /sales-invoices/{id}/fulfill), urut id."""
    ids = await conn.fetch(
        "SELECT id FROM sales_invoices WHERE tenant_id = $1 AND sales_order_id = $2 AND status <> 'void' ORDER BY id",
        tenant_id, so_id,
    )
    for r in ids:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))",
                           tenant_id, f"INVOICE_FULFILL:{str(r['id'])}")


async def _gudang(conn, tid: str, diminta: Optional[str], so) -> tuple:
    kol = "SELECT id, name, is_active, is_default FROM warehouses WHERE tenant_id = $1"
    if diminta:
        try:
            u = UUID(str(diminta))
        except (ValueError, TypeError):
            return None, {"code": "SO_FULFILL_WAREHOUSE_INVALID", "message": "Gudang tidak dikenal."}
        r = await conn.fetchrow(kol + " AND id = $2", tid, u)
        if not r:
            return None, {"code": "SO_FULFILL_WAREHOUSE_INVALID", "message": "Gudang tidak dikenal."}
        if not r["is_active"]:
            return None, {"code": "SO_FULFILL_WAREHOUSE_INACTIVE", "message": f"Gudang {r['name']} tidak aktif."}
        return {"id": str(r["id"]), "name": r["name"], "source": "body"}, None
    if so["warehouse_id"]:
        r = await conn.fetchrow(kol + " AND id = $2 AND is_active", tid, so["warehouse_id"])
        if r:
            return {"id": str(r["id"]), "name": r["name"], "source": "so"}, None
    aktif = await conn.fetch(kol + " AND is_active ORDER BY name", tid)
    for calon in (list(aktif) if len(aktif) == 1 else [], [r for r in aktif if r["is_default"]]):
        if len(calon) == 1:
            r = calon[0]
            return {"id": str(r["id"]), "name": r["name"], "source": "tenant_default"}, None
    return None, {"code": "SO_FULFILL_WAREHOUSE_REQUIRED", "message": "Pilih gudang pengiriman."}


async def rencana_kirim_so(conn, ctx: dict, so_id: UUID, body: SOFulfillRequest) -> dict:
    """Lapis 1: alokasi + SEMUA penghalang. Nol tulisan. Pemanggil sudah memegang kunci_faktur_so."""
    tid = ctx["tenant_id"]
    so = await conn.fetchrow(
        "SELECT id, order_number, status, warehouse_id FROM sales_orders WHERE id = $1 AND tenant_id = $2",
        so_id, tid,
    )
    if not so:
        raise HTTPException(status_code=404, detail="Pesanan penjualan tidak ditemukan.")
    so = dict(so)
    blocks, notes = [], []

    draf = [r["invoice_number"] for r in await conn.fetch(
        "SELECT invoice_number FROM sales_invoices WHERE tenant_id = $1 AND sales_order_id = $2 AND status = 'draft' "
        "ORDER BY invoice_date, invoice_number", tid, so_id)]
    faktur = [dict(r) for r in await conn.fetch(SQL_FAKTUR_KIRIM, tid, so_id,
                                               list(so_kirim.FAKTUR_TERBIT_KIRIM), list(so_kirim.KIRIM_TERBUKA))]
    urut = {f["id"]: i for i, f in enumerate(faktur)}
    baris = []
    if faktur:
        baris = [dict(r) for r in await conn.fetch(
            """SELECT sii.id, sii.invoice_id, sii.sales_order_item_id, sii.item_id, sii.description, sii.unit,
                      sii.quantity, COALESCE(sii.fulfilled_qty, 0) AS fulfilled_qty,
                      COALESCE(sii.allocated_amount, 0) AS allocated_amount,
                      COALESCE(sii.recognized_amount, 0) AS recognized_amount, sii.perlu_kirim, sii.line_number,
                      COALESCE(p.track_inventory, false) AS dilacak, p.nama_produk
               FROM sales_invoice_items sii
               LEFT JOIN products p ON p.id = sii.item_id AND p.tenant_id = $1
               WHERE sii.invoice_id = ANY($2::uuid[])""",
            tid, [f["id"] for f in faktur])]
    nk_penuh = set()
    if baris:
        nk_penuh = {r["invoice_item_id"] for r in await conn.fetch(
            """SELECT DISTINCT d.invoice_item_id FROM credit_note_deferral_lines d
               WHERE d.invoice_item_id = ANY($1::uuid[]) AND d.tenant_id = $2 AND d.reversed_at IS NULL""",
            [b["id"] for b in baris], tid)}
    for b in baris:
        b["sisa"] = max(Decimal(str(b["quantity"])) - Decimal(str(b["fulfilled_qty"])), _NOL)
        b["bisa_kirim"] = bool(b["dilacak"] or b["perlu_kirim"])
        b["kredit_penuh"] = (b["id"] in nk_penuh and
                             Decimal(str(b["allocated_amount"])) - Decimal(str(b["recognized_amount"])) <= Decimal("0.005"))
    baris.sort(key=lambda b: (urut[b["invoice_id"]], b["line_number"] or 0, str(b["id"])))
    fnum = {f["id"]: f["invoice_number"] for f in faktur}

    soi = {r["id"]: dict(r) for r in await conn.fetch(
        """SELECT soi.id, soi.description, soi.unit, soi.quantity, soi.sort_order,
                  (COALESCE(p.track_inventory, false) OR COALESCE(soi.perlu_kirim, false)) AS butuh_kirim
           FROM sales_order_items soi LEFT JOIN products p ON p.id = soi.item_id AND p.tenant_id = $2
           WHERE soi.sales_order_id = $1 ORDER BY soi.sort_order NULLS LAST, soi.id""", so_id, tid)}

    alokasi = []  # [{baris, qty}]
    if body.items is not None:  # [] / semua 0 = TIDAK ada yang dikirim (bukan "kirim semua")
        for it in body.items:
            try:
                sid = UUID(str(it.sales_order_item_id))
            except (ValueError, TypeError):
                sid = None
            if sid not in soi:
                blocks.append({"code": "SO_FULFILL_LINE_NOT_IN_SO",
                               "message": f"Baris {it.sales_order_item_id} bukan baris pesanan {so['order_number']}.",
                               "sales_order_item_id": it.sales_order_item_id})
                continue
            nama = soi[sid]["description"] or "-"
            if it.quantity < 0:
                blocks.append({"code": "SO_FULFILL_QTY_INVALID", "message": f"Jumlah kirim '{nama}' tidak boleh negatif.",
                               "sales_order_item_id": str(sid)})
                continue
            if it.quantity == 0:
                continue  # baris diturunkan ke 0 = tidak ikut dikirim
            if not soi[sid]["butuh_kirim"]:
                blocks.append({"code": "SO_FULFILL_NOT_SHIPPABLE",
                               "message": f"'{nama}' bukan barang yang dikirim (non-stok tanpa tanda 'bisa dikirim').",
                               "sales_order_item_id": str(sid)})
                continue
            calon = [b for b in baris if b["sales_order_item_id"] == sid and b["sisa"] > 0]
            tak_kirim = [b for b in calon if not b["bisa_kirim"]]
            kredit = [b for b in calon if b["bisa_kirim"] and b["kredit_penuh"]]
            calon = [b for b in calon if b["bisa_kirim"] and not b["kredit_penuh"]]
            if tak_kirim and not calon:
                blocks.append({"code": "SO_FULFILL_NOT_SHIPPABLE",
                               "message": f"'{nama}' bukan barang yang dikirim (non-stok tanpa tanda 'bisa dikirim').",
                               "sales_order_item_id": str(sid)})
                continue
            if kredit and not calon:
                blocks.append({"code": "SO_FULFILL_LINE_FULLY_CREDITED",
                               "message": f"'{nama}' sudah dikreditkan penuh lewat nota kredit; tak ada nilai untuk dikirim.",
                               "sales_order_item_id": str(sid)})
                continue
            tersedia = sum((b["sisa"] for b in calon), _NOL)
            if it.quantity > tersedia:
                blocks.append({"code": "SO_FULFILL_QTY_EXCEEDS",
                               "message": (f"'{nama}': diminta {tg.qty(it.quantity)}, yang sudah difakturkan dan belum "
                                           f"dikirim hanya {tg.qty(tersedia)}."
                                           + (" Terbitkan faktur draf " + ", ".join(draf) + " dulu." if draf else "")),
                               "sales_order_item_id": str(sid), "available": _f(tersedia)})
                continue
            sisa = it.quantity
            for b in calon:
                if sisa <= 0:
                    break
                a = min(b["sisa"], sisa)
                alokasi.append({"b": b, "qty": a})
                sisa -= a
    else:
        for b in baris:
            if b["sisa"] <= 0:
                continue
            if not b["bisa_kirim"] or b["kredit_penuh"]:
                continue
            if b["sales_order_item_id"] is None or b["sales_order_item_id"] not in soi:
                continue  # payload per baris SO tak bisa menyebutnya -> tulis != pratinjau; dilaporkan di bawah
            alokasi.append({"b": b, "qty": b["sisa"]})
        tanpa_tautan = [b for b in baris if b["sisa"] > 0 and b["bisa_kirim"]
                        and (b["sales_order_item_id"] is None or b["sales_order_item_id"] not in soi)]
        if tanpa_tautan:
            notes.append({"code": "SO_FULFILL_UNLINKED_SKIPPED",
                          "message": "Baris faktur tanpa tautan ke baris pesanan tidak ikut; kirim lewat faktur "
                                     + ", ".join(sorted({fnum[b["invoice_id"]] for b in tanpa_tautan})) + "."})
        lewat_nk = [b for b in baris if b["sisa"] > 0 and b["bisa_kirim"] and b["kredit_penuh"]]
        if lewat_nk:
            notes.append({"code": "SO_FULFILL_CREDITED_SKIPPED",
                          "message": "Baris yang sudah dikreditkan penuh lewat nota kredit tidak ikut dikirim: "
                                     + ", ".join(sorted({b["description"] or "-" for b in lewat_nk})) + "."})

    if draf:
        notes.append({"code": "SO_FULFILL_DRAFT_INVOICES",
                      "message": f"Faktur draf {', '.join(draf)} belum terbit — barangnya belum bisa dikirim."})

    gudang, blok_gudang = await _gudang(conn, tid, body.warehouse_id, so)
    if blok_gudang:
        blocks.append(blok_gudang)

    from ..utils.tanggal_tenant import tanggal_dokumen
    from ..routers.sales_invoices import check_period_is_open
    tgl = body.fulfillment_date or await tanggal_dokumen(conn, tid)
    try:
        await check_period_is_open(conn, tid, tgl)
    except HTTPException as e:
        blocks.append({"code": "SO_FULFILL_PERIOD_CLOSED", "message": _pesan(e)})

    # stok per barang DIJUMLAH lintas faktur (inti memeriksa per baris -> faktur kedua bisa lolos rencana palsu)
    if gudang:
        per_barang = {}
        for a in alokasi:
            b = a["b"]
            if b["dilacak"]:
                x = per_barang.setdefault(b["item_id"], {"qty": _NOL, "nama": b["nama_produk"] or b["description"],
                                                         "soi": []})
                x["qty"] += a["qty"]
                if b["sales_order_item_id"] and str(b["sales_order_item_id"]) not in x["soi"]:
                    x["soi"].append(str(b["sales_order_item_id"]))
        for pid, x in per_barang.items():
            stok = await conn.fetchval(
                "SELECT COALESCE(quantity, 0) FROM warehouse_stock WHERE item_id = $1 AND warehouse_id = $2 AND tenant_id = $3",
                pid, UUID(gudang["id"]), tid)
            stok = Decimal(str(stok)) if stok is not None else _NOL
            if stok < x["qty"]:
                blocks.append({"code": "SO_FULFILL_STOCK_SHORT",
                               "message": f"Stok {x['nama']} di {gudang['name']} {tg.qty(stok)}, dibutuhkan {tg.qty(x['qty'])}.",
                               "item_id": str(pid), "sales_order_item_ids": x["soi"],
                               "sales_order_item_id": x["soi"][0] if len(x["soi"]) == 1 else None,
                               "available": _f(stok), "requested": _f(x["qty"])})
            wac = await conn.fetchval("SELECT get_weighted_average_cost($1, $2)", tid, pid)
            if not wac or Decimal(str(wac)) == 0:
                blocks.append({"code": "SO_FULFILL_NO_COST",
                               "message": f"Harga pokok {x['nama']} belum ada. Catat penerimaan barang dulu.",
                               "item_id": str(pid)})

    # lines[]: SEMUA baris SO, termasuk yang tak ikut dikirim (FE menampilkan tiap baris + alasan)
    per_baris, _ = await so_kirim.terkirim_per_baris(conn, tid, [so_id])
    draf_qty = {r["soi_id"]: Decimal(str(r["q"])) for r in await conn.fetch(
        """SELECT sii.sales_order_item_id AS soi_id, SUM(sii.quantity) AS q FROM sales_invoice_items sii
           JOIN sales_invoices si ON si.id = sii.invoice_id
           WHERE si.tenant_id = $1 AND si.sales_order_id = $2 AND si.status = 'draft' GROUP BY 1""", tid, so_id)}
    lines = []
    for sid, x in soi.items():
        milik = [b for b in baris if b["sales_order_item_id"] == sid and b["sisa"] > 0]
        bisa = sum((b["sisa"] for b in milik if b["bisa_kirim"] and not b["kredit_penuh"]), _NOL)
        dikirim = sum((a["qty"] for a in alokasi if a["b"]["sales_order_item_id"] == sid), _NOL)
        terkirim = per_baris.get(sid, _NOL)
        alasan = None
        if bisa <= 0:
            if not x["butuh_kirim"] or any(not b["bisa_kirim"] for b in milik):
                alasan = "NON_STOCK"
            elif Decimal(str(x["quantity"])) - terkirim <= 0:
                alasan = "FULLY_SHIPPED"
            elif any(b["kredit_penuh"] for b in milik):
                alasan = "FULLY_CREDITED"
            elif draf_qty.get(sid, _NOL) > 0:
                alasan = "INVOICE_DRAFT"
            else:
                alasan = "NOT_INVOICED"
        lines.append({"sales_order_item_id": str(sid), "description": x["description"], "unit": x["unit"],
                      "ordered": _f(x["quantity"]), "shipped": _f(terkirim), "shippable": _f(bisa),
                      "quantity": _f(dikirim), "reason": alasan})

    selesai = all(x["reason"] in ("FULLY_SHIPPED", "NON_STOCK") for x in lines)
    if not faktur and not selesai:
        blocks.append({"code": "SO_FULFILL_NO_INVOICE",
                       "message": (f"Pesanan {so['order_number']} belum punya faktur terbit yang menunggu pengiriman."
                                   + (" Terbitkan faktur " + ", ".join(draf) + " dulu." if draf else ""))})
    elif not alokasi and not blocks:
        semua_terkirim = selesai and any(x["reason"] == "FULLY_SHIPPED" for x in lines)
        blocks.append({"code": "SO_FULFILL_NOTHING_TO_SHIP",
                       "message": "Semua barang pesanan ini sudah dikirim." if semua_terkirim
                       else "Tidak ada barang yang perlu dikirim."})

    kelompok = {}
    for a in alokasi:
        kelompok.setdefault(a["b"]["invoice_id"], []).append(a)
    faktur_kena = [f for f in faktur if f["id"] in kelompok]

    payload = None
    if not blocks:
        per_soi = {}
        for a in alokasi:
            k = str(a["b"]["sales_order_item_id"]) if a["b"]["sales_order_item_id"] else None
            if k:
                per_soi[k] = per_soi.get(k, _NOL) + a["qty"]
        payload = {
            "items": [{"sales_order_item_id": k, "quantity": _q(v)} for k, v in per_soi.items()],
            "warehouse_id": gudang["id"],
            "fulfillment_date": tgl.isoformat(),
            "notes": body.notes,
        }
    return {"so": so, "blocks": blocks, "notes": notes, "warehouse": gudang, "fulfillment_date": tgl,
            "lines": lines, "faktur": faktur_kena, "kelompok": kelompok, "fnum": fnum, "soi": soi, "baris": baris,
            "payload": payload}


async def tulis_kirim_so(conn, ctx: dict, r: dict, kunci: Optional[str] = None) -> list:
    """Lapis 2: inti per faktur (urut tertua) di transaksi PEMANGGIL. Galat -> HTTPException (pemanggil batal)."""
    from ..routers.sales_invoices import _execute_fulfillment
    hasil = []
    for f in r["faktur"]:
        items = [{"invoice_item_id": a["b"]["id"], "quantity": a["qty"]} for a in r["kelompok"][f["id"]]]
        h = await _execute_fulfillment(
            conn, ctx["tenant_id"], ctx["user_id"], f, items, UUID(r["warehouse"]["id"]), r["fulfillment_date"],
            recognize_revenue=True,
            idempotency_key=(f"SO_FULFILL:{kunci}:{f['id']}" if kunci else None),
            notes=r["payload"]["notes"] if r["payload"] else None,
        )
        if "fulfillment_number" not in h:  # jalur 'Already fulfilled' inti = kunci turunan sudah terpakai
            raise HTTPException(status_code=409, detail={
                "code": "IDEMPOTENCY_KEY_REUSED",
                "message": "Idempotency-Key sudah dipakai untuk pengiriman lain pesanan ini."})
        hasil.append({"invoice_id": f["id"], **h})
    return hasil


async def _so_sesudah(conn, tid: str, so_id: UUID) -> dict:
    so = await conn.fetchrow("SELECT status, shipped_qty FROM sales_orders WHERE id = $1 AND tenant_id = $2", so_id, tid)
    per_baris, _ = await so_kirim.terkirim_per_baris(conn, tid, [so_id])
    sisa = _NOL
    for x in await conn.fetch("SELECT id, quantity FROM sales_order_items WHERE sales_order_id = $1", so_id):
        sisa += so_kirim.belum_dikirim(x["quantity"], per_baris.get(x["id"]))
    return {"status": so["status"], "shipped_qty": _f(so["shipped_qty"]), "remaining_to_ship": _f(sisa)}


def bentuk(r: dict, hasil: Optional[list], so_after: Optional[dict]) -> dict:
    per = {h["invoice_id"]: h for h in (hasil or [])}
    alok = []
    tq = _NOL
    for f in r["faktur"]:
        h = per.get(f["id"])
        its = []
        for a in r["kelompok"][f["id"]]:
            b = a["b"]
            tq += a["qty"]
            its.append({"invoice_item_id": str(b["id"]),
                        "sales_order_item_id": str(b["sales_order_item_id"]) if b["sales_order_item_id"] else None,
                        "description": b["description"], "unit": b["unit"], "quantity": _f(a["qty"]),
                        "remaining_before": _f(b["sisa"]), "remaining_after": _f(b["sisa"] - a["qty"]),
                        "tracks_stock": b["dilacak"]})
        alok.append({"invoice_id": str(f["id"]), "invoice_number": f["invoice_number"],
                     "fulfillment_number": h["fulfillment_number"] if h else None,
                     "fulfillment_id": h.get("fulfillment_id") if h else None,
                     "cogs_amount": _f(Decimal(h["total_cogs"])) if h else None,
                     "revenue_recognized": _f(Decimal(h["total_revenue"])) if h else None,
                     "items": its})
    return {
        "order_number": r["so"]["order_number"],
        "ok": not r["blocks"],
        "can_save": not r["blocks"],
        "blocks": r["blocks"],
        "notes": r["notes"],
        "warehouse": r["warehouse"],
        "fulfillment_date": r["fulfillment_date"].isoformat(),
        "lines": r["lines"],
        "allocations": alok,
        "totals": {"quantity": _f(tq), "invoices_touched": len(alok),
                   "cogs_amount": _f(sum((Decimal(h["total_cogs"]) for h in hasil), _NOL)) if hasil else None,
                   "revenue_recognized": _f(sum((Decimal(h["total_revenue"]) for h in hasil), _NOL)) if hasil else None},
        "so_after": so_after,
        "so_status_after": so_after["status"] if so_after else None,
        "fulfillments": [{"id": a["fulfillment_id"], "fulfillment_number": a["fulfillment_number"],
                          "invoice_id": a["invoice_id"], "invoice_number": a["invoice_number"]}
                         for a in alok if a["fulfillment_number"]],
        "payload": r["payload"],
    }


async def pratinjau_kirim_so(conn, ctx: dict, so_id: UUID, body: SOFulfillRequest) -> dict:
    """Lapis 1 + 2 di transaksi PEMANGGIL (pemanggil WAJIB me-ROLLBACK)."""
    tid = ctx["tenant_id"]
    await kunci_faktur_so(conn, tid, so_id)
    r = await rencana_kirim_so(conn, ctx, so_id, body)
    hasil = so_after = None
    if r["payload"] is not None:
        try:
            async with conn.transaction():  # savepoint: galat SQL inti tak meracuni transaksi luar
                hasil = await tulis_kirim_so(conn, ctx, r)
                so_after = await _so_sesudah(conn, tid, so_id)
        except HTTPException as e:
            r["blocks"].append({"code": "SO_FULFILL_REJECTED", "message": _pesan(e)})
            r["payload"] = None
            hasil = so_after = None
    return bentuk(r, hasil, so_after)
