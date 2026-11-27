"""PENYESUAIAN STOK standar SO (gelombang 2, kontrak backend/docs/KONTRAK-persediaan-standar-so-20261109.md): SATU penentu untuk pratinjau DAN
tulis (buat, ubah, posting, batal) + kunci ?aksi= / teks posisi / dokumen / riwayat. Pratinjau = penentu yang sama di transaksi READ ONLY.

Putusan pemilik LANGSUNG 9 Nov 2026 (sesi BACKEND):
1. Nilai baris = harga pokok (WAC) saat DRAF dibuat, dibekukan (perilaku lama); pratinjau posting memberi ISYARAT bila WAC kini berbeda.
2. Gudang: penyesuaian memakai `warehouse_id` yang dipilih; bawaan = gudang UTAMA (is_default, cadangan gudang tertua). Stok sebelum dan
   hitung ulang dihitung PER gudang itu (dulu stok semua gudang + selalu gudang tertua).
3. Batal: periode TANGGAL penyesuaian juga harus terbuka (dulu hanya hari ini), seragam dengan batal modul lain.
Selain itu angka, akun, arah jurnal, dan gerak inventory_ledger PERSIS seperti posting/void lama (milkyhoop-inventory Rule 1/3/9; Law 2/5/13/20).
Periode: penentu lama modul ini dipertahankan -- tanpa baris fiscal_periods = DITOLAK (beda dari modul lain; diakui di kontrak U6)."""
from decimal import Decimal
from typing import Optional
from uuid import UUID

from . import satuan_dasar as SD   # BUG-123: satuan baris -> satuan dasar (penentu SATU jalur stok, seperti Pemindahan K1)
from . import teks_galat as tg
from . import teks_laporan_persediaan as TLP   # BUG-108/123: teks campur satuan besar
from .bayar_keluar_rencana import Blok
from .role_resolver import AccountRole, AccountRoleUnmappedError, resolve_account_id_by_role

JENIS_TEXT = {"increase": "Penambahan", "decrease": "Pengurangan", "recount": "Hitung ulang", "damaged": "Rusak", "expired": "Kedaluwarsa"}
AKSI = ("draf",)            # pill (hitung), desak 1 -- putusan MASTER 9 Nov
KARTU = ("bulan_ini",)      # kartu ringkasan
SEMUA_KUNCI = AKSI + KARTU
NOL = Decimal(0)


def _d(x) -> Decimal:
    return Decimal(str(x or 0))


def rp(x) -> str:
    return str(_d(x).quantize(Decimal("0.01")))


# ---------------------------------------------------------------- nilai bertanda (BUG-228 butir 3, putusan pemilik 22 Nov)
# total_value baris/header selalu MAGNITUDO (|selisih| x harga pokok). Arah = tanda quantity_adjustment baris (jenis menentukan tanda; hitung ulang = turunan fisik - sistem).
# Nilai bertanda = nilai baris dgn tanda qty; header = Σ baris (naik - turun) dari SUMBER YANG SAMA dgn total_value (stock_adjustment_items.total_value), bukan angka baru.
def nilai_bertanda(qty, total) -> Decimal:
    return -abs(_d(total)) if _d(qty) < 0 else abs(_d(total))


def teks_bertanda(v) -> str:
    """'+Rp 144.000' / '\u2212Rp 36.000' / 'Rp 0' (nol tanpa tanda)."""
    from .teks_galat import rp as _rp_teks
    d = _d(v)
    return f"+{_rp_teks(abs(d))}" if d > 0 else (f"\u2212{_rp_teks(abs(d))}" if d < 0 else _rp_teks(0))


def arah_nilai(naik, turun) -> str:
    """'tambah' (hanya naik) | 'kurang' (hanya turun) | 'campur' (keduanya, mis. hitung ulang) | 'nol'."""
    n, t = _d(naik), _d(turun)
    return "campur" if n > 0 and t > 0 else ("tambah" if n > 0 else ("kurang" if t > 0 else "nol"))


def medan_bertanda(naik, turun) -> dict:
    net = _d(naik) - _d(turun)
    return {"arah": arah_nilai(naik, turun), "total_value_signed": int(net), "nilai_bertanda_text": teks_bertanda(net)}


# ---------------------------------------------------------------- daftar
def sql_aksi(kunci: str, p_hari: str, a: str = "stock_adjustments") -> str:
    return {
        "draf": f"{a}.status = 'draft'",
        "bulan_ini": f"({a}.status <> 'void' AND date_trunc('month', {a}.adjustment_date) = date_trunc('month', {p_hari}::date))",
    }[kunci]


def aksi_semua(status: Optional[str]) -> list:
    return ["draf"] if status == "draft" else []


def jenis_text(tipe: Optional[str]) -> Optional[str]:
    return JENIS_TEXT.get(tipe or "", tipe)


STATUS_LABEL = {"draft": "Draf", "posted": "Diposting", "void": "Dibatalkan"}


def filter_tersedia() -> dict:
    """Nilai + label saringan daftar (teks dari server; FE tak merangkai)."""
    return {
        "status": [{"nilai": "all", "label": "Semua status"}] + [{"nilai": k, "label": v} for k, v in STATUS_LABEL.items()],
        "adjustment_type": [{"nilai": k, "label": v} for k, v in JENIS_TEXT.items()],
    }


def syarat_saring(status, adjustment_type, warehouse_id, date_from, date_to, mulai: int, a: str = "stock_adjustments") -> tuple:
    """(daftar predikat, parameter) SATU predikat saringan daftar DAN /summary (BUG-385). Dikomposisi AND dgn search/aksi.
    warehouse_id: gudang header ATAU gudang baris (baris NULL = ikut header). Tanggal: date_from/date_to inklusif; AND dgn aksi=bulan_ini."""
    c, p = [], []
    if status and status != "all":
        p.append(status); c.append(f"{a}.status = ${mulai + len(p) - 1}")
    if adjustment_type:
        p.append(adjustment_type); c.append(f"{a}.adjustment_type = ${mulai + len(p) - 1}")
    if warehouse_id:
        p.append(warehouse_id)
        n = mulai + len(p) - 1
        c.append(f"({a}.warehouse_id = ${n} OR EXISTS (SELECT 1 FROM stock_adjustment_items wi WHERE wi.stock_adjustment_id = {a}.id AND wi.warehouse_id = ${n}))")
    if date_from:
        p.append(date_from); c.append(f"{a}.adjustment_date >= ${mulai + len(p) - 1}")
    if date_to:
        p.append(date_to); c.append(f"{a}.adjustment_date <= ${mulai + len(p) - 1}")
    return c, p


async def ringkas_baris(conn, ids: list, tenant_id: str) -> dict:
    """Per dokumen (satu query per halaman): items_summary_text ('Bodrex +1 lagi') dan warehouse_name (satu gudang; beragam -> 'Beberapa gudang')."""
    if not ids:
        return {}
    rows = await conn.fetch(
        """SELECT sa.id, w.name AS gudang_header,
                  (SELECT product_name FROM stock_adjustment_items WHERE stock_adjustment_id = sa.id ORDER BY line_number LIMIT 1) AS pertama,
                  (SELECT COUNT(*) FROM stock_adjustment_items WHERE stock_adjustment_id = sa.id) AS n_baris,
                  (SELECT COUNT(DISTINCT COALESCE(i.warehouse_id, sa.warehouse_id)) FROM stock_adjustment_items i WHERE i.stock_adjustment_id = sa.id) AS n_gudang
           FROM stock_adjustments sa LEFT JOIN warehouses w ON w.id = sa.warehouse_id AND w.tenant_id = sa.tenant_id
           WHERE sa.id = ANY($1::uuid[]) AND sa.tenant_id = $2""", ids, tenant_id)
    out = {}
    for r in rows:
        n = int(r["n_baris"] or 0)
        nama = r["pertama"] or None
        teks = (f"{nama} +{n - 1} lagi" if n > 1 else nama) if nama else None
        out[str(r["id"])] = {"items_summary_text": teks,
                             "warehouse_name": "Beberapa gudang" if int(r["n_gudang"] or 0) > 1 else r["gudang_header"]}
    return out


