"""Surat Jalan dari PESANAN tanpa faktur (R1: irisan 1 non-stok + irisan 2 BER-STOK, 12 Okt 2026).

Pola rujukan: Accurate "Pengiriman Pesanan" + akun Barang Terkirim (putusan pemilik P1) / NetSuite Item Fulfillment / SAP
Outbound Delivery; pendapatan diakui saat FAKTUR (P2: SAP billing / NetSuite invoice / Accurate faktur).

NON-STOK (irisan 1, tanpa aturan buku baru): SJ = dokumen; nol jurnal, nol kartu stok, nol HPP.
BER-STOK (irisan 2, flag `pengiriman_stok_sebelum_faktur`; ATURAN BUKU BARU, izin LANGSUNG pemilik):
  saat kirim  : kartu keluar (source_type DELIVERY_NOTE, source_id = fulfillment_id, journal_id terisi) +
                Dr Barang Terkirim (GOODS_DELIVERED_NOT_INVOICED, 1-10660) / Cr Persediaan (INVENTORY_MERCHANDISE),
                nilai = qty x WAC SNAPSHOT di baris SJ (Law 1/16/20/23; WAC tak berubah di keluar, Inventory Rule 3).
  saat faktur : per alokasi (tertua-dulu) Dr HPP (COGS_SALES) / Cr Barang Terkirim pada SNAPSHOT yang sama (clearing tepat,
                bukan WAC hari faktur) + INVOICE_REVENUE Dr Dimuka / Cr Penjualan untuk qty teralokasi (aturan sisa:
                alokasi yang menghabiskan qty baris mengambil sisa, pembulatan seperti `_execute_fulfillment`).
  void mandiri: pembalik jurnal (DELIVERY_NOTE_REVERSAL, reversed_by_id; Law 2/26) + pembalik kartu (record_inventory_reversal),
                SJ asal 'so' yang belum ditagih; void faktur melepas alokasi + membalik reklas (SJ kembali 'belum ditagih').
                SJ asal 'faktur' (VOID-SJ-FAKTUR 15 Okt 2026): endpoint sama, aturan di services/sj_faktur_batal.

Satu penentu `rencana` (pratinjau == tulis; semua blok). Satu transaksi milik PEMANGGIL (Law 23); kunci `SJ_SO:{tenant}:{so}` +
INVOICE_FULFILL tiap faktur SO + `STOCK:{barang}:{gudang}` (Law 13). Tenant eksplisit di tiap SQL (Law 24). Tautan faktur =
`sj_faktur_alokasi` (INSERT-only; hanya voided_*). Decimal 2dp HALF_UP (Law 25).
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field

from . import pengiriman_serah as serah_mod
from . import so_kirim
from .role_resolver import AccountRole, resolve_account_id_by_role
from .so_riwayat import catat_riwayat
from ..utils.tanggal_tenant import tanggal_dokumen

FLAG = "pengiriman_sebelum_faktur"
FLAG_STOK = "pengiriman_stok_sebelum_faktur"
SUMBER = "DELIVERY_NOTE"
SUMBER_BALIK = "DELIVERY_NOTE_REVERSAL"
SUMBER_REKLAS = "DELIVERY_INVOICED"
SUMBER_REKLAS_BALIK = "DELIVERY_INVOICED_REVERSAL"
_NOL = Decimal("0")
_DUA = Decimal("0.01")
LABEL_SUMBER_GUDANG = {"diminta": "Dipilih", "so": "Gudang pesanan", "default": "Gudang bawaan"}
STATUS_SO_TERTUTUP = ("draft", "cancelled", "completed")


def _r2(x) -> Decimal:
    return Decimal(str(x)).quantize(_DUA, rounding=ROUND_HALF_UP)


class BarisIn(BaseModel):
    sales_order_item_id: UUID
    quantity: Decimal = Field(..., gt=0)


class SJSOIn(BaseModel):
    items: Optional[List[BarisIn]] = None          # absen/null -> semua sisa baris yang perlu dikirim (stok hanya bila flag stok)
    fulfillment_date: Optional[date] = None        # kosong -> tanggal bisnis tenant
    warehouse_id: Optional[UUID] = None            # hanya untuk baris ber-stok; kosong -> gudang SO -> gudang bawaan
    notes: Optional[str] = Field(None, max_length=1000)
    serah: Optional[serah_mod.SerahIn] = None


def _blok(kode: str, pesan: str, status: int = 409, baris=None, **tambahan) -> dict:
    return {"code": kode, "message": pesan, "status": status, "sales_order_item_id": str(baris) if baris else None, **tambahan}


# 13 Okt 2026 (W-20261013-SJSO-SUDAHFAKTUR, Law 1/16 + 13 + 24 + 25): baris SO yang SUDAH difaktur (terbit: bukan draf/void) tetapi
# belum dikirim TIDAK boleh dikirim lewat SJ tanpa faktur -- alokasi hanya terjadi SAAT faktur diposting, jadi SJ susulan tak pernah
# tertaut dan /fulfill faktur itu mengirim LAGI (stok + HPP keluar ganda). Sisa yang boleh = qty - difaktur - (SJ-so aktif - teralokasi).
# Faktur TERBIT (bukan draf/void) -- SATU predikat untuk qty difaktur per baris SO (di sini + services/so_aksi, 14 Okt 2026).
PRED_FAKTUR_TERBIT = "si.status NOT IN ('draft', 'void')"
SQL_FAKTUR_TERBIT_PER_BARIS = f"""
    SELECT sii.sales_order_item_id AS soi_id, si.id AS invoice_id, si.invoice_number, si.invoice_date, SUM(sii.quantity) AS q
    FROM sales_invoices si JOIN sales_invoice_items sii ON sii.invoice_id = si.id
    WHERE si.tenant_id = $1 AND si.sales_order_id = $2 AND {PRED_FAKTUR_TERBIT} AND sii.sales_order_item_id IS NOT NULL
    GROUP BY 1, 2, 3, 4 ORDER BY si.invoice_date, si.invoice_number
