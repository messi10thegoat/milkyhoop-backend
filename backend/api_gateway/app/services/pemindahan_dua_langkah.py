"""K8 (14 Nov 2026; putusan pemilik: opsi A + SATU dokumen dua fase): Pemindahan Barang DUA LANGKAH ala Accurate, TANPA jurnal.
Kirim: kaki KELUAR gudang asal + kaki MASUK lokasi Transit (gudang sistem, services/gudang_transit; V497). Terima: KELUAR Transit + MASUK gudang tujuan, boleh
berulang (penerimaan sebagian). Σ on_hand ledger tak berubah selama transit (verify_inventory_wac_reconciliation_all tak memakai gudang) dan GL tak bergerak
(satu akun Persediaan) -> nol jurnal. WAC tak dihitung ulang (relokasi bukan inbound ekonomis; sama dengan satu langkah, services/pemindahan_barang).
SATU dokumen (nomor sama): draft -> in_transit -> received; "diterima sebagian" = TURUNAN (Σ received < Σ shipped), bukan nilai status baru. Selisih =
quantity_variance (kolom GENERATED shipped - received). Pratinjau == tulis: penentu yang sama (rencana_*)."""
from decimal import Decimal
from typing import List, Optional

from . import gudang_transit as GT
from . import pemindahan_barang as pb
from ..utils.tanggal_tenant import tanggal_dokumen
from .so_riwayat import catat_riwayat

_NOL = Decimal("0")
SUMBER_KIRIM = "RELOKASI_KIRIM"
SUMBER_TERIMA = "RELOKASI_TERIMA"
SUMBER_KIRIM_BATAL = "RELOKASI_KIRIM_VOID"
KIRIM_KELUAR, KIRIM_MASUK = "RELOKASI_KIRIM_KELUAR", "RELOKASI_KIRIM_TRANSIT"
TERIMA_KELUAR, TERIMA_MASUK = "RELOKASI_TERIMA_TRANSIT", "RELOKASI_TERIMA_MASUK"


def _d(x) -> Decimal:
    return Decimal(str(x if x is not None else 0))


def teks_status(status: str, dikirim, diterima, diselesaikan=0) -> str:
    """Teks status dari server (pemindahan dua langkah). Diterima sebagian = TURUNAN, bukan status tersimpan.
    BUG-261: tertunda (dikirim - diterima - diselesaikan lewat Penyesuaian) = 0 dan ada penyelesaian -> "Diterima · selisih diselesaikan"."""
    if status in ("in_transit", "received") and _d(diselesaikan) > _NOL and _d(dikirim) - _d(diterima) - _d(diselesaikan) <= _NOL:
        return "Diterima · selisih diselesaikan"
    if status == "in_transit":
        return "Dikirim · diterima sebagian" if _d(diterima) > _NOL else "Dikirim"
    return {"draft": "Draf", "received": "Diterima", "completed": "Selesai", "voided": "Dibatalkan", "cancelled": "Dibatalkan"}.get(status, status)


def _blok(kode: str, pesan: str, status: int = 409, item_id=None) -> dict:
    return {"code": kode, "message": pesan, "status": status, "item_id": str(item_id) if item_id is not None else None}


# TRIGGER DB (diukur 14 Nov): prevent_st_modification menolak UPDATE header berstatus in_transit/received kecuali status/received_by/received_date berubah, dan
# trg_update_st_totals memperbarui header (updated_at) pada SETIAP UPDATE baris item -> baris item & header TAK BOLEH ditulis sesudah Kirim. Maka jumlah DITERIMA
# per barang DITURUNKAN dari ledger (Law 1/11; sumber kebenaran), BUKAN dari kolom stock_transfer_items.quantity_received (beku di 0 sesudah Kirim).
SQL_DITERIMA = ("SELECT il.product_id, COALESCE(SUM(il.quantity_in), 0) AS diterima FROM inventory_ledger il "
                "WHERE il.tenant_id = $1 AND il.source_type = $2 AND il.source_id = $3 GROUP BY il.product_id")


async def diterima_per_barang(conn, tid: str, transfer_id) -> dict:
    return {r["product_id"]: _d(r["diterima"]) for r in await conn.fetch(SQL_DITERIMA, tid, SUMBER_TERIMA, transfer_id)}


