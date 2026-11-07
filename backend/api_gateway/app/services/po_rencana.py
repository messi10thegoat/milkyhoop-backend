"""Pesanan Pembelian -- PENENTU per aksi (kontrak PO tahap 2, 22 Okt 2026; pola SO `_rencana_*`, acuan NetSuite PO).

SATU definisi aturan per aksi, dipakai DUA jalur:
  * endpoint TULIS (POST /purchase-orders, PATCH, /send, /receive, /close, /cancel, DELETE): blok pertama -> galat HTTP (pesan & status
    lama dipertahankan), tanpa blok -> tulis;
  * endpoint PRATINJAU (/calculate, /{id}/edit|send|receive|close|cancel|delete/preview): SEMUA blok {code, message}, 200, transaksi
    SELALU di-ROLLBACK (nol tulisan), `payload` siap kirim ke endpoint tulisnya.
Uang = services/po_hitung (Decimal 2dp HALF_UP) -- kalkulator yang SAMA dengan create/patch. Semua pembacaan tenant eksplisit.
KUNCI (koreksi MASTER 22 Okt; pola NetSuite: pratinjau = baca, commit = validasi ulang): endpoint TULIS memanggil penentu SESUDAH _kunci_po
(advisory + FOR UPDATE) dengan kunci=True -> hasil final diputuskan di bawah lock; PRATINJAU memanggil dengan kunci=False di transaksi
READ ONLY -> tak memegang row lock pada PO yang sedang dibuka orang lain.

Blok: {"code", "message", "status"} -- `status` = kode HTTP endpoint tulis (dibuang dari respons pratinjau)."""
from decimal import Decimal
from typing import Optional
from uuid import UUID

from . import po_daftar as PD
from . import po_hitung as PH

NOL = Decimal("0")
STATUS_BISA_UBAH = ("draft", "sent")
STATUS_BISA_TERIMA = ("sent", "partial_received")
JASA_LANGSUNG_TAGIH = "jasa tidak perlu diterima. Langsung buat faktur pembelian."  # JASA-TANPA-TERIMA (5 Nov 2026)
# GRN aturan D (pemilik 23 Okt): tagihan boleh LEBIH DULU dari penerimaan -> PO yang sudah (sebagian) ditagih tetap bisa diterima selama ada
# sisa terima. HANYA saat flag penerimaan_barang_v1 nyala: jalur lama (flag mati) memasukkan stok saat tagihan, penerimaan sesudahnya = dobel.
STATUS_BISA_TERIMA_D = STATUS_BISA_TERIMA + ("partial_billed", "billed")
STATUS_DITAGIH = ("partial_billed", "billed")
STATUS_TAK_BISA_TUTUP = ("draft", "cancelled", "closed")
STATUS_TAK_BISA_BATAL = ("cancelled", "closed")


def status_bisa_terima(aturan_d: bool) -> tuple:
    """Status PO yang bisa diterima: aturan D (flag penerimaan_barang_v1: tagih dulu, terima kemudian) menambah status ditagih.
    SATU sumber untuk penentu terima DAN saringan daftar ?aksi=bisa_diterima (po_daftar.sql_aksi)."""
    return STATUS_BISA_TERIMA_D if aturan_d else STATUS_BISA_TERIMA


def blok(code: str, message: str, status: int = 400) -> dict:
    return {"code": code, "message": message, "status": status}


def untuk_respons(bloks: list) -> list:
    return [{"code": b["code"], "message": b["message"]} for b in bloks]


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else NOL


def _f(v):
    return float(PH.r2(v)) if v is not None else None


# ---------------------------------------------------------------- hitung (create / patch / calculate)
async def faktor_masukan(conn, tenant_id: str, tarif, cache=None) -> tuple:
    """BUG-088: faktor DPP (num, den) pajak MASUKAN dari tarif -- SATU penentu PO + tagih-dari-PO (header & baris): kode masukan aktif bertarif
    sama (kode_pajak_masukan) -> resolve_dpp_factor 'input'. PPN 12% nyawit/kaos = 11/12; tarif 0 = (1, 1)."""
    from . import tax_factor as TF
    if _d(tarif) <= NOL:
        return (1, 1)
    return await TF.resolve_dpp_factor(conn, tenant_id, await kode_pajak_masukan(conn, tenant_id, tarif), _d(tarif), "input", cache)