"""
SQL_SJ_SO_PER_BARIS = """
    SELECT ifi.sales_order_item_id AS soi_id, SUM(ifi.quantity) AS s,
           COALESCE(SUM((SELECT SUM(a.quantity) FROM sj_faktur_alokasi a
                         WHERE a.fulfillment_item_id = ifi.id AND a.tenant_id = f.tenant_id AND a.voided_at IS NULL)), 0) AS alok
    FROM invoice_fulfillments f JOIN invoice_fulfillment_items ifi ON ifi.fulfillment_id = f.id
    WHERE f.tenant_id = $1 AND f.asal = 'so' AND f.sales_order_id = $2 AND f.voided_at IS NULL AND f.status <> 'voided'
      AND ifi.sales_order_item_id IS NOT NULL
    GROUP BY 1
"""


def angkat_blok_pertama(r: dict) -> None:
    if r["blocks"]:
        b = r["blocks"][0]
        raise HTTPException(status_code=b["status"], detail={"code": b["code"], "message": b["message"]})


async def _flag(conn, tenant_id: str, nama: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM tenant_features WHERE tenant_id = $1 AND feature = $2 AND enabled", tenant_id, nama))


async def aktif(conn, tenant_id: str) -> bool:
    return await _flag(conn, tenant_id, FLAG)


async def stok_aktif(conn, tenant_id: str) -> bool:
    return await _flag(conn, tenant_id, FLAG_STOK)


async def pastikan_aktif(conn, tenant_id: str) -> None:
    if not await aktif(conn, tenant_id):
        raise HTTPException(status_code=409, detail={
            "code": "SJ_SO_NONAKTIF", "message": "Pengiriman sebelum faktur belum tersedia untuk usaha ini."})


async def _kunci(conn, kunci: str) -> None:
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", kunci)


async def kunci_so(conn, tenant_id: str, so_id) -> None:
    """Kunci dokumen + kunci faktur (INVOICE_FULFILL, urut id) -- sama dengan so_pengiriman.kunci_faktur_so."""
    await _kunci(conn, f"SJ_SO:{tenant_id}:{so_id}")
    for f in await conn.fetch(
            "SELECT id FROM sales_invoices WHERE tenant_id = $1 AND sales_order_id = $2 AND status <> 'void' ORDER BY id",
            tenant_id, so_id):
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))", tenant_id, f"INVOICE_FULFILL:{f['id']}")


async def _periode_terbuka(conn, tenant_id: str, tanggal) -> Optional[str]:
    """None bila terbuka; selain itu pesan blok. Memakai penjaga yang sama dengan jalur faktur (Law 5)."""
    from ..routers.sales_invoices import check_period_is_open
    try:
        await check_period_is_open(conn, tenant_id, tanggal)
    except HTTPException as e:
        d = e.detail
        return d.get("message") if isinstance(d, dict) else str(d)
    return None


async def _gudang_default(conn, tid: str, diminta, so_gudang):
    """-> (gudang|None, sumber) ; sumber 'diminta' | 'so' | 'default' (pola /fulfill; FE tak menebak)."""
    for kandidat, sumber in ((diminta, "diminta"), (so_gudang, "so")):
        if kandidat is not None:
            g = await conn.fetchrow("SELECT id, name, is_active FROM warehouses WHERE id = $1 AND tenant_id = $2", kandidat, tid)
            return g, sumber
    return await conn.fetchrow(
        "SELECT id, name, is_active FROM warehouses WHERE tenant_id = $1 AND is_active ORDER BY is_default DESC NULLS LAST, created_at LIMIT 1",
        tid), "default"


async def rencana(conn, ctx: dict, so_id, body: SJSOIn, kunci: bool = False) -> dict:
    """-> {so, blocks, lines, dilewati, fulfillment_date, warehouse, total_value, serah, serah_aktif, notes, payload}. Tanpa tulis
    (kunci=True: baris SO + baris stok dikunci FOR UPDATE)."""
    tid = ctx["tenant_id"]
    blocks: list = []
    so = await conn.fetchrow(
        "SELECT id, order_number, status, customer_id, customer_name, warehouse_id FROM sales_orders WHERE id = $1 AND tenant_id = $2"
        + (" FOR UPDATE" if kunci else ""), so_id, tid)
    if not so:
        raise HTTPException(status_code=404, detail="Pesanan penjualan tidak ditemukan.")
    if so["status"] in STATUS_SO_TERTUTUP:
        label = {"draft": "masih draf", "cancelled": "sudah dibatalkan", "completed": "sudah selesai"}[so["status"]]
        blocks.append(_blok("SJ_SO_STATUS_TIDAK_BOLEH", f"Pesanan {so['order_number']} {label}; tak bisa dibuatkan Surat Jalan."))

    flag_stok = await stok_aktif(conn, tid)
    baris = await conn.fetch(
        """SELECT soi.id, soi.item_id, soi.description, soi.quantity, soi.unit, soi.perlu_kirim,
                  COALESCE(p.track_inventory, false) AS stok, COALESCE(p.track_batches, false) AS batch,
                  COALESCE(p.track_serial, false) AS serial, p.nama_produk, p.item_code
           FROM sales_order_items soi
           LEFT JOIN products p ON p.id = soi.item_id AND p.tenant_id = $2
           WHERE soi.sales_order_id = $1 ORDER BY soi.sort_order NULLS LAST, soi.id""", so_id, tid)
    peta = {b["id"]: b for b in baris}
    terkirim, _ = await so_kirim.terkirim_per_baris(conn, tid, [so_id])
    # Faktur terbit per baris (qty + daftar faktur) dan SJ-so aktif/teralokasi per baris -> sisa yang boleh dikirim TANPA faktur.
    faktur: dict = {}
    for r_ in await conn.fetch(SQL_FAKTUR_TERBIT_PER_BARIS, tid, so_id):
        f_ = faktur.setdefault(r_["soi_id"], {"q": _NOL, "daftar": []})
        qf = Decimal(str(r_["q"]))
        f_["q"] += qf
        f_["daftar"].append({"invoice_id": str(r_["invoice_id"]), "invoice_number": r_["invoice_number"]})
    sj: dict = {r_["soi_id"]: (Decimal(str(r_["s"])), Decimal(str(r_["alok"]))) for r_ in await conn.fetch(SQL_SJ_SO_PER_BARIS, tid, so_id)}
    dilewati: list = []
    diminta: dict = {}

    def perlu(b):
        return b["perlu_kirim"] if b["perlu_kirim"] is not None else b["stok"]

    def sisa_kirim(b):
        """-> (sisa lama = qty - terkirim, sisa boleh = min(lama, qty - difaktur - SJ-so belum teralokasi), difaktur)."""
        q = Decimal(str(b["quantity"]))
        lama = q - terkirim.get(b["id"], _NOL)
        i = faktur.get(b["id"], {}).get("q", _NOL)
        s_, alok = sj.get(b["id"], (_NOL, _NOL))
        baru = q - i - max(_NOL, s_ - alok)
        return lama, max(_NOL, min(lama, baru)), i

    def blok_difaktur(b, nama, sisa, diminta_qty=None):
        daftar = faktur.get(b["id"], {}).get("daftar", [])
        nomor = ", ".join(d["invoice_number"] for d in daftar)
        pesan = (f"{nama} sudah difaktur ({nomor}); kirim lewat Surat Jalan faktur itu. "
                 f"Sisa yang bisa dikirim tanpa faktur {sisa:f}" + (f", diminta {diminta_qty:f}." if diminta_qty is not None else "."))
        # kontrak final (MASTER 13 Okt): faktur[] {invoice_id, invoice_number} tertua-dulu + invoice_id/invoice_number tingkat atas = PERTAMA
        return pesan, {"faktur": [{"invoice_id": d["invoice_id"], "invoice_number": d["invoice_number"]} for d in daftar],
                       "invoice_id": daftar[0]["invoice_id"] if daftar else None,
                       "invoice_number": daftar[0]["invoice_number"] if daftar else None}

    if body.items is None:
        for b in baris:
            nama = b["nama_produk"] or b["description"]
            lama, sisa, i = sisa_kirim(b)

            def lewat(alasan, pesan, **tambahan):
                dilewati.append({"sales_order_item_id": str(b["id"]), "name": nama, "alasan": alasan, "message": pesan, **tambahan})

            if not perlu(b):
                lewat("SJ_SO_BARIS_TIDAK_PERLU_KIRIM", f"{nama} tidak perlu dikirim.")
                continue
            if lama <= _NOL:
                lewat("SJ_SO_BARIS_SUDAH_TERKIRIM", f"{nama} sudah terkirim seluruhnya.")
                continue
            if sisa <= _NOL:
                pesan, inv = blok_difaktur(b, nama, sisa)
                lewat("SJ_SO_BARIS_SUDAH_DIFAKTUR", pesan, **inv)
                continue
            if b["stok"] and not flag_stok:
                lewat("SJ_SO_STOK_BELUM_DIDUKUNG", f"{nama} ber-stok: Surat Jalan tanpa faktur untuk barang ber-stok belum didukung.")
                continue
            diminta[b["id"]] = sisa
    else:
        for it in body.items:
            b = peta.get(it.sales_order_item_id)
            if b is None:
                blocks.append(_blok("SJ_SO_BARIS_TAK_ADA", "Baris tidak ada di pesanan ini.", 404, it.sales_order_item_id))
                continue
            if it.sales_order_item_id in diminta:
                blocks.append(_blok("SJ_SO_BARIS_GANDA", "Baris yang sama muncul dua kali; gabungkan.", 422, b["id"]))
                continue
            nama = b["nama_produk"] or b["description"]
            if b["stok"] and not flag_stok:
                blocks.append(_blok("SJ_SO_STOK_BELUM_DIDUKUNG",
                                    f"{nama} ber-stok: Surat Jalan tanpa faktur untuk barang ber-stok belum didukung.", 409, b["id"]))
                continue
            if not perlu(b):
                blocks.append(_blok("SJ_SO_BARIS_TIDAK_PERLU_KIRIM", f"{nama} tidak perlu dikirim.", 409, b["id"]))
                continue
            lama, sisa, i = sisa_kirim(b)
            if it.quantity > sisa:
                if i > _NOL and it.quantity <= lama:   # akan lolos bila faktur tak dihitung -> penyebabnya faktur
                    pesan, inv = blok_difaktur(b, nama, sisa, it.quantity)
                    blocks.append(_blok("SJ_SO_BARIS_SUDAH_DIFAKTUR", pesan, 409, b["id"], **inv))
                else:
                    blocks.append(_blok("SJ_SO_QTY_MELEBIHI_SISA", f"Sisa kirim {nama} hanya {sisa:f}, diminta {it.quantity:f}.", 409, b["id"]))
                continue
            diminta[b["id"]] = it.quantity

    tgl = body.fulfillment_date or await tanggal_dokumen(conn, tid)
    ada_stok = any(peta[i]["stok"] for i in diminta)
    gudang, sumber_gudang = None, None
    if ada_stok:
        gudang, sumber_gudang = await _gudang_default(conn, tid, body.warehouse_id, so["warehouse_id"])
        if gudang is None:
            blocks.append(_blok("SJ_SO_GUDANG_WAJIB", "Pilih gudang asal barang.", 422))
        elif not gudang["is_active"]:
            blocks.append(_blok("SJ_SO_GUDANG_NONAKTIF", f"Gudang {gudang['name']} tidak aktif."))
        msg = await _periode_terbuka(conn, tid, tgl)
        if msg:
            blocks.append(_blok("SJ_SO_PERIODE_TUTUP", msg))

    lines, total_nilai = [], _NOL
    for bid, qty in diminta.items():
        b = peta[bid]
        nama = b["nama_produk"] or b["description"]
        ada = terkirim.get(bid, _NOL)
        ln = {"sales_order_item_id": str(bid), "item_id": str(b["item_id"]) if b["item_id"] else None, "name": nama,
              "unit": b["unit"], "ordered": Decimal(str(b["quantity"])), "delivered_before": ada, "quantity": qty,
              "remaining_after": Decimal(str(b["quantity"])) - ada - qty, "stok": bool(b["stok"]),
              "unit_cost": _NOL, "value": _NOL, "available": None, "item_code": b["item_code"],
              "invoiced_quantity": faktur.get(bid, {}).get("q", _NOL)}
        if b["stok"]:
            if b["batch"] or b["serial"]:
                blocks.append(_blok("SJ_SO_BATCH_SERIAL_BELUM_DIDUKUNG",
                                    f"{nama} dilacak per batch/serial; belum didukung di Surat Jalan tanpa faktur.", 409, bid))
            wac = Decimal(str(await conn.fetchval("SELECT get_weighted_average_cost($1, $2)", tid, b["item_id"]) or 0))
            if wac <= _NOL:
                blocks.append(_blok("SJ_SO_WAC_NOL", f"Biaya rata-rata {nama} belum ada (0); catat penerimaan barang dulu.", 409, bid))
            ln["unit_cost"] = wac
            ln["value"] = _r2(qty * wac)
            total_nilai += ln["value"]
            if gudang is not None:
                if kunci:
                    await _kunci(conn, f"STOCK:{b['item_id']}:{gudang['id']}")
                avail = Decimal(str(await conn.fetchval(
                    "SELECT COALESCE((SELECT available_quantity FROM warehouse_stock WHERE tenant_id = $1 AND warehouse_id = $2 "
                    "AND item_id = $3" + (" FOR UPDATE" if kunci else "") + "), 0)", tid, gudang["id"], b["item_id"])))
                ln["available"] = avail
                if avail < qty:
                    blocks.append(_blok("SJ_SO_STOK_TIDAK_CUKUP",
                                        f"Stok {nama} di {gudang['name']} hanya {avail:f}, diminta {qty:f}.", 409, bid))
        lines.append(ln)
    if not lines and not any(b["code"] != "SJ_SO_STATUS_TIDAK_BOLEH" for b in blocks):
        blocks.append(_blok("SJ_SO_TANPA_SISA", "Tidak ada barang yang tersisa untuk dikirim di pesanan ini."))

    flag = await serah_mod.aktif(conn, tid)
    serah = None
    if body.serah is not None and not flag:
        e = serah_mod.galat_nonaktif()
        blocks.append(_blok("SERAH_NONAKTIF", serah_mod.pesan_galat(e), 409))
    elif flag:
        try:
            serah = await serah_mod.selesaikan(conn, tid, so_id, body.serah)
        except HTTPException as e:
            blocks.append(_blok("SERAH_TIDAK_SAH", serah_mod.pesan_galat(e), 422))
    return {"so": so, "blocks": blocks, "lines": lines, "dilewati": dilewati, "fulfillment_date": tgl,
            "warehouse": ({"id": str(gudang["id"]), "name": gudang["name"], "source": sumber_gudang,
                           "source_label": LABEL_SUMBER_GUDANG[sumber_gudang]} if gudang else None),
            "total_value": total_nilai,
            "serah": serah, "serah_aktif": flag, "notes": body.notes,
            "payload": {"items": [{"sales_order_item_id": l["sales_order_item_id"], "quantity": str(l["quantity"])} for l in lines],
                        "fulfillment_date": tgl.isoformat(), "notes": body.notes,
                        "warehouse_id": str(gudang["id"]) if gudang else None}}


# ----------------------------------------------------------------------------
# Jurnal (DRAFT -> POSTED, Law 20/23); Σ debit == Σ kredit diverifikasi sendiri (Law 4)
# ----------------------------------------------------------------------------
async def _jurnal(conn, tid: str, user_id, *, prefix: str, tanggal, source_type: str, source_id, deskripsi: str,
                  baris: list, reversal_of=None, alasan: Optional[str] = None):
    deb = sum((Decimal(str(b[1])) for b in baris), _NOL)
    kre = sum((Decimal(str(b[2])) for b in baris), _NOL)
    if deb != kre or deb <= _NOL:
        raise HTTPException(status_code=500, detail=f"Jurnal tak seimbang ({deb} != {kre})")
    jid = uuid4()
    nomor = await conn.fetchval("SELECT get_next_journal_number($1, $2, $3)", tid, prefix, tanggal)
    if reversal_of is None:
        await conn.execute(
            """INSERT INTO journal_entries (id, tenant_id, journal_number, journal_date, description, source_type, source_id,
                                            trace_id, total_debit, total_credit, status, created_by)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $9, 'DRAFT', $10)""",
            jid, tid, nomor, tanggal, deskripsi, source_type, source_id, str(uuid4()), deb, user_id)
    else:
        await conn.execute(
            """INSERT INTO journal_entries (id, tenant_id, journal_number, journal_date, description, source_type, source_id,
                                            trace_id, total_debit, total_credit, status, created_by, reversal_of_id, reversal_reason)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $9, 'DRAFT', $10, $11, $12)""",
            jid, tid, nomor, tanggal, deskripsi, source_type, source_id, str(uuid4()), deb, user_id, reversal_of, alasan)
    for n, (akun, d, k, memo) in enumerate(baris, 1):
        await conn.execute(
            "INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            uuid4(), jid, n, akun, d, k, memo)
    await conn.execute("UPDATE journal_entries SET status = 'POSTED' WHERE id = $1 AND tenant_id = $2", jid, tid)
    return jid, nomor


async def tulis(conn, ctx: dict, so_id, body: SJSOIn) -> dict:
    """Satu SJ-so (header + baris [+ kartu + jurnal bila ber-stok]) di transaksi PEMANGGIL."""
    tid, uid = ctx["tenant_id"], ctx.get("user_id")
    await kunci_so(conn, tid, so_id)
    r = await rencana(conn, ctx, so_id, body, kunci=True)
    angkat_blok_pertama(r)
    so = r["so"]
    tgl = r["fulfillment_date"]
    nomor = await conn.fetchval("SELECT get_next_journal_number($1, $2, $3)", tid, "SJ", tgl)
    fid = uuid4()
    stok_lines = [l for l in r["lines"] if l["stok"]]
    jid = None
    gudang_id = UUID(r["warehouse"]["id"]) if r["warehouse"] else None
    if stok_lines:
        akun_gdni = await resolve_account_id_by_role(conn, tid, AccountRole.GOODS_DELIVERED_NOT_INVOICED)
        akun_inv = await resolve_account_id_by_role(conn, tid, AccountRole.INVENTORY_MERCHANDISE)
        total = sum((l["value"] for l in stok_lines), _NOL)
        jid, _ = await _jurnal(
            conn, tid, uid, prefix="DLV", tanggal=tgl, source_type=SUMBER, source_id=fid,
            deskripsi=f"Barang Terkirim {nomor} - {so['customer_name'] or ''}".strip(" -"),
            baris=[(akun_gdni, total, _NOL, f"Barang terkirim {nomor}"), (akun_inv, _NOL, total, f"Persediaan keluar {nomor}")])
    await conn.execute(
        """INSERT INTO invoice_fulfillments (id, tenant_id, invoice_id, sales_order_id, asal, fulfillment_number, fulfillment_date,
                                            warehouse_id, status, notes, journal_id, created_by, posted_at, posted_by, created_at, updated_at)
           VALUES ($1, $2, NULL, $3, 'so', $4, $5, $6, 'posted', $7, $8, $9, NOW(), $9, NOW(), NOW())""",
        fid, tid, so_id, nomor, tgl, gudang_id, r["notes"], jid, uid)
    for l in r["lines"]:
        await conn.execute(
            """INSERT INTO invoice_fulfillment_items (id, fulfillment_id, invoice_item_id, sales_order_item_id, product_id,
                                                      quantity, unit_cost, total_cost)
               VALUES ($1, $2, NULL, $3, $4, $5, $6, $7)""",
            uuid4(), fid, UUID(l["sales_order_item_id"]), UUID(l["item_id"]) if l["item_id"] else None, l["quantity"],
            l["unit_cost"], l["value"])
        if l["stok"]:   # kartu keluar (trigger ledger -> warehouse_stock; Inventory Rule 1: tak ada UPDATE langsung)
            saldo = Decimal(str(await conn.fetchval("SELECT get_inventory_balance($1, $2)", tid, UUID(l["item_id"])) or 0))
            await conn.execute(
                """INSERT INTO inventory_ledger (tenant_id, product_id, product_code, product_name, movement_type, movement_date,
                                                 source_type, source_id, source_number, quantity_in, quantity_out, quantity_balance,
                                                 unit_cost, total_cost, average_cost, journal_id, created_by, notes, warehouse_id)
                   VALUES ($1, $2, $3, $4, 'SALE', $5, $6, $7, $8, 0, $9, $10, $11, $12, $11, $13, $14, $15, $16)""",
                tid, UUID(l["item_id"]), l["item_code"] or "", l["name"], tgl, SUMBER, fid, nomor, l["quantity"],
                saldo - l["quantity"], l["unit_cost"], l["value"], jid, uid, f"Surat Jalan {nomor}", gudang_id)
    if r.get("serah") is not None:   # SJ + serah + resi: SATU transaksi (Law 23); nol UPDATE ke SJ
        await serah_mod.tulis(conn, tid, uid, fid, r["serah"])
    await catat_riwayat(conn, tid, "invoice_fulfillments", fid, nomor, "SURAT_JALAN_SO_DIBUAT", uid,
                        f"Surat Jalan {nomor} dibuat dari pesanan {so['order_number']} (tanpa faktur, {len(r['lines'])} baris"
                        + (f", barang terkirim Rp{r['total_value']:,.2f}" if stok_lines else "") + ")",
                        {"sales_order_id": str(so_id), "baris": len(r["lines"]), "nilai": str(r["total_value"])},
                        source="api:sj_so.create")
    return {"fulfillment_id": fid, "fulfillment_number": nomor, "rencana": r, "journal_id": jid}


# ----------------------------------------------------------------------------
# VOID mandiri: asal 'so' yang belum ditagih (di sini) + asal 'faktur' (VOID-SJ-FAKTUR 15 Okt 2026 -> services/sj_faktur_batal;
# faktur tetap utuh, pola NetSuite Item Fulfillment). Endpoint SAMA untuk keduanya.
# ----------------------------------------------------------------------------
async def rencana_void(conn, tid: str, fid, alasan: Optional[str], kunci: bool = False) -> dict:
    blocks: list = []
    f = await conn.fetchrow(
        """SELECT f.id, f.fulfillment_number, f.status, f.asal, f.sales_order_id, f.invoice_id, f.voided_at, f.journal_id,
                  f.revenue_journal_id, f.fulfillment_date
           FROM invoice_fulfillments f WHERE f.id = $1 AND f.tenant_id = $2""" + (" FOR UPDATE" if kunci else ""), fid, tid)
    if not f:
        raise HTTPException(status_code=404, detail="Surat Jalan tidak ditemukan.")
    if f["asal"] == "faktur":
        from . import sj_faktur_batal
        return await sj_faktur_batal.rencana(conn, tid, f, alasan, _blok, _periode_terbuka)
    await pastikan_aktif(conn, tid)  # flag pengiriman_sebelum_faktur: hanya SJ asal 'so'
    ulang = f["status"] == "voided"
    if not ulang:
        akt = await conn.fetch(
            """SELECT si.invoice_number, SUM(a.quantity) AS q FROM sj_faktur_alokasi a
               JOIN invoice_fulfillment_items i ON i.id = a.fulfillment_item_id
               JOIN sales_invoice_items sii ON sii.id = a.invoice_item_id
               JOIN sales_invoices si ON si.id = sii.invoice_id AND si.tenant_id = a.tenant_id
               WHERE i.fulfillment_id = $1 AND a.tenant_id = $2 AND a.voided_at IS NULL GROUP BY si.invoice_number""", fid, tid)
        if akt:
            blocks.append(_blok("SJ_SUDAH_DITAGIH", "Surat Jalan ini sudah ditautkan ke faktur " +
                                ", ".join(sorted(a["invoice_number"] for a in akt)) + "; batalkan fakturnya dulu."))
    if not ulang and not (alasan or "").strip():
        blocks.append(_blok("SJ_ALASAN_WAJIB", "Alasan pembatalan wajib diisi.", 422))
    if not ulang and f["asal"] == "so" and f["journal_id"] is not None:
        hari = await tanggal_dokumen(conn, tid)
        msg = await _periode_terbuka(conn, tid, hari)
        if msg:
            blocks.append(_blok("SJ_PERIODE_TUTUP", msg))
    return {"sj": f, "blocks": blocks, "ulang": ulang}


async def void(conn, ctx: dict, fid, alasan: str) -> dict:
    tid, uid = ctx["tenant_id"], ctx.get("user_id")
    pre = await conn.fetchrow("SELECT sales_order_id, asal, invoice_id FROM invoice_fulfillments WHERE id = $1 AND tenant_id = $2",
                              fid, tid)
    if not pre:
        raise HTTPException(status_code=404, detail="Surat Jalan tidak ditemukan.")
    if pre["asal"] == "faktur":
        from . import sj_faktur_batal
        await sj_faktur_batal.kunci(conn, tid, pre["invoice_id"])
        r = await rencana_void(conn, tid, fid, alasan, kunci=True)
        if r["ulang"]:
            return {"ulang": True}
        angkat_blok_pertama(r)
        await sj_faktur_batal.void(conn, ctx, r, alasan, _jurnal)
        return {"ulang": False}
    if pre["sales_order_id"] is not None:
        await kunci_so(conn, tid, pre["sales_order_id"])
    r = await rencana_void(conn, tid, fid, alasan, kunci=True)
    if r["ulang"]:
        return {"ulang": True}
    angkat_blok_pertama(r)
    f = r["sj"]
    hari = await tanggal_dokumen(conn, tid)
    if f["journal_id"] is not None:
        orig = await conn.fetch(
            "SELECT account_id, debit, credit, memo FROM journal_lines WHERE journal_id = $1 ORDER BY line_number", f["journal_id"])
        rid, _ = await _jurnal(
            conn, tid, uid, prefix="DLV-REV", tanggal=hari, source_type=SUMBER_BALIK, source_id=fid,
            deskripsi=f"VOID Barang Terkirim {f['fulfillment_number']}",
            baris=[(o["account_id"], o["credit"], o["debit"], f"VOID: {o['memo']}") for o in orig],
            reversal_of=f["journal_id"], alasan=alasan)
        await conn.execute("UPDATE journal_entries SET reversed_by_id = $2, reversed_at = NOW() WHERE id = $1 AND tenant_id = $3",
                           f["journal_id"], rid, tid)
        from .inventory_helpers import record_inventory_reversal
        await record_inventory_reversal(conn, tid, source_type=SUMBER, source_id=fid, reversal_journal_id=rid,
                                        created_by=uid, reversal_date=hari, notes_prefix="VOID")
    # trigger trg_so_shipped_recompute (AFTER UPDATE) menghitung ulang SO: yang DIHARAPKAN pada void.
    await conn.execute(
        "UPDATE invoice_fulfillments SET status = 'voided', voided_at = NOW(), voided_reason = $3, updated_at = NOW() "
        "WHERE id = $1 AND tenant_id = $2 AND asal = 'so'", fid, tid, alasan)
    await catat_riwayat(conn, tid, "invoice_fulfillments", fid, f["fulfillment_number"], "SURAT_JALAN_SO_DIBATALKAN", uid,
                        f"Surat Jalan {f['fulfillment_number']} dibatalkan: {alasan}", {"alasan": alasan}, source="api:sj_so.void")
    return {"ulang": False}


# ----------------------------------------------------------------------------
# Alokasi saat faktur diposting (dipanggil sales_invoices di transaksi posting) & pelepasan/pembalik saat void faktur
# ----------------------------------------------------------------------------
async def alokasikan_saat_faktur(conn, tenant_id: str, user_id, invoice_id) -> dict:
    """Tautkan sisa SJ-so ke baris faktur bertautan baris SO yang sama, TERTUA-DULU (INSERT-only ke sj_faktur_alokasi), lalu naikkan
    `sales_invoice_items.fulfilled_qty`. BER-STOK: jurnal reklas Dr HPP / Cr Barang Terkirim pada snapshot SJ + INVOICE_REVENUE
    untuk qty teralokasi, `recognized_amount` naik. Non-stok: hanya tautan + fulfilled_qty (pendapatan sudah diakui saat posting).
    Tanpa SJ-so -> nol efek. -> {"qty": Decimal total, "stok_qty": Decimal, "nilai_hpp": Decimal, "pendapatan": Decimal}."""
    hasil = {"qty": _NOL, "stok_qty": _NOL, "nilai_hpp": _NOL, "pendapatan": _NOL}
    if not await conn.fetchval(
            "SELECT 1 FROM invoice_fulfillments WHERE tenant_id = $1 AND asal = 'so' AND status <> 'voided' LIMIT 1", tenant_id):
        return hasil
    inv = await conn.fetchrow(
        "SELECT invoice_number, invoice_date, customer_name FROM sales_invoices WHERE id = $1 AND tenant_id = $2", invoice_id, tenant_id)
    items = await conn.fetch(
        """SELECT sii.id, sii.sales_order_item_id, sii.quantity, COALESCE(sii.fulfilled_qty, 0) AS fulfilled_qty,
                  COALESCE(sii.allocated_amount, 0) AS allocated, COALESCE(sii.recognized_amount, 0) AS recognized,
                  COALESCE(sii.perlu_kirim, false) AS perlu_kirim, COALESCE(p.track_inventory, false) AS stok
           FROM sales_invoice_items sii
           LEFT JOIN products p ON p.id = sii.item_id AND p.tenant_id = $2
           WHERE sii.invoice_id = $1 AND sii.sales_order_item_id IS NOT NULL
           ORDER BY sii.id""", invoice_id, tenant_id)
    rencana_alok = []   # (item, chunk list, ambil_total, biaya_total, pendapatan)
    for it in items:
        if not it["stok"] and not it["perlu_kirim"]:
            continue
        butuh = Decimal(str(it["quantity"])) - Decimal(str(it["fulfilled_qty"]))
        if butuh <= _NOL:
            continue
        sisa_sj = await conn.fetch(
            """SELECT i.id, i.quantity, i.unit_cost, i.total_cost,
                      i.quantity - COALESCE((SELECT SUM(a.quantity) FROM sj_faktur_alokasi a
                                             WHERE a.fulfillment_item_id = i.id AND a.voided_at IS NULL), 0) AS sisa,
                      COALESCE((SELECT SUM(a.total_cost) FROM sj_faktur_alokasi a
                                WHERE a.fulfillment_item_id = i.id AND a.voided_at IS NULL), 0) AS biaya_terpakai
               FROM invoice_fulfillment_items i
               JOIN invoice_fulfillments f ON f.id = i.fulfillment_id AND f.tenant_id = $1 AND f.asal = 'so'
                    AND f.status <> 'voided' AND f.voided_at IS NULL
               WHERE i.sales_order_item_id = $2
               ORDER BY f.fulfillment_date, f.created_at, i.id FOR UPDATE OF i""", tenant_id, it["sales_order_item_id"])
        chunks, ambil_total, biaya_total, sisa_butuh = [], _NOL, _NOL, butuh
        for s in sisa_sj:
            sisa = Decimal(str(s["sisa"]))
            if sisa <= _NOL or sisa_butuh <= _NOL:
                continue
            ambil = sisa if sisa < sisa_butuh else sisa_butuh
            if it["stok"]:
                # clearing tepat: alokasi yang menghabiskan sisa baris SJ mengambil sisa BIAYA (snapshot) -> Barang Terkirim nol
                biaya = (Decimal(str(s["total_cost"])) - Decimal(str(s["biaya_terpakai"]))) if ambil == sisa \
                    else _r2(Decimal(str(s["unit_cost"])) * ambil)
            else:
                biaya = _NOL
            chunks.append((s["id"], ambil, biaya))
            sisa_butuh -= ambil
            ambil_total += ambil
            biaya_total += biaya
        if ambil_total <= _NOL:
            continue
        pendapatan = _NOL
        if it["stok"]:
            sisa_rev = Decimal(str(it["allocated"])) - Decimal(str(it["recognized"]))
            if ambil_total == butuh:       # menghabiskan sisa qty baris -> ambil sisa pendapatan (aturan _execute_fulfillment)
                pendapatan = sisa_rev
            else:
                pendapatan = min(_r2(Decimal(str(it["allocated"])) * ambil_total / Decimal(str(it["quantity"]))), sisa_rev)
            if pendapatan < _NOL:
                pendapatan = _NOL
        rencana_alok.append((it, chunks, ambil_total, biaya_total, pendapatan))

    cogs_jid = rev_jid = None
    total_hpp = sum((x[3] for x in rencana_alok), _NOL)
    total_rev = sum((x[4] for x in rencana_alok), _NOL)
    if total_hpp > _NOL:
        akun_hpp = await resolve_account_id_by_role(conn, tenant_id, AccountRole.COGS_SALES)
        akun_gdni = await resolve_account_id_by_role(conn, tenant_id, AccountRole.GOODS_DELIVERED_NOT_INVOICED)
        cogs_jid, _ = await _jurnal(
            conn, tenant_id, user_id, prefix="COGS", tanggal=inv["invoice_date"], source_type=SUMBER_REKLAS, source_id=invoice_id,
            deskripsi=f"HPP {inv['invoice_number']} (barang terkirim ditagih) - {inv['customer_name'] or ''}".strip(" -"),
            baris=[(akun_hpp, total_hpp, _NOL, f"HPP {inv['invoice_number']}"),
                   (akun_gdni, _NOL, total_hpp, f"Barang Terkirim -> HPP {inv['invoice_number']}")])
    if total_rev > _NOL:
        from ..routers.sales_invoices import _resolve_unearned_revenue
        akun_dimuka = await _resolve_unearned_revenue(conn, tenant_id)
        akun_jual = await resolve_account_id_by_role(conn, tenant_id, AccountRole.REVENUE_SALES_GOODS)
        rev_jid, _ = await _jurnal(
            conn, tenant_id, user_id, prefix="RECOG", tanggal=inv["invoice_date"], source_type="INVOICE_REVENUE", source_id=invoice_id,
            deskripsi=f"Revenue Recognition {inv['invoice_number']} (barang terkirim ditagih)",
            baris=[(akun_dimuka, total_rev, _NOL, f"Dimuka -> Penjualan {inv['invoice_number']}"),
                   (akun_jual, _NOL, total_rev, f"Penjualan {inv['invoice_number']}")])
    for it, chunks, ambil_total, biaya_total, pendapatan in rencana_alok:
        for ifi_id, ambil, biaya in chunks:
            await conn.execute(
                """INSERT INTO sj_faktur_alokasi (tenant_id, fulfillment_item_id, invoice_item_id, quantity, total_cost,
                                                  cogs_journal_id, revenue_journal_id, created_by)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                tenant_id, ifi_id, it["id"], ambil, biaya, cogs_jid if biaya > _NOL else None,
                rev_jid if (pendapatan > _NOL) else None, user_id)
        await conn.execute(
            "UPDATE sales_invoice_items SET fulfilled_qty = COALESCE(fulfilled_qty, 0) + $2, "
            "recognized_amount = COALESCE(recognized_amount, 0) + $3 WHERE id = $1", it["id"], ambil_total, pendapatan)
        hasil["qty"] += ambil_total
        if it["stok"]:
            hasil["stok_qty"] += ambil_total
    hasil["nilai_hpp"], hasil["pendapatan"] = total_hpp, total_rev
    return hasil


