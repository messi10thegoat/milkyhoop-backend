"""Ubah Pesanan Penjualan yang SUDAH DIKONFIRMASI -- pola NetSuite (4 Okt 2026; putusan pemilik LANGSUNG di sesi
BACKEND, desain + ukur dikirim ke MASTER dulu).

Putusan pemilik:
  * baris yang belum terkirim/terfaktur boleh ditambah/diubah/dihapus; qty tak boleh di bawah yang sudah
    terkirim/terfaktur; baris yang terpakai TERKUNCI (harga, diskon, pajak, barang, deskripsi);
  * total baru di bawah yang sudah ditagih/diterima (proforma terbit, uang muka, faktur) -> DITOLAK;
  * proforma terbit TIDAK diubah (dokumen di tangan pelanggan) -> peringatan di pratinjau;
  * baris yang ada di FAKTUR DRAF -> terkunci juga, dan pratinjau menyebut faktur drafnya (id + nomor) per baris;
  * pelanggan & nomor SO terkunci; kode order tetap; semua di belakang flag tenant `so_edit_confirmed`.

Diukur 4 Okt (prod, baca saja) sebelum menulis ini:
  * PATCH draf mengganti baris dengan DELETE-semua + INSERT. Di SO terkonfirmasi itu FATAL: FK
    sales_order_shipment_items (tanpa ON DELETE) -> galat; FK sales_invoice_items.sales_order_item_id ON DELETE SET
    NULL -> tautan faktur PUTUS DIAM. Jalur ini mengubah baris DI TEMPAT per id (diff), tak pernah hapus-semua.
  * sales_order_items.quantity_shipped MATI sejak V264 -> terkirim per baris = so_kirim.terkirim_per_baris.
  * quantity_invoiced bisa melebihi Σ baris faktur tertaut (diisi tanpa tautan, mis. grapgrap 038-09-26) ->
    terpakai = MAX(kolom, Σ tertaut, terkirim).
  * trg_update_so_status hanya AFTER UPDATE -> INSERT/DELETE baris tak menghitung ulang status -> dihitung ulang
    eksplisit di akhir (compute_so_status + terapkan_selesai_so, sama dengan trigger).
  * SO tak memposting jurnal (0 jurnal ber-source SO) -> dampak jurnal NOL. Faktur terbit tak disentuh (Law 19).
"""
from decimal import Decimal, ROUND_HALF_UP
import uuid as uuid_module

from fastapi import HTTPException
from . import teks_galat as tg

FLAG = "so_edit_confirmed"
STATUS_BOLEH = ("confirmed", "partial_shipped", "shipped", "partial_invoiced", "invoiced")
# medan header yang BOLEH diubah di SO terkonfirmasi (bukan uang dokumen)
HEADER_BEBAS = ("order_date", "expected_ship_date", "reference", "shipping_address", "shipping_method", "notes",
                "internal_notes", "payment_terms", "payment_bank_name", "payment_account_number",
                "payment_account_holder")
HEADER_UANG = ("discount_amount", "shipping_amount", "shipping_tax_code_id")  # hanya bila belum ada faktur
HEADER_KUNCI = ("customer_id", "customer_name", "order_number")
MEDAN_BARIS = ("item_id", "description", "quantity", "unit", "unit_price", "discount_percent", "tax_id",
               "warehouse_id", "sort_order")
KUNCI_BARIS = ("item_id", "description", "unit", "unit_price", "discount_percent", "tax_id", "warehouse_id")
NOL = Decimal("0")


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else NOL


def _rp(x) -> str:
    return tg.rp(x)  # Rupiah baku (services/teks_galat); dulu dibulatkan ke rupiah utuh
def _sama(a, b) -> bool:
    """Perbandingan nilai baris tersimpan vs kiriman (uuid/str/angka)."""
    if a is None or b is None:
        return (a is None or a == "") and (b is None or b == "")
    if isinstance(a, (int, float, Decimal)) or isinstance(b, (int, float, Decimal)):
        try:
            return _d(a) == _d(b)
        except Exception:
            return False
    return str(a).strip() == str(b).strip()


def galat(status: int, code: str, message: str, **lain):
    raise HTTPException(status_code=status, detail={"code": code, "message": message, **lain})


