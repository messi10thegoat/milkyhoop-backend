"""K-5 (17 Nov 2026; putusan MASTER): IMPOR + EKSPOR master Kategori barang -- SATU pola berkas dengan Impor Barang: baca multipart sekali -> baris JSON -> pratinjau READ ONLY -> tulis
ATOMIK + X-Idempotency-Key (payload pratinjau == tulis; penentu SAMA: rencana() dipakai kedua-duanya, tulis memvalidasi ULANG di bawah kunci). Kolom ekspor = kolom templat impor (aman rumus).

Tahap 1: BUAT saja (nama yang sudah ada = blok; tak ada perbarui-by-nama). Satu tingkat: kolom Induk menunjuk kategori tingkat atas yang SUDAH ada (aktif) atau baris tingkat atas di berkas yang sama;
induk dibuat lebih dulu. Maksimal MAKS_BARIS baris. Audit per kategori (ITEM_CATEGORY_CREATED, impor=true)."""
import io
import re
from typing import Optional

from fastapi import HTTPException

from . import kategori_aksi as KA
from .bulk import buat_csv, _sel

MAKS_BARIS = 200
KOLOM = [("nama", "Nama kategori", True), ("induk", "Kategori induk", False), ("deskripsi", "Deskripsi", False)]
SINONIM = {"nama": ["nama kategori", "nama", "kategori", "category", "name"],
           "induk": ["kategori induk", "induk", "parent", "kategori utama", "parent category"],
           "deskripsi": ["deskripsi", "keterangan", "description", "catatan"]}


def _norm(h) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(h or "").lower()).strip()


def _peta() -> dict:
    p = {}
    for k, jud, _ in KOLOM:
        for s in SINONIM[k] + [jud]:
            p.setdefault(_norm(s), set()).add(k)
    return p


def blok(code: str, message: str, field: Optional[str] = None) -> dict:
    return {"code": code, "message": message, **({"field": field} if field else {})}


def templat_xlsx() -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Kategori"
    ws.append([j for _, j, _ in KOLOM])
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.append(["Minuman", "", "Semua minuman kemasan"])
    ws.append(["Teh", "Minuman", ""])
    p = wb.create_sheet("Petunjuk")
    for t in ["Satu baris = satu kategori baru. Wajib: Nama kategori.",
              "Kategori induk (opsional) = kategori tingkat atas yang sudah ada, atau baris tingkat atas di berkas ini. Sub kategori hanya satu tingkat.",
              "Nama kategori yang sudah ada ditolak (huruf besar/kecil tidak dibedakan).",
              f"Maksimal {MAKS_BARIS} baris per impor. Hapus baris contoh sebelum mengunggah."]:
        p.append([t])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def baca_xlsx(isi: bytes) -> dict:
    """Lembar pertama -> {baris, pemetaan, tak_dipakai, peringatan} (judul bebas lewat SINONIM; kolom tak dikenal DISEBUT; baris kosong dilewati). Nol DB."""
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(isi), read_only=True, data_only=True)
    except Exception:
        raise HTTPException(status_code=400, detail={"code": "BERKAS_TIDAK_TERBACA", "message": "Berkas bukan Excel (.xlsx) yang sah."})
    rows = list(wb.worksheets[0].iter_rows(values_only=True))
    if not rows:
        return {"baris": [], "pemetaan": [], "tak_dipakai": [], "peringatan": []}
    peta, label = _peta(), {k: j for k, j, _ in KOLOM}
    kepala, pemetaan, tak_dipakai, oleh = [], [], [], {}
    for h in rows[0]:
        judul = str(h).strip() if h is not None else ""
        cocok = peta.get(_norm(judul), set()) if judul else set()
        medan = next(iter(cocok), None)
        if medan and medan in oleh:
            raise HTTPException(status_code=400, detail={"code": "KOLOM_GANDA", "message": f"Kolom '{oleh[medan]}' dan '{judul}' sama-sama berarti {label[medan]}. Hapus salah satu."})
        if medan:
            oleh[medan] = judul
            pemetaan.append({"judul_berkas": judul, "medan": medan, "label": label[medan]})
        elif judul:
            tak_dipakai.append(judul)
        kepala.append(medan)
    if "nama" not in oleh:
        raise HTTPException(status_code=400, detail={"code": "KOLOM_TIDAK_DIKENAL", "message": f"Kolom {label['nama'].lower()} tidak ditemukan. Judul yang dikenali: {', '.join(x.capitalize() for x in SINONIM['nama'][:4])}."})
    hasil = []
    for r in rows[1:]:
        b = {k: v for k, v in zip(kepala, r) if k and v is not None and str(v).strip() != ""}
        if b:
            hasil.append({k: (v.strip() if isinstance(v, str) else v) for k, v in b.items()})
    return {"baris": hasil, "pemetaan": pemetaan, "tak_dipakai": tak_dipakai, "peringatan": [f"Kolom '{j}' tidak dipakai" for j in tak_dipakai]}