async def balik_alokasi_faktur(conn, ctx: dict, invoice_id, hari, alasan: str) -> int:
    """Void faktur: (1) jurnal reklas DELIVERY_INVOICED dibalik (DELIVERY_INVOICED_REVERSAL; Law 2/26) -> Barang Terkirim kembali
    memuat nilai SJ; (2) alokasi ditandai batal (SJ-so KEMBALI 'belum ditagih', TIDAK ikut void). Jurnal INVOICE_REVENUE alokasi
    ikut dibalik oleh blok 'orphan revenue' void faktur (dibuat mengulang SEMUA jurnal yang belum dibalik). -> jumlah alokasi dilepas."""
    tid, uid = ctx["tenant_id"], ctx.get("user_id")
    for j in await conn.fetch(
            """SELECT DISTINCT a.cogs_journal_id AS id FROM sj_faktur_alokasi a
               JOIN sales_invoice_items sii ON sii.id = a.invoice_item_id
               WHERE sii.invoice_id = $1 AND a.tenant_id = $2 AND a.voided_at IS NULL AND a.cogs_journal_id IS NOT NULL""",
            invoice_id, tid):
        je = await conn.fetchrow(
            "SELECT id, reversed_by_id FROM journal_entries WHERE id = $1 AND tenant_id = $2", j["id"], tid)
        if je is None or je["reversed_by_id"] is not None:
            continue
        orig = await conn.fetch(
            "SELECT account_id, debit, credit, memo FROM journal_lines WHERE journal_id = $1 ORDER BY line_number", j["id"])
        rid, _ = await _jurnal(
            conn, tid, uid, prefix="REV", tanggal=hari, source_type=SUMBER_REKLAS_BALIK, source_id=invoice_id,
            deskripsi=f"VOID HPP barang terkirim faktur {invoice_id}",
            baris=[(o["account_id"], o["credit"], o["debit"], f"VOID: {o['memo']}") for o in orig],
            reversal_of=j["id"], alasan=alasan)
        await conn.execute("UPDATE journal_entries SET reversed_by_id = $2, reversed_at = NOW() WHERE id = $1 AND tenant_id = $3",
                           j["id"], rid, tid)
    return await lepas_alokasi_faktur(conn, tid, invoice_id, alasan)


async def lepas_alokasi_faktur(conn, tenant_id: str, invoice_id, alasan: str) -> int:
    r = await conn.execute(
        """UPDATE sj_faktur_alokasi a SET voided_at = NOW(), voided_reason = $3
           FROM sales_invoice_items sii
           WHERE a.invoice_item_id = sii.id AND sii.invoice_id = $1 AND a.tenant_id = $2 AND a.voided_at IS NULL""",
        invoice_id, tenant_id, alasan)
    try:
        return int(str(r).split()[-1])
    except (ValueError, IndexError):
        return 0