def teks_alasan(alasan, tipe=None) -> Optional[str]:
    """BUG-379: alasan baris tanpa gema kode jenis mentah ('correction'). Kosong/kode mentah -> None (FE tak menampilkan kode)."""
    t = (alasan or "").strip()
    if not t or t.lower() in {"correction", "increase", "decrease", "recount", "damaged", "expired", "other"}:
        return None
    return t


def position_text(status: Optional[str], tipe: Optional[str]) -> str:
    if status == "draft":
        return "Draf · belum diposting"
    if status == "void":
        return "Dibatalkan"
    return f"Diposting · {jenis_text(tipe)}"


def documents_baris(status: Optional[str], journal_id) -> list:
    """penyesuaian · jurnal (+pembalik bila batal)."""
    if not journal_id:
        return [{"type": "stock_adjustment"}]
    return [{"type": "stock_adjustment"}, {"type": "journal"}] + ([{"type": "journal"}] if status == "void" else [])


def syarat_cari(search, mulai: int, a: str = "stock_adjustments") -> tuple:
    """(predikat, parameter) ?search= daftar DAN /summary?search= (SATU predikat, BUG-197): tiap kata wajib cocok salah satu: nomor penyesuaian, catatan,
    nama barang, kode di baris (barcode), dan item_code/SKU master barang (join bertenant) pada baris. Baris dibatasi lewat induk (`a`, sudah bersaring tenant). Tanpa kata -> (None, [])."""
    kata = search.strip().split() if isinstance(search, str) else []
    if not kata:
        return None, []
    s = [(f"({a}.adjustment_number ILIKE ${mulai + i} OR {a}.notes ILIKE ${mulai + i} OR EXISTS (SELECT 1 FROM stock_adjustment_items i "
          f"WHERE i.stock_adjustment_id = {a}.id AND ((i.product_name ILIKE ${mulai + i} OR i.product_code ILIKE ${mulai + i}) "
          f"OR i.product_id IN (SELECT pr.id FROM products pr WHERE pr.tenant_id = {a}.tenant_id AND pr.item_code ILIKE ${mulai + i}))))") for i in range(len(kata))]
    return (s[0] if len(s) == 1 else f"({' AND '.join(s)})"), [f"%{w}%" for w in kata]


LABEL_MEDAN = {"adjustment_date": "tanggal", "adjustment_type": "jenis", "storage_location_id": "lokasi", "warehouse_id": "gudang",
               "reference_no": "referensi", "notes": "catatan", "items": "barang"}


def ringkas_ubah(nomor, medan, total_sebelum, total_sesudah) -> str:
    """'Penyesuaian stok SA-1 diubah: tanggal, barang · total Rp 100.000 → Rp 120.000' (bagian total hanya bila berubah)."""
    from .so_riwayat import _rp
    label = []
    for m in medan:
        t = LABEL_MEDAN.get(m, m)
        if t not in label:
            label.append(t)
    total = ""
    if total_sebelum is not None and total_sesudah is not None and _d(total_sebelum) != _d(total_sesudah):
        total = f" · total {_rp(total_sebelum)} → {_rp(total_sesudah)}"
    return f"Penyesuaian stok {nomor} diubah{': ' + ', '.join(label) if label else ''}{total}"


# ---------------------------------------------------------------- gudang + stok + periode
async def gudang(conn, tid: str, warehouse_id, bl: Blok):
    """Gudang penyesuaian (putusan 2): yang dipilih (milik tenant, aktif) atau bawaan UTAMA, cadangan gudang tertua."""
    if warehouse_id:
        try:
            wid = UUID(str(warehouse_id))
        except (ValueError, TypeError):
            wid = None
        g = await conn.fetchrow("SELECT id, name, is_active FROM warehouses WHERE id = $1 AND tenant_id = $2 AND NOT is_system", wid, tid) if wid else None
        if not g:
            bl.galat("GUDANG_TIDAK_DITEMUKAN", 400, "Gudang tidak ditemukan di usaha ini.")
            return None
        if not g["is_active"]:
            bl.galat("GUDANG_NONAKTIF", 400, f"Gudang {g['name']} nonaktif.")
        return g
    g = await conn.fetchrow("SELECT id, name, is_active FROM warehouses WHERE tenant_id = $1 AND is_active AND NOT is_system "
                            "ORDER BY is_default DESC, created_at LIMIT 1", tid)
    if not g:
        bl.galat("GUDANG_TIDAK_ADA", 400, "Belum ada gudang aktif. Buat gudang dulu.")
    return g


async def stok_gudang(conn, tid: str, product_id, warehouse_id) -> Decimal:
    """Stok barang di SATU gudang = Σ masuk − Σ keluar inventory_ledger (Law 16; bukan cache warehouse_stock yang dibatasi 0)."""
    return _d(await conn.fetchval(
        "SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) FROM inventory_ledger "
        "WHERE tenant_id = $1 AND product_id = $2 AND warehouse_id = $3", tid, product_id, warehouse_id))


def isyarat_tanpa_harga(nama) -> str:
    """Kalimat isyarat SATU sumber: dipakai pratinjau buat/ubah (susun_baris) DAN pratinjau posting (BUG-212)."""
    return f"Barang {nama} belum punya harga pokok; nilai Rp 0."


TEKS_TANPA_JURNAL_POSTING = "Tanpa jurnal (nilai Rp 0)."
TEKS_TANPA_JURNAL_BATAL = "Tanpa jurnal pembalik (penyesuaian ini tanpa jurnal); hanya gerak stok yang dibalik."


async def harga_pokok(conn, tid: str, product_id) -> Decimal:
    """Harga pokok SAAT INI dengan penentu yang SAMA dengan nilai baris draf (router get_product_info: WAC terakhir, cadangan harga beli)."""
    from ..routers.stock_adjustments import get_product_info
    p = await get_product_info(conn, tid, product_id)
    return Decimal(int(p["unit_cost"] or 0)) if p else NOL


async def periode(conn, tid: str, tgl, bl: Blok) -> None:
    """== check_period_is_open lama modul ini (status 400): tanpa periode -> tolak; status bukan OPEN -> tolak."""
    p = await conn.fetchrow("SELECT id, period_name, status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2",
                            tid, tgl)
    if not p:
        bl.galat("PERIODE_TIDAK_ADA", 400, "Tidak ada periode akuntansi untuk tanggal ini")
    elif p["status"] != "OPEN":
        bl.galat("PERIODE_TERTUTUP", 400, tg.periode_tertutup(p["period_name"], p["status"]))


# ---------------------------------------------------------------- buat / ubah (baris draf)
# BUG-204 (putusan pemilik LANGSUNG 13 Nov 2026): SATU penentu aturan baris untuk pratinjau DAN tulis. JENIS menentukan arah (Penambahan +; Pengurangan/Rusak/
# Kedaluwarsa −; Hitung ulang = stok fisik); ditolak: tanda tak cocok jenis, barang non-stok, selisih 0, barang ganda, fisik wajib/negatif (hitung ulang).
# Harga pokok 0 = ISYARAT (boleh lanjut). Dokumen lama yang melanggar TIDAK diubah (aturan hanya untuk penulisan baru).
ARAH_JENIS = {"increase": 1, "decrease": -1, "damaged": -1, "expired": -1}     # recount: arah turunan (fisik - sistem)


def barang_bertstok(p) -> bool:
    """Barang yang melacak stok: item_type goods dan track_inventory bukan False (penentu SAMA dgn posting :gerak)."""
    return bool(p and p["item_type"] == "goods" and (p["track_inventory"] if p["track_inventory"] is not None else True))