# BUG-261 (V503): selisih terima yang DISELESAIKAN lewat Penyesuaian Persediaan = Σ |qty| baris penyesuaian POSTED (bukan void/draf) yang bertaut source_transfer_id.
# Void penyesuaian mengubah status ke 'void' -> otomatis tak terhitung (tertunda kembali).
SQL_DISELESAIKAN = ("SELECT i.product_id, COALESCE(SUM(ABS(i.quantity_adjustment)), 0) AS selesai FROM stock_adjustment_items i "
                    "JOIN stock_adjustments s ON s.id = i.stock_adjustment_id WHERE s.tenant_id = $1 AND s.status = 'posted' AND i.source_transfer_id = $2 "
                    "GROUP BY i.product_id")
SQL_DISELESAIKAN_BANYAK = ("SELECT i.source_transfer_id AS tid, COALESCE(SUM(ABS(i.quantity_adjustment)), 0) AS selesai FROM stock_adjustment_items i "
                           "JOIN stock_adjustments s ON s.id = i.stock_adjustment_id WHERE s.tenant_id = $1 AND s.status = 'posted' "
                           "AND i.source_transfer_id = ANY($2::uuid[]) GROUP BY i.source_transfer_id")


async def diselesaikan_per_barang(conn, tid: str, transfer_id) -> dict:
    return {r["product_id"]: _d(r["selesai"]) for r in await conn.fetch(SQL_DISELESAIKAN, tid, transfer_id)}


async def teks_daftar(conn, tid: str, rows) -> dict:
    """BUG-246: {id: (status_text, diterima_sebagian)} untuk daftar. SATU kueri ledger untuk semua pemindahan in_transit di halaman (bukan N kueri).
    BUG-261: + status 'received' dan penyelesaian lewat Penyesuaian (dua kueri tambahan, tetap per HALAMAN): teks 'Diterima · selisih diselesaikan'."""
    ids = [r["id"] for r in rows if r["status"] == "in_transit"]
    ada = {}
    if ids:
        for r in await conn.fetch("SELECT il.source_id, COALESCE(SUM(il.quantity_in), 0) AS diterima FROM inventory_ledger il "
                                  "WHERE il.tenant_id = $1 AND il.source_type = $2 AND il.source_id = ANY($3::uuid[]) GROUP BY il.source_id", tid, SUMBER_TERIMA, ids):
            ada[r["source_id"]] = _d(r["diterima"])
    kirim_ids = [r["id"] for r in rows if r["status"] in ("in_transit", "received")]
    selesai, dikirim = {}, {}
    if kirim_ids:
        for r in await conn.fetch(SQL_DISELESAIKAN_BANYAK, tid, kirim_ids):
            selesai[r["tid"]] = _d(r["selesai"])
        if selesai:   # jumlah dikirim hanya perlu utk yang punya penyelesaian
            for r in await conn.fetch("SELECT stock_transfer_id AS tid, COALESCE(SUM(quantity_shipped), 0) AS dikirim FROM stock_transfer_items "
                                      "WHERE stock_transfer_id = ANY($1::uuid[]) GROUP BY stock_transfer_id", list(selesai)):
                dikirim[r["tid"]] = _d(r["dikirim"])
    hasil = {}
    for r in rows:
        diterima = ada.get(r["id"], _NOL)
        sel = selesai.get(r["id"], _NOL)
        dik = dikirim.get(r["id"], _NOL)
        if r["status"] == "received" and sel > _NOL:
            diterima = dik - sel    # diterima penuh (= dikirim - diselesaikan) bila 'received' dan ada penyelesaian: tertunda 0 pasti
        hasil[r["id"]] = (teks_status(r["status"], dik, diterima, sel), r["status"] == "in_transit" and ada.get(r["id"], _NOL) > _NOL)
    return hasil


async def item_baris(conn, tid: str, transfer_id) -> list:
    """Baris item (dict) + quantity_received/quantity_variance DITURUNKAN dari ledger."""
    ada = await diterima_per_barang(conn, tid, transfer_id)
    selesai = await diselesaikan_per_barang(conn, tid, transfer_id)
    hasil = []
    for i in await conn.fetch("SELECT * FROM stock_transfer_items WHERE stock_transfer_id = $1 ORDER BY line_number, id", transfer_id):
        d = dict(i)
        d["quantity_received"] = ada.get(d["item_id"], _NOL)
        d["quantity_variance"] = _d(d["quantity_shipped"]) - d["quantity_received"]
        d["quantity_resolved"] = selesai.get(d["item_id"], _NOL)                        # BUG-261
        d["quantity_pending"] = d["quantity_variance"] - d["quantity_resolved"]          # tertunda = dikirim - diterima - diselesaikan
        hasil.append(d)
    return hasil


