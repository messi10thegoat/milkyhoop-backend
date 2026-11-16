"""Impor / ekspor Excel HARGA per daftar harga (BUG-251, putusan pemilik 16 Nov 2026; kontrak KONTRAK-be-dh-impor-ekspor-251-20261116.md).

Pola SAMA dengan Impor Barang (services/impor_barang): baca (urai berkas SEKALI) -> pratinjau (baris JSON, READ ONLY) -> tulis ATOMIK
(semua atau tidak; payload pratinjau == payload tulis, tanpa token). Penentu per baris = siapkan_baris_harga + tulis_baris_harga
(SATU penulis dengan Tambah/Ubah harga); di sini hanya aturan BATCH: pencocokan baris, kunci ganda di berkas, hitungan, peringatan.
Kunci baris = barang + satuan + mulai qty + MULAI BERLAKU (BUG-245: harga berjangka). Cocok = ubah, tak cocok = tambah, sama = tak berubah.
Tanpa jurnal (data induk). Tahap 1: tambah/ubah saja (tak pernah menghapus baris); impor .xlsx saja."""
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException

from . import bulk
from . import harga_daftar as HD
from . import harga_massal as HM
from . import harga_riwayat as HR
from . import impor_barang as IB
from . import satuan_dasar as SD
from .galat_berkode import GalatBerkode

MAKS_BARIS = 2000
MAKS_BYTE = 5 * 1024 * 1024
KODE_NOL, KODE_NANTI, KODE_BAWAH_HPP = "DH_HARGA_NOL", "DH_BERLAKU_NANTI", "DH_DI_BAWAH_HPP"

# (kunci, judul templat/ekspor, wajib). Urutan = urutan kolom ekspor.
KOLOM = [("kode", "Kode barang", True), ("nama", "Nama", False), ("satuan", "Satuan", False), ("min_qty", "Mulai qty", False),
         ("harga", "Harga", True), ("min_harga", "Harga minimum", False), ("diskon", "Diskon %", False), ("mulai", "Mulai berlaku", False), ("akhir", "Berakhir", False)]
SINONIM = {
    "kode": ["kode barang", "kode", "sku", "kode produk", "item code", "code"],
    "nama": ["nama", "nama barang", "nama produk", "produk", "item", "barang"],
    "satuan": ["satuan", "unit", "uom"],
    "min_qty": ["mulai qty", "mulai kuantitas", "untuk kts diatas", "kts diatas", "min qty", "minimum qty", "qty minimum", "qty", "kuantitas"],
    "harga": ["harga", "harga baru", "harga jual", "price"],
    "min_harga": ["harga minimum", "harga min", "minimum", "min price", "lantai harga"],
    "diskon": ["diskon", "diskon persen", "disc", "disc persen", "potongan", "potongan persen"],
    "mulai": ["mulai berlaku", "tanggal mulai", "berlaku mulai", "mulai", "start"],
    "akhir": ["berakhir", "tanggal berakhir", "berlaku sampai", "sampai", "akhir", "end"],
}
WAJIB = ("kode", "harga")


def blok(code: str, message: str, field: Optional[str] = None) -> dict:
    return IB.blok(code, message, field)


def _per(code: str, pesan: str) -> dict:
    return {"code": code, "butuh_konfirmasi": True, "pesan": pesan, "message": pesan}


# ------------------------------------------------------------------------------------------------ baca / ekspor
def baca_xlsx(isi: bytes) -> dict:
    """Berkas -> {baris, pemetaan, tak_dipakai, peringatan} (nol DB; judul kolom bebas seperti Impor Barang)."""
    if len(isi) > MAKS_BYTE:
        raise HTTPException(status_code=400, detail={"code": "BERKAS_TERLALU_BESAR", "message": f"Berkas maksimal {MAKS_BYTE // (1024 * 1024)} MB."})
    r = IB.baca_xlsx(isi, KOLOM, SINONIM, WAJIB, nomor=True)
    if len(r["baris"]) > MAKS_BARIS:
        raise HTTPException(status_code=400, detail={"code": "IMPOR_TERLALU_BANYAK", "message": f"Maksimal {MAKS_BARIS} baris per impor; berkas ini {len(r['baris'])} baris."})
    return r