async def _gudang_baris(conn, tid: str, item, gudang_id, bl: Blok, idx: int, nama: str, cache: dict):
    """BUG-261: gudang EFEKTIF satu baris. -> (gudang_id_efektif, warehouse_id_simpan|None, transfer|None) atau None (blok sudah dicatat).
    Baris penyelesaian Pemindahan (source_transfer_id): gudang = Transit, TERKUNCI (warehouse_id lain ditolak). Baris biasa: warehouse_id baris (milik tenant, aktif, bukan gudang sistem)
    atau gudang dokumen; warehouse_id kosong = NULL tersimpan = ikut gudang dokumen (perilaku lama utuh)."""
    wid, st_id = getattr(item, "warehouse_id", None), getattr(item, "source_transfer_id", None)
    if not st_id:
        if not wid:
            return gudang_id, None, None
        g = await gudang(conn, tid, wid, bl)
        return (g["id"], g["id"], None) if g else None
    from . import gudang_transit as GT
    try:
        uid = UUID(str(st_id))
    except (ValueError, TypeError):
        uid = None
    t = await conn.fetchrow("SELECT id, transfer_number, status FROM stock_transfers WHERE id = $1 AND tenant_id = $2", uid, tid) if uid else None
    if not t:
        bl.galat("PENYESUAIAN_PEMINDAHAN_TIDAK_ADA", 404, f"Pemindahan baris {idx} tidak ditemukan.")
        return None
    if t["status"] != "in_transit":
        bl.galat("PENYESUAIAN_PEMINDAHAN_STATUS", 409,
                 f"Pemindahan {t['transfer_number']} berstatus {t['status']}; hanya yang masih di Transit (dikirim, belum diterima seluruhnya) yang selisihnya bisa diselesaikan.")
        return None
    transit = cache.get("transit") or await GT.id_transit(conn, tid)
    cache["transit"] = transit
    if not transit:
        bl.galat("PENYESUAIAN_TRANSIT_TAK_ADA", 409, "Gudang Transit belum ada; tak ada selisih yang bisa diselesaikan.")
        return None
    if wid and str(wid) != str(transit):
        bl.galat("PENYESUAIAN_GUDANG_TERKUNCI", 422, f"Gudang baris {idx} ({nama}) terkunci ke Transit karena menyelesaikan selisih Pemindahan {t['transfer_number']}.")
        return None
    return transit, transit, t


async def _tertunda_barang(conn, tid: str, transfer_id, product_id, cache: dict) -> Decimal:
    """Tertunda SAAT INI barang di pemindahan (dikirim - diterima - diselesaikan POSTED). Barang bukan bagian pemindahan = -1 (ditolak pemanggil)."""
    from . import pemindahan_dua_langkah as pd2
    k = ("tertunda", str(transfer_id))
    if k not in cache:
        cache[k] = {i["item_id"]: i["quantity_pending"] for i in await pd2.item_baris(conn, tid, transfer_id)}
    return cache[k].get(product_id, Decimal(-1))


async def susun_baris(conn, tid: str, items, tipe: str, gudang_id, bl: Blok, isyarat: Optional[list] = None) -> list:
    """Baris draf: stok sebelum PER gudang, selisih (hitung ulang: fisik − sistem), nilai = |selisih| × harga pokok saat ini (dibekukan).
    BUG-204: + aturan jenis/tanda/non-stok/selisih 0/ganda/fisik (blok berkode) dan isyarat harga pokok 0 (ke `isyarat` bila diberikan)."""
    from ..routers.stock_adjustments import get_product_info
    hasil, terlihat, cache = [], set(), {}
    ktx = await SD.muat(conn, tid, SD.ids_bersatuan([{"product_id": getattr(i, "product_id", None), "unit": getattr(i, "unit", None)} for i in (items or [])]))
    konv_semua = await _konversi(conn, tid, [getattr(i, "product_id", None) for i in (items or [])])
    for idx, item in enumerate(items or [], 1):
        try:
            pid = UUID(str(item.product_id))
        except (ValueError, TypeError):
            pid = None
        p = await get_product_info(conn, tid, pid) if pid else None
        if not p:
            bl.galat("BARANG_TIDAK_DITEMUKAN", 400, f"Barang baris {idx} tidak ditemukan.")
            continue
        nama = p["name"]
        tipe_brg = await conn.fetchrow("SELECT item_type, track_inventory FROM products WHERE id = $1 AND tenant_id = $2", pid, tid)
        if not barang_bertstok(tipe_brg):
            bl.galat("PENYESUAIAN_BARANG_NON_STOK", 409, f"{nama} tidak melacak stok; penyesuaian stok hanya untuk barang ber-stok.")
            continue
        gb = await _gudang_baris(conn, tid, item, gudang_id, bl, idx, nama, cache)
        if gb is None:
            continue
        gid, wid_simpan, transfer = gb
        if (pid, gid) in terlihat:     # BUG-261: ganda = barang yang sama di gudang yang sama (gudang beda antar baris boleh)
            bl.galat("PENYESUAIAN_BARANG_GANDA", 409, f"{nama} muncul lebih dari sekali di dokumen ini (baris {idx}); gabungkan jadi satu baris.")
            continue
        terlihat.add((pid, gid))
        sebelum = await stok_gudang(conn, tid, pid, gid) if gid else NOL
        # BUG-123: jumlah bersatuan (14 Karton) -> SATUAN DASAR oleh server; satuan tanpa konversi = blok berkalimat (+field). Baris draf SELALU dalam satuan dasar.
        unit_masuk = (getattr(item, "unit", None) or "").strip() or None
        hq = SD.hitung(ktx, pid, unit_masuk, item.quantity_adjustment, "konversi", "Penyesuaian")
        hf = SD.hitung(ktx, pid, unit_masuk, item.physical_quantity, "konversi", "Penyesuaian") if item.physical_quantity is not None else None
        if hq["blok"]:
            bl.galat(hq["blok"]["code"], hq["blok"]["status"], hq["blok"]["message"], field=f"items[{idx - 1}].unit")
            continue
        ubah = hq["qty_dasar"]
        sistem = fisik = None
        if tipe == "recount":
            if item.physical_quantity is None:
                bl.galat("PENYESUAIAN_FISIK_WAJIB", 422, f"Isi stok fisik {nama} untuk hitung ulang.")
                continue
            if _d(item.physical_quantity) < 0:
                bl.galat("PENYESUAIAN_FISIK_NEGATIF", 422, f"Stok fisik {nama} tidak boleh negatif.")
                continue
            fisik, sistem = hf["qty_dasar"], sebelum
            ubah = fisik - sistem
        elif ARAH_JENIS.get(tipe) and ubah != 0 and (ubah > 0) != (ARAH_JENIS[tipe] > 0):
            bl.galat("PENYESUAIAN_TANDA_TIDAK_COCOK", 422,
                     f"Jumlah {nama}: jenis {jenis_text(tipe)} harus {'positif (menambah stok)' if ARAH_JENIS[tipe] > 0 else 'negatif (mengurangi stok)'}.")
            continue
        if ubah == 0:
            bl.galat("PENYESUAIAN_SELISIH_NOL", 422, f"Selisih {nama} nol; tak ada yang disesuaikan.")
            continue
        alasan = item.reason_detail
        if transfer is not None:   # BUG-261: penyelesaian selisih terima Pemindahan = PENGURANGAN <= tertunda
            if tipe != "decrease":
                bl.galat("PENYESUAIAN_PEMINDAHAN_JENIS", 422, f"Baris {idx} ({nama}) menyelesaikan selisih Pemindahan {transfer['transfer_number']}; jenis harus Pengurangan.")
                continue
            tertunda = await _tertunda_barang(conn, tid, transfer["id"], pid, cache)
            if tertunda < 0:
                bl.galat("PENYESUAIAN_BARANG_BUKAN_PEMINDAHAN", 422, f"{nama} bukan barang di Pemindahan {transfer['transfer_number']}.")
                continue
            if -ubah > tertunda:
                bl.galat("PENYESUAIAN_MELEBIHI_TERTUNDA", 409,
                         f"Jumlah {nama} ({-ubah:f}) melebihi selisih tertunda Pemindahan {transfer['transfer_number']} ({tertunda:f}).")
                continue
            alasan = alasan or f"Hilang (selisih terima {transfer['transfer_number']})"
        harga = Decimal(int(p["unit_cost"] or 0))
        if harga == 0 and isyarat is not None:
            isyarat.append(isyarat_tanpa_harga(nama))
        if getattr(item, "serials", None) is not None:   # Serial S1: nomor seri hanya untuk barang wajib serial (flag serial_v1 + track_serial)
            from . import serial_gerak
            if not await serial_gerak.wajib(conn, tid, pid):
                bl.galat("SERIAL_TAK_DILACAK", 422, f"{nama} tidak memakai nomor seri; kosongkan nomor seri.")
                continue
        hasil.append({"product_id": pid, "product_code": p["code"], "product_name": nama, "unit": p["unit"],
                      "quantity_before": sebelum, "quantity_adjustment": ubah, "quantity_after": fisik if fisik is not None else sebelum + ubah,
                      "unit_cost": int(harga), "total_value": (abs(ubah) * harga).quantize(Decimal("1")),
                      "reason_detail": alasan, "system_quantity": sistem, "physical_quantity": fisik, "line_number": idx,
                      "warehouse_id": wid_simpan, "source_transfer_id": transfer["id"] if transfer is not None else None,
                      "source_transfer_number": transfer["transfer_number"] if transfer is not None else None,
                      "unit_masuk": unit_masuk if unit_masuk and hq["faktor"] != 1 else None, "jumlah_masuk": _d(item.physical_quantity if tipe == "recount" else item.quantity_adjustment) if unit_masuk and hq["faktor"] != 1 else None,
                      "konversi": konv_semua.get(str(pid), []), "serials": getattr(item, "serials", None)})
    return hasil