async def hitung_po(conn, tenant_id: str, items: list, discount_percent=0, discount_amount=0, tax_rate=0, tolak: bool = False) -> dict:
    """items: dict baris (bentuk PurchaseOrderItemCreate.model_dump() atau baris DB). -> {baris (Decimal), header (Decimal)}. SATU kalkulator
    /calculate, create, PATCH, pratinjau ubah (BUG-088: + faktor DPP per tarif). tolak=False (pratinjau): diskon berlebih jadi BLOK
    (PH.blok_diskon_baris) di pemanggil; tolak=True (penulis): GalatBerkode 400. Baris tanpa medan qty (baris DB) dipakai apa adanya."""
    cache = {}
    baris = [PH.hitung_baris(i, tolak=tolak, faktor=await faktor_masukan(conn, tenant_id, i.get("tax_rate"), cache)) if "quantity" in i else i
             for i in items]
    faktor = await faktor_masukan(conn, tenant_id, tax_rate, cache)
    return {"baris": baris, "header": PH.hitung_header(baris, discount_percent, discount_amount, tax_rate, faktor=faktor)}


def hitung_json(h: dict) -> dict:
    """Bentuk respons /calculate (sejajar SO /calculate): per baris + header, angka float 2dp."""
    t = h["header"]
    return {
        "subtotal": _f(t["subtotal"]), "discount_amount": _f(t["discount_amount"]), "tax_amount": _f(t["tax_amount"]),
        "total_amount": _f(t["total_amount"]),
        "items": [{"line_number": n, "description": b.get("description"), "quantity": _f(b.get("quantity")) if b.get("quantity") is not None else None,
                   "unit": b.get("unit"), "unit_price": _f(b.get("unit_price")), "discount_percent": float(b.get("discount_percent") or 0),
                   "tax_rate": float(b.get("tax_rate") or 0), "subtotal": _f(b["subtotal"]), "discount_amount": _f(b["discount_amount"]),
                   "tax_amount": _f(b["tax_amount"]), "total": _f(b["total"])}
                  for n, b in enumerate(h["baris"], 1)],
    }


async def blok_vendor(conn, tenant_id: str, vendor_id) -> tuple:
    """(vendor|None, [blok]) -- Law 24: vendor_id wajib milik tenant. Sama dengan _vendor_tenant router (422)."""
    if not vendor_id:
        return None, []
    try:
        vid = UUID(str(vendor_id))
    except (ValueError, TypeError):
        return None, [blok("VENDOR_TIDAK_SAH", "ID vendor tidak sah.", 422)]
    v = await conn.fetchrow("SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2", vid, tenant_id)
    if not v:
        return None, [blok("VENDOR_TIDAK_DITEMUKAN", "Vendor tidak ditemukan di usaha ini.", 422)]
    return v, []


