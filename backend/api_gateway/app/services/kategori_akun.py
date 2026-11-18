"""BUG-282 (18 Nov 2026; putusan pemilik LANGSUNG 17 Nov 21:15, V505): AKUN BAWAAN per KATEGORI barang (penjualan / persediaan / HPP / pembelian), gaya Accurate.

SATU penentu: rencana_akun (murni; pratinjau == penulis) dan salin_ke_barang (dipakai _siapkan_buat_barang = pratinjau Buat Barang == POST /items == impor).
Aturan pemilik: akun kategori DISALIN ke barang BARU saat dibuat; isian eksplisit di barang menang; mengubah akun kategori / kategori barang TAK menyentuh barang dan jurnal lama.
Tipe akun (diukur di CoA nyata 18 Nov: ASSET, COGS, EXPENSE, REVENUE, OTHER_INCOME ...): penjualan = REVENUE/OTHER_INCOME; persediaan = ASSET bernama persediaan/inventory
(sama dgn GET /items/accounts/inventory); HPP = COGS saja; pembelian = EXPENSE/COGS atau persediaan (ASSET persediaan). Akun harus milik tenant, aktif, bukan header."""
import contextvars
import json
import uuid
from typing import Optional

from . import label_rencana as LR
from .barang_aksi import _blok, _hasil

JENIS = ("penjualan", "persediaan", "hpp", "pembelian")
KOLOM = {"penjualan": "sales_account_id", "persediaan": "inventory_account_id", "hpp": "cogs_account_id", "pembelian": "purchase_account_id"}
LABEL = {"penjualan": "Penjualan", "persediaan": "Persediaan", "hpp": "HPP", "pembelian": "Pembelian"}
TIPE = {"penjualan": ("REVENUE", "OTHER_INCOME"), "hpp": ("COGS",), "pembelian": ("EXPENSE", "COGS")}   # hpp = HANYA COGS (ikut Accurate; diukur prod 18 Nov: semua akun HPP aktif bertipe COGS)
SELECT_AKUN = ", ".join(
    f"(SELECT jsonb_build_object('id', a.id, 'kode', a.account_code, 'nama', a.name, 'aktif', a.is_active)::text FROM chart_of_accounts a WHERE a.id = c.{KOLOM[j]}) AS akun_{j}"
    for j in JENIS)
# Hasil salin TERAKHIR di konteks permintaan ini (untuk rencana pratinjau Buat Barang; _siapkan_buat_barang tetap mengembalikan 2 nilai)
_TERAKHIR = contextvars.ContextVar("kategori_akun_terakhir", default=None)
KATA_PERSEDIAAN = ("persediaan", "inventory")


def _persediaan(a: dict) -> bool:
    return a.get("account_type") == "ASSET" and any(k in (a.get("name") or "").lower() for k in KATA_PERSEDIAAN)


def tipe_sah(jenis: str, a: dict) -> bool:
    if jenis == "persediaan":
        return _persediaan(a)
    if jenis == "pembelian" and _persediaan(a):
        return True
    return a.get("account_type") in TIPE[jenis]


def akun_sah(jenis: str, a: dict) -> bool:
    """SATU penentu akun yang boleh dipilih untuk <jenis>: aktif + BUKAN header + tipe sah. Dipakai penulis kategori, salin ke barang, dan SEMUA daftar pilihan
    (GET /items/accounts/<jenis> + GET /items/categories/akun-pilihan)."""
    return bool(a.get("is_active", True)) and not a.get("is_header") and tipe_sah(jenis, a)


async def daftar_akun(conn, tid: str, jenis: str) -> list:
    """Daftar pilihan akun <jenis> milik tenant (urut kode), disaring oleh akun_sah -> [{id, code, name, account_type, text}]. Hanya DAFTAR; akun yang sudah tersimpan tak disentuh."""
    rows = await conn.fetch("SELECT id, account_code, name, account_type, is_active, is_header FROM chart_of_accounts WHERE tenant_id = $1 AND is_active = true AND NOT COALESCE(is_header, false) ORDER BY account_code ASC", tid)
    return [{"id": str(r["id"]), "code": r["account_code"], "name": r["name"], "account_type": r["account_type"], "text": f"{r['account_code']} {r['name']}"}
            for r in map(dict, rows) if akun_sah(jenis, r)]


def _teks_akun(a: Optional[dict]) -> Optional[str]:
    return f"{a['kode']} {a['nama']}" if a else None


def bentuk(r) -> dict:
    """Dari baris SQL (kolom akun_<jenis> = json teks atau None) -> {jenis: {id, kode, nama, aktif, text} | None}."""
    hasil = {}
    for j in JENIS:
        mentah = r.get(f"akun_{j}") if hasattr(r, "get") else None
        a = json.loads(mentah) if isinstance(mentah, str) else mentah
        hasil[j] = {"id": str(a["id"]), "kode": a["kode"], "nama": a["nama"], "aktif": bool(a["aktif"]), "text": _teks_akun(a)} if a else None
    return hasil


def akun_text(akun: dict) -> str:
    isi = [f"{LABEL[j]} {akun[j]['text']}" for j in JENIS if akun.get(j)]
    return " · ".join(isi) if isi else "Tanpa akun bawaan"


