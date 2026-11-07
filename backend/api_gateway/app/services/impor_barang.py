"""Impor barang massal (BUG-021, 5 Nov 2026; putusan MASTER Q1-Q5): Excel DAN tambah barang 1 baris di chatbox = SATU jalur.

Pola Odoo/Xero/QBO: pratinjau per baris (READ ONLY) -> tulis ATOMIK (semua atau tidak). Saldo awal = jurnal, jadi impor separuh
(lalu ulang) = buku ganda. Tiap baris memakai penentu YANG SAMA dengan POST /items (CreateItemRequest + periksa_badan_item +
_siapkan_buat_barang + tulis_barang_baru); di sini hanya aturan BATCH: nama/kode/barcode ganda di dalam berkas, kode otomatis
berurutan, satuan & kategori baru dikumpulkan (dibuat sekali saat tulis), harga selalu tampil "Rp X / <satuan>".
Tulis memvalidasi ULANG di bawah kunci; satu blok = seluruh batch ditolak dengan alasan per baris (nol tulis).
Tahap 1: BUAT saja (nama/kode yang sudah ada = blok); satu satuan besar per baris; stok awal dalam satuan dasar.
Tahap 2 (DITUNDA): perbarui-by-kode, daftar harga, Penyesuaian stok untuk usaha yang sudah bertransaksi (Law 28)."""
import io
import re
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import HTTPException

from . import satuan_master as SM
from . import teks_galat as tg

MAKS_BARIS = 200
# (kunci baris, judul kolom templat, wajib)
KOLOM = [
    ("nama", "Nama barang", True), ("kode", "Kode", False), ("tipe", "Tipe (barang/non stok/jasa)", False),
    ("satuan_dasar", "Satuan dasar", True), ("harga_jual", "Harga jual / satuan dasar", False),
    ("harga_beli", "Harga beli / satuan dasar", False), ("satuan_besar", "Satuan besar", False),
    ("isi", "Isi satuan besar (dalam satuan dasar)", False), ("harga_jual_besar", "Harga jual / satuan besar", False),
    ("harga_beli_besar", "Harga beli / satuan besar", False), ("kategori", "Kategori", False), ("barcode", "Barcode", False),
    ("stok_awal", "Stok awal (satuan dasar)", False), ("harga_pokok_awal", "Harga pokok awal / satuan dasar", False),
]
_TIPE = {"barang": "goods", "goods": "goods", "stok": "goods", "non stok": "non_inventory", "non_stok": "non_inventory",
         "nonstok": "non_inventory", "non_inventory": "non_inventory", "jasa": "service", "service": "service"}
_KALI = {"rb": 1000, "ribu": 1000, "k": 1000, "jt": 1000000, "juta": 1000000}


def blok(code: str, message: str, field: Optional[str] = None) -> dict:
    return {"code": code, "message": message, **({"field": field} if field else {})}


def angka(teks) -> Optional[Decimal]:
    """"243rb" / "1,5jt" / "35.000" / "0,5" / "0.5" / 35000 -> Decimal; kosong -> None; tak terbaca -> ValueError.
    Gaya Indonesia: koma = desimal; titik = ribuan bila tiap kelompok sesudahnya tepat 3 angka ("35.000"), selain itu desimal ("0.5")."""
    if teks is None or (isinstance(teks, str) and not teks.strip()):
        return None
    if isinstance(teks, (int, float, Decimal)):
        return Decimal(str(teks))
    s = str(teks).strip().lower().replace("rp", "").replace(" ", "")
    m = re.fullmatch(r"(\d+(?:[.,]\d+)*)(rb|ribu|k|jt|juta)?", s)
    if not m:
        raise ValueError(teks)
    num, akhiran = m.group(1), m.group(2)
    if "," in num:
        num = num.replace(".", "").replace(",", ".")
    elif "." in num and all(len(x) == 3 for x in num.split(".")[1:]) and not akhiran:
        num = num.replace(".", "")
    try:
        d = Decimal(num)
    except InvalidOperation:
        raise ValueError(teks)
    return d * _KALI[akhiran] if akhiran else d


