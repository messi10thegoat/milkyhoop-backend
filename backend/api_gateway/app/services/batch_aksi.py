"""BE-A (17 Nov 2026): Batch standar SO + BUG-164 -- SATU penentu keadaan, teks tampil, aksi_tersedia, dan rencana penyesuaian batch.

Penyesuaian batch = DOKUMEN Penyesuaian Stok bernomor (+ jurnal persediaan + kartu stok) -- putusan pemilik 17 Nov (ikut Accurate). Dulu
POST /item-batches/{id}/adjust hanya mengubah batch_warehouse_stock: tanpa kartu stok, tanpa jurnal, stok batch dan stok barang bisa selisih.
Sekarang: rencana (pratinjau == penulis) -> penulis SA yang SAMA dgn penyesuaian cepat -> baris ledger dokumen itu ditandai batch_id ->
saldo batch di gudang itu bergerak. Pembatalan dokumen SA membalik batch lewat record_inventory_reversal (ledger.batch_id), tanpa kode baru.
Hanya MEMBACA di sini; penulisan ada di routers/item_batches.py."""
from datetime import date
from decimal import Decimal
from typing import Optional

from . import stok_sesuai as SS

AKSI = ["sesuaikan", "ubah", "karantina", "aktifkan"]
NAMA_STATUS = {"active": "Aktif", "expired": "Kedaluwarsa", "depleted": "Habis", "quarantine": "Karantina"}


def _d(x) -> Decimal:
    return Decimal(str(x if x is not None else 0))


def angka(x) -> str:
    """Bilangan tampil: tanpa nol belakang (12,5 bukan 12,500000)."""
    d = _d(x)
    teks = format(d.normalize(), "f") if d != d.to_integral() else str(int(d))
    return teks.replace(".", ",")


def keadaan(status: str, expiry: Optional[date], hari_ini: date) -> str:
    """Status tampil: batch 'active' yang lewat tanggal kedaluwarsa dihitung Kedaluwarsa (kolom status bisa basi sampai ada yang memperbaruinya)."""
    if status == "active" and expiry and expiry < hari_ini:
        return "expired"
    return status


def status_text(status: str, expiry: Optional[date], hari_ini: date) -> str:
    return NAMA_STATUS.get(keadaan(status, expiry, hari_ini), str(status))


def expiry_text(expiry: Optional[date], hari_ini: date) -> str:
    if not expiry:
        return "Tanpa tanggal kedaluwarsa"
    n = (expiry - hari_ini).days
    if n < 0:
        return f"Kedaluwarsa {-n} hari lalu"
    if n == 0:
        return "Kedaluwarsa hari ini"
    return f"Kedaluwarsa {n} hari lagi"


def stock_text(qty, unit: Optional[str]) -> str:
    return f"{angka(qty)} {unit}".strip() if unit else angka(qty)


def position_text(b: dict, hari_ini: date) -> str:
    """Satu kalimat posisi: '<stok> · <status> · <kedaluwarsa>' (status Aktif tanpa tanggal dihilangkan agar ringkas)."""
    bagian = [stock_text(b.get("current_quantity"), b.get("base_unit")), status_text(b["status"], b.get("expiry_date"), hari_ini)]
    if b.get("expiry_date"):
        bagian.append(expiry_text(b["expiry_date"], hari_ini))
    return " · ".join(bagian)


def aksi_tersedia(b: dict, hari_ini: date) -> dict:
    """Satu penentu untuk daftar, detail, dan penolak tulis: {aksi: {tersedia, alasan}}."""
    ada = {"tersedia": True, "alasan": None}
    tak = lambda s: {"tersedia": False, "alasan": s}
    st = keadaan(b["status"], b.get("expiry_date"), hari_ini)
    a = {"ubah": dict(ada), "sesuaikan": dict(ada)}
    a["karantina"] = tak("Batch sudah dikarantina.") if st == "quarantine" else (tak("Batch sudah habis.") if st == "depleted" else dict(ada))
    a["aktifkan"] = dict(ada) if st == "quarantine" else tak("Hanya batch karantina yang bisa diaktifkan kembali.")
    return a