def _tgl(v, medan: str):
    """Sel tanggal -> date | None; tak terbaca -> ValueError. Menerima date/datetime (sel Excel), ISO, dd/mm/yyyy, dd-mm-yyyy."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for f in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    raise ValueError(s)


def _baris_ekspor(r, dasar) -> list:
    return [r["item_code"], r["nama"], HD.unit_text(r["unit"], dasar), Decimal(str(r["min_quantity"])).normalize(), Decimal(str(r["price"])),
            Decimal(str(r["min_price"])) if r["min_price"] is not None else None,
            Decimal(str(r["discount_percent"])) if r["discount_percent"] is not None else None, r["start_date"], r["end_date"]]


async def ukur_ekspor(conn, tid: str, pl_id) -> list:
    """Baris daftar untuk ekspor (bersaring tenant), urut kode barang, satuan, min qty, mulai berlaku."""
    return [dict(r) for r in await conn.fetch(
        """SELECT COALESCE(NULLIF(pli.item_code, ''), p.item_code, p.sku) AS item_code, p.nama_produk AS nama, pli.unit, pli.min_quantity, pli.price, pli.min_price, pli.discount_percent,
                  pli.start_date, pli.end_date, COALESCE(NULLIF(p.base_unit, ''), p.satuan) AS dasar
           FROM price_list_items pli JOIN price_lists pl ON pl.id = pli.price_list_id
           LEFT JOIN products p ON p.id = pli.item_id AND p.tenant_id = pl.tenant_id
           WHERE pl.tenant_id = $1 AND pli.price_list_id = $2
           ORDER BY 1, p.nama_produk, pli.unit NULLS FIRST, pli.min_quantity, pli.start_date NULLS FIRST""", tid, pl_id)]


def ekspor_csv(baris: list) -> bytes:
    """CSV id-ID: UTF-8 BOM, pemisah ';', desimal ','. Angka tanpa ribuan (impor membaca koma desimal)."""
    import csv
    out = io.StringIO()
    w = csv.writer(out, delimiter=";", lineterminator="\r\n")
    w.writerow([j for _, j, _ in KOLOM])
    for r in baris:
        w.writerow([_sel_csv(x) for x in _baris_ekspor(r, r["dasar"])])
    return out.getvalue().encode("utf-8-sig")


def _sel_csv(v) -> str:
    if isinstance(v, Decimal):
        return format(v, "f").replace(".", ",")
    return bulk._sel(v)


def ekspor_xlsx(baris: list) -> bytes:
    """Lembar Harga (kolom = KOLOM; judul dikenali impor) + lembar Petunjuk. Daftar kosong = header + petunjuk (templat impor)."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Harga"
    ws.append([j for _, j, _ in KOLOM])
    for r in baris:
        ws.append([bulk._sel(x) if isinstance(x, str) else (float(x) if isinstance(x, Decimal) else x) for x in _baris_ekspor(r, r["dasar"])])
    for k in ("H", "I"):
        for c in ws[k][1:]:
            c.number_format = "yyyy-mm-dd"
    pt = wb.create_sheet("Petunjuk")
    for t in ("Satu baris = satu harga. Baris dicocokkan dengan Kode barang + Satuan + Mulai qty + Mulai berlaku.",
              "Cocok = harga diubah; belum ada = ditambah. Impor tidak pernah menghapus baris.",
              "Satuan kosong = satuan dasar barang. Mulai qty kosong = 1. Mulai berlaku / Berakhir kosong = tanpa tanggal.",
              "Tanggal: YYYY-MM-DD atau DD/MM/YYYY. Kolom Nama hanya untuk dibaca; tidak dipakai saat impor."):
        pt.append([t])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------------------------------------ rencana (pratinjau == tulis)
