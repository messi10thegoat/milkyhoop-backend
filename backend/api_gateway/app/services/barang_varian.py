"""Barang MATRIKS / varian (BUG-159 B1, putusan MASTER 12 Nov 2026): induk + anak per kombinasi ukuran x warna dalam SATU POST.

Model (tanpa migrasi; kolom products.is_matrix_parent / matrix_parent_id / matrix_attributes sudah ada sejak V090): barang INDUK
(is_matrix_parent, tanpa stok) + ANAK per kombinasi = baris products sendiri (stok/HPP/harga/SKU sendiri). Dokumen (faktur/SO/PO/GRN)
tak berubah: baris menunjuk id anak. Kunci atribut `ukuran`/`size` dibaca pencarian barang chat (milkybook/v2/cari_barang).
Penulisan memakai jalur resmi routers.items.tulis_barang_baru (kode barang, nama unik, akun bawaan) -- tak ada INSERT sendiri; semua dalam
transaksi PEMANGGIL (atomik: satu anak bentrok = tak ada yang tertulis). Batas wajar: 2 dimensi, 20 nilai/dimensi, 100 varian/induk."""
import itertools
import json
from types import SimpleNamespace
from typing import Optional

from fastapi import HTTPException

MAKS_DIMENSI, MAKS_NILAI, MAKS_ANAK, MAKS_NAMA = 2, 20, 100, 100  # MAKS_NAMA == CreateItemRequest.name


def _galat(pesan: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": "VARIAN_TAK_VALID", "message": pesan})


def _rapi(teks) -> str:
    return " ".join(str(teks or "").split())


def _dimensi_dict(dimensi) -> list:
    return [d if isinstance(d, dict) else d.model_dump() for d in dimensi]


def rakit(nama_induk: str, dimensi: list) -> list:
    """Kombinasi dimensi -> [{name, atribut}] (dimensi pertama paling luar). Murni; semua batas + pesan di sini."""
    if not dimensi:
        raise _galat("Isi paling sedikit satu dimensi (mis. ukuran).")
    if len(dimensi) > MAKS_DIMENSI:
        raise _galat(f"Barang matriks paling banyak {MAKS_DIMENSI} dimensi (mis. ukuran dan warna).")
    kunci_dipakai, daftar = set(), []
    for d in dimensi:
        kunci = _rapi(d.get("nama")).lower()
        if not kunci:
            raise _galat("Nama dimensi tidak boleh kosong.")
        if kunci in kunci_dipakai:
            raise _galat(f"Dimensi \"{kunci}\" kembar.")
        kunci_dipakai.add(kunci)
        nilai = [_rapi(v) for v in (d.get("nilai") or [])]
        if not nilai:
            raise _galat(f"Dimensi \"{kunci}\" butuh paling sedikit satu nilai.")
        if len(nilai) > MAKS_NILAI:
            raise _galat(f"Dimensi \"{kunci}\" paling banyak {MAKS_NILAI} nilai.")
        if any(not v for v in nilai):
            raise _galat(f"Nilai dimensi \"{kunci}\" tidak boleh kosong.")
        if len({v.lower() for v in nilai}) != len(nilai):
            raise _galat(f"Dimensi \"{kunci}\" punya nilai kembar.")
        daftar.append((kunci, nilai))
    jumlah = 1
    for _, nilai in daftar:
        jumlah *= len(nilai)
    if jumlah > MAKS_ANAK:
        raise _galat(f"Barang matriks paling banyak {MAKS_ANAK} varian (kombinasi ini {jumlah}).")
    induk = _rapi(nama_induk)
    hasil = []
    for kombinasi in itertools.product(*[nilai for _, nilai in daftar]):
        nama = " ".join([induk, *kombinasi])
        if len(nama) > MAKS_NAMA:
            raise _galat(f"Nama varian \"{nama[:40]}…\" melebihi {MAKS_NAMA} karakter; pendekkan nama barang atau nilai.")
        hasil.append({"name": nama, "atribut": {k: v for (k, _), v in zip(daftar, kombinasi)}})
    return hasil


async def _bentrok(conn, tenant_id: str, nama: list) -> list:
    """Blok NAMA_BARANG_DIPAKAI untuk nama yang sudah dipakai barang aktif tenant (indeks unik nama); baca saja."""
    rows = await conn.fetch(
        "SELECT nama_produk FROM products WHERE tenant_id = $1 AND deleted_at IS NULL AND nama_produk = ANY($2::text[])",
        tenant_id, nama)
    return [{"code": "NAMA_BARANG_DIPAKAI", "message": f"Nama barang \"{n}\" sudah dipakai.", "name": n}
            for n in sorted({r["nama_produk"] for r in rows})]


async def rencana(conn, tenant_id: str, body) -> dict:
    """Pratinjau BUAT matriks (baca saja, SAMA dengan tulis): induk + varian yang akan dibuat + blok bentrok nama."""
    anak = rakit(body.name, _dimensi_dict(body.dimensi))
    blocks = await _bentrok(conn, tenant_id, [body.name, *[a["name"] for a in anak]])
    return {"ok": not blocks, "induk": {"name": body.name}, "varian": anak, "blocks": blocks, "jumlah": len(anak)}


def _ditolak(blocks: list) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": "VARIAN_DITOLAK", "blocks": blocks,
                                                  "message": "Nama barang sudah dipakai: " + ", ".join(b["name"] for b in blocks) + "."})


