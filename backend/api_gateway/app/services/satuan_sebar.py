"""G3-b (BE, 17 Nov 2026): SATU mesin penyebaran TEKS satuan master (ganti nama + gabung) -- pratinjau dan penulis memakai kode yang sama.

Satuan hanya tersimpan sebagai TEKS bebas (PETA-be-satuan-teks-tersimpan). Mesin ini menulis ulang teks di kolom MASTER saja: barang (satuan, base_unit), konversi (base_unit,
conversion_unit), baris daftar harga (unit), BOM/resep (output_unit/unit/yield_unit). Dokumen historis + ledger TIDAK diubah: nama lama disimpan sebagai ALIAS (V504,
product_unit_aliases) -> kamus satuan_dasar tetap mengenalinya. Pemetaan teks: bentuk SINGKATAN lama (+alias) -> singkatan baru; bentuk NAMA lama -> nama baru; selain itu tetap.

Konflik (blok BERNAMA per barang/daftar; apa pun yang memblok membatalkan seluruh aksi): dua konversi ke satuan yang sama dengan FAKTOR beda, konversi yang menjadi sama dengan satuan
dasarnya berfaktor != 1, dua baris harga (daftar+barang+satuan+min qty+mulai) berharga beda. Duplikat IDENTIK (faktor/harga sama) dibuang otomatis (peringatan menyebut jumlahnya)."""
from decimal import Decimal
from typing import Optional

from .satuan_dasar import _NORM_SQL, normal

# (kelompok, tabel, kolom, dari-gabung (tabel alias, syarat) | None, syarat pemilik tenant)
KOLOM = (
    ("barang", "products", ("satuan", "base_unit"), None, "t.tenant_id = $1"),          # satu pernyataan: hitungan = barang (bukan kolom)
    ("konversi", "unit_conversions", ("base_unit", "conversion_unit"), None, "t.tenant_id = $1"),     # satu pernyataan: tak ada keadaan antara yang bentrok indeks unik
    ("harga", "price_list_items", "unit", ("price_lists p", "p.id = t.price_list_id AND p.tenant_id = $1"), None),
    ("bom", "bom_components", "unit", ("bill_of_materials b", "b.id = t.bom_id AND b.tenant_id = $1"), None),
    ("bom", "bill_of_materials", "output_unit", None, "t.tenant_id = $1"),
    ("bom", "recipe_ingredients", "unit", ("recipes r", "r.id = t.recipe_id AND r.tenant_id = $1"), None),
    ("bom", "recipes", "yield_unit", None, "t.tenant_id = $1"),
)
MAKS_NAMA_BLOK = 5


def peta_teks(bentuk_singk, bentuk_nama, teks_singk, teks_nama):
    """-> fungsi teks -> teks baru (None tetap None). Singkatan menang atas nama bila satu bentuk ada di keduanya."""
    def f(t):
        if t is None:
            return None
        n = normal(t)
        return teks_singk if n in bentuk_singk else teks_nama if n in bentuk_nama else t
    return f


def _kondisi(kolom):
    """kolom = nama atau tuple nama. -> (daftar SET 'kol = CASE...', syarat cocok). $2 = bentuk singkatan, $3 = teks nama baru, $4 = teks singkatan baru, $5 = bentuk nama."""
    kols = (kolom,) if isinstance(kolom, str) else tuple(kolom)
    sets, cocok, beda = [], [], []
    for k in kols:
        n = _NORM_SQL.format(k=f"t.{k}")
        baru = f"CASE WHEN {n} = ANY($2::text[]) THEN $4::text ELSE $3::text END"
        sets.append(f"{k} = CASE WHEN {n} = ANY($2::text[]) THEN $4::text WHEN {n} = ANY($5::text[]) THEN $3::text ELSE t.{k} END")
        cocok.append(f"{n} = ANY($2::text[]) OR {n} = ANY($5::text[])")
        beda.append(f"t.{k} IS DISTINCT FROM (CASE WHEN {n} = ANY($2::text[]) THEN $4::text WHEN {n} = ANY($5::text[]) THEN $3::text ELSE t.{k} END)")
    return ", ".join(sets), f"({' OR '.join(cocok)}) AND ({' OR '.join(beda)})"