def _norm_baris(no: int, b: dict, mulai_bawaan: Optional[date]) -> tuple:
    """Baris berkas -> (nilai terurai | None, blok[]). Hanya bentuk/angka/tanggal; barang & satuan diperiksa di rencana (butuh DB)."""
    bl = []
    kode = str(b.get("kode") or "").strip()
    if not kode:
        bl.append(blok("IMPOR_KODE_WAJIB", "Kode barang wajib diisi.", "kode"))
    harga = qty = None
    try:
        harga = IB.angka(b.get("harga"))
        if harga is None:
            bl.append(blok("IMPOR_HARGA_WAJIB", "Harga wajib diisi.", "harga"))
        elif harga < 0:
            bl.append(blok("IMPOR_HARGA_TIDAK_SAH", "Harga tidak boleh negatif.", "harga"))
        elif harga != harga.quantize(Decimal("0.01")):
            bl.append(blok("IMPOR_HARGA_TIDAK_SAH", "Harga maksimal 2 angka di belakang koma.", "harga"))
    except ValueError:
        bl.append(blok("IMPOR_HARGA_TIDAK_SAH", f"Harga '{b.get('harga')}' tidak terbaca sebagai angka.", "harga"))
    try:
        qty = IB.angka(b.get("min_qty")) if b.get("min_qty") not in (None, "") else Decimal("1")
        if qty is None or qty <= 0:
            bl.append(blok("IMPOR_QTY_TIDAK_SAH", "Mulai qty harus lebih dari 0.", "min_qty"))
    except ValueError:
        bl.append(blok("IMPOR_QTY_TIDAK_SAH", f"Mulai qty '{b.get('min_qty')}' tidak terbaca sebagai angka.", "min_qty"))
    minimum = None
    try:
        minimum = IB.angka(b.get("min_harga"))
        if minimum is not None and (minimum < 0 or minimum != minimum.quantize(Decimal("0.01"))):
            bl.append(blok("IMPOR_HARGA_TIDAK_SAH", "Harga minimum tidak boleh negatif dan maksimal 2 angka di belakang koma.", "min_harga"))
    except ValueError:
        bl.append(blok("IMPOR_HARGA_TIDAK_SAH", f"Harga minimum '{b.get('min_harga')}' tidak terbaca sebagai angka.", "min_harga"))
    diskon = None
    try:
        diskon = IB.angka(b.get("diskon"))
        if diskon is not None and (diskon < 0 or diskon > 100 or diskon != diskon.quantize(Decimal("0.01"))):
            bl.append(blok("IMPOR_DISKON_TIDAK_SAH", "Diskon % harus antara 0 dan 100, maksimal 2 angka di belakang koma.", "diskon"))
    except ValueError:
        bl.append(blok("IMPOR_DISKON_TIDAK_SAH", f"Diskon '{b.get('diskon')}' tidak terbaca sebagai angka.", "diskon"))
    mulai = akhir = None
    ada_akhir = "akhir" in b
    for medan, nama in (("mulai", "Mulai berlaku"), ("akhir", "Berakhir")):
        try:
            v = _tgl(b.get(medan), medan)
        except ValueError:
            bl.append(blok("IMPOR_TANGGAL_TIDAK_SAH", f"{nama} '{b.get(medan)}' bukan tanggal yang sah (YYYY-MM-DD atau DD/MM/YYYY).", medan))
            continue
        if medan == "mulai":
            mulai = v
        else:
            akhir = v
    if mulai is None:
        mulai = mulai_bawaan
    if mulai and akhir and akhir < mulai:
        bl.append(blok("PERIODE_TIDAK_SAH", "Tanggal berakhir tidak boleh sebelum tanggal mulai.", "akhir"))
    if bl:
        return None, bl
    return {"kode": kode, "satuan": " ".join(str(b.get("satuan") or "").split()) or None, "harga": harga, "min_qty": qty,
            "mulai": mulai, "akhir": akhir, "ada_akhir": ada_akhir,
            "min_harga": minimum, "ada_min": "min_harga" in b, "diskon": diskon, "ada_diskon": "diskon" in b}, []


async def _cari_barang(conn, tid: str, kode: str):
    """Kode barang (item_code ATAU sku, tanpa beda huruf) -> baris barang | 'ambigu' | None."""
    r = await conn.fetch(
        """SELECT id, nama_produk, COALESCE(NULLIF(base_unit, ''), satuan) AS dasar, item_code FROM products
           WHERE tenant_id = $1 AND (lower(item_code) = lower($2) OR lower(sku) = lower($2))""", tid, kode)
    return None if not r else ("ambigu" if len({x["id"] for x in r}) > 1 else r[0])