async def flag_aktif(conn, tenant_id: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM tenant_features WHERE tenant_id = $1 AND feature = $2 AND enabled", tenant_id, FLAG))


async def pemakaian_baris(conn, tenant_id: str, so_id) -> dict:
    """{soi_id: {terfaktur, terkirim, terpakai, draf: [{id, invoice_number, quantity}], draf_qty, batal: [{id, invoice_number}]}}
    untuk SEMUA baris SO. `batal` = faktur VOID yang menaut baris: baris tetap boleh diubah, tapi TAK boleh dihapus
    (FK ON DELETE SET NULL akan memutus riwayat faktur batal itu diam-diam; terukur 4 Okt: INV-2609-0104 kaos)."""
    from . import so_kirim
    rows = await conn.fetch(
        """SELECT soi.id, soi.quantity_invoiced,
                  COALESCE((SELECT SUM(sii.quantity) FROM sales_invoice_items sii
                            JOIN sales_invoices si ON si.id = sii.invoice_id AND si.tenant_id = $1
                            WHERE sii.sales_order_item_id = soi.id AND si.status NOT IN ('void', 'draft')), 0) AS tertaut
           FROM sales_order_items soi
           JOIN sales_orders so ON so.id = soi.sales_order_id AND so.tenant_id = $1
           WHERE soi.sales_order_id = $2""",
        tenant_id, so_id)
    draf = await conn.fetch(
        """SELECT sii.sales_order_item_id AS soi_id, si.id, si.invoice_number, SUM(sii.quantity) AS quantity
           FROM sales_invoice_items sii
           JOIN sales_invoices si ON si.id = sii.invoice_id AND si.tenant_id = $1
           JOIN sales_order_items soi ON soi.id = sii.sales_order_item_id AND soi.sales_order_id = $2
           WHERE si.status = 'draft'
           GROUP BY 1, 2, 3 ORDER BY si.invoice_number""",
        tenant_id, so_id)
    batal = await conn.fetch(
        """SELECT DISTINCT sii.sales_order_item_id AS soi_id, si.id, si.invoice_number
           FROM sales_invoice_items sii
           JOIN sales_invoices si ON si.id = sii.invoice_id AND si.tenant_id = $1
           JOIN sales_order_items soi ON soi.id = sii.sales_order_item_id AND soi.sales_order_id = $2
           WHERE si.status = 'void' ORDER BY si.invoice_number""",
        tenant_id, so_id)
    terkirim, tanpa_tautan = await so_kirim.terkirim_per_baris(conn, tenant_id, [so_id])
    draf_per_baris = {}
    for x in draf:
        draf_per_baris[x["soi_id"]] = draf_per_baris.get(x["soi_id"], NOL) + _d(x["quantity"])
    hasil = {}
    for r in rows:
        k = _d(terkirim.get(r["id"]))
        # 5 Okt 2026 (MASTER GO): kolom quantity_invoiced SUDAH memuat qty faktur DRAF bertaut (SO jadi 'invoiced' sejak
        # draf, pola Q-016) -- dulu draf terhitung DUA KALI (kolom + draf_qty): SO-2609-0166 kaos min 126 utk qty 63.
        # Aturan pemilik: qty >= max(terfaktur HIDUP, terkirim) + draf. Kolom tetap jadi pagar faktur lama tak bertaut.
        f = max(_d(r["quantity_invoiced"]) - draf_per_baris.get(r["id"], NOL), _d(r["tertaut"]))
        hasil[r["id"]] = {"terfaktur": f, "terkirim": k, "terpakai": max(f, k), "draf": [], "draf_qty": NOL, "batal": []}
    for x in draf:
        h = hasil.get(x["soi_id"])
        if h is not None:
            h["draf"].append({"id": str(x["id"]), "invoice_number": x["invoice_number"], "quantity": float(x["quantity"])})
            h["draf_qty"] += _d(x["quantity"])
    for x in batal:
        h = hasil.get(x["soi_id"])
        if h is not None:
            h["batal"].append({"id": str(x["id"]), "invoice_number": x["invoice_number"]})
    return {"baris": hasil, "terkirim_tanpa_tautan": sum(tanpa_tautan.values(), NOL)}


async def batas_bawah_total(conn, tenant_id: str, so_id, total_lama) -> dict:
    """Uang yang SUDAH terikat pada SO: total baru tak boleh di bawah MAX ketiganya (putusan pemilik: tolak)."""
    from ..routers.customer_deposits import received_total_for_order
    from ..routers.proformas import rincian_tagih
    dp = _d(await received_total_for_order(conn, tenant_id, so_id))
    r = await rincian_tagih(conn, tenant_id, so_id, float(total_lama))
    tagih = _d(r["issued_total"]) + _d(r["received_not_billed"])
    faktur = _d(await conn.fetchval(
        """SELECT COALESCE(SUM(total_amount), 0) FROM sales_invoices
           WHERE tenant_id = $1 AND sales_order_id = $2 AND status NOT IN ('void', 'draft')""", tenant_id, so_id))
    return {"uang_muka_diterima": dp, "proforma_terbit": _d(r["issued_total"]),
            "uang_muka_di_luar_tagihan": _d(r["received_not_billed"]), "tertagih_proforma": tagih,
            "faktur_terbit": faktur, "batas": max(dp, tagih, faktur)}


async def ubah(conn, ctx: dict, so: dict, body, hitung) -> dict:
    """Jalur TUNGGAL (PATCH asli DAN pratinjau yang di-rollback). Pemanggil memegang transaksi + FOR UPDATE baris SO.
    `hitung(conn, tenant, src_items, discount, shipping, shipping_code)` -> dokumen terhitung (kalkulator SO yang sama
    dengan jalur draf). Kembalikan rencana + hasil; SEMUA tulis terjadi di sini."""
    from .default_dokumen import hitung_dp
    from .so_riwayat import catat_riwayat
    tid, so_id = ctx["tenant_id"], so["id"]
    if so["status"] not in STATUS_BOLEH:
        galat(409, "SO_NOT_EDITABLE", f"Pesanan berstatus '{so['status']}' tidak bisa diubah.")
    # kunci yang SAMA dengan buat/terbit proforma -> plafon tagihan & ubah SO ter-serialkan (uang muka: FOR UPDATE SO)
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"PROFORMA_SO:{tid}:{so_id}")

    fs = set(body.model_fields_set) - {"order_title"}
    kunci = sorted(fs & set(HEADER_KUNCI))
    kunci = [k for k in kunci if not _sama(getattr(body, k), so.get(k))]
    if kunci:
        galat(409, "SO_FIELD_LOCKED", "Pelanggan dan nomor pesanan tidak bisa diubah setelah dikonfirmasi.", fields=kunci)
    dikenal = set(HEADER_BEBAS) | set(HEADER_UANG) | set(HEADER_KUNCI) | {"items", "dp_percent", "dp_amount"}
    asing = sorted(fs - dikenal)
    if asing:
        galat(422, "SO_FIELD_UNKNOWN", "Medan tidak dikenal.", fields=asing)

    pakai = await pemakaian_baris(conn, tid, so_id)
    lama = [dict(r) for r in await conn.fetch(
        """SELECT soi.id, soi.item_id, soi.description, soi.quantity, soi.unit, soi.unit_price, soi.discount_percent,
                  soi.tax_id, soi.tax_rate, soi.tax_amount, soi.line_total, soi.dpp, soi.warehouse_id, soi.sort_order
           FROM sales_order_items soi JOIN sales_orders so ON so.id = soi.sales_order_id AND so.tenant_id = $1
           WHERE soi.sales_order_id = $2 ORDER BY soi.sort_order, soi.id""", tid, so_id)]
    per_id = {str(r["id"]): r for r in lama}

    def info(r):
        p = pakai["baris"].get(r["id"], {"terfaktur": NOL, "terkirim": NOL, "terpakai": NOL, "draf": [], "draf_qty": NOL, "batal": []})
        minimum = p["terpakai"] + p["draf_qty"]
        alasan = []
        if p["terfaktur"] > 0:
            alasan.append("terfaktur")
        if p["terkirim"] > 0:
            alasan.append("terkirim")
        if p["draf"]:
            alasan.append("faktur_draf")
        return p, minimum, alasan

    ada_faktur = bool(await conn.fetchval(
        "SELECT 1 FROM sales_invoices WHERE tenant_id = $1 AND sales_order_id = $2 AND status <> 'void' LIMIT 1", tid, so_id))
    uang_hdr = sorted(k for k in fs & set(HEADER_UANG) if not _sama(getattr(body, k), so.get(k)))
    if uang_hdr and ada_faktur:
        galat(409, "SO_DOC_MONEY_LOCKED", "Diskon dan ongkir pesanan tidak bisa diubah setelah ada faktur.", fields=uang_hdr)

    # ---- rencana baris (diff per id; tak pernah hapus-semua) ----
    ubahan, tambah, hapus, src = [], [], [], []
    if body.items is not None:
        if pakai["terkirim_tanpa_tautan"] > 0:
            galat(409, "SO_SHIPPED_UNLINKED",
                  "Sebagian pengiriman pesanan ini tak tertaut ke baris mana pun, jadi baris tidak bisa diubah.")
        dikirim = set()
        for i, it in enumerate(body.items):
            d = it.model_dump(exclude_unset=True)
            if it.id:
                r = per_id.get(str(it.id))
                if r is None:
                    galat(422, "SO_LINE_UNKNOWN", "Baris tidak ditemukan di pesanan ini.", line_id=str(it.id))
                if str(it.id) in dikirim:
                    galat(422, "SO_LINE_DUPLICATE", "Baris dikirim dua kali.", line_id=str(it.id))
                dikirim.add(str(it.id))
                baru = {k: r[k] for k in ("id", "tax_rate") + MEDAN_BARIS}
                for k in MEDAN_BARIS:
                    if k in d:
                        baru[k] = d[k]
                baru["_turunkan"] = not _sama(r["tax_id"], baru["tax_id"])
                if "sort_order" not in d:
                    baru["sort_order"] = i
                p, minimum, alasan = info(r)
                beda = {k: [r[k], baru[k]] for k in MEDAN_BARIS if k != "sort_order" and not _sama(r[k], baru[k])}
                if alasan:
                    terkunci = sorted(k for k in beda if k in KUNCI_BARIS)
                    if terkunci:
                        galat(409, "SO_LINE_LOCKED", f"Baris '{r['description']}' sudah "
                              + _alasan_teks(alasan, p) + " -- hanya jumlahnya yang bisa dinaikkan.",
                              line_id=str(r["id"]), fields=terkunci, draft_invoices=p["draf"])
                    if _d(baru["quantity"]) < minimum:
                        galat(409, "SO_LINE_QTY_BELOW_USED",
                              f"Jumlah baris '{r['description']}' tidak bisa di bawah {_q(minimum)} ("
                              + _alasan_teks(alasan, p) + ").", line_id=str(r["id"]), minimum=float(minimum),
                              draft_invoices=p["draf"])
                if beda:
                    ubahan.append({"id": r["id"], "description": r["description"], "changes": beda})
                src.append(baru)
            else:
                for wajib in ("description", "quantity", "unit_price"):
                    if d.get(wajib) in (None, ""):
                        galat(422, "SO_LINE_INCOMPLETE", f"Baris baru wajib mengisi {wajib}.", field=wajib)
                baru = {"id": None, **{k: d.get(k) for k in MEDAN_BARIS}, "tax_rate": d.get("tax_rate") or 0,
                        "_turunkan": True}
                baru["discount_percent"] = baru["discount_percent"] or 0
                if baru["sort_order"] is None:
                    baru["sort_order"] = i
                tambah.append({"description": baru["description"], "quantity": float(_d(baru["quantity"]))})
                src.append(baru)
        for r in lama:
            if str(r["id"]) not in dikirim:
                p, minimum, alasan = info(r)
                if alasan:
                    galat(409, "SO_LINE_LOCKED", f"Baris '{r['description']}' sudah " + _alasan_teks(alasan, p)
                          + " -- tidak bisa dihapus.", line_id=str(r["id"]), draft_invoices=p["draf"])
                if p["batal"]:
                    galat(409, "SO_LINE_HAS_VOID_INVOICE", f"Baris '{r['description']}' pernah ditagih di faktur batal "
                          + ", ".join(x["invoice_number"] for x in p["batal"]) + " -- tidak bisa dihapus (riwayat faktur "
                          "batal menunjuk baris ini). Ubah jumlah atau harganya saja.", line_id=str(r["id"]),
                          void_invoices=p["batal"])
                hapus.append({"id": r["id"], "description": r["description"]})
        if not src:
            galat(422, "SO_NO_LINES", "Pesanan harus punya minimal satu baris.")
    else:
        src = [{**{k: r[k] for k in ("id", "tax_rate") + MEDAN_BARIS}, "_turunkan": False} for r in lama]

    # badan PATCH yang DINORMALKAN server (permintaan WORKSPACE 4 Okt, pola Terima pelunasan): FE mengirim balik apa
    # adanya; baris lengkap ber-id (baris lama) / tanpa id (baru), header hanya medan yang dikirim.
    payload = {k: _js(getattr(body, k)) for k in sorted(fs - {"items"})}
    if body.items is not None:
        payload["items"] = [{**({"id": str(x["id"])} if x["id"] else {}),
                             **{k: _js(x.get(k)) for k in MEDAN_BARIS}} for x in src]

    # ---- hitung ulang dengan kalkulator SO yang sama ----
    disc = body.discount_amount if "discount_amount" in fs else so["discount_amount"]
    ship = body.shipping_amount if "shipping_amount" in fs else so["shipping_amount"]
    ship_code = ((body.shipping_tax_code_id or None) if "shipping_tax_code_id" in fs
                 else (str(so["shipping_tax_code_id"]) if so["shipping_tax_code_id"] else None))
    from .tax_factor import turunkan_tarif_baris
    for s in src:  # kalkulator & penurun tarif bekerja dengan teks id
        for k in ("item_id", "tax_id", "warehouse_id"):
            s[k] = str(s[k]) if s.get(k) else None
    # #34 tarif dari kode pajak -- HANYA baris baru / berganti kode (baris lama tetap tarif tersimpannya, sama dengan
    # jalur draf saat baris tak dikirim; tarif kode yang berubah sejak itu tak boleh menggeser baris terpakai)
    turun = [s for s in src if s.pop("_turunkan")]
    if turun:
        await turunkan_tarif_baris(conn, tid, turun, "tax_id")
    doc = await hitung(conn, tid, src, disc, ship, ship_code)
    hitungan = doc["items"]
    # baris TERPAKAI tak boleh berubah nilainya (diskon/ongkir dokumen bisa membagi ulang PPN baris)
    for s, h in zip(src, hitungan):
        if s["id"] is None:
            continue
        r = per_id[str(s["id"])]
        p, minimum, alasan = info(r)
        if alasan and _d(s["quantity"]) == _d(r["quantity"]) and not (
                _d(h["tax_amount"]) == _d(r["tax_amount"]) and _d(h["line_total"]) == _d(r["line_total"])):
            galat(409, "SO_LINE_AMOUNT_SHIFT",
                  f"Perubahan ini menggeser nilai baris '{r['description']}' yang sudah " + _alasan_teks(alasan, p)
                  + " (diskon/ongkir pesanan dibagi ulang ke baris).", line_id=str(r["id"]))

    total_lama, total_baru = _d(so["total_amount"]), _d(doc["total_amount"])
    batas = await batas_bawah_total(conn, tid, so_id, total_lama)
    if total_baru < total_lama and total_baru < batas["batas"]:
        galat(409, "SO_TOTAL_BELOW_COMMITTED",
              f"Nilai pesanan baru {_rp(total_baru)} di bawah yang sudah ditagih/diterima: uang muka diterima "
              f"{_rp(batas['uang_muka_diterima'])}, proforma terbit {_rp(batas['proforma_terbit'])}"
              f" (+ uang muka di luar tagihan {_rp(batas['uang_muka_di_luar_tagihan'])}), faktur terbit "
              f"{_rp(batas['faktur_terbit'])}. Minimal {_rp(batas['batas'])}.",
              minimum_total=float(batas["batas"]), new_total=float(total_baru))

    # ---- uang muka (aturan sama dengan jalur draf) ----
    dp_kirim = {"dp_percent", "dp_amount"} & fs
    dp = None
    if dp_kirim:
        pct = body.dp_percent if "dp_percent" in fs else so["dp_percent"]
        amt = body.dp_amount if "dp_amount" in fs else (so["dp_amount"] if so["dp_amount_source"] == "manual" else None)
        dp = hitung_dp(total_baru, pct, amt)
    elif total_baru != total_lama and so["dp_amount_source"] == "percent" and so["dp_percent"] is not None:
        dp = hitung_dp(total_baru, so["dp_percent"], None)
    dp_akhir = dp["dp_amount"] if dp is not None else so["dp_amount"]
    if dp_akhir is not None and _d(dp_akhir) > total_baru:
        galat(409, "SO_DP_ABOVE_TOTAL", f"Uang muka {_rp(dp_akhir)} melebihi nilai pesanan baru {_rp(total_baru)}.")

    # ---- peringatan proforma terbit (TIDAK diubah) ----
    peringatan = []
    if total_baru != total_lama:
        for pf in await conn.fetch(
                """SELECT id, proforma_number, amount, percent_of_order FROM proformas
                   WHERE tenant_id = $1 AND sales_order_id = $2 AND status = 'issued' ORDER BY created_at""", tid, so_id):
            pesan = f"Proforma {pf['proforma_number']} ({_rp(pf['amount'])}) tidak berubah."
            if pf["percent_of_order"] is not None:
                seharusnya = (total_baru * _d(pf["percent_of_order"]) / Decimal(100)).quantize(Decimal("1"), ROUND_HALF_UP)
                if seharusnya != _d(pf["amount"]).quantize(Decimal("1"), ROUND_HALF_UP):
                    pesan = (f"Proforma {pf['proforma_number']} tetap {_rp(pf['amount'])}; {_q(pf['percent_of_order'])}% "
                             f"dari nilai baru = {_rp(seharusnya)}. Buat proforma baru bila perlu.")
            peringatan.append({"code": "PROFORMA_UNCHANGED", "proforma_id": str(pf["id"]), "message": pesan})

    # ---- TULIS (di tempat, per id) ----
    if body.items is not None:
        for x in hapus:
            n = await conn.execute(
                """DELETE FROM sales_order_items WHERE id = $1 AND sales_order_id = $2
                   AND NOT EXISTS (SELECT 1 FROM sales_invoice_items WHERE sales_order_item_id = $1)""", x["id"], so_id)
            if n != "DELETE 1":  # dibaca di bawah kunci, tapi faktur bisa menautnya lewat jalur lain -> jangan putuskan
                galat(409, "SO_LINE_LOCKED", f"Baris '{x['description']}' sudah ditautkan ke faktur.", line_id=str(x["id"]))
    for s, h in zip(src, hitungan):
        nilai = (uuid_module.UUID(s["item_id"]) if s["item_id"] else None, s["description"], s["quantity"],
                 s.get("unit"), s["unit_price"], s.get("discount_percent") or 0,
                 uuid_module.UUID(s["tax_id"]) if s["tax_id"] else None, s.get("tax_rate") or 0,
                 h["tax_amount"], h["line_total"], uuid_module.UUID(s["warehouse_id"]) if s["warehouse_id"] else None,
                 s["sort_order"], h["dpp"])
        if s["id"] is None:
            await conn.execute(
                """INSERT INTO sales_order_items (id, sales_order_id, item_id, description, quantity, unit, unit_price,
                       discount_percent, tax_id, tax_rate, tax_amount, line_total, warehouse_id, sort_order, dpp)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)""",
                uuid_module.uuid4(), so_id, *nilai)
        else:
            await conn.execute(
                """UPDATE sales_order_items SET item_id=$3, description=$4, quantity=$5, unit=$6, unit_price=$7,
                       discount_percent=$8, tax_id=$9, tax_rate=$10, tax_amount=$11, line_total=$12, warehouse_id=$13,
                       sort_order=$14, dpp=$15
                   WHERE id = $1 AND sales_order_id = $2""", s["id"], so_id, *nilai)

    set_, param = [], []
    def pasang(kolom, nilai):
        param.append(nilai)
        set_.append(f"{kolom} = ${len(param)}")
    for k in HEADER_BEBAS:
        if k in fs:
            pasang(k, getattr(body, k))
    for k in HEADER_UANG:
        if k in fs:
            v = getattr(body, k)
            pasang(k, uuid_module.UUID(v) if (k == "shipping_tax_code_id" and v) else v)
    for k, v in (("subtotal", doc["net_subtotal"]), ("tax_amount", doc["tax_amount"]), ("total_amount", doc["total_amount"]),
                 ("shipping_tax_rate", doc["shipping_tax_rate"]), ("shipping_tax_amount", doc["shipping_tax_amount"]),
                 ("shipping_dpp", doc["shipping_dpp"])):
        pasang(k, v)
    if dp is not None:
        for k in ("dp_percent", "dp_amount", "dp_amount_source"):
            pasang(k, dp[k])
    param += [so_id, tid]
    await conn.execute(f"UPDATE sales_orders SET {', '.join(set_)} WHERE id = ${len(param) - 1} AND tenant_id = ${len(param)}",
                       *param)
    # status: trigger baris hanya AFTER UPDATE -> INSERT/DELETE tak menghitung ulang; ulangi rumus trigger di sini
    await conn.execute(
        """UPDATE sales_orders so SET invoiced_qty = x.inv, status = compute_so_status(x.inv, so.shipped_qty, x.ord, so.status)
           FROM (SELECT COALESCE(SUM(quantity), 0) AS ord, COALESCE(SUM(quantity_invoiced), 0) AS inv
                 FROM sales_order_items WHERE sales_order_id = $1) x
           WHERE so.id = $1 AND so.tenant_id = $2""", so_id, tid)
    await conn.execute("SELECT terapkan_selesai_so($1)", so_id)
    akhir = await conn.fetchrow("SELECT status, total_amount, order_code FROM sales_orders WHERE id = $1 AND tenant_id = $2",
                                so_id, tid)

    medan = sorted((fs - {"items"}) | ({"items"} if body.items is not None else set()))
    meta = {"fields": medan, "confirmed_edit": True,
            "lines": {"added": [x["description"] for x in tambah], "removed": [x["description"] for x in hapus],
                      "changed": [{"description": x["description"],
                                   "changes": {k: [_js(a), _js(b)] for k, (a, b) in x["changes"].items()}} for x in ubahan]},
            "total": [float(total_lama), float(total_baru)]}
    await catat_riwayat(conn, tid, "sales_orders", so_id, so["order_number"], "SALES_ORDER_UPDATED", ctx["user_id"],
                        "Pesanan diubah (" + ", ".join(medan or ["-"]) + ")", meta, source="api:sales_orders.update_confirmed")

    baris_akhir = []
    pakai2 = await pemakaian_baris(conn, tid, so_id)
    for r in await conn.fetch("SELECT id, description, quantity FROM sales_order_items WHERE sales_order_id = $1 "
                              "ORDER BY sort_order, id", so_id):
        p = pakai2["baris"].get(r["id"], {"terfaktur": NOL, "terkirim": NOL, "terpakai": NOL, "draf": [], "draf_qty": NOL, "batal": []})
        alasan = [a for a, ada in (("terfaktur", p["terfaktur"] > 0), ("terkirim", p["terkirim"] > 0),
                                   ("faktur_draf", bool(p["draf"]))) if ada]
        baris_akhir.append({"id": str(r["id"]), "description": r["description"], "quantity": float(r["quantity"]),
                            "min_quantity": float(p["terpakai"] + p["draf_qty"]), "locked": bool(alasan),
                            "locked_reasons": alasan, "quantity_invoiced": float(p["terfaktur"]),
                            "quantity_shipped": float(p["terkirim"]), "draft_invoices": p["draf"],
                            "void_invoices": p["batal"], "deletable": not alasan and not p["batal"]})
    return {"id": str(so_id), "order_number": so["order_number"], "status": akhir["status"],
            "order_code": akhir["order_code"], "old_total": float(total_lama), "new_total": float(akhir["total_amount"]),
            "minimum_total": float(batas["batas"]),
            "changes": {"added": tambah, "removed": [{"id": str(x["id"]), "description": x["description"]} for x in hapus],
                        "changed": [{"id": str(x["id"]), "description": x["description"],
                                     "changes": {k: [_js(a), _js(b)] for k, (a, b) in x["changes"].items()}} for x in ubahan],
                        "fields": medan},
            "lines": baris_akhir, "warnings": peringatan, "payload": payload}


def _q(x) -> str:
    return f"{_d(x).normalize():f}"


def _js(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, uuid_module.UUID):
        return str(v)
    return v


def _alasan_teks(alasan: list, p: dict) -> str:
    bag = []
    if "terfaktur" in alasan:
        bag.append(f"terfaktur {_q(p['terfaktur'])}")
    if "terkirim" in alasan:
        bag.append(f"terkirim {_q(p['terkirim'])}")
    if "faktur_draf" in alasan:
        bag.append("ada di faktur draf " + ", ".join(x["invoice_number"] for x in p["draf"]))
    return " dan ".join(bag)