SQL_EKSPOR = ("SELECT c.name AS nama, p.name AS induk, c.description AS deskripsi FROM item_categories c LEFT JOIN item_categories p ON p.id = c.parent_id AND p.tenant_id = c.tenant_id "
              "WHERE c.tenant_id = $1 ORDER BY (c.parent_id IS NOT NULL), lower(COALESCE(p.name, c.name)), (c.parent_id IS NOT NULL), lower(c.name), c.id")


async def baris_ekspor(conn, tid: str) -> list:
    """Induk lebih dulu, anak sesudah induknya (impor ulang di usaha lain berurutan benar). Kolom = KOLOM."""
    return [[r["nama"], r["induk"] or "", r["deskripsi"] or ""] for r in await conn.fetch(SQL_EKSPOR, tid)]


def ekspor_csv(baris: list) -> bytes:
    return buat_csv([j for _, j, _ in KOLOM], baris)


def ekspor_xlsx(baris: list) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Kategori"
    ws.append([j for _, j, _ in KOLOM])
    for c in ws[1]:
        c.font = Font(bold=True)
    for b in baris:
        ws.append([_sel(x) if isinstance(x, str) else x for x in b])   # teks berawalan = + - @ diberi awalan ' (bukan rumus)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _kunci(nama) -> str:
    return " ".join(str(nama or "").split()).lower()