# ---------------------------------------------------------------- ubah (PATCH)
async def rencana_ubah(conn, tenant_id: str, po, body) -> dict:
    """po = baris PO terkunci (id, status, po_number + kolom header). body = UpdatePurchaseOrderRequest.
    -> {blocks, perubahan[{field, before, after}], header_sesudah (Decimal|None bila uang tak berubah), baris_baru (list|None)}."""
    bloks = []
    if po["status"] not in STATUS_BISA_UBAH:
        bloks.append(blok("PO_TAK_BISA_DIUBAH",
                          f"Pesanan pembelian berstatus '{po['status']}' tidak bisa diubah. Hanya Draf atau Terkirim."))
    data = body.model_dump(exclude_unset=True)
    if data.get("vendor_id"):
        _, bv = await blok_vendor(conn, tenant_id, data["vendor_id"])
        bloks += bv
    lama = await conn.fetch(
        """SELECT id, description, quantity, quantity_received, quantity_billed, subtotal, discount_amount, tax_amount
           FROM purchase_order_items WHERE po_id = $1 ORDER BY line_number""", po["id"])
    baris_baru = None
    if data.get("items"):
        # PATCH mengganti SEMUA baris: baris yang sudah diterima/ditagih akan kehilangan jejak qty -> tolak (NetSuite: baris yang sudah
        # diterima tak boleh dikurangi di bawah qty diterima). Dengan status Draf/Terkirim mestinya nol; penjaga ini menjaga data lama.
        if any(_d(r.get("quantity_received")) > NOL or _d(r.get("quantity_billed")) > NOL for r in lama):
            bloks.append(blok("PO_BARIS_SUDAH_DITERIMA", "Baris yang barangnya sudah diterima atau ditagih tidak bisa diganti."))
        bloks.extend(PH.blok_diskon_baris([i.model_dump() for i in body.items]))  # U-SI-LAW: kalimat == 400 penulis
        baris_baru = (await hitung_po(conn, tenant_id, [i.model_dump() for i in body.items]))["baris"]
    uang = baris_baru is not None or any(k in data for k in ("discount_percent", "discount_amount", "tax_rate"))
    header = None
    if uang:
        dasar = baris_baru if baris_baru is not None else [{k: v for k, v in dict(r).items() if k != "quantity"} for r in lama]
        header = (await hitung_po(conn, tenant_id, dasar, data.get("discount_percent", po.get("discount_percent")) or 0,
                                  data.get("discount_amount", po.get("discount_amount")) or 0,
                                  data.get("tax_rate", po.get("tax_rate")) or 0))["header"]
    perubahan = []
    for k, v in data.items():
        if k == "items":
            perubahan.append({"field": "items", "before": len(lama), "after": len(v)})
        else:
            sebelum = po.get(k)
            if str(sebelum if sebelum is not None else "") != str(v if v is not None else ""):
                perubahan.append({"field": k, "before": _j(sebelum), "after": _j(v)})
    if header is not None and _d(po.get("total_amount")) != header["total_amount"]:
        perubahan.append({"field": "total_amount", "before": _f(po.get("total_amount")), "after": _f(header["total_amount"])})
    return {"blocks": bloks, "perubahan": perubahan, "header_sesudah": header, "baris_baru": baris_baru}


def _j(v):
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return None if v is None else (v if isinstance(v, (int, float, bool)) else str(v))


# ---------------------------------------------------------------- kirim / tutup / batal / hapus
async def rencana_kirim(conn, tid: str, po) -> dict:
    """Kirim (Draf -> Terkirim). Baris ber-stok wajib bersatuan dasar/berkonversi (penentu SAMA dgn Terima, kalimat sama) -- satuan
    tanpa konversi ditangkap di dokumen awal, bukan baru saat barang datang. Draf tetap boleh disimpan."""
    bloks = [] if po["status"] == "draft" else [
        blok("PO_TAK_BISA_DIKIRIM", f"Pesanan pembelian berstatus '{po['status']}' tidak bisa dikirim. Hanya Draf.")]
    from . import satuan_dasar as SD
    stok = await conn.fetch(
        """SELECT i.item_id AS product_id, i.unit, i.quantity FROM purchase_order_items i JOIN products p ON p.id = i.item_id AND p.tenant_id = $2
           WHERE i.po_id = $1 AND p.item_type = 'goods' AND COALESCE(p.track_inventory, true) ORDER BY i.line_number""", po["id"], tid)
    bloks += [blok(x["code"], x["message"], x["status"]) for x in await SD.blok_baris(conn, tid, [dict(r) for r in stok], "konversi")]
    return {"blocks": bloks, "status_sesudah": "sent"}