async def _konversi(conn, tid: str, product_ids) -> dict:
    """BUG-123: {str(product_id): [(satuan besar, faktor>1) urut faktor menurun]} -- bahan teks campur ("14 Karton 4 Pcs"); satu query per dokumen."""
    ids = list({str(p) for p in product_ids if p})
    if not ids:
        return {}
    hasil: dict = {}
    for r in await conn.fetch("SELECT product_id, conversion_unit, conversion_factor FROM unit_conversions WHERE tenant_id = $1 AND product_id = ANY($2::uuid[]) "
                              "AND COALESCE(is_active, true) AND conversion_factor > 1 ORDER BY conversion_factor DESC", tid, ids):
        hasil.setdefault(str(r["product_id"]), []).append((r["conversion_unit"], r["conversion_factor"]))
    return hasil


async def rencana_buat(conn, tid: str, body, bl: Blok) -> dict:
    g = await gudang(conn, tid, getattr(body, "warehouse_id", None), bl)
    isyarat: list = []
    baris = await susun_baris(conn, tid, body.items, body.adjustment_type, g["id"] if g else None, bl, isyarat)
    return {"blocks": bl.daftar, "gudang": g, "baris_draf": baris, "baris": [], "saldo": [], "isyarat": isyarat}


async def muat(conn, tid: str, sa_id, kunci: bool = False):
    return await conn.fetchrow("SELECT * FROM stock_adjustments WHERE id = $1 AND tenant_id = $2" + (" FOR UPDATE" if kunci else ""),
                               sa_id, tid)


async def rencana_ubah(conn, tid: str, sa_id, body, bl: Blok) -> dict:
    hasil = {"blocks": bl.daftar, "sa": None, "gudang": None, "baris_draf": None, "baris": [], "saldo": [], "isyarat": []}
    sa = await muat(conn, tid, sa_id)
    if not sa:
        bl.galat("PENYESUAIAN_TIDAK_DITEMUKAN", 404, "Penyesuaian stok tidak ditemukan.")
        return hasil
    hasil["sa"] = sa
    if sa["status"] != "draft":
        bl.galat("PENYESUAIAN_BUKAN_DRAF", 400, "Hanya penyesuaian draf yang bisa diubah.")
        return hasil
    wid = body.warehouse_id if getattr(body, "warehouse_id", None) is not None else sa["warehouse_id"]
    g = hasil["gudang"] = await gudang(conn, tid, wid, bl)
    if body.items is not None:
        hasil["baris_draf"] = await susun_baris(conn, tid, body.items, body.adjustment_type or sa["adjustment_type"], g["id"] if g else None, bl, hasil["isyarat"])
    return hasil


async def rencana_hapus(conn, tid: str, sa_id, bl: Blok) -> dict:
    sa = await muat(conn, tid, sa_id)
    if not sa:
        bl.galat("PENYESUAIAN_TIDAK_DITEMUKAN", 404, "Penyesuaian stok tidak ditemukan.")
    elif sa["status"] != "draft":
        bl.galat("PENYESUAIAN_BUKAN_DRAF", 400, "Hanya penyesuaian draf yang bisa dihapus; batalkan yang sudah diposting.")
    return {"blocks": bl.daftar, "sa": sa, "baris": [], "saldo": [], "catatan": "Draf akan dihapus." if sa and not bl.daftar else None}


# ---------------------------------------------------------------- posting
async def _akun(conn, tid: str, bl: Blok):
    try:
        return (await resolve_account_id_by_role(conn, tid, AccountRole.INVENTORY_MERCHANDISE),
                await resolve_account_id_by_role(conn, tid, AccountRole.INVENTORY_ADJUSTMENT_EXPENSE))
    except AccountRoleUnmappedError:
        bl.galat("AKUN_BELUM_DIATUR", 400, "Akun persediaan / penyesuaian persediaan belum diatur untuk usaha ini.")
        return None, None


# ---------------------------------------------------------------- akun lawan (penyesuaian-2; putusan pemilik LANGSUNG 10 Nov, TESTER #7a)
# Akun lawan jurnal penyesuaian = PILIHAN pengguna, divalidasi SERVER (ikut Accurate); bawaan = peran INVENTORY_ADJUSTMENT_EXPENSE.
# Ditolak berkalimat: akun persediaan, induk (header), nonaktif, piutang/utang usaha, kas/bank. SATU predikat (alasan_tolak_lawan) dipakai
# validasi tulis, pratinjau, DAN daftar dropdown (hanya akun sah), supaya dropdown tak pernah menawarkan yang ditolak tulis.
def alasan_tolak_lawan(a, persediaan_id) -> Optional[str]:
    """None = sah; selain itu kalimat alasan. `a` = baris chart_of_accounts (id, account_code, name, is_header, is_active, is_cash, account_type,
    psak_sub_category); persediaan_id = id akun peran INVENTORY_MERCHANDISE (boleh None bila peran belum diatur)."""
    n = f"{a['account_code']} {a['name']}"
    if a["is_header"]:
        return f"Akun {n} adalah akun induk; pilih akun rinci."
    if a["is_active"] is False:
        return f"Akun {n} sudah nonaktif."
    if (persediaan_id is not None and str(a["id"]) == str(persediaan_id)) or (a["psak_sub_category"] or "") == "INVENTORY":
        return f"Akun {n} adalah akun persediaan; lawan penyesuaian bukan persediaan itu sendiri."
    if str(a["account_type"] or "").upper() in ("RECEIVABLE", "PAYABLE"):
        return f"Akun {n} adalah akun piutang/utang usaha; gunakan modul pembayaran."
    if a["is_cash"]:
        return f"Akun {n} adalah akun kas/bank; gunakan modul Kas & Bank."
    return None


_SQL_AKUN = ("SELECT c.id, c.account_code, c.name, c.account_type, c.is_header, c.is_active, c.is_cash, c.psak_sub_category "
             "FROM chart_of_accounts c WHERE c.tenant_id = $1")