async def terapkan_diterima(conn, tid: str, transfer_id, status: str, items: list) -> list:
    """Untuk detail: pemindahan yang sudah dikirim -> quantity_received/variance dari ledger (kolom DB beku); selain itu apa adanya."""
    items = [dict(i) for i in items]
    if status in ("in_transit", "received"):
        ada = await diterima_per_barang(conn, tid, transfer_id)
        selesai = await diselesaikan_per_barang(conn, tid, transfer_id)
        for d in items:
            d["quantity_received"] = ada.get(d["item_id"], _NOL)
            d["quantity_variance"] = _d(d["quantity_shipped"]) - ada.get(d["item_id"], _NOL)
            d["quantity_resolved"] = selesai.get(d["item_id"], _NOL)                    # BUG-261
            d["quantity_pending"] = d["quantity_variance"] - d["quantity_resolved"]
    return items


# ============================================================================
# KIRIM
# ============================================================================
async def rencana_kirim(conn, tid: str, h, items, kunci: bool = False) -> dict:
    """draft -> in_transit. Validasi barang/gudang/stok asal = penentu SATU langkah (pb._rencana); fase SO TIDAK disentuh di Kirim (baru saat diterima seluruhnya)."""
    r = await pb._rencana(conn, tid, h["from_warehouse_id"], h["to_warehouse_id"],
                          [{"item_id": i["item_id"], "qty": _d(i["quantity_requested"]), "notes": i["notes"],
                            "batch_id": i.get("batch_id")} for i in items], None, kunci=kunci)   # BUG-453: batch pilihan / FEFO
    r["ulang"] = h["status"] == "in_transit"
    if h["status"] == "in_transit":
        r["blocks"] = []
    elif h["status"] != "draft":
        r["blocks"].insert(0, _blok("PB_STATUS_TIDAK_BOLEH", f"Pemindahan {h['transfer_number']} berstatus {h['status']}; hanya draf yang bisa dikirim."))
    if not items:
        r["blocks"].append(_blok("PB_TANPA_BARANG", "Pemindahan tak punya baris barang.", 422))
    r["status_sesudah"] = "in_transit"
    return r


async def pratinjau_kirim(conn, ctx: dict, transfer_id) -> dict:
    tid = ctx["tenant_id"]
    h = await pb._header(conn, tid, transfer_id)
    r = await rencana_kirim(conn, tid, h, await item_baris(conn, tid, transfer_id), kunci=False)
    r["ok"] = not r["blocks"]
    r["transfer_number"] = h["transfer_number"]
    transit_id = await GT.id_transit(conn, tid)
    for ln in r["lines"]:   # BUG-248-a (ADITIF, putusan MASTER): barang masuk TRANSIT; transit_before/after ditambah. to_before/to_after/available_to TETAP dikirim sementara (USANG:
        ada = await pb._stok(conn, tid, transit_id, ln["item_id"], False) if transit_id else _NOL   # dihapus sesudah FE pindah ke transit_*; unit pembersihan terpisah)
        ln["transit_before"], ln["transit_after"] = _d(ada), _d(ada) + _d(ln["quantity"])
    r["lokasi_transit"] = GT.NAMA
    r["catatan"] = "Tanpa jurnal: barang pindah dari gudang asal ke lokasi Transit; nilai persediaan tidak berubah."
    r["payload"] = {"transfer_id": str(transfer_id)}
    return r