def sql_ubah(tabel, kol, gabung, pemilik) -> str:
    sets, cocok = _kondisi(kol)
    if gabung:
        return f"UPDATE {tabel} t SET {sets} FROM {gabung[0]} WHERE {gabung[1]} AND {cocok}"
    return f"UPDATE {tabel} t SET {sets} WHERE {pemilik} AND {cocok}"


def sql_hitung(tabel, kol, gabung, pemilik) -> str:
    sets, cocok = _kondisi(kol)
    if gabung:
        return f"SELECT count(*) FROM {tabel} t JOIN {gabung[0]} ON {gabung[1]} WHERE {cocok}"
    return f"SELECT count(*) FROM {tabel} t WHERE {pemilik} AND {cocok}"


# ------------------------------------------------------------------ analisis konflik (fungsi murni: dites tanpa DB)
def _faktor(v) -> Decimal:
    return Decimal(str(v))


def analisis_konversi(rows: list, f, nama_barang: dict) -> dict:
    """rows = konversi SEMUA baris barang-barang terdampak [{id, product_id, base_unit, conversion_unit, conversion_factor, aktif}]; f = peta_teks.
    -> {hapus: [id], blocks: [(code, pesan)], dibuang: n}. Hanya baris yang BERUBAH atau bertabrakan dengan yang berubah yang dinilai."""
    per = {}
    for r in rows:
        per.setdefault(r["product_id"], []).append({**r, "nb": f(r["base_unit"]), "nc": f(r["conversion_unit"])})
    hapus, blocks = [], []
    for pid, items in per.items():
        if not any(x["nb"] != x["base_unit"] or x["nc"] != x["conversion_unit"] for x in items):
            continue
        nama = nama_barang.get(pid, "barang")
        n_blok, n_hapus = len(blocks), len(hapus)
        sisa = []
        for x in items:                                                      # (1) konversi aktif yang menjadi satuan dasarnya sendiri
            berubah = x["nb"] != x["base_unit"] or x["nc"] != x["conversion_unit"]
            if berubah and x["aktif"] and normal(x["nb"]) == normal(x["nc"]):
                if _faktor(x["conversion_factor"]) == 1:
                    hapus.append(x["id"])
                    continue
                blocks.append(("SATUAN_FAKTOR_BEDA", f"Barang {nama}: konversi {x['conversion_unit']} akan menjadi sama dengan satuan dasarnya ({x['nb']}) tetapi faktornya "
                                                     f"{_faktor(x['conversion_factor']).normalize():f} (bukan 1). Hapus konversi itu dulu."))
                continue
            sisa.append(x)
        grup = {}
        for x in sisa:
            if x["aktif"]:
                grup.setdefault((normal(x["nb"]), normal(x["nc"])), []).append(x)
        for g in grup.values():                                              # (2) dua konversi aktif ke satuan yang sama
            if len(g) < 2:
                continue
            if len({_faktor(x["conversion_factor"]) for x in g}) > 1:
                fs = " dan ".join(f"{_faktor(x['conversion_factor']).normalize():f}" for x in g)
                blocks.append(("SATUAN_FAKTOR_BEDA", f"Barang {nama}: dua konversi ke satuan yang sama ({g[0]['nc']}) berfaktor beda ({fs}). Samakan atau hapus salah satunya dulu."))
                continue
            utama = next((x for x in g if x["nb"] == x["base_unit"] and x["nc"] == x["conversion_unit"]), g[0])    # yang tak berubah dipertahankan
            hapus.extend(x["id"] for x in g if x is not utama)
        sisa = [x for x in sisa if x["id"] not in hapus]
        kunci = {}
        for x in sisa:                                                       # (3) tabrakan indeks unik tepat (baris nonaktif ikut dihitung): buang yang nonaktif
            kunci.setdefault((x["nb"], x["nc"]), []).append(x)
        for g in kunci.values():
            if len(g) > 1:
                urut = sorted(g, key=lambda x: (not x["aktif"], x["nb"] != x["base_unit"] or x["nc"] != x["conversion_unit"]))
                hapus.extend(x["id"] for x in urut[1:])
        if len(blocks) > n_blok:                                             # barang ini diblok -> seluruh aksi batal; jangan umumkan penghapusan darinya
            del hapus[n_hapus:]
    return {"hapus": sorted(set(hapus)), "blocks": blocks, "dibuang": len(set(hapus))}