async def akun_lawan(conn, tid: str, lawan_id, bl: Blok, persediaan_id=None):
    """Id akun lawan pilihan (UUID) bila sah; None bila tak dipilih (-> bawaan peran) atau ditolak (blok berkalimat)."""
    if lawan_id in (None, ""):
        return None
    try:
        aid = UUID(str(lawan_id))
    except ValueError:
        bl.galat("AKUN_LAWAN_TAK_SAH", 422, "Akun lawan tidak dikenal.")
        return None
    a = await conn.fetchrow(_SQL_AKUN + " AND c.id = $2", tid, aid)
    if not a:
        bl.galat("AKUN_LAWAN_TAK_SAH", 422, "Akun lawan tidak dikenal.")
        return None
    alasan = alasan_tolak_lawan(a, persediaan_id)
    if alasan:
        bl.galat("AKUN_LAWAN_DITOLAK", 422, alasan)
        return None
    return aid


async def daftar_akun_lawan(conn, tid: str) -> dict:
    """Dropdown: akun sah saja (predikat sama dengan validasi) + id bawaan peran (None bila belum diatur)."""
    try:
        persediaan = await resolve_account_id_by_role(conn, tid, AccountRole.INVENTORY_MERCHANDISE)
        bawaan = await resolve_account_id_by_role(conn, tid, AccountRole.INVENTORY_ADJUSTMENT_EXPENSE)
    except AccountRoleUnmappedError:
        persediaan = bawaan = None
    baris = await conn.fetch(_SQL_AKUN + " ORDER BY c.account_code", tid)
    return {"bawaan_id": str(bawaan) if bawaan else None,
            "akun": [{"id": str(a["id"]), "account_code": a["account_code"], "name": a["name"], "account_type": a["account_type"],
                      "bawaan": bool(bawaan) and str(a["id"]) == str(bawaan)}
                     for a in baris if alasan_tolak_lawan(a, persediaan) is None]}


async def rencana_posting(conn, tid: str, sa_id, bl: Blok, kunci: bool = False, counter_account_id=None) -> dict:
    """Jurnal (Dr/Cr == posting lama) + gerak stok per barang di gudang penyesuaian + isyarat (stok minus, WAC berubah).
    counter_account_id (opsional, putusan 10 Nov #7a): akun lawan pilihan pengguna, divalidasi akun_lawan; kosong = peran bawaan."""
    hasil = {"blocks": bl.daftar, "sa": None, "items": [], "gudang": None, "baris": [], "gerak": [], "isyarat": [], "saldo": [],
             "akun": (None, None), "total": NOL}
    sa = await muat(conn, tid, sa_id, kunci)
    if not sa:
        bl.galat("PENYESUAIAN_TIDAK_DITEMUKAN", 404, "Penyesuaian stok tidak ditemukan.")
        return hasil
    hasil["sa"] = sa
    if sa["status"] != "draft":
        bl.galat("PENYESUAIAN_BUKAN_DRAF", 400, f"Penyesuaian berstatus '{sa['status']}' tidak bisa diposting.")
        return hasil
    await periode(conn, tid, sa["adjustment_date"], bl)
    items = hasil["items"] = await conn.fetch(
        "SELECT i.* FROM stock_adjustment_items i JOIN stock_adjustments s ON s.id = i.stock_adjustment_id AND s.tenant_id = $2 "
        "WHERE i.stock_adjustment_id = $1 ORDER BY i.line_number", sa_id, tid)
    if not items:
        bl.galat("TANPA_BARIS", 400, "Tidak ada barang untuk diposting.")
        return hasil
    g = hasil["gudang"] = await gudang(conn, tid, sa["warehouse_id"], bl)
    persediaan, penyesuaian = hasil["akun"] = await _akun(conn, tid, bl)
    pilihan = await akun_lawan(conn, tid, counter_account_id, bl, persediaan)
    if pilihan:
        penyesuaian = pilihan
        hasil["akun"] = (persediaan, penyesuaian)
    naik = sum((_d(i["total_value"]) for i in items if _d(i["quantity_adjustment"]) > 0), NOL)
    turun = sum((_d(i["total_value"]) for i in items if _d(i["quantity_adjustment"]) <= 0), NOL)
    hasil["total"] = naik + turun
    n = sa["adjustment_number"]
    if naik > 0:
        hasil["baris"] += [(persediaan, naik, NOL, f"Penambahan Persediaan - {n}"), (penyesuaian, NOL, naik, f"Koreksi Persediaan - {n}")]
    if turun > 0:
        hasil["baris"] += [(penyesuaian, turun, NOL, f"Penyesuaian Persediaan - {n}"), (persediaan, NOL, turun, f"Pengurangan Persediaan - {n}")]
    hasil["tanpa_jurnal"] = not hasil["baris"]  # BUG-212 (putusan pemilik 13 Nov): total nilai 0 = TANPA jurnal; stok tetap bergerak
    if not g:
        return hasil
    # BUG-261: kunci + revalidasi baris penyelesaian Pemindahan (tertunda bisa berubah sejak draf: Terima / penyesuaian lain). Kunci STOCK:<barang>:<Transit> SAMA dengan Terima.
    await _revalidasi_pemindahan(conn, tid, items, bl, kunci)
    nama_gudang = {g["id"]: g["name"]}
    for i in items:
        p = await conn.fetchrow("SELECT item_type, track_inventory FROM products WHERE id = $1 AND tenant_id = $2", i["product_id"], tid)
        if not (p and p["item_type"] == "goods" and (p["track_inventory"] if p["track_inventory"] is not None else True)):
            continue
        q = _d(i["quantity_adjustment"])
        gid = i.get("warehouse_id") or g["id"]     # BUG-261: gudang PER BARIS (NULL = gudang dokumen)
        if gid not in nama_gudang:
            nama_gudang[gid] = await conn.fetchval("SELECT name FROM warehouses WHERE id = $1 AND tenant_id = $2", gid, tid) or "gudang"
        sebelum = await stok_gudang(conn, tid, i["product_id"], gid)
        hasil["gerak"].append({"product_id": i["product_id"], "product_code": i["product_code"], "product_name": i["product_name"],
                               "masuk": q if q > 0 else NOL, "keluar": -q if q < 0 else NOL, "unit_cost": _d(i["unit_cost"]),
                               "stok_sebelum": sebelum, "stok_sesudah": sebelum + q, "warehouse_id": gid, "gudang": nama_gudang[gid],
                               "serials": i.get("serials")})   # Serial S1: dinilai pintu kartu stok (serial_gerak) saat posting
        if q != 0 and _d(i["unit_cost"]) == 0:
            hasil["isyarat"].append(isyarat_tanpa_harga(i["product_name"]))
        if i.get("source_transfer_id") and sebelum + q < 0:
            bl.galat("PENYESUAIAN_STOK_TRANSIT_KURANG", 409, f"Stok {i['product_name']} di {nama_gudang[gid]} hanya {sebelum:f}, penyesuaian mengurangi {-q:f}.")
        elif q < 0 and sebelum + q < 0:
            hasil["isyarat"].append(f"Stok {i['product_name']} di {nama_gudang[gid]} akan minus ({sebelum + q}).")
        kini = await harga_pokok(conn, tid, i["product_id"])
        if q != 0 and kini != _d(i["unit_cost"]):
            hasil["isyarat"].append(f"Harga pokok {i['product_name']} berubah sejak draf dibuat ({tg.rp(i['unit_cost'])} → {tg.rp(kini)}); "
                                    "nilai memakai harga draf.")
    return hasil