async def kirim(conn, ctx: dict, transfer_id) -> dict:
    tid, uid = ctx["tenant_id"], ctx.get("user_id")
    h = await pb._draf_terkunci(conn, tid, transfer_id)
    items = await item_baris(conn, tid, transfer_id)
    if h["status"] == "in_transit":
        return {"ulang": True}
    rencana = await rencana_kirim(conn, tid, h, items, kunci=True)
    pb.angkat_blok_pertama(rencana)
    by_item = {i["item_id"]: i for i in items}
    asal, tujuan = rencana["dari"], rencana["ke"]
    transit_id = await GT.pastikan_transit(conn, tid)
    hari = await tanggal_dokumen(conn, tid)
    total = _NOL
    for ln in rencana["lines"]:
        it = by_item[ln["item_id"]]
        qty, wac, nilai = ln["quantity"], ln["unit_cost"], ln["value"]
        saldo = await pb._saldo_produk(conn, tid, ln["item_id"])
        kw = dict(product_id=ln["item_id"], kode=ln["item_code"], nama=ln["name"], sumber=SUMBER_KIRIM, tanggal=hari, transfer_id=transfer_id,
                  nomor=h["transfer_number"], qty=qty, unit_cost=wac, total=nilai, wac=wac)
        for q_b, v_b, bid in pb.potong_batch(ln):   # BUG-453: per batch asal -> Transit (batch_id SAMA)
            kw.update(qty=q_b, total=v_b)
            await pb._tulis_ledger(conn, tid, uid, tipe=KIRIM_KELUAR, masuk=False, saldo=saldo - q_b, gudang_id=h["from_warehouse_id"],
                                   catatan=f"Kirim ke {tujuan['name']} (Transit)", batch_id=bid, **kw)
            await pb._tulis_ledger(conn, tid, uid, tipe=KIRIM_MASUK, masuk=True, saldo=saldo, gudang_id=transit_id,
                                   catatan=f"Transit dari {asal['name']} ke {tujuan['name']}", batch_id=bid, **kw)
        await conn.execute("UPDATE stock_transfer_items SET quantity_shipped = $2, unit_cost = $3, total_value = $4 WHERE id = $1", it["id"], qty, wac, nilai)
        total += nilai
    await conn.execute(
        "UPDATE stock_transfers SET status = 'in_transit', shipped_date = $3, shipped_by = $4, updated_at = NOW() WHERE id = $1 AND tenant_id = $2",
        transfer_id, tid, hari, uid)
    await catat_riwayat(conn, tid, "stock_transfers", transfer_id, h["transfer_number"], "PEMINDAHAN_BARANG_DIKIRIM", uid,
                        f"Pemindahan {h['transfer_number']} dikirim: {asal['name']} -> {tujuan['name']} ({len(items)} barang, dalam Transit)",
                        {"dari": asal["name"], "ke": tujuan["name"], "barang": len(items), "nilai": str(total)}, source="api:stock_transfers.ship")
    return {"ulang": False}


# ============================================================================
# TERIMA
# ============================================================================
def peringatan_terima(lines: List[dict]) -> List[dict]:
    """K8-W (17 Nov 2026; Accurate PT BIS via TESTER): Terima yang MENYISAKAN barang di Transit = PERINGATAN (bukan blok): "Selisih N belum diterima; dapat diterima di penerimaan
    berikutnya." MURNI. lines = lines rencana_terima (name, unit, tertunda_sesudah, item_id). Modul ini nol float (K8): selisih = Decimal; ROUTER mengubah ke float di batas JSON (stock_transfers._pw). Terima tuntas = daftar kosong."""
    from .barang_daftar import _q
    out = []
    for ln in lines:
        sisa = _d(ln["tertunda_sesudah"])
        if sisa > _NOL:
            satuan = f" {ln['unit']}" if ln.get("unit") else ""
            out.append({"code": "PB_TERIMA_KURANG", "item_id": str(ln["item_id"]), "nama": ln["name"], "selisih": sisa, "unit": ln.get("unit"),
                        "message": f"Selisih {_q(sisa)}{satuan} {ln['name']} belum diterima; dapat diterima di penerimaan berikutnya."})
    return out