def _kunci(pid, satuan_kanon: str, qty, mulai):
    return (str(pid), satuan_kanon, Decimal(str(qty)).normalize(), mulai)


async def rencana(conn, tid: str, pl_id, baris: list, mulai_bawaan: Optional[date] = None, kunci: bool = False) -> dict:
    """Penentu BERSAMA pratinjau dan tulis (kunci=True: baris daftar dikunci FOR UPDATE). Hanya membaca. ->
    {ok, blocks, ringkas, baris[{no, ok, blocks, aksi, rencana, peringatan}], teks}. Blok batch: kosong / terlalu banyak."""
    if not isinstance(baris, list) or not baris:
        return _hasil([blok("IMPOR_KOSONG", "Tidak ada baris untuk diimpor.")], [])
    if len(baris) > MAKS_BARIS:
        return _hasil([blok("IMPOR_TERLALU_BANYAK", f"Maksimal {MAKS_BARIS} baris per impor; ini {len(baris)} baris.")], [])
    from ..routers import price_lists as PL  # impor lokal: PL mengimpor modul ini lewat rute
    from ..schemas.price_lists import PriceListItemCreate
    from ..utils.tanggal_tenant import tanggal_dokumen
    hari_ini = await tanggal_dokumen(conn, tid)
    ada = [dict(r) for r in await conn.fetch(
        f"""SELECT pli.id, pli.item_id, pli.unit, pli.min_quantity, pli.price, pli.start_date, pli.end_date, pli.discount_percent, pli.min_price,
                   pli.is_default_unit, pli.item_code
            FROM price_list_items pli WHERE pli.price_list_id = $1{' FOR UPDATE' if kunci else ''}""", pl_id)]
    cache_brg: dict = {}
    alias = await SD._alias(conn, tid)  # satu kali baca untuk seluruh batch
    hasil, terlihat = [], {}
    for i, b in enumerate(baris):
        no = int(b.get("_no") or 0) or i + 2  # baris 1 = judul
        nilai, bl = _norm_baris(no, b, mulai_bawaan)
        row = {"no": no, "ok": False, "blocks": bl, "aksi": None, "rencana": None, "peringatan": []}
        hasil.append(row)
        if bl:
            continue
        kode = nilai["kode"]
        if kode.lower() not in cache_brg:
            cache_brg[kode.lower()] = await _cari_barang(conn, tid, kode)
        brg = cache_brg[kode.lower()]
        if brg is None:
            bl.append(blok("IMPOR_BARANG_TAK_ADA", f"Barang dengan kode '{kode}' tidak ditemukan di usaha ini.", "kode"))
            continue
        if brg == "ambigu":
            bl.append(blok("IMPOR_BARANG_AMBIGU", f"Kode '{kode}' cocok ke lebih dari satu barang.", "kode"))
            continue
        pid = brg["id"]
        satuan = nilai["satuan"]
        kanon = SD.kanon(satuan or brg["dasar"], alias)
        k = _kunci(pid, kanon, nilai["min_qty"], nilai["mulai"])
        if k in terlihat:
            bl.append(blok("HARGA_GANDA", f"Baris ini sama dengan baris {terlihat[k]} di berkas (barang, satuan, mulai qty, dan mulai berlaku sama).", "kode"))
            continue
        terlihat[k] = no
        cocok = next((a for a in ada if str(a["item_id"]) == str(pid) and SD.kanon(a["unit"] or brg["dasar"], alias) == kanon
                      and Decimal(str(a["min_quantity"])).normalize() == k[2] and a["start_date"] == nilai["mulai"]), None)
        unit_tulis = None if kanon == SD.kanon(brg["dasar"], alias) else satuan
        akhir = nilai["akhir"] if (nilai["ada_akhir"] or not cocok) else cocok["end_date"]
        diskon_lama = float(cocok["discount_percent"]) if cocok and cocok["discount_percent"] is not None else None
        diskon = (float(nilai["diskon"]) if nilai["diskon"] is not None else None) if (nilai["ada_diskon"] or not cocok) else diskon_lama   # kolom Diskon % tak ada = diskon lama utuh
        minimum = nilai["min_harga"] if (nilai["ada_min"] or not cocok) else cocok["min_price"]   # kolom Harga minimum tak ada = lantai lama utuh; ada tapi kosong = dikosongkan
        try:
            if cocok:
                item = PriceListItemCreate(item_id=str(pid), item_code=cocok["item_code"], unit=cocok["unit"], price=nilai["harga"],
                                           min_quantity=Decimal(str(cocok["min_quantity"])), is_default_unit=bool(cocok["is_default_unit"]),
                                           discount_percent=diskon,
                                           start_date=cocok["start_date"], end_date=akhir, min_price=minimum)
            else:
                item = PriceListItemCreate(item_id=str(pid), item_code=kode, unit=unit_tulis, price=nilai["harga"], min_quantity=nilai["min_qty"],
                                           discount_percent=diskon, start_date=nilai["mulai"], end_date=akhir, min_price=minimum)
            await PL.siapkan_baris_harga(conn, tid, pl_id, item, cocok["id"] if cocok else None)
        except GalatBerkode as e:
            bl.append(blok(e.code, e.detail, e.field))
            continue
        except HTTPException as e:
            d = e.detail
            bl.append(blok(d.get("code", "IMPOR_BARIS_DITOLAK") if isinstance(d, dict) else "IMPOR_BARIS_DITOLAK",
                           d.get("message") if isinstance(d, dict) else str(d)))
            continue
        except ValueError as e:  # pydantic ValidationError (harga/diskon di luar batas skema)
            bl.append(blok("IMPOR_BARIS_TIDAK_SAH", "Isi baris tidak sah (harga/qty di luar batas)."))
            continue
        dari = Decimal(str(cocok["price"])) if cocok else None
        sama = bool(cocok) and dari == nilai["harga"] and cocok["end_date"] == akhir and diskon == diskon_lama and (
            (Decimal(str(cocok["min_price"])) if cocok["min_price"] is not None else None) == minimum)
        row["aksi"] = "tetap" if sama else ("ubah" if cocok else "tambah")
        row["_tulis"] = {"item": item, "line_id": cocok["id"] if cocok else None}
        row["rencana"] = {"barang": brg["nama_produk"], "item_code": kode, "unit_text": HD.unit_text(satuan, brg["dasar"]),
                          "min_quantity": float(nilai["min_qty"]), "dari": dari, "ke": nilai["harga"],
                          "harga_minimum": minimum, "diskon_persen": diskon,
                          "selisih": (nilai["harga"] - dari) if dari is not None else None,
                          "berlaku_mulai": nilai["mulai"].isoformat() if nilai["mulai"] else None,
                          "berakhir": akhir.isoformat() if akhir else None}
        row["ok"] = True
        ke_c = {"line_id": None, "item_id": str(pid), "kode": kode, "nama": brg["nama_produk"], "satuan": HD.unit_text(satuan, brg["dasar"]), "min_quantity": float(nilai["min_qty"]),
                "harga": float(nilai["harga"]), "min_price": float(minimum) if minimum is not None else None, "diskon_persen": diskon,
                "mulai": nilai["mulai"].isoformat() if nilai["mulai"] else None, "berakhir": akhir.isoformat() if akhir else None}
        dari_c = ({**ke_c, "line_id": str(cocok["id"]), "harga": float(cocok["price"]), "min_price": float(cocok["min_price"]) if cocok["min_price"] is not None else None,
                   "diskon_persen": diskon_lama, "berakhir": cocok["end_date"].isoformat() if cocok["end_date"] else None} if cocok else None)
        row["_riwayat"] = {"dari": dari_c, "ke": ke_c}
        if row["aksi"] != "tetap":
            if nilai["harga"] == 0:
                row["peringatan"].append(_per(KODE_NOL, f"Harga {brg['nama_produk']} akan jadi Rp 0. Konfirmasi dulu bila memang disengaja."))
            if nilai["mulai"] and nilai["mulai"] > hari_ini:
                row["peringatan"].append(_per(KODE_NANTI, f"Harga baru {brg['nama_produk']} berlaku mulai {HD.tgl_text(nilai['mulai'])}; harga lama tetap berlaku sampai tanggal itu."))
            b_hpp = [{"unit": unit_tulis, "price": nilai["harga"]}]
            await HM._hpp_baris(conn, tid, pid, b_hpp)
            if b_hpp[0].get("hpp") is not None and nilai["harga"] > 0 and nilai["harga"] < b_hpp[0]["hpp"]:
                row["peringatan"].append(_per(KODE_BAWAH_HPP, f"Harga {brg['nama_produk']} (Rp {nilai['harga']:f}) di bawah HPP (Rp {b_hpp[0]['hpp'].quantize(Decimal('0.01')):f}). Konfirmasi dulu bila memang disengaja."))
    return _hasil([], hasil)