async def _tulis_barang(conn, ctx: dict, dasar, nama: str, berstok: bool, user_id, user_name):
    """Satu baris barang lewat jalur resmi (prasyarat yang diwajibkan docstring tulis_barang_baru dipanggil lebih dulu)."""
    from ..routers import items as I
    from ..schemas.items import CreateItemRequest
    b = CreateItemRequest(name=nama, item_type="goods", track_inventory=berstok, base_unit=dasar.base_unit, kategori=dasar.kategori,
                          deskripsi=dasar.deskripsi, sales_price=dasar.sales_price, purchase_price=dasar.purchase_price)
    I.tanpa_stok_bila_bukan_barang(b)
    I.periksa_badan_item(b, "POST /items")
    return await I.tulis_barang_baru(conn, ctx, b, user_id, user_name)


async def _tulis_anak(conn, ctx: dict, dasar, induk_id, anak: list, user_id, user_name) -> list:
    hasil = []
    for a in anak:
        vid = await _tulis_barang(conn, ctx, dasar, a["name"], True, user_id, user_name)
        await conn.execute(
            "UPDATE products SET matrix_parent_id = $2, matrix_attributes = $3::jsonb WHERE id = $1 AND tenant_id = $4",
            vid, induk_id, json.dumps(a["atribut"], ensure_ascii=False), ctx["tenant_id"])
        hasil.append({"id": str(vid), "name": a["name"], "atribut": a["atribut"]})
    return hasil