async def rencana_terima(conn, tid: str, h, items, baris: Optional[List[dict]], kunci: bool = False) -> dict:
    """baris: [{"item_id": UUID, "qty": Decimal}] atau None/[] = SEMUA tertunda. -> {blocks, lines, status_sesudah, tuntas, ...}. qty <= tertunda; boleh berulang."""
    blocks: list = []
    if h["status"] != "in_transit":
        blocks.append(_blok("PB_STATUS_TIDAK_BOLEH", f"Pemindahan {h['transfer_number']} berstatus {h['status']}; hanya yang sedang dikirim (Transit) yang bisa diterima.")
                      if h["status"] != "received" else
                      _blok("PB_SUDAH_DITERIMA", f"Pemindahan {h['transfer_number']} sudah diterima seluruhnya."))
    by = {i["item_id"]: i for i in items}
    tertunda = {i["item_id"]: _d(i["quantity_shipped"]) - _d(i["quantity_received"]) - _d(i.get("quantity_resolved")) for i in items}   # BUG-261: dikurangi yang diselesaikan Penyesuaian
    if not baris:
        baris = [{"item_id": k, "qty": v} for k, v in tertunda.items() if v > _NOL]
    transit_id = await GT.id_transit(conn, tid)
    tujuan = await conn.fetchrow("SELECT id, name, is_active FROM warehouses WHERE id = $1 AND tenant_id = $2", h["to_warehouse_id"], tid)
    if tujuan is None or not tujuan["is_active"]:
        blocks.append(_blok("PB_GUDANG_TUJUAN_NONAKTIF", f"Gudang tujuan{' ' + tujuan['name'] if tujuan else ''} tidak aktif; aktifkan dulu."))
    lihat, akan = set(), {}
    for b in baris:
        iid, qty = b["item_id"], _d(b.get("qty"))
        it = by.get(iid)
        if it is None:
            blocks.append(_blok("PB_BARANG_BUKAN_DOKUMEN", "Barang tidak ada di pemindahan ini.", 404, iid))
            continue
        if iid in lihat:
            blocks.append(_blok("PB_BARANG_GANDA", "Barang yang sama muncul dua kali; gabungkan jadi satu baris.", 422, iid))
            continue
        lihat.add(iid)
        if qty < _NOL:
            blocks.append(_blok("PB_TERIMA_QTY_NEGATIF", f"Jumlah diterima {it['item_name']} tidak boleh negatif.", 422, iid))
            continue
        if qty > tertunda[iid]:
            blocks.append(_blok("PB_TERIMA_QTY_MELEBIHI", f"Jumlah diterima {it['item_name']} ({qty:f}) melebihi yang tertunda ({tertunda[iid]:f}).", 409, iid))
            continue
        akan[iid] = qty
    if blocks == [] and all(v == _NOL for v in akan.values()):
        blocks.append(_blok("PB_TERIMA_KOSONG", "Isi jumlah diterima minimal satu barang.", 422))
    if kunci and transit_id:
        for iid in sorted(akan, key=str):
            await pb._kunci(conn, f"STOCK:{iid}:{transit_id}")
    lines = []
    for i in items:
        iid = i["item_id"]
        a = akan.get(iid, _NOL)
        ada_transit = await pb._stok(conn, tid, transit_id, iid, kunci) if transit_id else _NOL
        if a > ada_transit:
            blocks.append(_blok("PB_STOK_TRANSIT_KURANG", f"Stok {i['item_name']} di Transit hanya {ada_transit:f}, diminta {a:f}.", 409, iid))
        lines.append({"item_id": iid, "name": i["item_name"], "item_code": i["item_code"], "unit": i["unit"], "shipped": _d(i["quantity_shipped"]),
                      "received_before": _d(i["quantity_received"]), "resolved": _d(i.get("quantity_resolved")), "tertunda": tertunda[iid], "akan_diterima": a,
                      "tertunda_sesudah": tertunda[iid] - a, "unit_cost": _d(i["unit_cost"]), "value": (a * _d(i["unit_cost"])).quantize(Decimal("0.01"))})
    tuntas = all(l["tertunda_sesudah"] <= _NOL for l in lines)
    return {"blocks": blocks, "lines": lines, "status_sesudah": "received" if tuntas else "in_transit", "tuntas": tuntas, "peringatan": peringatan_terima(lines),
            "tujuan": {"id": str(h["to_warehouse_id"]), "name": tujuan["name"] if tujuan else None},
            "total_quantity": sum((l["akan_diterima"] for l in lines), _NOL), "total_value": sum((l["value"] for l in lines), _NOL),
            "status_text_sesudah": teks_status("received" if tuntas else "in_transit", sum((l["shipped"] for l in lines), _NOL),
                                               sum((l["received_before"] + l["akan_diterima"] for l in lines), _NOL),
                                               sum((l["resolved"] for l in lines), _NOL))}