# ---------------------------------------------------------------- tafsir 1 baris chatbox (deterministik, tanpa LLM: Law 0/10)
_KK = r"(?:jual|beli|stok|kode|kategori|per|isi)"
_KATA = r"[^\W\d_][\w.-]*"
_POLA = [
    ("konv", re.compile(rf"(?P<besar>{_KATA})\s*(?:isi|=|@)\s*(?P<isi>\d+(?:[.,]\d+)*)(?:\s+(?!{_KK}\b)(?P<dasar>{_KATA}))?", re.I)),
    ("harga", re.compile(rf"\b(?P<arah>jual|beli)\s*(?:rp\.?\s*)?(?P<n>\d+(?:[.,]\d+)*\s*(?:rb|ribu|jt|juta|k)?)\b(?:\s*(?:/|per\s)\s*(?P<unit>{_KATA}))?", re.I)),
    ("stok", re.compile(rf"\bstok\s*(?P<n>\d+(?:[.,]\d+)*)(?:\s+(?!{_KK}\b)(?P<unit>{_KATA}))?", re.I)),
    ("kode", re.compile(r"\bkode\s+(?P<kode>[\w./-]+)", re.I)),
    ("kategori", re.compile(r"\bkategori\s+(?P<kat>.+?)(?=\s*,|\s+(?:jual|beli|stok|kode)\b|$)", re.I)),
    ("satuan", re.compile(rf"\bsatuan\s+(?P<unit>{_KATA})", re.I)),
    ("tipe", re.compile(r"\btipe\s+(?P<tipe>barang|jasa|non[\s_]?stok)\b", re.I)),
]


def _satuan_ke(b: dict, unit: str) -> Optional[str]:
    """'/karton' -> 'besar' | 'dasar'; satuan dasar belum disebut -> satuan ini jadi dasar; satuan lain -> galat (None)."""
    k = SM.kunci(unit)
    if k == SM.kunci(b.get("satuan_besar") or ""):
        return "besar"
    if "satuan_dasar" not in b or k == SM.kunci(b["satuan_dasar"]):
        b["satuan_dasar"] = b.get("satuan_dasar", unit)
        return "dasar"
    b.setdefault("_galat", []).append(f"satuan \"{unit}\" bukan {b['satuan_dasar']} atau {b.get('satuan_besar')}")
    return None


_KATA_MEDAN = re.compile(r"\b(?:jual|beli|stok|kode|kategori|satuan|tipe)\b.*$", re.I | re.S)


def _nama_tanpa_sisa(depan: str, galat: list) -> str:
    """Bagian sebelum medan pertama = NAMA + satu pemisah. Sisanya tak pernah ditelan ke nama diam-diam (temuan FRONTEND 6 Nov:
    "rusak ,,, jual abc" -> nama itu, nol galat): bagian lain sesudah koma/titik koma, kata medan yang nilainya tak terbaca
    ("jual abc", "beli"), dan tanda baca beruntun -> galat per baris (blok TAK_TERBACA di ke_badan)."""
    bagian = re.split(r"[,;]", depan)
    ekor = [x.strip() for x in bagian[1:]]
    if sum(1 for x in ekor if not x) > 1:
        galat.append(f"tanda baca berlebih \"{''.join(re.findall(r'[,;]', depan))}\" — pisahkan bagian dengan satu koma")
    nama = bagian[0].strip()
    m = _KATA_MEDAN.search(nama)
    if m:
        ekor.insert(0, m.group(0).strip())
        nama = nama[:m.start()].strip()
    sisa = [x for x in ekor if x]
    if sisa:
        galat.append(f"tak terbaca: \"{', '.join(sisa)}\" — tulis mis. jual 35rb, beli 31rb, stok 10")
    return nama