async def _revalidasi_pemindahan(conn, tid: str, items, bl: Blok, kunci: bool) -> None:
    """BUG-261: tiap baris bertaut Pemindahan: status masih in_transit dan qty <= tertunda SAAT POSTING (kunci advisory bila `kunci`). Pratinjau == tulis (penentu sama)."""
    from . import gudang_transit as GT
    from . import pemindahan_barang as pb
    terkait = [i for i in items if i.get("source_transfer_id")]
    if not terkait:
        return
    transit = await GT.id_transit(conn, tid)
    if kunci and transit:
        for pid in sorted({str(i["product_id"]) for i in terkait}):
            await pb._kunci(conn, f"STOCK:{pid}:{transit}")
    cache: dict = {}
    for i in terkait:
        t = await conn.fetchrow("SELECT id, transfer_number, status FROM stock_transfers WHERE id = $1 AND tenant_id = $2", i["source_transfer_id"], tid)
        if not t or t["status"] != "in_transit":
            bl.galat("PENYESUAIAN_PEMINDAHAN_STATUS", 409,
                     f"Pemindahan {t['transfer_number'] if t else ''} tidak lagi di Transit (status {t['status'] if t else 'hilang'}); selisihnya tak bisa diselesaikan lewat penyesuaian ini.")
            continue
        tertunda = await _tertunda_barang(conn, tid, t["id"], i["product_id"], cache)
        qty = abs(_d(i["quantity_adjustment"]))
        if tertunda < 0:
            bl.galat("PENYESUAIAN_BARANG_BUKAN_PEMINDAHAN", 422, f"{i['product_name']} bukan barang di Pemindahan {t['transfer_number']}.")
        elif qty > tertunda:
            bl.galat("PENYESUAIAN_MELEBIHI_TERTUNDA", 409,
                     f"Jumlah {i['product_name']} ({qty:f}) melebihi selisih tertunda Pemindahan {t['transfer_number']} ({tertunda:f}).")


# ---------------------------------------------------------------- batal
async def rencana_batal(conn, tid: str, sa_id, alasan, bl: Blok, kunci: bool = False) -> dict:
    """Jurnal pembalik (== void lama) + cermin gerak stok + isyarat stok minus. Draf -> dihapus (perilaku lama). Putusan 3: periode tanggal
    penyesuaian DAN hari ini (jurnal pembalik) harus terbuka."""
    from ..utils.tanggal_tenant import tanggal_dokumen
    hasil = {"blocks": bl.daftar, "sa": None, "baris": [], "gerak": [], "isyarat": [], "saldo": [], "hapus_draf": False, "hari_ini": None}
    sa = await muat(conn, tid, sa_id, kunci)
    if not sa:
        bl.galat("PENYESUAIAN_TIDAK_DITEMUKAN", 404, "Penyesuaian stok tidak ditemukan.")
        return hasil
    hasil["sa"] = sa
    if sa["status"] == "void":
        bl.galat("PENYESUAIAN_SUDAH_DIBATALKAN", 400, "Penyesuaian stok ini sudah dibatalkan.")
        return hasil
    if sa["status"] == "draft":
        hasil["hapus_draf"], hasil["catatan"] = True, "Draf akan dihapus (belum berjurnal)."
        return hasil
    await periode(conn, tid, sa["adjustment_date"], bl)
    hari_ini = hasil["hari_ini"] = await tanggal_dokumen(conn, tid)  # t10-tanggal-bisnis: jurnal pembalik bertanggal hari ini
    await periode(conn, tid, hari_ini, bl)
    for t in await conn.fetch("SELECT DISTINCT t.transfer_number, t.status FROM stock_adjustment_items i JOIN stock_transfers t ON t.id = i.source_transfer_id "
                              "AND t.tenant_id = $2 WHERE i.stock_adjustment_id = $1", sa_id, tid):   # BUG-261: pembalik mengembalikan barang ke Transit -> pemindahan harus masih di Transit
        if t["status"] != "in_transit":
            bl.galat("PENYESUAIAN_PEMINDAHAN_STATUS", 409,
                     f"Pemindahan {t['transfer_number']} berstatus {t['status']}; pembatalan ini akan mengembalikan barang ke Transit yang sudah tak dipakai pemindahan itu.")
    hasil["tanpa_jurnal"] = not sa["journal_id"]  # BUG-212: dokumen nilai 0 tak punya jurnal -> tanpa jurnal pembalik, gerak stok TETAP dibalik
    if sa["journal_id"]:
        lines = await conn.fetch("SELECT l.* FROM journal_lines l JOIN journal_entries e ON e.id = l.journal_id AND e.tenant_id = $2 "
                                 "WHERE l.journal_id = $1 ORDER BY l.line_number", sa["journal_id"], tid)
        hasil["baris"] = [(x["account_id"], _d(x["credit"]), _d(x["debit"]), f"Reversal - {x['memo'] or ''}") for x in lines]
    for r in await conn.fetch(
            "SELECT product_id, product_name, warehouse_id, SUM(quantity_in) AS masuk, SUM(quantity_out) AS keluar FROM inventory_ledger "
            "WHERE tenant_id = $1 AND source_type = 'STOCK_ADJUSTMENT' AND source_id = $2 GROUP BY 1, 2, 3 ORDER BY 2, 1", tid, sa_id):
        sebelum = await stok_gudang(conn, tid, r["product_id"], r["warehouse_id"])
        sesudah = sebelum - _d(r["masuk"]) + _d(r["keluar"])
        hasil["gerak"].append({"product_id": r["product_id"], "product_name": r["product_name"], "warehouse_id": r["warehouse_id"],
                               "masuk": _d(r["keluar"]), "keluar": _d(r["masuk"]), "stok_sebelum": sebelum, "stok_sesudah": sesudah})
        if sesudah < 0:
            hasil["isyarat"].append(f"Stok {r['product_name']} akan minus ({sesudah}) sesudah pembatalan.")
    return hasil


# ---------------------------------------------------------------- aksi_tersedia + keluaran pratinjau
# BUG-199: kalimat bila PENGGUNA tak berizin (izin dari penentu rute yang SAMA dgn middleware: izin_rute_pengguna atas jalur tulis aksinya)
ALASAN_TAK_BERIZIN = {"posting": "Anda tidak punya izin untuk memposting penyesuaian stok.", "ubah": "Anda tidak punya izin untuk mengubah penyesuaian stok.",
                      "hapus": "Anda tidak punya izin untuk menghapus penyesuaian stok.", "batalkan": "Anda tidak punya izin untuk membatalkan penyesuaian stok."}
JALUR_IZIN = {"posting": ("POST", "/api/stock-adjustments/{id}/post"), "batalkan": ("POST", "/api/stock-adjustments/{id}/void"),
              "ubah": ("PATCH", "/api/stock-adjustments/{id}"), "hapus": ("DELETE", "/api/stock-adjustments/{id}")}


async def izin_aksi(user: dict, sa_id) -> dict:
    """{aksi: boleh?} untuk pengguna: SATU penentu dgn middleware (ROUTE_PERMISSIONS + PolicyEngine.can lewat izin_rute_pengguna)."""
    from ..middleware.permission_middleware import izin_rute_pengguna
    hasil = {}
    for kunci, (metode, jalur) in JALUR_IZIN.items():
        j = jalur.replace("{id}", str(sa_id))
        hasil[kunci] = bool((await izin_rute_pengguna(user, [j], metode)).get(j))
    return hasil


async def aksi_tersedia(conn, tid: str, sa, izin: Optional[dict] = None) -> dict:
    """izin (opsional, BUG-199) = {aksi: bool} dari izin_aksi: aksi tersedia = KEADAAN dokumen DAN izin pengguna; tak berizin -> tersedia False + kalimat izin
    (didahulukan: pengguna tak bisa menjalankannya apa pun keadaannya). izin None = hanya keadaan (perilaku lama)."""
    from ..schemas.aksi_tersedia import aksi
    draf = sa["status"] == "draft"
    post = (await rencana_posting(conn, tid, sa["id"], Blok(True)))["blocks"] if draf else [{"message": "Hanya penyesuaian draf yang bisa diposting."}]
    batal = (await rencana_batal(conn, tid, sa["id"], None, Blok(True)))["blocks"]
    hasil = {
        "posting": aksi(not post, post[0]["message"] if post else None),
        "ubah": aksi(draf, "Penyesuaian yang sudah diposting tidak bisa diubah; batalkan lalu buat baru."),
        "hapus": aksi(draf, "Penyesuaian yang sudah diposting tidak bisa dihapus; batalkan saja."),
        "batalkan": aksi(not draf and not batal, "Draf dihapus, bukan dibatalkan." if draf else (batal[0]["message"] if batal else None)),
    }
    if izin is not None:
        for kunci, boleh in izin.items():
            if kunci in hasil and not boleh:
                hasil[kunci] = aksi(False, ALASAN_TAK_BERIZIN[kunci])
    return hasil