def hiasi(b: dict, hari_ini: date) -> dict:
    """Medan standar tampil + aksi untuk satu baris batch (dict dari item_batches + item)."""
    return {"status_text": status_text(b["status"], b.get("expiry_date"), hari_ini),
            "expiry_text": expiry_text(b.get("expiry_date"), hari_ini),
            "stock_text": stock_text(b.get("current_quantity"), b.get("base_unit")),
            "position_text": position_text(b, hari_ini),
            "aksi_tersedia": aksi_tersedia(b, hari_ini), "aksi_semua": list(AKSI)}


async def muat_batch(conn, tid: str, batch_id, kunci: bool = False):
    return await conn.fetchrow(
        "SELECT ib.*, p.nama_produk AS item_name, p.sku AS item_code, p.base_unit, p.item_type, p.track_inventory "
        "FROM item_batches ib JOIN products p ON p.id = ib.item_id AND p.tenant_id = ib.tenant_id "
        "WHERE ib.id = $1 AND ib.tenant_id = $2" + (" FOR UPDATE OF ib" if kunci else ""), batch_id, tid)


async def stok_batch_gudang(conn, tid: str, batch_id, warehouse_id) -> Decimal:
    return _d(await conn.fetchval(
        "SELECT COALESCE(SUM(quantity), 0) FROM batch_warehouse_stock WHERE tenant_id = $1 AND batch_id = $2 AND warehouse_id = $3",
        tid, batch_id, warehouse_id))


async def rencana_sesuaikan(conn, tid: str, batch_id, body, bl, kunci: bool = False) -> dict:
    """Penentu BERSAMA pratinjau + penulis: batch milik usaha, gudang sah, jumlah != 0, barang berstok, batch tak cukup/stok barang tak cukup.
    -> {blocks, batch, gudang, ubah, batch_sebelum, batch_sesudah, stok_sebelum, stok_sesudah, isyarat}. Pratinjau: blok dikumpulkan; tulis: blok pertama dilempar."""
    hasil = {"blocks": bl.daftar, "batch": None, "gudang": None, "ubah": Decimal(0), "batch_sebelum": None, "batch_sesudah": None,
             "stok_sebelum": None, "stok_sesudah": None, "isyarat": []}
    b = await muat_batch(conn, tid, batch_id, kunci)
    if not b:
        bl.galat("BATCH_TIDAK_DITEMUKAN", 404, "Batch tidak ditemukan.")
        return hasil
    hasil["batch"] = b
    ubah = _d(body.quantity_change)
    hasil["ubah"] = ubah
    if ubah == 0:
        bl.galat("JUMLAH_NOL", 422, "Jumlah penyesuaian tidak boleh 0.")
    if not (b["item_type"] == "goods" and (b["track_inventory"] if b["track_inventory"] is not None else True)):
        bl.galat("BARANG_TANPA_STOK", 422, f"{b['item_name']} tidak melacak stok; penyesuaian batch tidak berlaku.")
    g = await SS.gudang(conn, tid, body.warehouse_id, bl)
    hasil["gudang"] = g
    if not g:
        return hasil
    sebelum = hasil["batch_sebelum"] = await stok_batch_gudang(conn, tid, b["id"], g["id"])
    hasil["batch_sesudah"] = sebelum + ubah
    if sebelum + ubah < 0:
        bl.galat("BATCH_STOK_TIDAK_CUKUP", 422,
                 f"Stok batch {b['batch_number']} di {g['name']} tidak cukup: tersedia {angka(sebelum)}, dikurangi {angka(abs(ubah))}.")
    stok = hasil["stok_sebelum"] = await SS.stok_gudang(conn, tid, b["item_id"], g["id"])
    hasil["stok_sesudah"] = stok + ubah
    if stok + ubah < 0:
        bl.galat("STOK_TIDAK_CUKUP", 422, f"Stok {b['item_name']} di {g['name']} tidak cukup: tersedia {angka(stok)}, dikurangi {angka(abs(ubah))}.")
    return hasil