async def pratinjau_terima(conn, ctx: dict, transfer_id, baris: Optional[List[dict]]) -> dict:
    tid = ctx["tenant_id"]
    h = await pb._header(conn, tid, transfer_id)
    r = await rencana_terima(conn, tid, h, await item_baris(conn, tid, transfer_id), baris, kunci=False)
    r["ok"] = not r["blocks"]
    r["transfer_number"] = h["transfer_number"]
    r["bawaan_semua_tertunda"] = not baris
    transit_id = await GT.id_transit(conn, tid)
    r["dari"] = {"id": str(transit_id) if transit_id else None, "name": GT.NAMA}   # BUG-248-b: Terima = dari Transit ke gudang tujuan
    r["ke"] = r["tujuan"]
    r["payload"] = {"items": [{"item_id": str(l["item_id"]), "quantity_received": str(l["akan_diterima"])} for l in r["lines"] if l["akan_diterima"] > _NOL]}
    return r


async def potong_terima(conn, tid: str, transfer_id, transit_id, item_id, qty: Decimal, nilai: Decimal) -> list:
    """BUG-453: qty diterima -> [(qty, nilai, batch_id)] dari sisa per batch di Transit milik pemindahan ini (Σ masuk − keluar kartu ber-batch,
    urut kedaluwarsa terdekat). Barang tanpa batch / tak ada sisa ber-batch = satu potongan tanpa batch (perilaku lama)."""
    rows = await conn.fetch(
        "SELECT l.batch_id, SUM(COALESCE(l.quantity_in, 0) - COALESCE(l.quantity_out, 0)) AS sisa, MIN(b.expiry_date) AS exp "
        "FROM inventory_ledger l LEFT JOIN item_batches b ON b.id = l.batch_id AND b.tenant_id = l.tenant_id "
        "WHERE l.tenant_id = $1 AND l.source_id = $2 AND l.warehouse_id = $3 AND l.product_id = $4 AND l.batch_id IS NOT NULL "
        "GROUP BY l.batch_id HAVING SUM(COALESCE(l.quantity_in, 0) - COALESCE(l.quantity_out, 0)) > 0 ORDER BY exp NULLS LAST, l.batch_id",
        tid, transfer_id, transit_id, item_id)
    if not rows:
        return [(qty, nilai, None)]
    hasil, sisa_q, sisa_v = [], qty, nilai
    for r in rows:
        if sisa_q <= _NOL:
            break
        q = min(_d(r["sisa"]), sisa_q)
        v = sisa_v if q == sisa_q else (nilai * q / qty).quantize(Decimal("0.01"))
        hasil.append((q, v, r["batch_id"]))
        sisa_q -= q
        sisa_v -= v
    if sisa_q > _NOL:
        hasil.append((sisa_q, sisa_v, None))
    return hasil