async def _kunci_tenant(conn, tenant_id: str) -> None:
    """Pembuatan matriks serentak di tenant yang sama BERURUTAN: kode barang otomatis (BRG-NNNN dari nomor terbesar) bentrok bila dua
    penulis menghitung bersamaan; yang kedua kemudian melihat nama bentrok -> 409 bersih, bukan 500 (uji salinan 12 Nov)."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"MATRIX:{tenant_id}")


async def tulis(conn, ctx: dict, body, user_id, user_name) -> dict:
    """Buat induk + semua anak di transaksi PEMANGGIL. Bentrok nama -> 409 VARIAN_DITOLAK sebelum menulis apa pun."""
    await _kunci_tenant(conn, ctx["tenant_id"])
    r = await rencana(conn, ctx["tenant_id"], body)
    if r["blocks"]:
        raise _ditolak(r["blocks"])
    induk_id = await _tulis_barang(conn, ctx, body, body.name, False, user_id, user_name)
    await conn.execute("UPDATE products SET is_matrix_parent = true WHERE id = $1 AND tenant_id = $2", induk_id, ctx["tenant_id"])
    varian = await _tulis_anak(conn, ctx, body, induk_id, r["varian"], user_id, user_name)
    return {"id": str(induk_id), "name": body.name, "varian": varian}


async def _induk(conn, tenant_id: str, induk_id):
    induk = await conn.fetchrow(
        "SELECT id, nama_produk, base_unit, kategori, deskripsi, sales_price, purchase_price FROM products "
        "WHERE tenant_id = $1 AND id = $2 AND is_matrix_parent AND deleted_at IS NULL", tenant_id, induk_id)
    if not induk:
        raise HTTPException(status_code=404, detail={"code": "BARANG_MATRIKS_TAK_ADA", "message": "Barang matriks tidak ditemukan."})
    return induk


async def _rencana_tambah(conn, tenant_id: str, induk_id, dimensi) -> tuple:
    """-> (induk, anak, blocks). Dimensi harus sama dengan varian yang sudah ada; total varian <= batas."""
    induk = await _induk(conn, tenant_id, induk_id)
    dims = _dimensi_dict(dimensi)
    anak = rakit(induk["nama_produk"], dims)
    contoh = await conn.fetchrow(
        "SELECT matrix_attributes FROM products WHERE tenant_id = $1 AND matrix_parent_id = $2 AND deleted_at IS NULL "
        "ORDER BY nama_produk LIMIT 1", tenant_id, induk_id)
    if contoh:
        atr = contoh["matrix_attributes"]
        ada = sorted((json.loads(atr) if isinstance(atr, str) else dict(atr or {})).keys())
        baru = sorted(_rapi(d.get("nama")).lower() for d in dims)
        if ada and ada != baru:
            raise _galat("Dimensi harus sama dengan varian yang sudah ada: " + ", ".join(ada) + ".")
    sudah = await conn.fetchval(
        "SELECT count(*) FROM products WHERE tenant_id = $1 AND matrix_parent_id = $2 AND deleted_at IS NULL", tenant_id, induk_id)
    if (sudah or 0) + len(anak) > MAKS_ANAK:
        raise _galat(f"Barang matriks paling banyak {MAKS_ANAK} varian (sudah {sudah}, menambah {len(anak)}).")
    return induk, anak, await _bentrok(conn, tenant_id, [a["name"] for a in anak])


async def rencana_tambah(conn, tenant_id: str, induk_id, dimensi) -> dict:
    induk, anak, blocks = await _rencana_tambah(conn, tenant_id, induk_id, dimensi)
    return {"ok": not blocks, "induk": {"name": induk["nama_produk"]}, "varian": anak, "blocks": blocks, "jumlah": len(anak)}


async def tambah(conn, ctx: dict, induk_id, dimensi, user_id, user_name) -> dict:
    """Tambah varian ke induk yang ada (harga/satuan/kategori mengikuti induk). Bentrok nama -> 409 sebelum menulis."""
    await _kunci_tenant(conn, ctx["tenant_id"])
    induk, anak, blocks = await _rencana_tambah(conn, ctx["tenant_id"], induk_id, dimensi)
    if blocks:
        raise _ditolak(blocks)
    dasar = SimpleNamespace(base_unit=induk["base_unit"], kategori=induk["kategori"], deskripsi=induk["deskripsi"],
                            sales_price=_angka(induk["sales_price"]), purchase_price=_angka(induk["purchase_price"]))
    varian = await _tulis_anak(conn, ctx, dasar, induk["id"], anak, user_id, user_name)
    return {"id": str(induk["id"]), "name": induk["nama_produk"], "varian": varian}


def _angka(x) -> Optional[float]:
    return None if x is None else float(x)


async def daftar(conn, tenant_id: str, cari: Optional[str], limit: int, offset: int) -> dict:
    kata = f"%{_rapi(cari).lower()}%" if cari and _rapi(cari) else None
    rows = await conn.fetch(
        """SELECT p.id, p.nama_produk, p.item_code, p.kategori, p.base_unit, p.sales_price, p.purchase_price,
                  (SELECT count(*) FROM products v WHERE v.tenant_id = p.tenant_id AND v.matrix_parent_id = p.id
                      AND v.deleted_at IS NULL) AS jumlah_varian
           FROM products p WHERE p.tenant_id = $1 AND p.is_matrix_parent AND p.deleted_at IS NULL
             AND ($2::text IS NULL OR lower(p.nama_produk) LIKE $2) ORDER BY p.nama_produk LIMIT $3 OFFSET $4""",
        tenant_id, kata, limit, offset)
    total = await conn.fetchval(
        "SELECT count(*) FROM products p WHERE p.tenant_id = $1 AND p.is_matrix_parent AND p.deleted_at IS NULL "
        "AND ($2::text IS NULL OR lower(p.nama_produk) LIKE $2)", tenant_id, kata)
    items = [{"id": str(r["id"]), "name": r["nama_produk"], "item_code": r["item_code"], "kategori": r["kategori"],
              "base_unit": r["base_unit"], "sales_price": _angka(r["sales_price"]), "purchase_price": _angka(r["purchase_price"]),
              "jumlah_varian": int(r["jumlah_varian"])} for r in rows]
    return {"items": items, "total": int(total), "has_more": offset + len(items) < int(total)}


async def detail(conn, tenant_id: str, induk_id) -> dict:
    r = await conn.fetchrow(
        "SELECT id, nama_produk, item_code, kategori, base_unit, sales_price, purchase_price FROM products "
        "WHERE tenant_id = $1 AND id = $2 AND is_matrix_parent AND deleted_at IS NULL", tenant_id, induk_id)
    if not r:
        raise HTTPException(status_code=404, detail={"code": "BARANG_MATRIKS_TAK_ADA", "message": "Barang matriks tidak ditemukan."})
    anak = await conn.fetch(
        "SELECT id, nama_produk, matrix_attributes, item_code, sku, sales_price, purchase_price, status FROM products "
        "WHERE tenant_id = $1 AND matrix_parent_id = $2 AND deleted_at IS NULL ORDER BY nama_produk", tenant_id, induk_id)

    def atr(x):
        return json.loads(x) if isinstance(x, str) else dict(x or {})
    return {"id": str(r["id"]), "name": r["nama_produk"], "item_code": r["item_code"], "kategori": r["kategori"],
            "base_unit": r["base_unit"], "sales_price": _angka(r["sales_price"]), "purchase_price": _angka(r["purchase_price"]),
            "jumlah_varian": len(anak),
            "varian": [{"id": str(a["id"]), "name": a["nama_produk"], "atribut": atr(a["matrix_attributes"]), "item_code": a["item_code"],
                        "sku": a["sku"], "sales_price": _angka(a["sales_price"]), "purchase_price": _angka(a["purchase_price"]),
                        "status": a["status"]} for a in anak]}


# ---------------------------------------------------------------- U-V1 (26 Nov, flag gelap varian_v1): daftar varian per induk
FLAG_V1 = "varian_v1"
MAKS_LIMIT_VARIAN = 100


def _atr(x) -> dict:
    return json.loads(x) if isinstance(x, str) else dict(x or {})


def urut_dimensi(anak_atr: list) -> list:
    """Urutan dimensi = urutan kemunculan kunci di atribut anak pertama (rakit: dimensi pertama paling luar). Murni."""
    urut = []
    for a in anak_atr:
        for k in a:
            if k not in urut:
                urut.append(k)
    return urut


def parse_saring_atribut(atribut: list) -> dict:
    """['ukuran:XL', 'warna:Hitam'] -> {ukuran: [XL], warna: [Hitam]} (nilai sama dimensi = ATAU, beda dimensi = DAN). Tak berformat -> 422."""
    hasil: dict = {}
    for s in atribut or []:
        nama, sep, nilai = str(s).partition(":")
        if not sep or not _rapi(nama) or not _rapi(nilai):
            raise _galat(f"Saringan atribut '{s}' tidak dikenali; gunakan bentuk nama:nilai, mis. ukuran:XL.")
        hasil.setdefault(_rapi(nama), []).append(_rapi(nilai))
    return hasil


def stok_status(b: dict, stok) -> Optional[str]:
    from .barang_daftar import _d, dilacak
    if not dilacak(b):
        return None
    s = _d(stok)
    return "minus" if s < 0 else ("habis" if s == 0 else "ada")


def baris_varian(r: dict, urut: list) -> dict:
    """Satu baris varian (murni, teruji): teks & aksi dari server; aksi = aksi_tersedia_detail barang biasa (rute /api/items/{id})."""
    from .barang_aksi import aksi_tersedia_detail, keadaan, keadaan_text, rp
    from .barang_daftar import stok_fisik_text
    atr = _atr(r.get("matrix_attributes"))
    stok = r.get("stok") or 0
    return {
        "id": str(r["id"]), "nama": r["nama_produk"], "item_code": r.get("item_code"), "sku": r.get("sku"),
        "atribut": [{"nama": k, "nilai": atr[k]} for k in urut if k in atr],
        "atribut_text": " · ".join(str(atr[k]) for k in urut if k in atr),
        "status": keadaan(r), "status_text": keadaan_text(r),
        "stok": float(stok), "stok_text": stok_fisik_text(r, stok, r.get("base_unit")),
        "stok_status": stok_status(r, stok),
        "harga_jual_text": rp(r.get("sales_price")), "harga_beli_text": rp(r.get("purchase_price")),
        "aksi_tersedia": aksi_tersedia_detail(r),
    }


def ringkasan_varian(baris: list, satuan: Optional[str]) -> dict:
    """Server menjumlah (FE tak menjumlah). baris = keluaran baris_varian (SEMUA varian tersaring, bukan satu halaman)."""
    from decimal import Decimal
    from .barang_daftar import _q
    total = sum((Decimal(str(b["stok"])) for b in baris if b["stok_status"] is not None), Decimal(0))
    ada = sum(1 for b in baris if b["stok_status"] == "ada")
    habis = sum(1 for b in baris if b["stok_status"] in ("habis", "minus"))
    sat = f" {satuan}" if satuan else ""
    return {"jumlah_varian": len(baris), "jumlah_varian_text": f"{len(baris)} varian",
            "total_stok_text": f"{_q(total)}{sat}", "jumlah_ada_stok_text": f"{ada} ada stok", "jumlah_habis_text": f"{habis} habis"}


def filter_atribut(atr_semua: list, urut: list) -> list:
    """[{nama, nilai:[{nilai, jumlah}]}] dari SEMUA varian induk (tak tersaring), urut dimensi lalu kemunculan nilai."""
    hasil = []
    for k in urut:
        hit: dict = {}
        for a in atr_semua:
            if k in a:
                hit[str(a[k])] = hit.get(str(a[k]), 0) + 1
        hasil.append({"nama": k, "nilai": [{"nilai": v, "jumlah": n} for v, n in hit.items()]})
    return hasil


async def daftar_varian(conn, tenant_id: str, induk_id, search: Optional[str], atribut: list, skip: int, limit: int) -> dict:
    from .barang_aksi import SQL_HITUNG_JEJAK
    from .barang_daftar import SQL_PER_STOK
    induk = await conn.fetchrow(
        "SELECT id, nama_produk, base_unit FROM products WHERE tenant_id = $1 AND id = $2 AND is_matrix_parent AND deleted_at IS NULL",
        tenant_id, induk_id)
    if not induk:
        raise HTTPException(status_code=404, detail={"code": "BARANG_MATRIKS_TAK_ADA", "message": "Barang matriks tidak ditemukan."})
    saring = parse_saring_atribut(atribut)
    rows = await conn.fetch(
        f"""SELECT p.id, p.nama_produk, p.item_code, p.sku, p.matrix_attributes, p.status, p.item_type, p.track_inventory,
                   p.base_unit, p.sales_price, p.purchase_price, COALESCE(per.jumlah, 0) AS stok, ({SQL_HITUNG_JEJAK}) AS transaction_count
            FROM products p {SQL_PER_STOK}
            WHERE p.tenant_id = $1 AND p.matrix_parent_id = $2 AND p.deleted_at IS NULL
            ORDER BY p.created_at, p.nama_produk""", tenant_id, induk_id)
    semua = [dict(r) for r in rows]
    atr_semua = [_atr(r["matrix_attributes"]) for r in semua]
    urut = urut_dimensi(atr_semua)
    kata = _rapi(search).lower() if search and _rapi(search) else None

    def lolos(r, a) -> bool:
        if any(str(a.get(k)) not in vs for k, vs in saring.items()):
            return False
        if kata:
            teks = " ".join(str(x or "") for x in (r["nama_produk"], r["item_code"], r["sku"], *a.values())).lower()
            return kata in teks
        return True
    tersaring = [baris_varian(r, urut) for r, a in zip(semua, atr_semua) if lolos(r, a)]
    halaman = tersaring[skip:skip + limit]
    return {"induk": {"id": str(induk["id"]), "nama": induk["nama_produk"]},
            "data": halaman, "total": len(tersaring), "has_more": skip + len(halaman) < len(tersaring),
            "ringkasan": ringkasan_varian(tersaring, induk["base_unit"]),
            "filter_atribut": filter_atribut(atr_semua, urut)}


async def info_varian_barang(conn, tenant_id: str, row) -> dict:
    """Detail barang (GET /api/items/{id}): is_matrix_parent, matrix_parent{id,nama} (anak), atribut[{nama,nilai}] (anak). Selalu ada kunci."""
    pid = row.get("matrix_parent_id")
    induk = None
    if pid:
        r = await conn.fetchrow("SELECT id, nama_produk FROM products WHERE tenant_id = $1 AND id = $2", tenant_id, pid)
        induk = {"id": str(r["id"]), "nama": r["nama_produk"]} if r else None
    atr = _atr(row.get("matrix_attributes"))
    return {"is_matrix_parent": bool(row.get("is_matrix_parent")), "matrix_parent": induk,
            "atribut": [{"nama": k, "nilai": str(v)} for k, v in atr.items()] if pid else []}