async def rencana_tutup(conn, po) -> dict:
    bloks = [] if po["status"] not in STATUS_TAK_BISA_TUTUP else [
        blok("PO_TAK_BISA_DITUTUP", f"Pesanan pembelian berstatus '{po['status']}' tidak bisa ditutup.")]
    q = await conn.fetchrow(
        """SELECT COALESCE(SUM(GREATEST(quantity - COALESCE(quantity_received, 0), 0)), 0) AS sisa_terima,
                  COALESCE(SUM(GREATEST(COALESCE(quantity_received, 0) - COALESCE(quantity_billed, 0), 0)), 0) AS belum_ditagih
           FROM purchase_order_items WHERE po_id = $1""", po["id"]) or {}
    catatan = []
    if _d(q.get("sisa_terima")) > NOL:
        catatan.append({"code": "SISA_BARANG_TAK_DITERIMA", "message": f"Sisa {PH.r2(q['sisa_terima'])} unit belum diterima tidak akan ditunggu lagi."})
    if _d(q.get("belum_ditagih")) > NOL:
        catatan.append({"code": "DITERIMA_BELUM_DITAGIH",
                        "message": f"{PH.r2(q['belum_ditagih'])} unit sudah diterima tetapi belum ditagih; sesudah ditutup tidak bisa ditagih dari pesanan ini."})
    return {"blocks": bloks, "status_sesudah": "closed", "sisa_ditagih_sesudah": NOL, "notes": catatan}


async def rencana_batal(conn, tenant_id: str, po, alasan: Optional[str]) -> dict:
    bloks = []
    if po["status"] in STATUS_TAK_BISA_BATAL:
        bloks.append(blok("PO_TAK_BISA_DIBATALKAN", f"Pesanan pembelian berstatus '{po['status']}' tidak bisa dibatalkan."))
    q = await conn.fetchrow(
        "SELECT COALESCE(SUM(quantity_received), 0) AS diterima, COALESCE(SUM(quantity_billed), 0) AS ditagih "
        "FROM purchase_order_items WHERE po_id = $1", po["id"]) or {}
    if _d(q.get("diterima")) > NOL:
        bloks.append(blok("PO_SUDAH_DITERIMA", "Pesanan pembelian yang barangnya sudah diterima tidak bisa dibatalkan."))
    if _d(q.get("ditagih")) > NOL:
        bloks.append(blok("PO_SUDAH_DITAGIH", "Pesanan pembelian yang sudah ditagih tidak bisa dibatalkan."))
    if not (alasan or "").strip():
        bloks.append(blok("ALASAN_WAJIB", "Alasan pembatalan wajib diisi.", 422))
    um = await conn.fetch(
        """SELECT deposit_number FROM vendor_deposits WHERE tenant_id = $1 AND purchase_order_id = $2 AND status NOT IN ('draft', 'void')
           ORDER BY deposit_number""", tenant_id, po["id"])
    catatan = [{"code": "UANG_MUKA_TETAP", "message": f"Uang muka {r['deposit_number']} tetap tercatat; terapkan ke tagihan lain atau minta pengembalian."}
               for r in um]
    return {"blocks": bloks, "status_sesudah": "cancelled", "sisa_ditagih_sesudah": NOL, "notes": catatan}


def rencana_hapus(po) -> dict:
    bloks = [] if po["status"] == "draft" else [
        blok("PO_BUKAN_DRAF", "Hanya pesanan pembelian berstatus Draf yang bisa dihapus. Yang sudah terkirim dibatalkan.")]
    return {"blocks": bloks}


# ---------------------------------------------------------------- terima
async def toleransi_terima(conn, tenant_id: str) -> Decimal:
    """TERIMA-TABEL (V477): persen lebih-terima yang diizinkan per tenant (purchase_settings); tanpa baris = 0 = perilaku lama."""
    return _d(await conn.fetchval("SELECT penerimaan_toleransi_persen FROM purchase_settings WHERE tenant_id = $1", tenant_id) or 0)