def tafsir_baris(teks: str) -> dict:
    """SATU baris -> baris impor ternormalisasi + 'tak_terbaca' (sisa teks yang tak dikenali; tak pernah dibuang diam).
    Harga berlaku untuk satuan yang disebut TEPAT SEBELUMNYA ("karton isi 6 jual 243rb" = per karton); "/karton" selalu
    menang; tanpa satuan sebelumnya = per satuan dasar (putusan MASTER Q2)."""
    s, pos, temuan = teks.strip(), 0, []
    while pos < len(s):
        cocok = [(p.search(s, pos), j) for j, p in _POLA]
        cocok = [(m, j) for m, j in cocok if m]
        if not cocok:
            break
        m, j = min(cocok, key=lambda x: x[0].start())
        temuan.append((j, m))
        pos = m.end()
    awal = temuan[0][1].start() if temuan else len(s)
    galat_nama = []
    b = {"nama": _nama_tanpa_sisa(s[:awal], galat_nama), "teks": s}
    if galat_nama:
        b["_galat"] = galat_nama
    sisa = [s[(temuan[i - 1][1].end() if i else awal):m.start()] for i, (_, m) in enumerate(temuan)]
    sisa.append(s[temuan[-1][1].end():] if temuan else "")
    terakhir = None  # satuan yang disebut terakhir: "besar" | "dasar"
    for j, m in temuan:
        g = m.groupdict()
        if j == "konv":
            b["satuan_besar"], b["isi"] = g["besar"], g["isi"]
            terakhir = "besar"
            if g["dasar"]:
                b["satuan_dasar"], terakhir = g["dasar"], "dasar"
        elif j == "harga":
            ke = _satuan_ke(b, g["unit"]) if g["unit"] else (terakhir or "dasar")
            if ke:
                b[f"harga_{g['arah'].lower()}" + ("_besar" if ke == "besar" else "")] = g["n"].replace(" ", "")
        elif j == "stok":
            b["stok_awal"] = g["n"]
            b["_stok_besar"] = bool(g["unit"]) and _satuan_ke(b, g["unit"]) == "besar"
        elif j == "kode":
            b["kode"] = g["kode"]
        elif j == "kategori":
            b["kategori"] = g["kat"].strip()
        elif j == "satuan":
            b["satuan_dasar"], terakhir = g["unit"], "dasar"
        elif j == "tipe":
            b["tipe"] = g["tipe"]
    tak = " ".join(x.strip(" ,;") for x in sisa if x.strip(" ,;"))
    if tak:
        b.setdefault("_galat", []).append(f"Tak terbaca: \"{tak}\"")
    if b.pop("_stok_besar", False):  # "stok 10 karton" -> satuan dasar (stok awal selalu dalam satuan dasar)
        try:
            b["stok_awal"] = str(angka(b["stok_awal"]) * angka(b["isi"]))
        except (ValueError, TypeError):
            b.setdefault("_galat", []).append("stok dalam satuan besar butuh isi yang terbaca")
    b.setdefault("satuan_dasar", "Pcs")
    return b


def tafsir_teks(teks: str) -> list:
    return [tafsir_baris(x) for x in (teks or "").splitlines() if x.strip()]


# ---------------------------------------------------------------- Excel
def templat_xlsx(satuan: list) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Barang"
    ws.append([j for _, j, _ in KOLOM])
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.append(["Minyak Goreng 2L", "", "barang", "Botol", 35000, 31000, "Karton", 12, 410000, 365000, "Minyak", "", "", ""])
    p = wb.create_sheet("Petunjuk")
    for t in ["Satu baris = satu barang baru. Wajib: Nama barang, Satuan dasar.",
              "Harga ditulis per satuan sesuai kolomnya; harga satuan besar tidak dihitung otomatis.",
              "Satuan atau kategori yang belum ada akan dibuat saat impor (terlihat di pratinjau).",
              "Stok awal hanya untuk usaha yang belum bertransaksi; selain itu pakai Penyesuaian stok.",
              f"Maksimal {MAKS_BARIS} baris per impor. Hapus baris contoh sebelum mengunggah.",
              "", "Satuan yang sudah ada:"] + [u["abbreviation"] for u in satuan]:
        p.append([t])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def baca_xlsx(isi: bytes) -> list:
    """Lembar pertama -> baris impor. Kolom dicocokkan dengan JUDUL (urutan bebas); baris kosong dilewati."""
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(isi), read_only=True, data_only=True)
    except Exception:
        raise HTTPException(status_code=400, detail={"code": "BERKAS_TIDAK_TERBACA", "message": "Berkas bukan Excel (.xlsx) yang sah."})
    rows = list(wb.worksheets[0].iter_rows(values_only=True))
    if not rows:
        return []
    peta = {j.lower(): k for k, j, _ in KOLOM}
    kepala = [peta.get(str(h or "").strip().lower()) for h in rows[0]]
    if "nama" not in kepala or "satuan_dasar" not in kepala:
        raise HTTPException(status_code=400, detail={"code": "KOLOM_TIDAK_DIKENAL",
                                                     "message": "Judul kolom tak cocok dengan templat. Unduh templat lalu isi."})
    hasil = []
    for r in rows[1:]:
        b = {k: v for k, v in zip(kepala, r) if k and v is not None and str(v).strip() != ""}
        if b:
            hasil.append({k: (v.strip() if isinstance(v, str) else v) for k, v in b.items()})
    return hasil


