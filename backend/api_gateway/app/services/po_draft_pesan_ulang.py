"""PU-1 (26 Nov 2026): Pesan ulang -> Buat PO. Pratinjau + penulis PO DRAF per vendor memakai jalur PO yang sudah ada
(po_rencana.hitung_po = kalkulator SAMA dgn create/PATCH; penulis = POST /purchase-orders per vendor, jadi nomor/harga/alamat/idempotensi TIDAK diduplikasi).
PO draf TIDAK membuat jurnal. Vendor: body.vendor_id (semua baris) > vendor utama barang (products.preferred_vendor_id) > vendor tagihan terakhir; tanpa vendor = blok.
Harga satuan: tagihan terakhir vendor itu > harga beli barang > 0 (peringatan 'isi harga di PO')."""
from decimal import Decimal
from uuid import UUID

from . import po_rencana as PR
from . import teks_galat as tg

NOL = Decimal(0)

SQL_BARANG = ("SELECT p.id, p.item_code, p.nama_produk, COALESCE(NULLIF(p.base_unit, ''), p.satuan) AS satuan, p.preferred_vendor_id, "
              "COALESCE(NULLIF(p.harga_beli, 0), NULLIF(p.purchase_price, 0), 0) AS harga_beli "
              "FROM products p WHERE p.tenant_id = $1 AND p.id = ANY($2::uuid[])")
SQL_TERAKHIR = ("SELECT DISTINCT ON (bi.product_id, b.vendor_id) bi.product_id, b.vendor_id, bi.unit_price, b.created_at AS waktu "
                "FROM bill_items bi JOIN bills b ON b.id = bi.bill_id AND b.tenant_id = $1 "
                "WHERE bi.product_id = ANY($2::uuid[]) AND b.vendor_id IS NOT NULL AND b.status NOT IN ('void', 'draft') "
                "ORDER BY bi.product_id, b.vendor_id, b.created_at DESC")
SQL_VENDOR = "SELECT id, name FROM vendors WHERE tenant_id = $1 AND id = ANY($2::uuid[])"


async def rencana(conn, tid: str, body, bl: list) -> dict:
    """-> {kelompok:[{vendor_id, vendor_nama, baris:[...], total_text, items:[PurchaseOrderItemCreate-dict]}], diblok:[...], peringatan:[...]}.
    bl = daftar blok (dict PR.blok); kosong = boleh ditulis."""
    hasil = {"kelompok": [], "diblok": [], "peringatan": []}
    try:
        pids = [UUID(str(i.item_id)) for i in body.baris]
    except (ValueError, TypeError):
        bl.append(PR.blok("BARANG_TIDAK_SAH", "ID barang tidak sah.", 422))
        return hasil
    barang = {str(r["id"]): r for r in await conn.fetch(SQL_BARANG, tid, pids)}
    vendor_pilihan, vblok = await PR.blok_vendor(conn, tid, body.vendor_id)
    bl.extend(vblok)
    if vblok:
        return hasil
    terakhir = {}
    for r in await conn.fetch(SQL_TERAKHIR, tid, pids):
        terakhir.setdefault(str(r["product_id"]), {})[str(r["vendor_id"])] = (Decimal(str(r["unit_price"] or 0)), r["waktu"])
    # vendor tiap barang
    per_vendor, tanpa = {}, []
    for i in body.baris:
        b = barang.get(str(i.item_id))
        if not b:
            bl.append(PR.blok("BARANG_TIDAK_DITEMUKAN", "Ada barang yang tidak ditemukan di usaha ini.", 422))
            continue
        harga_vendor = terakhir.get(str(i.item_id), {})
        if vendor_pilihan:
            vid = str(vendor_pilihan["id"])
        elif b["preferred_vendor_id"]:
            vid = str(b["preferred_vendor_id"])
        elif harga_vendor:
            vid = max(harga_vendor, key=lambda v: harga_vendor[v][1])   # vendor tagihan TERAKHIR
        else:
            tanpa.append(b)
            continue
        harga = (harga_vendor.get(vid) or (NOL, None))[0] or Decimal(str(b["harga_beli"] or 0))
        per_vendor.setdefault(vid, []).append((i, b, harga))
    for b in tanpa:
        hasil["diblok"].append({"item_id": str(b["id"]), "nama": b["nama_produk"], "alasan_kode": "VENDOR_TIDAK_ADA",
                                "alasan_text": f"{b['nama_produk']}: belum ada vendor. Pilih vendor untuk barang ini."})
    nama_vendor = {str(r["id"]): r["name"] for r in await conn.fetch(SQL_VENDOR, tid, [UUID(v) for v in per_vendor])} if per_vendor else {}
    for vid in sorted(per_vendor, key=lambda v: nama_vendor.get(v, "")):
        baris_in = per_vendor[vid]
        items = [{"item_id": str(b["id"]), "item_code": b["item_code"], "description": b["nama_produk"], "quantity": i.jumlah, "unit": b["satuan"],
                  "unit_price": harga, "discount_percent": 0, "discount_amount": 0, "tax_rate": 0} for i, b, harga in baris_in]
        h = await PR.hitung_po(conn, tid, items, 0, 0, 0)
        baris = []
        for (i, b, harga), hb in zip(baris_in, h["baris"]):
            q = Decimal(str(i.jumlah))
            baris.append({"item_id": str(b["id"]), "nama": b["nama_produk"], "jumlah_text": tg.angka_laporan(q, 0) if q == q.to_integral_value() else tg.angka_laporan(q, 2),
                          "satuan_text": b["satuan"] or "", "harga_text": tg.rp(harga), "subtotal_text": tg.rp(hb["subtotal"])})
            if not harga:
                hasil["peringatan"].append({"kode": "HARGA_KOSONG", "teks": f"{b['nama_produk']}: harga beli belum ada; isi harga di PO."})
        hasil["kelompok"].append({"vendor_id": vid, "vendor_nama": nama_vendor.get(vid, ""), "baris": baris,
                                  "total_text": tg.rp(h["header"]["total_amount"]), "total_nilai": str(h["header"]["total_amount"]), "items": items})
    return hasil


def total_text(r: dict) -> str:
    return tg.rp(sum((Decimal(str(g["total_nilai"])) for g in r["kelompok"]), NOL))


def teks_ringkas(r: dict) -> str:
    n, d = len(r["kelompok"]), len(r["diblok"])
    s = f"{n} PO draf akan dibuat" if n else "Tidak ada PO yang bisa dibuat"
    return s + (f"; {d} barang belum punya vendor" if d else "") + "."