async def rencana_terima(conn, po, receive_date, items: Optional[list], hari_ini, kunci: bool = True, notes: Optional[str] = None,
                         sj_vendor: Optional[str] = None) -> dict:
    """items: [{po_item_id, quantity_received, quantity_rejected?, reject_reason?}] atau None/[] = SEMUA sisa (bawaan pratinjau). -> {blocks,
    notes, baris[], nilai_diterima (penerimaan ini, yang diterima saja), amount_received_sesudah, status_sesudah, toleransi_persen, payload}.
    Aturan & urutan = /receive. TERIMA-TABEL (4 Nov 2026): DITOLAK hanya dicatat di baris GRN (bukan stok/HPP/jurnal/tagih) dan TIDAK
    mengurangi sisa (pola NetSuite/Odoo: barang pengganti masih ditunggu). Lebih-terima boleh sampai sisa x (1 + toleransi%).
    JASA-TANPA-TERIMA (5 Nov 2026): baris jasa (grn_rencana.sql_jasa) tak pernah diterima -- dilewati bawaan, diblok bila dikirim, tak ikut
    baris[]/lengkap; PO tanpa baris barang = PO_TANPA_BARANG (jasa ditagih menurut qty pesanan)."""
    bloks, catatan = [], []
    from . import grn_rencana as G
    aturan_d = po["status"] in STATUS_DITAGIH and await G.aktif(conn, po.get("tenant_id"))
    if po["status"] not in status_bisa_terima(aturan_d):
        bloks.append(blok("PO_TAK_BISA_DITERIMA", f"Barang tidak bisa diterima untuk pesanan pembelian berstatus '{po['status']}'."))
    if receive_date is None:
        receive_date = hari_ini
    if receive_date > hari_ini:
        bloks.append(blok("TANGGAL_TERIMA_MASA_DEPAN", "Tanggal terima tidak boleh di masa depan."))
    if po.get("po_date") and receive_date < po["po_date"]:
        bloks.append(blok("TANGGAL_TERIMA_SEBELUM_PO", "Tanggal terima tidak boleh sebelum tanggal pesanan."))
    semua = await conn.fetch(
        f"""SELECT i.id, i.description, i.quantity, i.quantity_received, i.quantity_billed, i.unit, i.total, {G.sql_jasa("i", "$2")} AS jasa
            FROM purchase_order_items i WHERE i.po_id = $1 ORDER BY i.line_number""" + (" FOR UPDATE" if kunci else ""),
        po["id"], po.get("tenant_id"))
    peta = {str(r["id"]): r for r in semua}
    barang = [r for r in semua if not r["jasa"]]
    if semua and not barang:
        bloks.append(blok("PO_TANPA_BARANG", f"Pesanan ini hanya berisi jasa; {JASA_LANGSUNG_TAGIH}"))
        items = []
    elif not items:
        items = [{"po_item_id": str(r["id"]), "quantity_received": _d(r["quantity"]) - _d(r["quantity_received"])}
                 for r in barang if _d(r["quantity"]) - _d(r["quantity_received"]) > NOL]
        if not items:
            bloks.append(blok("TAK_ADA_SISA_TERIMA", "Semua barang pesanan ini sudah diterima."))
    from .teks_galat import qty as _q
    toleransi = await toleransi_terima(conn, po.get("tenant_id"))
    akan, tolak, alasan = {}, {}, {}
    for it in items:
        pid, ditolak = str(it["po_item_id"]), _d(it.get("quantity_rejected"))
        r = peta.get(pid)
        if r is None:
            bloks.append(blok("BARIS_TIDAK_DITEMUKAN", f"Baris pesanan {pid} tidak ditemukan."))
            continue
        if r["jasa"]:
            bloks.append(blok("BARIS_JASA_TANPA_TERIMA", f"{r.get('description') or pid} adalah jasa; {JASA_LANGSUNG_TAGIH}"))
            continue
        # putusan MASTER (A) 4 Nov: diterima ABSEN = sisa - ditolak ("vendor bawa 90, 5 rusak" -> 85 + 5, sisa 5 masih ditunggu);
        # diterima eksplisit dihormati apa adanya (aturan blok lama tetap)
        qty = (_d(it["quantity_received"]) if it.get("quantity_received") is not None
               else max(_d(r["quantity"]) - _d(r["quantity_received"]) - ditolak, NOL))
        if qty < NOL or ditolak < NOL:
            if not any(b["code"] == "QTY_NEGATIF" for b in bloks):
                bloks.append(blok("QTY_NEGATIF", "Jumlah tidak boleh negatif."))
            continue
        nama = r.get("description") or pid
        sisa = _d(r["quantity"]) - _d(r["quantity_received"])
        batas = sisa * (1 + toleransi / 100)
        # angka kalimat lewat teks_galat.qty (putusan MASTER 4 Nov: tanpa Decimal mentah "1.0000"/"10.00%"; sama dengan to-bill)
        if toleransi == NOL and qty > sisa:
            bloks.append(blok("QTY_MELEBIHI_SISA", f"Jumlah diterima ({_q(qty)}) melebihi sisa ({_q(sisa)})."))
        elif qty > batas:
            bloks.append(blok("QTY_MELEBIHI_TOLERANSI",
                              f"Jumlah diterima {nama} ({_q(qty)}) melebihi sisa ({_q(sisa)}) lebih dari toleransi {_q(toleransi)}%."))
        elif qty > sisa:
            catatan.append({"code": "RECEIPT_LEBIH", "message": f"Diterima {_q(qty - sisa)} lebih dari pesanan (dalam toleransi {_q(toleransi)}%)."})
        if ditolak > NOL and qty + ditolak > max(batas, sisa):  # yang datang = diterima + ditolak, paling banyak sisa (+ toleransi)
            bloks.append(blok("QTY_DITOLAK_MELEBIHI", f"Jumlah ditolak {nama} ({_q(ditolak)}) melebihi yang datang."))
        akan[pid] = akan.get(pid, NOL) + qty
        tolak[pid] = tolak.get(pid, NOL) + ditolak
        if ditolak > NOL and (it.get("reject_reason") or "").strip():
            alasan[pid] = it["reject_reason"].strip()[:200]
    if items and not bloks and all(v == NOL for v in akan.values()) and all(v == NOL for v in tolak.values()):
        bloks.append(blok("BARIS_KOSONG", "Isi jumlah diterima atau ditolak minimal satu barang."))
    if any(v > NOL for v in tolak.values()) and not await G.aktif(conn, po.get("tenant_id")):
        bloks.append(blok("DITOLAK_BUTUH_PENERIMAAN", "Pencatatan barang ditolak butuh fitur Penerimaan Barang."))
    sebelum = PD.jumlah_turunan([dict(r) for r in semua])["amount_received"]
    sesudah_baris = [{**dict(r), "quantity_received": _d(r["quantity_received"]) + akan.get(str(r["id"]), NOL)} for r in semua]
    sesudah = PD.jumlah_turunan(sesudah_baris)["amount_received"]
    lengkap = all(_d(r["quantity_received"]) >= _d(r["quantity"]) for r in sesudah_baris if not r["jasa"])
    baris = []
    for r in barang:
        k, sisa = str(r["id"]), _d(r["quantity"]) - _d(r["quantity_received"])
        baris.append({"po_item_id": k, "description": r.get("description"), "unit": r.get("unit"), "qty": _d(r["quantity"]),
                      "sudah_diterima": _d(r["quantity_received"]), "sisa": sisa, "akan_diterima": akan.get(k, NOL),
                      "akan_ditolak": tolak.get(k, NOL), "sisa_sesudah": max(sisa - akan.get(k, NOL), NOL),
                      "lebih": max(akan.get(k, NOL) - sisa, NOL), "toleransi_persen": toleransi})
    payload = {"receive_date": receive_date.isoformat(),
               "items": [{"po_item_id": k, "quantity_received": str(akan.get(k, NOL)),
                          **({"quantity_rejected": str(tolak[k])} if tolak.get(k, NOL) > NOL else {}),
                          **({"reject_reason": alasan[k]} if k in alasan else {})}
                         for k in dict.fromkeys([*akan, *tolak]) if akan.get(k, NOL) > NOL or tolak.get(k, NOL) > NOL]}
    # GRN-SJ-VENDOR (2 Nov 2026): payload = badan /receive LENGKAP (catatan + No. SJ vendor); tanpa nilai = kunci tak ditambahkan
    if notes is not None:
        payload["notes"] = notes
    if sj_vendor is not None:
        payload["vendor_delivery_note"] = sj_vendor
    return {"blocks": bloks, "notes": catatan, "baris": baris, "nilai_diterima": sesudah - sebelum, "amount_received_sesudah": sesudah,
            "toleransi_persen": toleransi, "tolak": tolak, "alasan": alasan,
            # (D) PO yang sudah ditagih: status penagihan tetap (penerimaan tak menurunkannya; po_daftar.sinkron_tagih -- tagih menang)
            "status_sesudah": po["status"] if aturan_d else ("received" if lengkap else "partial_received"), "payload": payload, "akan": akan,
            "semua": semua}