def rencana_akun(k: Optional[dict], diminta: Optional[dict], akun: dict) -> dict:
    """k = baris ukur_kategori (memuat akun_<jenis>); diminta = {jenis: id | None(kosongkan)} (jenis yang tak disebut tak berubah); akun = {id_str: baris CoA tenant ini
    (id, account_code, name, account_type, is_active, is_header)}. -> {ok, blocks[{code,message,field}], peringatan[], rencana}."""
    if not k:
        return _hasil([_blok("KATEGORI_TAK_ADA", "Kategori tidak ditemukan.")], None)
    diminta = diminta or {}
    asing = [j for j in diminta if j not in JENIS]
    if asing:
        return _hasil([_blok("AKUN_JENIS_TAK_DIKENAL", f"Jenis akun tak dikenal: {asing[0]}. Pilihan: {', '.join(JENIS)}.", field=asing[0])], None)
    if not diminta:
        return _hasil([_blok("AKUN_TAK_DIISI", "Tidak ada akun yang diisi.")], None)
    sekarang = bentuk(k)
    blok, ubah = [], []
    for j in JENIS:
        if j not in diminta:
            continue
        baru = diminta[j]
        lama_id = sekarang[j]["id"] if sekarang[j] else None
        if baru in (None, ""):
            if lama_id:
                ubah.append({"jenis": j, "dari": sekarang[j]["text"], "ke": None, "ke_id": None, **LR.baris(f"akun_{j}", f"Akun {LABEL[j].lower()}", sekarang[j]["text"], None)})
            continue
        a = akun.get(str(baru))
        if not a:
            blok.append(_blok("AKUN_TAK_DITEMUKAN", f"Akun {LABEL[j].lower()} tidak ditemukan.", field=j))
        elif not a["is_active"]:
            blok.append(_blok("AKUN_NONAKTIF", f"Akun '{a['account_code']} {a['name']}' nonaktif.", field=j))
        elif a.get("is_header"):
            blok.append(_blok("AKUN_HEADER", f"Akun '{a['account_code']} {a['name']}' adalah akun induk (header); pilih akun detailnya.", field=j))
        elif not tipe_sah(j, a):
            blok.append(_blok("AKUN_TIPE_SALAH", f"Akun '{a['account_code']} {a['name']}' bukan akun {LABEL[j].lower()} yang sah.", field=j))
        elif str(a["id"]) != lama_id:
            ubah.append({"jenis": j, "dari": sekarang[j]["text"] if sekarang[j] else None, "ke": f"{a['account_code']} {a['name']}", "ke_id": str(a["id"]),
                          **LR.baris(f"akun_{j}", f"Akun {LABEL[j].lower()}", sekarang[j]["text"] if sekarang[j] else None, f"{a['account_code']} {a['name']}")})
    if blok:
        return _hasil(blok, None)
    if not ubah:
        return _hasil([_blok("TIDAK_ADA_PERUBAHAN", "Akun kategori sudah seperti itu.")], None)
    return _hasil([], {"id": str(k["id"]), "kategori": k["name"], "perubahan": ubah},
                  [f"Hanya barang yang dibuat sesudah ini mengikuti akun baru kategori '{k['name']}'; barang dan jurnal yang sudah ada tidak berubah."])


async def ukur_akun(conn, tid: str, ids) -> dict:
    """CoA tenant ini untuk id yang diminta (id tak sah/UUID rusak diabaikan -> 'tak ditemukan')."""
    bersih = []
    for i in ids:
        try:
            bersih.append(str(uuid.UUID(str(i))))
        except (ValueError, TypeError):
            continue
    if not bersih:
        return {}
    rows = await conn.fetch("SELECT id, account_code, name, account_type, is_active, is_header FROM chart_of_accounts WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tid, bersih)
    return {str(r["id"]): dict(r) for r in rows}


async def tulis_akun(conn, tid: str, k: dict, rencana: dict) -> None:
    """Pemanggil: transaksi + kunci kategori + audit."""
    for p in rencana["perubahan"]:
        kolom = KOLOM[p["jenis"]]   # dari kamus tetap, bukan masukan pengguna
        await conn.execute(f"UPDATE item_categories SET {kolom} = $3::uuid, updated_at = NOW() WHERE tenant_id = $1 AND id = $2", tid, str(k["id"]), p["ke_id"])


async def salin_ke_barang(conn, tid: str, body) -> list:
    """Penentu SALIN (aturan pemilik): akun barang yang KOSONG diisi dari kategori barang (isian eksplisit menang). inventory/HPP hanya barang ber-stok (goods + track_inventory).
    Akun kategori yang kini nonaktif/header diabaikan (barang tetap lolos jalur bawaan _auto_default). -> daftar jenis yang disalin."""
    _TERAKHIR.set(None)
    nama = (getattr(body, "kategori", None) or "").strip()
    if not nama:
        return []
    r = await conn.fetchrow(
        "SELECT sales_account_id, inventory_account_id, cogs_account_id, purchase_account_id FROM item_categories WHERE tenant_id = $1 AND lower(name) = lower($2)", tid, nama)
    if not r:
        return []
    stok = getattr(body, "item_type", None) == "goods" and bool(getattr(body, "track_inventory", False))
    kandidat = {j: r[KOLOM[j]] for j in JENIS if r[KOLOM[j]] and not getattr(body, KOLOM[j], None) and (stok or j not in ("persediaan", "hpp"))}
    if not kandidat:
        return []
    sah = await ukur_akun(conn, tid, kandidat.values())
    disalin = []
    for j, aid in kandidat.items():
        a = sah.get(str(aid))
        if a and akun_sah(j, a):
            setattr(body, KOLOM[j], str(aid))
            disalin.append(j)
    if disalin:
        _TERAKHIR.set({"kategori": nama, "jenis": disalin})
    return disalin


def terakhir() -> Optional[dict]:
    """{kategori, jenis[]} dari salin_ke_barang terakhir di permintaan ini; None = tak ada yang disalin."""
    return _TERAKHIR.get()