# ---------------------------------------------------------------- keadaan sebagai SATU penentu: ringkasan + saringan ?aksi= / ?status=
KUNCI = ("semua", "aktif", "kedaluwarsa", "segera_kedaluwarsa", "karantina", "habis")
BATAS_SEGERA = 30                                  # hari ke depan untuk "segera kedaluwarsa"
KUNCI_BERTANGGAL = ("aktif", "kedaluwarsa", "segera_kedaluwarsa")   # potongan syarat() yang memakai placeholder tanggal
STATUS_KE_KUNCI = {"active": "aktif", "expired": "kedaluwarsa", "depleted": "habis", "quarantine": "karantina"}
KUNCI_KE_STATUS = {"aktif": "active", "kedaluwarsa": "expired", "habis": "depleted", "karantina": "quarantine"}
KODE_FIELD = {"BATCH_STOK_TIDAK_CUKUP": "quantity_change", "STOK_TIDAK_CUKUP": "quantity_change", "JUMLAH_NOL": "quantity_change",
              "GUDANG_TIDAK_DITEMUKAN": "warehouse_id", "GUDANG_NONAKTIF": "warehouse_id", "GUDANG_TIDAK_ADA": "warehouse_id",
              "BATCH_AKSI_TIDAK_TERSEDIA": "status", "STATUS_TAK_SAH": "status",
              "NOMOR_BATCH_KEMBAR": "batch_number", "NOMOR_BATCH_TERPAKAI": "batch_number", "NOMOR_BATCH_KOSONG": "batch_number",
              "TANGGAL_KEDALUWARSA_SEBELUM_PRODUKSI": "expiry_date"}


def kunci_dari_baris(status: str, expiry: Optional[date], hari_ini: date) -> str:
    """Kunci keadaan satu baris -- cermin Python dari syarat() (diuji harness salinan: ringkasan == saringan == ini). Memakai keadaan() yang sama."""
    st = keadaan(status, expiry, hari_ini)
    if st == "active" and expiry and 0 <= (expiry - hari_ini).days <= BATAS_SEGERA:
        return "segera_kedaluwarsa"
    return STATUS_KE_KUNCI.get(st, "aktif")


def syarat(kunci: str, h: str, a: str = "ib") -> Optional[str]:
    """Potongan SQL keadaan; h = placeholder tanggal hari ini ('$3'). None untuk 'semua'. Tak pernah memuat masukan pengguna (kunci dari daftar tetap)."""
    if kunci == "aktif":
        return f"({a}.status = 'active' AND ({a}.expiry_date IS NULL OR {a}.expiry_date >= {h}::date))"
    if kunci == "kedaluwarsa":
        return f"({a}.status = 'expired' OR ({a}.status = 'active' AND {a}.expiry_date < {h}::date))"
    if kunci == "segera_kedaluwarsa":
        return f"({a}.status = 'active' AND {a}.expiry_date >= {h}::date AND {a}.expiry_date <= {h}::date + {BATAS_SEGERA})"
    if kunci == "karantina":
        return f"{a}.status = 'quarantine'"
    if kunci == "habis":
        return f"{a}.status = 'depleted'"
    return None


async def ringkasan(conn, tid: str, hari_ini: date, item_id=None) -> dict:
    """{semua, aktif, kedaluwarsa, segera_kedaluwarsa, karantina, habis} + *_text. 'segera_kedaluwarsa' adalah bagian dari 'aktif'."""
    kolom = ", ".join(f"COUNT(*) FILTER (WHERE {syarat(k, '$2')}) AS {k}" for k in KUNCI if k != "semua")
    r = await conn.fetchrow(
        f"SELECT COUNT(*) AS semua, {kolom} FROM item_batches ib WHERE ib.tenant_id = $1 AND ($3::uuid IS NULL OR ib.item_id = $3)",
        tid, hari_ini, item_id)
    out = {k: int(r[k]) for k in KUNCI}
    out["teks"] = {k: f"{out[k]} batch" for k in KUNCI}
    return out


def gudang_text(ws: dict, unit: Optional[str]) -> dict:
    """stock_text/available_text/reserved_text satu baris stok batch per gudang."""
    return {"stock_text": stock_text(ws.get("quantity"), unit), "available_text": stock_text(ws.get("available_quantity"), unit),
            "reserved_text": stock_text(ws.get("reserved_quantity"), unit) if _d(ws.get("reserved_quantity")) else None}