def _gerak_json(g: dict) -> dict:
    return {"product_id": str(g["product_id"]), "product_name": g["product_name"], "masuk": str(g["masuk"]), "keluar": str(g["keluar"]),
            "stok_sebelum": str(g["stok_sebelum"]), "stok_sesudah": str(g["stok_sesudah"]), "minus": g["stok_sesudah"] < 0,
            "warehouse_id": str(g["warehouse_id"]) if g.get("warehouse_id") else None, "gudang": g.get("gudang")}


def _draf_json(b: dict) -> dict:
    return {"product_id": str(b["product_id"]), "product_name": b["product_name"], "unit": b["unit"],
            "quantity_before": str(b["quantity_before"]), "quantity_adjustment": str(b["quantity_adjustment"]),
            "quantity_after": str(b["quantity_after"]), "unit_cost": rp(b["unit_cost"]), "total_value": rp(b["total_value"]),
            "total_value_signed": int(nilai_bertanda(b["quantity_adjustment"], b["total_value"])), "nilai_bertanda_text": teks_bertanda(nilai_bertanda(b["quantity_adjustment"], b["total_value"])),
            "warehouse_id": str(b["warehouse_id"]) if b.get("warehouse_id") else None, "gudang_terkunci": bool(b.get("source_transfer_id")),
            "source_transfer_id": str(b["source_transfer_id"]) if b.get("source_transfer_id") else None, "source_transfer_number": b.get("source_transfer_number"),
            "reason_detail": b.get("reason_detail"),
            # BUG-123: jumlah yang diketik (satuan besar) + teks campur dari server; baris selalu dalam satuan dasar
            "unit_input": b.get("unit_masuk"), "quantity_input": str(b["jumlah_masuk"]) if b.get("jumlah_masuk") is not None else None,
            "quantity_before_campur_text": TLP.campur(b["quantity_before"], b["unit"], b.get("konversi")),
            "quantity_adjustment_campur_text": TLP.campur(b["quantity_adjustment"], b["unit"], b.get("konversi")),
            "quantity_after_campur_text": TLP.campur(b["quantity_after"], b["unit"], b.get("konversi"))}


async def pratinjau(pool, tid: str, rencana, payload) -> dict:
    """Bentuk B8 (kasbank_tulis.keluaran) + gerak[] / baris_draf[] / isyarat[] / gudang; transaksi READ ONLY (nol tulisan)."""
    from .kasbank_tulis import keluaran
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            r = await rencana(conn)
            d = await keluaran(conn, tid, r, payload)
            d.pop("saldo", None)
            d["gudang"] = {"id": str(r["gudang"]["id"]), "name": r["gudang"]["name"]} if r.get("gudang") else None
            d["gerak"] = [_gerak_json(g) for g in r.get("gerak", [])]
            d["isyarat"] = list(r.get("isyarat", []))
            if r.get("tanpa_jurnal") and not r.get("blocks"):  # BUG-212: pratinjau posting/batal menyatakan tanpa jurnal
                d["tanpa_jurnal"] = True
                d["jurnal_catatan"] = TEKS_TANPA_JURNAL_BATAL if "hapus_draf" in r else TEKS_TANPA_JURNAL_POSTING
            if r.get("baris_draf") is not None:
                d["baris_draf"] = [_draf_json(b) for b in r["baris_draf"]]
            # BUG-204 (+ permintaan WORKSPACE): total nilai dokumen buat/ubah dari penentu YANG SAMA dgn yang tersimpan (trigger trg_update_sa_totals = Σ total_value baris);
            # FE tak menjumlah. Ubah tanpa `items` = total tersimpan sekarang. Ditolak (ada blok) = tanpa total (tak ada yang tersimpan).
            if "baris_draf" in r and not r.get("blocks"):
                if r["baris_draf"] is not None:
                    d["total_value"] = rp(sum((b["total_value"] for b in r["baris_draf"]), NOL))
                elif r.get("sa") is not None:
                    d["total_value"] = rp(r["sa"]["total_value"])
            return {"success": True, "data": d}



# ---------------------------------------------------------------- riwayat + dokumen (bentuk biaya_riwayat / so_riwayat)
async def dokumen(conn, tid: str, sa_id) -> Optional[dict]:
    """{anchor, default, groups}: penyesuaian · jurnal (+pembalik) · gerak stok (STOCK_ADJUSTMENT / _VOID). None = tak ada (404)."""
    from .biaya_riwayat import _dok, _tgl
    sa = await muat(conn, tid, sa_id)
    if not sa:
        return None
    batal = sa["status"] == "void"
    kel = [{"key": "penyesuaian", "label": "Penyesuaian stok", "docs": [
        _dok("stock_adjustment", sa["id"], sa["adjustment_number"], sa["adjustment_date"], rp(sa["total_value"]), sa["status"], batal)]}]
    jurnal = []
    if sa["journal_id"]:
        j = await conn.fetchrow("SELECT id, journal_number, journal_date, total_debit, reversed_by_id FROM journal_entries "
                                "WHERE id = $1 AND tenant_id = $2", sa["journal_id"], tid)
        if j:
            jurnal.append(_dok("journal", j["id"], j["journal_number"], j["journal_date"], rp(j["total_debit"]), "diposting", batal))
            r = await conn.fetchrow("SELECT id, journal_number, journal_date, total_debit FROM journal_entries WHERE id = $1 AND tenant_id = $2",
                                    j["reversed_by_id"], tid) if j["reversed_by_id"] else None
            if r:
                jurnal.append(_dok("journal", r["id"], r["journal_number"], r["journal_date"], rp(r["total_debit"]), "pembalik"))
    if jurnal:
        kel.append({"key": "jurnal", "label": "Jurnal", "docs": jurnal})
    gerak = await conn.fetch(
        "SELECT id, source_type, product_name, movement_date, quantity_in, quantity_out FROM inventory_ledger WHERE tenant_id = $1 "
        "AND source_id = $2 AND source_type IN ('STOCK_ADJUSTMENT', 'STOCK_ADJUSTMENT_VOID') ORDER BY created_at, id", tid, sa_id)
    if gerak:
        kel.append({"key": "gerak", "label": "Gerak stok", "docs": [
            _dok("inventory_movement", g["id"], g["product_name"], g["movement_date"], None,
                 "pembalik" if g["source_type"].endswith("_VOID") else ("dibatalkan" if batal else "tercatat"),
                 batal and not g["source_type"].endswith("_VOID")) for g in gerak]})
    return {"anchor": {"kind": "stock_adjustment", "id": str(sa["id"]), "number": sa["adjustment_number"], "date": _tgl(sa["adjustment_date"]),
                       "position_text": position_text(sa["status"], sa["adjustment_type"]), "total": rp(sa["total_value"]),
                       "jenis_text": jenis_text(sa["adjustment_type"])},
            "default": {"group": "penyesuaian", "index": 0}, "groups": kel}