def _hasil(bloks: list, baris: list) -> dict:
    ditolak = [r for r in baris if r["blocks"]]
    n = {a: sum(1 for r in baris if r["aksi"] == a) for a in ("tambah", "ubah", "tetap")}
    per = sum(len(r["peringatan"]) for r in baris)
    ringkas = {"dibaca": len(baris), "tambah": n["tambah"], "ubah": n["ubah"], "tetap": n["tetap"], "ditolak": len(ditolak), "peringatan": per}
    teks = (f"{len(baris)} baris dibaca: {n['tambah']} ditambah, {n['ubah']} diubah, {n['tetap']} tak berubah"
            + (f", {len(ditolak)} ditolak." if ditolak else "."))
    return {"ok": not bloks and not ditolak, "blocks": bloks, "ringkas": ringkas, "baris": [
        {k: v for k, v in r.items() if k not in ("_tulis", "_riwayat")} for r in baris], "teks": teks, "_baris_tulis": baris}


def bersih(r: dict) -> dict:
    """Buang tanda internal (_baris_tulis) sebelum respons."""
    return {k: v for k, v in r.items() if k != "_baris_tulis"}


async def tulis(conn, ctx: dict, pl_id, baris: list, mulai_bawaan: Optional[date] = None) -> dict:
    """Tulis ATOMIK di transaksi PEMANGGIL: validasi ULANG di bawah kunci; satu blok = seluruh batch ditolak (422 IMPOR_DITOLAK, nol tulis)."""
    from ..routers import price_lists as PL
    tid = ctx["tenant_id"]
    r = await rencana(conn, tid, pl_id, baris, mulai_bawaan, kunci=True)
    if not r["ok"]:
        raise GalatImporDitolak(bersih(r))
    n = {"tambah": 0, "ubah": 0, "tetap": 0}
    for row in r["_baris_tulis"]:
        n[row["aksi"]] += 1
        if row["aksi"] != "tetap":
            await PL.tulis_baris_harga(conn, tid, pl_id, row["_tulis"]["item"], row["_tulis"]["line_id"])
    await bulk.catat_audit(conn, ctx, "impor_harga", "price_list", [pl_id], {"tambah": n["tambah"], "ubah": n["ubah"], "tetap": n["tetap"]})
    perubahan = [x["_riwayat"] for x in r["_baris_tulis"] if x["aksi"] != "tetap"]
    await HR.catat(conn, ctx, pl_id, None, "IMPOR", f"Impor Excel: {n['tambah']} ditambah, {n['ubah']} diubah, {n['tetap']} tak berubah", perubahan,
                   jumlah=n["tambah"] + n["ubah"])
    return {"tambah": n["tambah"], "ubah": n["ubah"], "tetap": n["tetap"], "jumlah": n["tambah"] + n["ubah"] + n["tetap"]}


class GalatImporDitolak(HTTPException):
    """422 IMPOR_DITOLAK: badan = rencana (blok per baris), bentuk sama dengan Impor Barang."""
    def __init__(self, rencana_: dict):
        super().__init__(status_code=422, detail={"code": "IMPOR_DITOLAK", "message": "Impor ditolak: periksa baris yang bermasalah.", "rencana": rencana_})
