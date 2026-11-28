"""BUG-451: satuan yang DIKETIK di Kirim Faktur ("kirim 1 peti") -> qty dalam satuan BARIS faktur. SATU penentu untuk pratinjau DAN tulis
(pola BUG-085 satuan_dasar.ke_satuan_baris). items[].unit kosong = perilaku lama (qty dalam satuan baris)."""
from typing import List, Optional, Tuple
from uuid import UUID

from . import satuan_dasar as SD
from . import teks_galat as tg

KODE_TAK_DIKENAL = "SATUAN_TAK_DIKENAL"


def _pesan_tak_dikenal(unit, nama: str, ada: List[str]) -> str:
    return f'Satuan "{unit}" tak dikenal — {nama} tak punya satuan {unit}; ada: {", ".join(ada)}'


def konversi(ktx: dict, product_id, unit_baris, nama: str, unit, qin):
    """Satu baris: -> (qty dalam satuan baris | None, blok | None, info). Dipakai faktur DAN pesanan (SO-fulfill)."""
    b = ktx["barang"].get(str(product_id)) if product_id else None
    al = ktx["alias"]
    if b is None or (SD.kanon(unit, al) != SD.kanon(b["dasar"], al) and SD.kanon(unit, al) not in b["konversi"]):
        ada = [b["dasar"], *b["konversi"].keys()] if b else [unit_baris]
        return None, {"code": KODE_TAK_DIKENAL, "status": 422, "message": _pesan_tak_dikenal(unit, nama, ada)}, None
    k = SD.ke_satuan_baris(ktx, product_id, unit, qin, unit_baris, nama)
    if k["blok"]:
        return None, {"code": k["blok"]["code"], "status": 422, "message": k["blok"]["message"]}, None
    return k["qty"], None, {"unit_input": unit, "quantity_input": str(qin),
                            "quantity_text": f"{tg.qty(qin)} {unit} = {tg.qty(k['qty'])} {unit_baris or ''}".strip()}


async def terapkan(conn, tid: str, invoice_id, items: List[dict]) -> Tuple[List[dict], dict, List[dict]]:
    """-> (items dengan quantity dalam satuan baris & tanpa 'unit', info{invoice_item_id|index: {unit_input, quantity_input, quantity_text}}, blok[]).
    Baris tanpa unit lolos apa adanya; baris faktur asing dibiarkan (pemeriksa hilir yang menolak)."""
    ber = [i for i in items if SD.normal(i.get("unit"))]
    if not ber:
        return [dict(i) for i in items], {}, []
    ids = list({str(i["invoice_item_id"]) for i in ber})
    baris = {str(r["id"]): r for r in await conn.fetch(
        "SELECT id, item_id, description, unit FROM sales_invoice_items WHERE invoice_id = $1 AND id = ANY($2::uuid[])", invoice_id, ids)}
    ktx = await SD.muat(conn, tid, [r["item_id"] for r in baris.values() if r["item_id"]])
    hasil, info, blok = [], {}, []
    for n, i in enumerate(items):
        i = dict(i)
        unit = i.pop("unit", None)
        r = baris.get(str(i["invoice_item_id"]))
        if not SD.normal(unit) or r is None:
            hasil.append(i)
            continue
        q, b, inf = konversi(ktx, r["item_id"], r["unit"], r["description"] or "", unit, i["quantity"])
        if b:
            blok.append({**b, "invoice_item_id": str(i["invoice_item_id"])})
        else:
            i["quantity"] = q
            info[str(i["invoice_item_id"])] = inf
        hasil.append(i)
    return hasil, info, blok