# ---------------------------------------------------------------- tagih (to-bill)
STATUS_BISA_DITAGIH = ("sent", "partial_received", "received", "partial_billed")


def _po_disc_pct(po_item, qty, unit_price) -> Decimal:
    """Persen diskon baris untuk BillCalculator: persen PO apa adanya, atau nominal diprorata qty ditagih."""
    if po_item["discount_percent"] and po_item["discount_percent"] > 0:
        return Decimal(str(po_item["discount_percent"]))
    amt = po_item["discount_amount"] or 0
    gross = qty * unit_price
    if amt and gross and po_item["quantity"]:
        prorated = Decimal(str(amt)) * qty / Decimal(str(po_item["quantity"]))
        return prorated / gross * Decimal("100")
    return Decimal("0")


async def kode_pajak_masukan(conn, tenant_id: str, tarif) -> Optional[UUID]:
    """TERIMA-TABEL (5): kode pajak MASUKAN aktif dengan tarif sama (is_default dulu, lalu kode); tarif 0 / tak ada -> None.
    purchase_order_items.tax_code (teks) belum pernah terisi di produksi (terukur 4 Nov) -> dipetakan dari tarif."""
    if _d(tarif) <= NOL:
        return None
    return await conn.fetchval(
        """SELECT id FROM tax_codes WHERE tenant_id = $1 AND is_active AND direction = 'input' AND rate = $2
           ORDER BY is_default DESC, code LIMIT 1""", tenant_id, _d(tarif))