# ---------------------------------------------------------------- baris -> badan POST /items
def ke_badan(b: dict, no: Optional[int] = None) -> tuple:
    """-> (payload CreateItemRequest, blok[], harga[{arah, teks}]). Harga = persis kolomnya (tanpa turunan).
    no = nomor baris pratinjau (sama dengan baris[].no = nomor tabel FE) -> jalan keluar TAK_TERBACA "hapus <no>"."""
    keluar = f". Ketik ulang baris ini atau hapus {no}." if no else ""
    bl, harga = [blok("TAK_TERBACA", g + keluar) for g in b.get("_galat", [])], []
    def uang(k, label):
        try:
            v = angka(b.get(k))
        except ValueError:
            bl.append(blok("ANGKA_TIDAK_SAH", f"{label} \"{b.get(k)}\" tak terbaca — tulis mis. 35000 atau 35rb.", k))
            return None
        if v is not None and v != v.quantize(Decimal("0.01")):
            bl.append(blok("ANGKA_TIDAK_SAH", f"{label} maksimal 2 desimal.", k))
        return v
    tipe_teks = str(b.get("tipe") or "barang").strip().lower()
    tipe = _TIPE.get(tipe_teks)
    if not tipe:
        bl.append(blok("TIPE_TIDAK_DIKENAL", f"Tipe \"{b.get('tipe')}\" tak dikenal — pakai barang, non stok, atau jasa.", "tipe"))
        tipe = "goods"
    p = {"name": str(b.get("nama") or "").strip(), "item_type": tipe, "base_unit": str(b.get("satuan_dasar") or "").strip(),
         "track_inventory": tipe == "goods"}
    for k, kunci_p in (("kode", "item_code"), ("kategori", "kategori"), ("barcode", "barcode")):
        if b.get(k) not in (None, ""):
            p[kunci_p] = str(b[k]).strip()
    for k, kunci_p, arah in (("harga_jual", "sales_price", "jual"), ("harga_beli", "purchase_price", "beli")):
        v = uang(k, f"Harga {arah}")
        if v is not None:
            p[kunci_p] = float(v)
            harga.append({"arah": arah, "teks": f"{tg.rp(v)} / {p['base_unit']}"})
    if b.get("satuan_besar"):
        try:
            isi = angka(b.get("isi"))
        except ValueError:
            isi = None
        if not isi:
            bl.append(blok("ISI_WAJIB", f"Isi {b['satuan_besar']} (berapa {p['base_unit']}) wajib diisi.", "isi"))
        k = {"conversion_unit": str(b["satuan_besar"]).strip(), "conversion_factor": str(isi or 1)}
        for kb, kunci_p, arah in (("harga_jual_besar", "sales_price", "jual"), ("harga_beli_besar", "purchase_price", "beli")):
            v = uang(kb, f"Harga {arah} {b['satuan_besar']}")
            if v is not None:
                k[kunci_p] = float(v)
                harga.append({"arah": arah, "teks": f"{tg.rp(v)} / {k['conversion_unit']}"})
        if tipe != "goods":
            bl.append(blok("KONVERSI_HANYA_BARANG", "Satuan besar hanya untuk tipe barang (stok).", "satuan_besar"))
        p["conversions"] = [k]
    elif b.get("harga_jual_besar") or b.get("harga_beli_besar"):
        bl.append(blok("SATUAN_BESAR_WAJIB", "Harga satuan besar diisi tanpa satuan besar.", "satuan_besar"))
    for k, kunci_p, label in (("stok_awal", "opening_stock", "Stok awal"), ("harga_pokok_awal", "opening_stock_rate", "Harga pokok awal")):
        try:
            v = angka(b.get(k))
        except ValueError:
            bl.append(blok("ANGKA_TIDAK_SAH", f"{label} \"{b.get(k)}\" tak terbaca.", k))
            continue
        if v is not None:
            p[kunci_p] = float(v)
    if p.get("opening_stock") and tipe != "goods":
        bl.append(blok("STOK_HANYA_BARANG", "Stok awal hanya untuk tipe barang (stok).", "stok_awal"))
    return p, bl, harga