async def terima(conn, ctx: dict, transfer_id, baris: Optional[List[dict]] = None) -> dict:
    tid, uid = ctx["tenant_id"], ctx.get("user_id")
    h = await pb._draf_terkunci(conn, tid, transfer_id)
    items = await item_baris(conn, tid, transfer_id)
    if h["status"] == "received" and not baris:
        return {"ulang": True, "catatan_fase": None}
    rencana = await rencana_terima(conn, tid, h, items, baris, kunci=True)
    pb.angkat_blok_pertama(rencana)
    by_item = {i["item_id"]: i for i in items}
    transit_id = await GT.id_transit(conn, tid)
    hari = await tanggal_dokumen(conn, tid)
    asal_nama = await conn.fetchval("SELECT name FROM warehouses WHERE id = $1 AND tenant_id = $2", h["from_warehouse_id"], tid)
    tujuan = rencana["tujuan"]
    total = _NOL
    for ln in rencana["lines"]:
        a = ln["akan_diterima"]
        if a <= _NOL:
            continue
        it = by_item[ln["item_id"]]
        wac_kini = _d(await conn.fetchval("SELECT get_weighted_average_cost($1, $2)", tid, ln["item_id"]))
        saldo = await pb._saldo_produk(conn, tid, ln["item_id"])
        nilai = (a * _d(it["unit_cost"])).quantize(Decimal("0.01"))
        kw = dict(product_id=ln["item_id"], kode=ln["item_code"], nama=ln["name"], sumber=SUMBER_TERIMA, tanggal=hari, transfer_id=transfer_id,
                  nomor=h["transfer_number"], qty=a, unit_cost=_d(it["unit_cost"]), total=nilai, wac=wac_kini)
        potong = await potong_terima(conn, tid, transfer_id, transit_id, ln["item_id"], a, nilai)
        for q_b, v_b, bid in potong:   # BUG-453: batch dibaca dari kartu Transit pemindahan INI (bukan FEFO ulang), Transit -> tujuan
            kw.update(qty=q_b, total=v_b)
            await pb._tulis_ledger(conn, tid, uid, tipe=TERIMA_KELUAR, masuk=False, saldo=saldo - q_b, gudang_id=transit_id,
                                   catatan=f"Terima di {tujuan['name']} (keluar Transit)", batch_id=bid, **kw)
            await pb._tulis_ledger(conn, tid, uid, tipe=TERIMA_MASUK, masuk=True, saldo=saldo, gudang_id=h["to_warehouse_id"],
                                   catatan=f"Terima dari {asal_nama} (via Transit)", batch_id=bid, **kw)
        total += nilai
    if rencana["tuntas"]:   # satu-satunya tulisan header sesudah Kirim yang diizinkan trigger (status berubah + received_date/by)
        await conn.execute("UPDATE stock_transfers SET status = 'received', received_date = $3, received_by = $4 WHERE id = $1 AND tenant_id = $2",
                           transfer_id, tid, hari, uid)
    await catat_riwayat(conn, tid, "stock_transfers", transfer_id, h["transfer_number"], "PEMINDAHAN_BARANG_DITERIMA", uid,
                        f"Pemindahan {h['transfer_number']} diterima di {tujuan['name']}"
                        f"{'' if rencana['tuntas'] else ' (sebagian; sisa masih di Transit)'}",
                        {"qty": str(rencana["total_quantity"]), "nilai": str(total), "tuntas": rencana["tuntas"]}, source="api:stock_transfers.receive")
    catatan = await pb._fase_so(conn, ctx, h, "tersedia", h["to_warehouse_id"]) if rencana["tuntas"] else None
    return {"ulang": False, "catatan_fase": catatan, "peringatan": rencana["peringatan"]}


# ============================================================================
# VOID kirim (hanya yang masih di Transit dan belum ada penerimaan)
# ============================================================================
async def rencana_void_kirim(conn, tid: str, h, kunci: bool) -> dict:
    blocks: list = []
    items = await item_baris(conn, tid, h["id"])
    asli = []
    if h["status"] == "received":
        blocks.append(_blok("PB_DUA_LANGKAH_SUDAH_DITERIMA", f"Pemindahan {h['transfer_number']} sudah diterima; buat pemindahan balik untuk mengembalikan barangnya."))
    elif h["status"] != "in_transit":
        blocks.append(_blok("PB_STATUS_TIDAK_BOLEH", f"Pemindahan {h['transfer_number']} berstatus {h['status']}; hanya yang sedang dikirim bisa di-void."))
    elif any(_d(i.get("quantity_resolved")) > _NOL for i in items):
        blocks.append(_blok("PB_SUDAH_ADA_PENYELESAIAN", f"Pemindahan {h['transfer_number']} punya selisih yang sudah diselesaikan lewat Penyesuaian; batalkan penyesuaiannya dulu."))
    elif (dit := [i for i in items if _d(i["quantity_received"]) > _NOL]):
        # BUG-386 (putusan MASTER 26 Nov, pola Accurate/SAP): sudah ada penerimaan (sebagian/tuntas) -> batal DITOLAK; kalimat menyebut yang sudah diterima.
        from .barang_daftar import _q
        tujuan = await conn.fetchval("SELECT name FROM warehouses WHERE id = $1 AND tenant_id = $2", h["to_warehouse_id"], tid) or "gudang tujuan"
        sebut = ", ".join(f"{_q(_d(i['quantity_received']))}{' ' + i['unit'] if i.get('unit') else ''} {i['item_name']}" for i in dit)
        blocks.append(_blok("PB_VOID_SEBAGIAN_DITERIMA", f"{sebut} sudah diterima di {tujuan}; pembatalan tidak bisa. Terima sisanya atau buat pemindahan balik."))
    else:
        asli = await conn.fetch("SELECT * FROM inventory_ledger WHERE tenant_id = $1 AND source_type = $2 AND source_id = $3 ORDER BY created_at, id",
                                tid, SUMBER_KIRIM, h["id"])
        if not asli:
            blocks.append(_blok("PB_LEDGER_TAK_ADA", "Baris kartu stok pengiriman ini tidak ditemukan; tak bisa dibalik."))
        elif await conn.fetchval("SELECT 1 FROM inventory_ledger WHERE tenant_id = $1 AND source_type = $2 AND source_id = $3 LIMIT 1",
                                 tid, SUMBER_KIRIM_BATAL, h["id"]):
            blocks.append(_blok("PB_SUDAH_DIBALIK", "Kartu stok pengiriman ini sudah pernah dibalik."))
    return {"blocks": blocks, "asli": list(asli), "lines": [{"item_id": i["item_id"], "name": i["item_name"], "quantity": _d(i["quantity_shipped"])} for i in items]}