def analisis_harga(rows: list, f, nama_barang: dict, nama_daftar: dict) -> dict:
    """rows = baris daftar harga SEMUA (daftar, barang) terdampak [{id, price_list_id, item_id, unit, price, min_quantity, start_date}]. Kunci unik DB:
    (daftar, barang, satuan ternormalisasi, min qty, mulai). -> {hapus: [id], blocks}."""
    grup = {}
    for r in rows:
        nu = f(r["unit"])
        grup.setdefault((r["price_list_id"], r["item_id"], normal(nu), str(r["min_quantity"]), str(r["start_date"])), []).append({**r, "nu": nu, "berubah": nu != r["unit"]})
    hapus, blocks = [], []
    for k, g in grup.items():
        if len(g) < 2 or not any(x["berubah"] for x in g):
            continue
        if len({Decimal(str(x["price"])) for x in g}) > 1:
            blocks.append(("SATUAN_HARGA_BENTROK", f"Daftar harga {nama_daftar.get(k[0], 'daftar')}, barang {nama_barang.get(k[1], 'barang')}: dua baris harga satuan {g[0]['nu'] or '(dasar)'} "
                                                   f"akan bertabrakan dengan harga berbeda. Hapus salah satunya dulu."))
            continue
        utama = next((x for x in g if not x["berubah"]), g[0])
        hapus.extend(x["id"] for x in g if x is not utama)
    return {"hapus": sorted(set(hapus)), "blocks": blocks}


def _ringkas_blok(blocks: list) -> list:
    """Satu kode, pesan pertama + hitungan sisanya (daftar panjang tak membanjiri pratinjau)."""
    out = []
    for kode, pesan in blocks[:MAKS_NAMA_BLOK]:
        out.append((kode, pesan))
    if len(blocks) > MAKS_NAMA_BLOK:
        out.append((blocks[0][0], f"... dan {len(blocks) - MAKS_NAMA_BLOK} konflik lain."))
    return out


# ------------------------------------------------------------------ pembaca + penulis
async def alias_unit(conn, tid: str, unit_id: str) -> list:
    return [r["alias"] for r in await conn.fetch("SELECT alias FROM product_unit_aliases WHERE tenant_id = $1 AND unit_id::text = $2", tid, unit_id)]