async def riwayat(conn, tid: str, sa_id, limit: int = 200) -> Optional[dict]:
    """dibuat · diposting · dibatalkan (kolom beraktor stock_adjustments). Ubah/hapus draf tak tercatat (Law 34: tak diklaim lengkap)."""
    from . import audit_dokumen as _AD
    from .so_riwayat import _Kumpul, _rp, _selesaikan
    sa = await muat(conn, tid, sa_id)
    if not sa:
        # BUG-197: draf yang DIHAPUS tak punya baris dokumen; riwayatnya tinggal dari audit_logs (tenant + event hapus), selain itu 404
        nomor = await conn.fetchval(
            """SELECT entity_number FROM audit_logs WHERE tenant_id = $1 AND entity_id = $2::uuid AND entity_type = 'stock_adjustments'
               AND "eventType" = $3 ORDER BY "createdAt" DESC LIMIT 1""", tid, str(sa_id), _AD.nama("stock_adjustment", "deleted"))
        if nomor is None:
            return None
        keluar, total = await _selesaikan(conn, tid, _Kumpul(), {"stock_adjustment": [sa_id]}, {"stock_adjustment": True}, limit)
        return {"adjustment_id": str(sa_id), "adjustment_number": nomor, "events": keluar, "total": total, "omitted": [], "dihapus": True}
    n, k = sa["adjustment_number"], _Kumpul()
    k.tambah(sa["created_at"], "PENYESUAIAN_DIBUAT", f"Penyesuaian stok {n} ({jenis_text(sa['adjustment_type']).lower()}) dibuat",
             sa["created_by"], "stock_adjustment", sa["id"], n)
    k.tambah(sa["posted_at"], "PENYESUAIAN_DIPOSTING", f"Penyesuaian stok {n} {_rp(sa['total_value'])} diterbitkan", sa["posted_by"],   # BUG-221: tanpa kata "diposting"
             "stock_adjustment", sa["id"], n)
    alasan = f": {sa['voided_reason']}" if sa["voided_reason"] else ""
    k.tambah(sa["voided_at"], "PENYESUAIAN_DIBATALKAN", f"Penyesuaian stok {n} dibatalkan{alasan}", sa["voided_by"],   # BUG-221: tanpa "(void)"
             "stock_adjustment", sa["id"], n)
    keluar, total = await _selesaikan(conn, tid, k, {"stock_adjustment": [sa["id"]]}, {"stock_adjustment": True}, limit)    # BUG-197: ikut audit_logs (ubah/hapus draf)
    return {"adjustment_id": str(sa["id"]), "adjustment_number": n, "events": keluar, "total": total, "omitted": []}


# ---------------------------------------------------------------- BUG-261: "Ambil dari Pemindahan" (selisih terima -> baris Pengurangan di Transit)
_SQL_TERTUNDA = """
    SELECT st.id, st.transfer_number, st.shipped_date, fw.name AS dari, tw.name AS ke,
           COUNT(*) AS n_barang, SUM(i.quantity_shipped - d.diterima - r.selesai) AS total_tertunda
    FROM stock_transfers st
    JOIN stock_transfer_items i ON i.stock_transfer_id = st.id
    LEFT JOIN warehouses fw ON fw.id = st.from_warehouse_id AND fw.tenant_id = st.tenant_id
    LEFT JOIN warehouses tw ON tw.id = st.to_warehouse_id AND tw.tenant_id = st.tenant_id
    LEFT JOIN LATERAL (SELECT COALESCE(SUM(il.quantity_in), 0) AS diterima FROM inventory_ledger il
                       WHERE il.tenant_id = st.tenant_id AND il.source_type = 'RELOKASI_TERIMA' AND il.source_id = st.id AND il.product_id = i.item_id) d ON true
    LEFT JOIN LATERAL (SELECT COALESCE(SUM(ABS(sai.quantity_adjustment)), 0) AS selesai FROM stock_adjustment_items sai
                       JOIN stock_adjustments sa ON sa.id = sai.stock_adjustment_id AND sa.tenant_id = st.tenant_id AND sa.status = 'posted'
                       WHERE sai.source_transfer_id = st.id AND sai.product_id = i.item_id) r ON true
    WHERE st.tenant_id = $1 AND st.status = 'in_transit' AND COALESCE(i.quantity_shipped, 0) > d.diterima + r.selesai
    GROUP BY st.id, st.transfer_number, st.shipped_date, fw.name, tw.name
    ORDER BY st.shipped_date, st.transfer_number LIMIT $2"""


async def pemindahan_tertunda(conn, tid: str, batas: int = 100) -> list:
    """Pemindahan yang masih di Transit dengan selisih tertunda > 0 (dikirim - diterima - diselesaikan). Teks dari server; angka string."""
    from .barang_daftar import _q
    hasil = []
    for r in await conn.fetch(_SQL_TERTUNDA, tid, batas):
        total = _d(r["total_tertunda"])
        hasil.append({"id": str(r["id"]), "transfer_number": r["transfer_number"], "shipped_date": r["shipped_date"].isoformat() if r["shipped_date"] else None,
                      "dari": r["dari"], "ke": r["ke"], "jumlah_barang": int(r["n_barang"]), "total_tertunda": _n(total),
                      "teks": f"{int(r['n_barang'])} barang · tertunda {_q(total)}"})
    return hasil


def _n(x: Decimal) -> str:
    """Angka JSON sebagai string tanpa nol ekor ('-1', bukan '-1.0000' dari NUMERIC(15,4))."""
    return format(x.normalize(), "f") if x != 0 else "0"


async def tarik_pemindahan(conn, tid: str, transfer_id) -> Optional[dict]:
    """Baris siap-pakai untuk satu Pemindahan (putusan pemilik 17 Nov, ikut Accurate): tiap barang bertertunda > 0 -> baris PENGURANGAN qty = tertunda, gudang = Transit
    TERKUNCI, source_transfer_id, alasan 'Hilang (selisih terima <no>)'. Penentu tertunda = pd2.item_baris (SATU sumber dgn Terima/K9). None = pemindahan tak ada (404)."""
    from . import gudang_transit as GT
    from . import pemindahan_dua_langkah as pd2
    t = await conn.fetchrow("SELECT id, transfer_number, status, shipped_date FROM stock_transfers WHERE id = $1 AND tenant_id = $2", transfer_id, tid)
    if not t:
        return None
    transit = await GT.id_transit(conn, tid)
    blocks, baris = [], []
    if t["status"] != "in_transit":
        blocks.append({"code": "PENYESUAIAN_PEMINDAHAN_STATUS", "status": 409,
                       "message": f"Pemindahan {t['transfer_number']} berstatus {t['status']}; hanya yang masih di Transit yang selisihnya bisa diselesaikan."})
    elif not transit:
        blocks.append({"code": "PENYESUAIAN_TRANSIT_TAK_ADA", "status": 409, "message": "Gudang Transit belum ada."})
    else:
        for n, i in enumerate(await pd2.item_baris(conn, tid, transfer_id), 1):
            tertunda = i["quantity_pending"]
            if tertunda <= NOL:
                continue
            p = await conn.fetchrow("SELECT item_type, track_inventory FROM products WHERE id = $1 AND tenant_id = $2", i["item_id"], tid)
            if not barang_bertstok(p):
                continue
            harga = await harga_pokok(conn, tid, i["item_id"])
            sebelum = await stok_gudang(conn, tid, i["item_id"], transit)
            baris.append({"product_id": str(i["item_id"]), "product_code": i["item_code"], "product_name": i["item_name"], "unit": i["unit"],
                          "quantity_adjustment": _n(-tertunda), "quantity_tertunda": _n(tertunda), "quantity_before": _n(sebelum),
                          "unit_cost": rp(harga), "total_value": rp((tertunda * harga).quantize(Decimal("1"))),
                          "warehouse_id": str(transit), "gudang": GT.NAMA, "gudang_terkunci": True,
                          "source_transfer_id": str(t["id"]), "source_transfer_number": t["transfer_number"],
                          "reason_detail": f"Hilang (selisih terima {t['transfer_number']})"})
        if not baris:
            blocks.append({"code": "PENYESUAIAN_TIDAK_ADA_TERTUNDA", "status": 409, "message": f"Pemindahan {t['transfer_number']} tidak punya selisih tertunda."})
    return {"transfer": {"id": str(t["id"]), "transfer_number": t["transfer_number"], "status": t["status"]},
            "adjustment_type": "decrease", "jenis_text": JENIS_TEXT["decrease"], "gudang": {"id": str(transit) if transit else None, "name": GT.NAMA, "terkunci": True},
            "baris": baris, "blocks": blocks, "ok": not blocks}