# ---------------------------------------------------------------- pratinjau status (karantina / aktifkan) -- penentu SAMA dgn penulis
async def rencana_status(conn, tid: str, batch_id, status, bl, hari_ini: date, kunci: bool = False) -> dict:
    hasil = {"blocks": bl.daftar, "peringatan": [], "rencana": None, "batch": None}
    if status not in ("quarantine", "active"):
        bl.galat("STATUS_TAK_SAH", 422, "Status batch yang bisa diatur di sini: karantina atau aktif.")
        return hasil
    b = await muat_batch(conn, tid, batch_id, kunci)
    if not b:
        bl.galat("BATCH_TIDAK_DITEMUKAN", 404, "Batch tidak ditemukan.")
        return hasil
    hasil["batch"] = b
    bd = dict(b)
    aksi = "karantina" if status == "quarantine" else "aktifkan"
    a = aksi_tersedia(bd, hari_ini)[aksi]
    if not a["tersedia"]:
        bl.galat("BATCH_AKSI_TIDAK_TERSEDIA", 409, a["alasan"])
    sesudah = "Karantina" if status == "quarantine" else status_text("active", b["expiry_date"], hari_ini)
    if status == "quarantine" and _d(b["current_quantity"]) > 0:
        hasil["peringatan"].append(f"Stok batch {b['batch_number']} ({stock_text(b['current_quantity'], b['base_unit'])}) tidak bisa dijual selama karantina.")
    if status == "active" and b["expiry_date"] and b["expiry_date"] < hari_ini:
        hasil["peringatan"].append(f"Batch {b['batch_number']} sudah lewat tanggal kedaluwarsa; setelah diaktifkan tetap tampil Kedaluwarsa.")
    hasil["rencana"] = {"batch_number": b["batch_number"], "item_name": b["item_name"], "aksi": aksi,
                        "status_sebelum_text": status_text(b["status"], b["expiry_date"], hari_ini), "status_sesudah_text": sesudah,
                        "stock_text": stock_text(b["current_quantity"], b["base_unit"]),
                        "ringkas": f"Batch {b['batch_number']}: {status_text(b['status'], b['expiry_date'], hari_ini)} → {sesudah}"}
    return hasil

# ---------------------------------------------------------------- ubah batch (Batch B, 24 Nov 2026): pratinjau == PATCH
# Tabel yang merujuk batch lewat batch_id (diukur dari migrasi: V047 sales_invoice/sales_receipt/stock_adjustment/stock_transfer/bill_items/ledger,
# V048 serial, V062 cabang, V064 produksi). Dokumen menyalin batch_number sebagai TEKS (sales_receipt/stock_transfer/PO) dan penerimaan bill mencocokkan
# batch lewat nomor (ON CONFLICT tenant,item,batch_number) -> nomor yang sudah punya gerakan/dirujuk dokumen TIDAK boleh diganti (pola Accurate);
# tanggal & mutu tetap boleh.
TABEL_RUJUKAN_BATCH = ("inventory_ledger", "bill_items", "sales_invoice_items", "sales_receipt_items", "stock_adjustment_items", "stock_transfer_items", "item_serials")
LABEL_UBAH = {"batch_number": "Nomor batch", "manufacture_date": "Tanggal produksi", "expiry_date": "Tanggal kedaluwarsa",
              "supplier_batch_number": "Nomor batch pemasok", "quality_grade": "Mutu", "quality_notes": "Catatan mutu"}
BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def tgl_text(d: Optional[date]) -> str:
    return f"{d.day} {BULAN[d.month - 1]} {d.year}" if d else "—"


def _teks(v, tanggal: bool) -> str:
    return tgl_text(v) if tanggal else (str(v).strip() if v not in (None, "") else "—")


async def pemakai_batch(conn, tid: str, b) -> int:
    """Jumlah rujukan ke batch ini (baris tabel rujukan + penerimaan asal bill/PO). >0 = nomor batch terkunci."""
    n = 1 if (b.get("bill_id") or b.get("purchase_order_id")) else 0
    for t in TABEL_RUJUKAN_BATCH:
        n += int(await conn.fetchval(f"SELECT count(*) FROM {t} WHERE batch_id = $1", b["id"]) or 0)
    return n


