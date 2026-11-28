"""V517 (28 Nov 2026; GO pemilik LANGSUNG; ikut Accurate): DIMENSI/BERAT + MINIMAL KUANTITAS jual/beli per transaksi pada barang.
Flag gelap `dimensi_barang_v1`: mati = medan ditolak 404 FITUR_BELUM_AKTIF saat diisi (perilaku lama utuh), teks tak dikirim.
min_qty_* dalam SATUAN DASAR; hanya PERINGATAN di pratinjau dokumen (bukan blok). Satu penentu: cek() (pratinjau == tulis), teks(), peringatan()."""
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException

from . import teks_galat as tg

FLAG = "dimensi_barang_v1"
MEDAN = ("weight", "weight_unit", "length", "width", "height", "dimension_unit", "min_qty_jual", "min_qty_beli")
KODE_JUAL, KODE_BELI = "QTY_DI_BAWAH_MIN_JUAL", "QTY_DI_BAWAH_MIN_BELI"


async def nyala(conn, tid: str) -> bool:
    return (await conn.fetchval("SELECT EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = $1 AND feature = $2 AND enabled)", tid, FLAG)) is True


def diisi(body) -> list:
    d = body.model_dump(exclude_unset=True) if hasattr(body, "model_dump") else dict(body or {})
    return [k for k in MEDAN if d.get(k) not in (None, "")]


async def cek(conn, tid: str, body) -> None:
    """Pratinjau == tulis (Buat/Ubah Barang): medan V517 diisi saat flag mati -> 404 berkode."""
    if diisi(body) and not await nyala(conn, tid):
        raise HTTPException(status_code=404, detail={"code": "FITUR_BELUM_AKTIF", "message": "Dimensi, berat, dan minimal beli/jual belum diaktifkan untuk usaha ini."})


async def tulis_buat(conn, tid: str, item_id, body) -> None:
    """Sesudah INSERT barang baru: isi medan V517 yang diisi (cek() sudah lolos)."""
    k = diisi(body)
    if not k:
        return
    d = body.model_dump()
    await conn.execute(f"UPDATE products SET {', '.join(f'{c} = ${i + 3}' for i, c in enumerate(k))} WHERE id = $1 AND tenant_id = $2",
                       str(item_id), tid, *[d[c] for c in k])


def _a(x) -> str:
    q = Decimal(str(x)).normalize()
    return tg.angka_laporan(q, 0) if q == q.to_integral_value() else tg.angka_laporan(q, 3).rstrip("0").rstrip(",")


def teks(row, satuan: Optional[str] = None) -> dict:
    g = (lambda k: row.get(k)) if hasattr(row, "get") else (lambda k: row[k])
    berat = f"{_a(g('weight'))} {g('weight_unit') or 'kg'}" if g("weight") is not None else None
    ukuran = [g(k) for k in ("length", "width", "height")]
    dimensi = (" × ".join(_a(x) if x is not None else "–" for x in ukuran) + f" {g('dimension_unit') or 'cm'}") if any(x is not None for x in ukuran) else None
    s = satuan or ""
    return {"berat_text": berat, "dimensi_text": dimensi,
            "min_qty_jual_text": f"Min. jual {_a(g('min_qty_jual'))} {s}".strip() if g("min_qty_jual") is not None else None,
            "min_qty_beli_text": f"Min. beli {_a(g('min_qty_beli'))} {s}".strip() if g("min_qty_beli") is not None else None}


def mentah(row) -> dict:
    g = (lambda k: row.get(k)) if hasattr(row, "get") else (lambda k: row[k])
    return {k: (str(g(k)) if isinstance(g(k), Decimal) else g(k)) for k in MEDAN}


async def peringatan(conn, tid: str, baris: list, arah: str) -> list:
    """baris: [{item_id, qty_dasar, baris(nomor 1..)}]; arah 'jual' | 'beli'. -> peringatan[{code, message, item_id, baris}] (flag mati = [])."""
    ids = sorted({str(b["item_id"]) for b in baris if b.get("item_id")})
    if not ids or not await nyala(conn, tid):
        return []
    kol = "min_qty_jual" if arah == "jual" else "min_qty_beli"
    rows = {str(r["id"]): r for r in await conn.fetch(
        f"SELECT id, nama_produk, COALESCE(NULLIF(base_unit, ''), satuan) AS satuan, {kol} AS minq FROM products "
        f"WHERE tenant_id = $1 AND id::text = ANY($2::text[]) AND {kol} IS NOT NULL AND item_type = 'goods'", tid, ids)}
    out = []
    for b in baris:
        r = rows.get(str(b.get("item_id")))
        if r is None or b.get("qty_dasar") is None:
            continue
        q = Decimal(str(b["qty_dasar"]))
        if q < Decimal(str(r["minq"])):
            kata = "jual" if arah == "jual" else "beli"
            out.append({"code": KODE_JUAL if arah == "jual" else KODE_BELI,
                        "message": f"{r['nama_produk']}: {_a(q)} {r['satuan'] or ''} di bawah minimal {kata} {_a(r['minq'])} {r['satuan'] or ''}.".replace("  ", " "),
                        "item_id": str(b["item_id"]), "baris": b.get("baris")})
    return out


async def peringatan_baris(conn, tid: str, items, arah: str) -> list:
    """Baris dokumen apa adanya ({item_id|product_id, quantity, unit}) -> peringatan min qty. Qty dikonversi ke SATUAN DASAR (penentu satuan_dasar);
    satuan tanpa konversi dilewati (blok satuan sudah dilaporkan penentunya sendiri). Flag mati = [] tanpa kueri barang."""
    if not await nyala(conn, tid):
        return []
    from . import satuan_dasar as SD
    baris = []
    for i, it in enumerate(items or [], start=1):
        iid = it.get("item_id") or it.get("product_id")
        if not iid or it.get("quantity") in (None, ""):
            continue
        baris.append({"item_id": str(iid), "quantity": Decimal(str(it["quantity"])), "unit": it.get("unit"), "baris": i})
    if not baris:
        return []
    ktx = await SD.muat(conn, tid, sorted({b["item_id"] for b in baris}))
    for b in baris:
        h = SD.hitung(ktx, b["item_id"], b["unit"], b["quantity"], "konversi")
        b["qty_dasar"] = None if h["blok"] else h["qty_dasar"]
    return await peringatan(conn, tid, baris, arah)