async def siapkan(conn, tid: str, bentuk_singk: set, bentuk_nama: set, teks_singk: str, teks_nama: str) -> dict:
    """Hitung dampak + konflik (READ ONLY). -> {peta: {...}, jumlah: {barang, konversi, harga, bom}, hapus_konversi, hapus_harga, blocks[(kode, pesan)]}."""
    bentuk_nama = set(bentuk_nama) - set(bentuk_singk)
    argumen = (tid, sorted(bentuk_singk), teks_nama, teks_singk, sorted(bentuk_nama))
    jumlah = {"barang": 0, "konversi": 0, "harga": 0, "bom": 0}
    for kel, tabel, kol, gabung, pemilik in KOLOM:
        n = await conn.fetchval(sql_hitung(tabel, kol, gabung, pemilik), *argumen) or 0
        jumlah[kel] += int(n)
    semua = sorted(set(bentuk_singk) | bentuk_nama)
    f = peta_teks(set(bentuk_singk), bentuk_nama, teks_singk, teks_nama)
    n_base, n_conv = _NORM_SQL.format(k="base_unit"), _NORM_SQL.format(k="conversion_unit")
    rows = [dict(r, product_id=str(r["product_id"]), id=str(r["id"])) for r in await conn.fetch(
        f"""SELECT id, product_id, base_unit, conversion_unit, conversion_factor, COALESCE(is_active, true) AS aktif FROM unit_conversions
            WHERE tenant_id = $1 AND product_id IN (SELECT product_id FROM unit_conversions WHERE tenant_id = $1 AND product_id IS NOT NULL
                                                    AND ({n_base} = ANY($2::text[]) OR {n_conv} = ANY($2::text[])))""", tid, semua)] if jumlah["konversi"] else []
    nb = {str(r["id"]): r["nama_produk"] for r in await conn.fetch("SELECT id, nama_produk FROM products WHERE tenant_id = $1 AND id::text = ANY($2::text[])",
                                                                    tid, sorted({r["product_id"] for r in rows}))} if rows else {}
    ak = analisis_konversi(rows, f, nb)
    n_unit = _NORM_SQL.format(k="t.unit")
    hrows = [dict(r, id=str(r["id"]), price_list_id=str(r["price_list_id"]), item_id=str(r["item_id"])) for r in await conn.fetch(
        f"""SELECT t.id, t.price_list_id, t.item_id, t.unit, t.price, t.min_quantity, t.start_date FROM price_list_items t JOIN price_lists p ON p.id = t.price_list_id AND p.tenant_id = $1
            WHERE (t.price_list_id, t.item_id) IN (SELECT x.price_list_id, x.item_id FROM price_list_items x JOIN price_lists q ON q.id = x.price_list_id AND q.tenant_id = $1
                                                   WHERE {_NORM_SQL.format(k='x.unit')} = ANY($2::text[]))""", tid, semua)] if jumlah["harga"] else []
    nbh = {str(r["id"]): r["nama_produk"] for r in await conn.fetch("SELECT id, nama_produk FROM products WHERE tenant_id = $1 AND id::text = ANY($2::text[])",
                                                                     tid, sorted({r["item_id"] for r in hrows}))} if hrows else {}
    nd = {str(r["id"]): r["name"] for r in await conn.fetch("SELECT id, name FROM price_lists WHERE tenant_id = $1 AND id::text = ANY($2::text[])",
                                                             tid, sorted({r["price_list_id"] for r in hrows}))} if hrows else {}
    ah = analisis_harga(hrows, f, nbh, nd)
    return {"peta": {"bentuk_singkatan": sorted(bentuk_singk), "bentuk_nama": sorted(bentuk_nama), "teks_singkatan": teks_singk, "teks_nama": teks_nama},
            "jumlah": jumlah, "hapus_konversi": ak["hapus"], "hapus_harga": ah["hapus"], "blocks": _ringkas_blok(ak["blocks"] + ah["blocks"])}


async def terapkan(conn, tid: str, h: dict) -> dict:
    """Tulis hasil siapkan (di transaksi pemanggil): buang duplikat identik, tulis ulang teks master. -> jumlah baris yang berubah per kelompok."""
    if h["hapus_konversi"]:
        await conn.execute("DELETE FROM unit_conversions WHERE tenant_id = $1 AND id::text = ANY($2::text[])", tid, h["hapus_konversi"])
    if h["hapus_harga"]:
        await conn.execute("DELETE FROM price_list_items t USING price_lists p WHERE p.id = t.price_list_id AND p.tenant_id = $1 AND t.id::text = ANY($2::text[])", tid, h["hapus_harga"])
    p = h["peta"]
    argumen = (tid, p["bentuk_singkatan"], p["teks_nama"], p["teks_singkatan"], p["bentuk_nama"])
    for kel, tabel, kol, gabung, pemilik in KOLOM:
        await conn.execute(sql_ubah(tabel, kol, gabung, pemilik), *argumen)
    return h["jumlah"]


async def catat_alias(conn, tid: str, unit_id: str, teks: list) -> None:
    """Nama lama -> alias satuan unit_id (idempoten; alias yang sudah menunjuk satuan lain dialihkan ke unit_id)."""
    for t in dict.fromkeys(" ".join(str(x).split()) for x in teks if normal(x)):
        await conn.execute("""INSERT INTO product_unit_aliases (tenant_id, unit_id, alias) VALUES ($1, $2::uuid, $3)
                              ON CONFLICT (tenant_id, lower(btrim(alias))) DO UPDATE SET unit_id = EXCLUDED.unit_id""", tid, unit_id, t)