def _galat_ke_blok(e: HTTPException) -> dict:
    from ..routers.customers import blok_dari_galat
    return blok_dari_galat(e)


def _nomor(kode: str) -> tuple:
    m = re.fullmatch(r"(.*-)(\d+)", kode or "")
    return (m.group(1), int(m.group(2)), len(m.group(2))) if m else (None, None, None)


# ---------------------------------------------------------------- pratinjau (READ ONLY) == validasi ulang saat tulis
async def rencana(conn, ctx: dict, baris: list) -> dict:
    """Baca saja. -> {ok, blocks (tingkat batch), ringkas{barang, satuan_baru[], kategori_baru[]}, baris:[{no, ok, blocks, rencana, payload}]}."""
    from pydantic import ValidationError
    from ..routers import items as R
    from ..routers.customers import blok_validasi
    from ..schemas.items import CreateItemRequest
    tid = ctx["tenant_id"]
    if not baris:
        return {"ok": False, "blocks": [blok("TANPA_BARIS", "Tidak ada baris barang.")], "ringkas": None, "baris": []}
    if len(baris) > MAKS_BARIS:
        return {"ok": False, "blocks": [blok("TERLALU_BANYAK", f"Maksimal {MAKS_BARIS} baris per impor (ada {len(baris)}).")],
                "ringkas": None, "baris": []}
    satuan = {SM.kunci(u["abbreviation"]): u["abbreviation"] for u in await SM.daftar(conn, tid)}
    kat = {SM.kunci(r["name"]): r["name"] for r in await conn.fetch("SELECT name FROM item_categories WHERE tenant_id = $1", tid)}
    satuan_baru, kategori_baru, pertama, maks, out = {}, {}, {}, {}, []
    for no, b in enumerate(baris, 1):
        p, bl, harga = ke_badan(b, no)
        for kunci_p, wadah in (("base_unit", p), *(("conversion_unit", c) for c in p.get("conversions", []))):
            u = wadah.get(kunci_p)
            if u:  # satuan yang ada = ejaan tenant; baru = dibuat sekali
                wadah[kunci_p] = satuan.get(SM.kunci(u)) or satuan_baru.setdefault(SM.kunci(u), u)
        if p.get("kategori"):
            p["kategori"] = kat.get(SM.kunci(p["kategori"])) or kategori_baru.setdefault(SM.kunci(p["kategori"]), p["kategori"])
        for medan, label in (("name", "Nama"), ("barcode", "Barcode")):
            k = SM.kunci(p.get(medan) or "")
            if k:
                lain = pertama.setdefault((medan, k), no)
                if lain != no:
                    bl.append(blok("GANDA_DI_BERKAS", f"{label} \"{p[medan]}\" sama dengan baris {lain}.", medan))
        kode = None
        try:
            body = CreateItemRequest.model_validate(p)
        except ValidationError as e:
            bl += blok_validasi(e)
            body = None
        if body is not None:
            try:
                R.tanpa_stok_bila_bukan_barang(body)
                R.periksa_badan_item(body, "POST /items/impor")
                kode = (await R._siapkan_buat_barang(conn, tid, ctx, body))[0]
            except HTTPException as e:
                bl.append(_galat_ke_blok(e))
        kode = kode or p.get("item_code")
        awal, n, lebar = _nomor(kode)
        if awal and not p.get("item_code"):  # simulasi kode otomatis: tulis berurutan = MAX(DB, kode lebih awal di berkas) + 1
            n = max(n, maks.get(awal, 0) + 1)
            kode = f"{awal}{n:0{lebar}d}"
        if awal:
            maks[awal] = max(maks.get(awal, 0), n)
        if kode:
            lain = pertama.setdefault(("item_code", SM.kunci(kode)), no)
            if lain != no:
                bl.append(blok("GANDA_DI_BERKAS", f"Kode \"{kode}\" sama dengan baris {lain}.", "item_code"))
        out.append({"no": no, "ok": not bl, "blocks": bl, "payload": p,
                    "rencana": {"name": p["name"], "item_code": kode, "item_type": p["item_type"], "base_unit": p["base_unit"],
                                "konversi": [f"1 {c['conversion_unit']} = {c['conversion_factor']} {p['base_unit']}" for c in p.get("conversions", [])],
                                "harga": harga, "kategori": p.get("kategori"),
                                "stok_awal": (f"{p['opening_stock']:g} {p['base_unit']}" if p.get("opening_stock") else None)}})
    ok = all(x["ok"] for x in out)
    return {"ok": ok, "blocks": [] if ok else [blok("BARIS_BERGALAT", f"{sum(not x['ok'] for x in out)} baris perlu diperbaiki.")],
            "ringkas": {"barang": len(out), "satuan_baru": sorted(satuan_baru.values()), "kategori_baru": sorted(kategori_baru.values())},
            "baris": out}