async def rencana_ubah(conn, tid: str, batch_id, body, bl, hari_ini: date, kunci: bool = False) -> dict:
    """Pratinjau == PATCH /item-batches/{id}. Blok: NOMOR_BATCH_KOSONG 422, NOMOR_BATCH_KEMBAR 409, NOMOR_BATCH_TERPAKAI 409,
    TANGGAL_KEDALUWARSA_SEBELUM_PRODUKSI 422 (+ blok status bila status ikut). rencana = sebelum -> sesudah sebagai TEKS server."""
    hasil = {"blocks": bl.daftar, "peringatan": [], "rencana": None, "batch": None}
    b = await muat_batch(conn, tid, batch_id, kunci)
    if not b:
        bl.galat("BATCH_TIDAK_DITEMUKAN", 404, "Batch tidak ditemukan.")
        return hasil
    hasil["batch"] = b
    nomor_lama = b["batch_number"]
    perubahan = []
    for f in ("batch_number", "manufacture_date", "expiry_date", "supplier_batch_number", "quality_grade", "quality_notes"):
        baru = getattr(body, f, None)
        if baru is None:
            continue
        if f == "batch_number":
            baru = baru.strip()
            if not baru:
                bl.galat("NOMOR_BATCH_KOSONG", 422, "Nomor batch tidak boleh kosong.")
                continue
        if baru != b.get(f):
            tgl = f in ("manufacture_date", "expiry_date")
            perubahan.append({"field": f, "label": LABEL_UBAH[f], "sebelum_text": _teks(b.get(f), tgl), "sesudah_text": _teks(baru, tgl), "_baru": baru})
    ganti = {x["field"]: x["_baru"] for x in perubahan}
    if "batch_number" in ganti:
        if await conn.fetchval("SELECT 1 FROM item_batches WHERE tenant_id = $1 AND item_id = $2 AND batch_number = $3 AND id <> $4",
                               tid, b["item_id"], ganti["batch_number"], b["id"]):
            bl.galat("NOMOR_BATCH_KEMBAR", 409, f"Nomor batch {ganti['batch_number']} sudah dipakai batch lain untuk barang {b['item_name']}.")
        else:
            n = await pemakai_batch(conn, tid, b)
            if n:
                bl.galat("NOMOR_BATCH_TERPAKAI", 409,
                         f"Batch {nomor_lama} sudah punya gerakan atau dirujuk dokumen, jadi nomornya tidak bisa diganti. Tanggal dan mutu masih bisa diubah.")
    prod, kedal = ganti.get("manufacture_date", b.get("manufacture_date")), ganti.get("expiry_date", b.get("expiry_date"))
    if prod and kedal and kedal < prod and ("manufacture_date" in ganti or "expiry_date" in ganti):
        bl.galat("TANGGAL_KEDALUWARSA_SEBELUM_PRODUKSI", 422,
                 f"Tanggal kedaluwarsa ({tgl_text(kedal)}) tidak boleh sebelum tanggal produksi ({tgl_text(prod)}).")
    if "expiry_date" in ganti and kedal and kedal < hari_ini:
        hasil["peringatan"].append(f"Tanggal kedaluwarsa {tgl_text(kedal)} sudah lewat; batch {nomor_lama} akan tampil Kedaluwarsa dan tidak bisa dijual.")
    if getattr(body, "status", None) in ("quarantine", "active"):
        st = await rencana_status(conn, tid, batch_id, body.status, bl, hari_ini, kunci)
        hasil["peringatan"] += st["peringatan"]
        if st["rencana"]:
            perubahan.append({"field": "status", "label": "Status", "sebelum_text": st["rencana"]["status_sebelum_text"], "sesudah_text": st["rencana"]["status_sesudah_text"]})
    for x in perubahan:
        x.pop("_baru", None)
    hasil["rencana"] = {"batch_number": nomor_lama, "item_name": b["item_name"], "perubahan": perubahan, "ganti": ganti,
                        "ringkas": (f"Batch {nomor_lama}: " + "; ".join(f"{x['label']} {x['sebelum_text']} → {x['sesudah_text']}" for x in perubahan)) if perubahan
                        else f"Batch {nomor_lama}: tidak ada perubahan."}
    if not perubahan:
        hasil["peringatan"].append("Tidak ada yang berubah dari data sekarang.")
    return hasil


def pratinjau(r: dict, payload=None) -> dict:
    """Bentuk pratinjau seragam dgn G3 Gudang: {success, data:{ok, blocks[{code,message,field}], peringatan[], rencana, payload}}."""
    return {"success": True, "data": {"ok": not r["blocks"],
                                      "blocks": [{"code": x["code"], "message": x["message"], "field": KODE_FIELD.get(x["code"])} for x in r["blocks"]],
                                      "peringatan": r.get("peringatan", []), "rencana": r.get("rencana"), "payload": payload}}