async def rencana_tagih(conn, po, items: Optional[list], kunci: bool = True) -> dict:
    """items: [{po_item_id, quantity_to_bill?}] atau None = semua sisa. Aturan & rumus = /to-bill (BillCalculator per baris HALF_UP + pajak per
    baris 2dp HALF_UP; satu rumus dgn bill manual create_bill_v2). -> {blocks, lines, bill_subtotal, bill_tax, bill_total, tax_rate, tax_code_id}.
    TERIMA-TABEL (4 Nov 2026, putusan MASTER): 3-ARAH KETAT -- PO dengan GRN aktif: qty dasar = diterima - ditagih (dasar "diterima");
    tanpa GRN = dipesan - ditagih ("dipesan", perilaku lama). Pajak: kode masukan dari tarif baris (header 0) atau tarif header PO."""
    from decimal import ROUND_HALF_UP
    from . import grn_rencana as G
    from .bills_service import BillCalculator, _bill_line_tax
    bloks, cache_faktor = [], {}
    if po["status"] not in STATUS_BISA_DITAGIH:
        bloks.append(blok("PO_TAK_BISA_DITAGIH", f"Pesanan pembelian berstatus '{po['status']}' tidak bisa dibuatkan tagihan."))
    kuncian = " FOR UPDATE" if kunci else ""
    pilih = []
    if items:
        baca = []
        for it in items:
            pid = str(it["po_item_id"])
            try:
                uid = UUID(pid)
            except (ValueError, TypeError):
                uid = None
            baca.append((it, pid, await conn.fetchrow(
                "SELECT * FROM purchase_order_items WHERE id = $1 AND po_id = $2" + kuncian, uid, po["id"]) if uid else None))
        ada = [r["id"] for _, _, r in baca if r]
        bts = await G.batas_tagih(conn, po["tenant_id"], ada) if ada else {}  # SATU kueri untuk semua baris
        for it, pid, po_item in baca:
            if not po_item:
                bloks.append(blok("BARIS_TIDAK_DITEMUKAN", f"Baris pesanan {pid} tidak ditemukan."))
                continue
            bt = bts[str(po_item["id"])]
            q = it.get("quantity_to_bill")
            qty = Decimal(str(q)) if q else bt["qty_dasar"]
            if qty > bt["qty_dasar"]:
                if bt["dasar"] == "dipesan":  # teks lama PERSIS
                    bloks.append(blok("QTY_MELEBIHI_SISA_TAGIH", f"Jumlah ditagih ({qty}) melebihi sisa belum tertagih ({bt['qty_dasar']})."))
                else:
                    from .teks_galat import qty as _q
                    bloks.append(blok("QTY_MELEBIHI_SISA_TAGIH",
                                      f"Jumlah ditagih ({_q(qty)}) melebihi yang diterima belum ditagih ({_q(bt['qty_dasar'])})."))
                continue
            pilih.append({"po_item": po_item, "quantity": qty, "batas": bt})
    else:
        rows = await conn.fetch("SELECT * FROM purchase_order_items WHERE po_id = $1 ORDER BY line_number" + kuncian, po["id"])
        bts = await G.batas_tagih(conn, po["tenant_id"], [r["id"] for r in rows]) if rows else {}
        pilih = [{"po_item": r, "quantity": bts[str(r["id"])]["qty_dasar"], "batas": bts[str(r["id"])]}
                 for r in rows if bts[str(r["id"])]["qty_dasar"] > NOL]
    if not pilih and not any(b["code"] in ("BARIS_TIDAK_DITEMUKAN", "QTY_MELEBIHI_SISA_TAGIH") for b in bloks):
        bloks.append(blok("TAK_ADA_SISA_TAGIH", "Tidak ada baris yang belum ditagih."))
    lines, kode = [], {}
    for item in pilih:
        po_item = item["po_item"]
        qty = Decimal(str(item["quantity"]))
        unit_price = Decimal(str(po_item["unit_price"]))
        disc_pct = _po_disc_pct(po_item, qty, unit_price)
        ic = BillCalculator.calculate_item_total(qty, unit_price, disc_pct)
        tax_rate = Decimal(str(po_item["tax_rate"] or 0))
        if tax_rate not in kode:
            kode[tax_rate] = await kode_pajak_masukan(conn, po["tenant_id"], tax_rate)
        # BUG-088: faktor DPP kode lewat rumus tagihan manual (_bill_line_tax: DPP x faktor, 2dp, lalu pajak); dulu tarif x subtotal (12% penuh).
        # Dasar TETAP subtotal bruto baris (perilaku lama); tagihan manual memakai neto sesudah diskon baris = TEMUAN TERPISAH (putusan MASTER).
        _, item_tax = await _bill_line_tax(conn, po["tenant_id"], {"total": ic["subtotal"]}, kode[tax_rate], tax_rate, cache_faktor)
        lines.append({"po_item": po_item, "qty": qty, "unit_price": unit_price, "disc_pct": disc_pct, "ic": ic,
                      "tax_rate": float(po_item["tax_rate"] or 0), "tax": item_tax, "tax_code_id": kode[tax_rate], "batas": item["batas"]})
    # pajak header PO (baris tanpa pajak): ikut ke tagihan dengan kode masukan + faktor DPP-nya (dulu hilang diam)
    tarif_h = _d(po.get("tax_rate")) if not any(ln["tax"] for ln in lines) else NOL
    kode_h = await kode_pajak_masukan(conn, po["tenant_id"], tarif_h) if tarif_h > NOL else None
    if lines:
        faktor = await faktor_masukan(conn, po["tenant_id"], tarif_h, cache_faktor)  # SATU penentu dgn PO (BUG-088)
        calc = BillCalculator.calculate(
            items=[{"qty": ln["qty"], "price": ln["unit_price"], "discount_percent": ln["disc_pct"]} for ln in lines],
            invoice_discount_percent=Decimal("0"), invoice_discount_amount=0.0, cash_discount_percent=Decimal("0"),
            cash_discount_amount=0.0, tax_rate=float(tarif_h), dpp_factor=faktor)  # tarif header 0: pajak per baris dijumlah
        bill_subtotal = calc["subtotal"]
        bill_tax = float(sum(Decimal(str(ln["tax"])) for ln in lines)) if tarif_h == NOL else calc["tax_amount"]
        bill_total = (float((Decimal(str(calc["grand_total"])) + Decimal(str(bill_tax))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                      if tarif_h == NOL else calc["grand_total"])
    else:
        bill_subtotal = bill_tax = bill_total = 0.0
    return {"blocks": bloks, "lines": lines, "bill_subtotal": bill_subtotal, "bill_tax": bill_tax, "bill_total": bill_total,
            "tax_rate": float(tarif_h), "tax_code_id": kode_h}