def _tolak(r: dict, status: int = 422):
    raise HTTPException(status_code=status, detail={"code": "IMPOR_DITOLAK", "message": "Impor ditolak; tidak ada barang yang disimpan.",
                                                    "blocks": r["blocks"], "baris": [x for x in r["baris"] if not x["ok"]]})


async def tulis(conn, ctx: dict, baris: list, user_id, user_name) -> dict:
    """WAJIB di transaksi pemanggil (Law 23). Kunci tenant (Law 13) -> validasi ULANG (penentu sama dengan pratinjau) -> satu blok =
    tolak semua -> satuan & kategori baru -> tulis_barang_baru per baris (saldo awal = saldo_awal_stok, savepoint di tx ini).
    Galat di tengah (balapan) -> HTTPException -> pemanggil rollback seluruh batch."""
    from ..routers import items as R
    from ..schemas.items import CreateItemRequest
    if not conn.is_in_transaction():
        raise RuntimeError("impor_barang.tulis wajib di dalam transaksi pemanggil (Law 23)")
    tid = ctx["tenant_id"]
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"ITEM_IMPORT:{tid}")
    r = await rencana(conn, ctx, baris)
    if not r["ok"]:
        _tolak(r)
    for u in r["ringkas"]["satuan_baru"]:
        await SM.buat(conn, tid, u)
    for k in r["ringkas"]["kategori_baru"]:
        await conn.execute("INSERT INTO item_categories (tenant_id, name) VALUES ($1, $2) ON CONFLICT (tenant_id, lower(name)) DO NOTHING",
                           tid, k)
    hasil = []
    for x in r["baris"]:
        body = CreateItemRequest.model_validate(x["payload"])
        R.tanpa_stok_bila_bukan_barang(body)
        try:
            item_id = await R.tulis_barang_baru(conn, ctx, body, user_id, user_name)
        except HTTPException as e:
            x["ok"], x["blocks"] = False, [_galat_ke_blok(e)]
            _tolak(r, 409)
        kode = await conn.fetchval("SELECT item_code FROM products WHERE id = $1 AND tenant_id = $2", item_id, tid)
        hasil.append({"no": x["no"], "id": str(item_id), "item_code": kode, "name": body.name})
    return {"jumlah": len(hasil), "barang": hasil, "satuan_baru": r["ringkas"]["satuan_baru"],
            "kategori_baru": r["ringkas"]["kategori_baru"]}