async def void_kirim(conn, ctx: dict, transfer_id, alasan: str) -> dict:
    tid, uid = ctx["tenant_id"], ctx.get("user_id")
    h = await pb._draf_terkunci(conn, tid, transfer_id)
    rencana = await rencana_void_kirim(conn, tid, h, kunci=True)
    pb.angkat_blok_pertama(rencana)
    hari = await tanggal_dokumen(conn, tid)
    for r in sorted(rencana["asli"], key=lambda r: 0 if _d(r["quantity_in"]) > _NOL else 1):   # kaki masuk Transit dibalik jadi KELUAR lebih dulu
        masuk_asli = _d(r["quantity_in"]) > _NOL
        qty = _d(r["quantity_in"] if masuk_asli else r["quantity_out"])
        wac_kini = _d(await conn.fetchval("SELECT get_weighted_average_cost($1, $2)", tid, r["product_id"]))
        saldo = await pb._saldo_produk(conn, tid, r["product_id"])
        await pb._tulis_ledger(conn, tid, uid, product_id=r["product_id"], kode=r["product_code"], nama=r["product_name"], tipe=SUMBER_KIRIM_BATAL,
                               sumber=SUMBER_KIRIM_BATAL, tanggal=hari, transfer_id=transfer_id, nomor=h["transfer_number"], qty=qty,
                               masuk=not masuk_asli, saldo=saldo + (qty if not masuk_asli else -qty), unit_cost=_d(r["unit_cost"]),
                               total=_d(r["total_cost"]), wac=wac_kini, gudang_id=r["warehouse_id"], catatan=f"Void pengiriman {h['transfer_number']}: {alasan}",
                               batch_id=r.get("batch_id"))   # BUG-453: pembalik per baris kartu kiriman, batch ikut
    await conn.execute("UPDATE stock_transfers SET status = 'voided', voided_at = NOW(), voided_by = $3, void_reason = $4, updated_at = NOW() WHERE id = $1 AND tenant_id = $2",
                       transfer_id, tid, uid, alasan)
    await catat_riwayat(conn, tid, "stock_transfers", transfer_id, h["transfer_number"], "PEMINDAHAN_BARANG_DI_VOID", uid,
                        f"Pemindahan {h['transfer_number']} di-void (barang kembali dari Transit): {alasan}", {"alasan": alasan}, source="api:stock_transfers.void")
    return {"ulang": False, "catatan_fase": None}


async def pratinjau_void_kirim(conn, ctx: dict, transfer_id, alasan: Optional[str]) -> dict:
    tid = ctx["tenant_id"]
    h = await pb._header(conn, tid, transfer_id)
    r = await rencana_void_kirim(conn, tid, h, kunci=False)
    if not (alasan or "").strip():
        r["blocks"].append(_blok("PB_ALASAN_WAJIB", "Alasan void wajib diisi.", 422))
    return {"ok": not r["blocks"], "blocks": r["blocks"], "lines": r["lines"], "dari": None, "ke": None,
            "total_quantity": sum((l["quantity"] for l in r["lines"]), _NOL), "total_value": _NOL, "sales_order": None, "catatan_fase": None,
            "transfer_number": h["transfer_number"], "ulang": h["status"] == "voided", "payload": {"transfer_id": str(transfer_id), "reason": alasan},
            "catatan": "Barang kembali dari Transit ke gudang asal; tanpa jurnal."}