async def rencana(conn, ctx: dict, baris: list) -> dict:
    """Baca saja. -> {ok, blocks (batch), ringkas{kategori, sub, induk_baru[]}, baris:[{no, ok, blocks, rencana, payload}]}."""
    tid = ctx["tenant_id"]
    if not baris:
        return {"ok": False, "blocks": [blok("TANPA_BARIS", "Tidak ada baris kategori.")], "ringkas": None, "baris": []}
    if len(baris) > MAKS_BARIS:
        return {"ok": False, "blocks": [blok("TERLALU_BANYAK", f"Maksimal {MAKS_BARIS} baris per impor (ada {len(baris)}).")], "ringkas": None, "baris": []}
    ada = {_kunci(r["name"]): r for r in await conn.fetch("SELECT id, name, parent_id, is_active FROM item_categories WHERE tenant_id = $1", tid)}
    # nama baris berkas yang TINGKAT ATAS (kandidat induk dalam berkas)
    atas_berkas = {_kunci(b.get("nama")): b for b in baris if not str(b.get("induk") or "").strip() and _kunci(b.get("nama"))}
    pertama, out = {}, []
    for no, b in enumerate(baris, 1):
        bl, nama, induk = [], " ".join(str(b.get("nama") or "").split()), " ".join(str(b.get("induk") or "").split())
        _, galat = KA._nama_sah(nama)
        if galat:
            bl.append(blok(galat["code"], galat["message"], "nama"))
        k = _kunci(nama)
        if k and k in pertama:
            bl.append(blok("NAMA_GANDA_DI_BERKAS", f"Kategori '{nama}' muncul juga di baris {pertama[k]}.", "nama"))
        elif k:
            pertama[k] = no
        if k and k in ada:
            bl.append(blok("KATEGORI_SUDAH_ADA", f"Kategori '{nama}' sudah ada (huruf besar/kecil tidak dibedakan).", "nama"))
        if induk:
            ki = _kunci(induk)
            if ki == k:
                bl.append(blok("KATEGORI_INDUK_DIRI_SENDIRI", "Kategori tidak bisa menjadi sub dari dirinya sendiri.", "induk"))
            elif ki in ada:
                pa = ada[ki]
                if pa["parent_id"]:
                    bl.append(blok("KATEGORI_INDUK_BUKAN_TINGKAT_ATAS", f"'{pa['name']}' sudah menjadi sub kategori; sub kategori hanya satu tingkat.", "induk"))
                elif not pa["is_active"]:
                    bl.append(blok("KATEGORI_INDUK_NONAKTIF", f"Kategori induk '{pa['name']}' nonaktif; aktifkan dulu.", "induk"))
            elif ki in atas_berkas:
                pass   # induk = baris tingkat atas di berkas ini (dibuat lebih dulu)
            else:
                bl.append(blok("KATEGORI_INDUK_TAK_ADA", f"Kategori induk '{induk}' tidak ada di usaha ini maupun di berkas (induk harus tingkat atas).", "induk"))
        out.append({"no": no, "ok": not bl, "blocks": bl, "rencana": {"nama": nama, "induk": induk or None, "deskripsi": str(b.get("deskripsi") or "").strip() or None, "tingkat": "anak" if induk else "induk"},
                    "payload": {"nama": nama, "induk": induk or None, "deskripsi": str(b.get("deskripsi") or "").strip() or None}})
    sub = sum(1 for x in out if x["rencana"]["induk"])
    return {"ok": all(x["ok"] for x in out), "blocks": [], "ringkas": {"kategori": len(out), "sub": sub, "induk": len(out) - sub}, "baris": out}


def _tolak(r: dict, status: int = 422):
    raise HTTPException(status_code=status, detail={"code": "IMPOR_DITOLAK", "message": "Impor ditolak; tidak ada kategori yang disimpan.", "blocks": r["blocks"],
                                                    "baris": [x for x in r["baris"] if not x["ok"]]})


async def tulis(conn, ctx: dict, baris: list, user_id) -> dict:
    """WAJIB di transaksi pemanggil (Law 23). Kunci tenant (sama dgn penulis kategori lain) -> validasi ULANG (penentu SAMA) -> satu blok = tolak semua -> induk dulu, lalu sub."""
    if not conn.is_in_transaction():
        raise RuntimeError("impor_kategori.tulis wajib di dalam transaksi pemanggil (Law 23)")
    tid = ctx["tenant_id"]
    await KA._kunci(conn, tid)
    r = await rencana(conn, ctx, baris)
    if not r["ok"]:
        _tolak(r)
    id_by = {_kunci(x["name"]): x["id"] for x in await conn.fetch("SELECT id, name FROM item_categories WHERE tenant_id = $1", tid)}
    hasil = []
    for tingkat in ("induk", "anak"):
        for x in r["baris"]:
            p = x["payload"]
            if x["rencana"]["tingkat"] != tingkat:
                continue
            parent = id_by.get(_kunci(p["induk"])) if p["induk"] else None
            row = await conn.fetchrow("INSERT INTO item_categories (tenant_id, name, parent_id, description) VALUES ($1, $2, $3, $4) RETURNING id, name", tid, p["nama"], parent, p["deskripsi"])
            id_by[_kunci(row["name"])] = row["id"]
            await KA.catat_audit(conn, tid, user_id, "buat", row["id"], row["name"], {"nama": row["name"], "induk": p["induk"], "impor": True})
            hasil.append({"no": x["no"], "id": str(row["id"]), "name": row["name"], "induk": p["induk"]})
    hasil.sort(key=lambda h: h["no"])
    return {"jumlah": len(hasil), "kategori": hasil}
