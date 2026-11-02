"""U-SI-TERBIT B1 (2 Nov 2026, disetujui MASTER): fungsi MURNI posting faktur penjualan -- dipakai PENULIS
(routers/sales_invoices._internal_post_invoice) DAN perencana pratinjau READ ONLY (rencana_terbit_ro, tanpa kunci/tulis/penomoran).
Satu penentu: baris JV, alokasi per baris, pilihan cabang 3-event, pecahan pendapatan barang/jasa, jumlah RECOG. Angka dari KOLOM
dokumen (compute_document saat buat/ubah); di sini tak ada hitung ulang pajak/diskon. Law 4 (seimbang = sifat pemanggil), 9/25
(Decimal; alokasi 0,01 HALF_UP, baris terakhir menyerap sisa == sales_doc_calc.allocate), 27 (akun dari peran, diberikan pemanggil).
B2 (tahap berikut): kirim-otomatis (HPP + pengakuan barang) dan alokasi Surat Jalan tanpa faktur -- di sini hanya DIKENALI
(cabang) supaya pratinjau jujur menandai complete:false."""
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

NOL = Decimal("0")
SEN = Decimal("0.01")

CABANG_SJ_PENUH = "sj_penuh"            # semua barang stok sudah terkirim lewat SJ dari pesanan (reklas ditulis alokasi SJ)
CABANG_TANGGUH_KIRIM = "tangguh_kirim"  # kebijakan 'delivery': pendapatan + HPP saat /fulfill
CABANG_KIRIM_OTOMATIS = "kirim_otomatis"  # jual dari stok, kebijakan 'invoice': kirim otomatis saat terbit (B2)
CABANG_TANGGUH_WAC0 = "tangguh_wac0"    # ada barang stok WAC 0: tangguh + peringatan
CABANG_JASA = "jasa"                    # tanpa barang stok: pendapatan diakui saat terbit


def _d(v) -> Decimal:
    """== routers/sales_invoices._d: angka apa pun -> Decimal; kosong/0/None -> 0."""
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v)) if v else Decimal("0")


def baris_jv(total, pajak, ongkir, akun_ar, akun_dimuka, akun_ongkir, akun_ppn, nomor: str) -> list:
    """Baris jurnal tagihan (INVOICE) berurutan == line_number 1..n: Dr Piutang = total · Cr Pendapatan Diterima Dimuka =
    total - pajak - ongkir (SELALU, walau 0) · Cr Pendapatan Ongkos Kirim bila ongkir > 0 (V290) · Cr PPN Keluaran bila pajak > 0.
    -> [{akun, debit, kredit, memo, sisi}] (sisi 'D'/'K' = bentuk INSERT penulis)."""
    total, pajak, ongkir = _d(total), _d(pajak), _d(ongkir)
    out = [{"akun": akun_ar, "debit": total, "kredit": NOL, "memo": f"Piutang - {nomor}", "sisi": "D"},
           {"akun": akun_dimuka, "debit": NOL, "kredit": total - pajak - ongkir, "memo": f"Pendapatan Diterima Dimuka - {nomor}", "sisi": "K"}]
    if ongkir > 0:
        out.append({"akun": akun_ongkir, "debit": NOL, "kredit": ongkir, "memo": f"Pendapatan Ongkos Kirim - {nomor}", "sisi": "K"})
    if pajak > 0:
        out.append({"akun": akun_ppn, "debit": NOL, "kredit": pajak, "memo": f"PPN Keluaran - {nomor}", "sisi": "K"})
    return out


def alokasi_baris(items: list, total, pajak, ongkir) -> list:
    """allocated_amount per baris (urutan items = ORDER BY line_number, id). Dasar = total - pajak - ongkir; proporsi subtotal baris
    (jumlah_baris V427, atau qty x harga) dikurangi diskon baris; 0,01 HALF_UP; baris TERAKHIR menyerap sisa. Σ subtotal <= 0 -> 0."""
    dasar = _d(total) - _d(pajak) - _d(ongkir)

    def sub(it):
        kotor = _d(it["jumlah_baris"]) if it.get("jumlah_baris") is not None else _d(it["quantity"]) * _d(it["unit_price"])
        return kotor - _d(it.get("discount_amount", 0))
    jumlah = sum((sub(it) for it in items), NOL)
    if jumlah <= 0 or not items:
        return [Decimal("0") for _ in items]
    hasil = [((sub(it) / jumlah) * dasar).quantize(SEN, rounding=ROUND_HALF_UP) for it in items]
    hasil[-1] = dasar - sum(hasil[:-1], NOL)
    return hasil


def pilih_cabang(alok_qty, ada_stok: bool, semua_berbiaya: bool, ada_untuk_kirim: bool, kebijakan: str) -> str:
    """Cabang 3-event == urutan if/elif penulis (eksklusif)."""
    if _d(alok_qty) > NOL and ada_stok and semua_berbiaya and not ada_untuk_kirim:
        return CABANG_SJ_PENUH
    if ada_stok and semua_berbiaya and kebijakan == "delivery":
        return CABANG_TANGGUH_KIRIM
    if ada_stok and semua_berbiaya:
        return CABANG_KIRIM_OTOMATIS
    if ada_stok and not semua_berbiaya:
        return CABANG_TANGGUH_WAC0
    return CABANG_JASA


def pecah_pendapatan(pasangan: list) -> tuple:
    """[(alokasi, item_type)] -> (barang, jasa). item_type 'service' -> jasa, selain itu (goods/non_inventory/manual/None) -> barang.
    U-SI-LAW penjaga Law 4 (putusan pemilik 2 Nov): alokasi != 0 masuk embernya (dulu <= 0 DIBUANG -> kredit > debit); ember bersih <= 0
    dilipat ke ember lain -> Σ kredit == Σ alokasi == debit RECOG. Semua alokasi positif (seluruh data 2 Nov) -> hasil IDENTIK."""
    barang, jasa = NOL, NOL
    for a, jenis in pasangan:
        a = _d(a)
        if a == 0:
            continue
        if jenis == "service":
            jasa += a
        else:
            barang += a
    if jasa < 0 or (jasa == 0 and barang < 0):
        barang, jasa = barang + jasa, NOL
    if barang < 0 and jasa > 0:
        barang, jasa = NOL, barang + jasa
    return barang, jasa


def jumlah_recog_jasa(alokasi: list) -> Decimal:
    """Debit RECOG cabang jasa == penulis: Σ SEMUA alokasi (termasuk <= 0). Kredit = pecah_pendapatan (alokasi > 0). Kasus tepi
    alokasi <= 0 (laten, data 0 per 2 Nov) = tiket terpisah -- perilaku DIPERTAHANKAN di sini (SETARA)."""
    return sum((_d(a) for a in alokasi), NOL)


def baris_recog(debit, barang, jasa, akun_dimuka, memo_debit: str, nomor: str) -> list:
    """Baris RECOG (INVOICE_REVENUE) == penulis: Dr Dimuka = debit · Cr barang bila > 0 · Cr jasa bila > 0 (urutan barang lalu jasa).
    Akun kredit = None + flag 'jasa': pemanggil me-resolve (resolve_line_revenue_account) TEPAT sebelum baris itu ditulis/ditampilkan
    -- urutan bacaan penulis tak berubah."""
    out = [{"akun": akun_dimuka, "debit": _d(debit), "kredit": NOL, "memo": memo_debit, "sisi": "D", "jasa": None}]
    for jenis_jasa, amt in ((False, barang), (True, jasa)):
        if _d(amt) > 0:
            out.append({"akun": None, "debit": NOL, "kredit": _d(amt), "memo": f"Penjualan {nomor}", "sisi": "K", "jasa": jenis_jasa})
    return out


# ---------------------------------------------------------------- perencana pratinjau READ ONLY
SQL_ITEMS = """
        SELECT id, item_id, item_code, description, quantity, unit_price,
               batch_id, batch_no, exp_date, discount_amount, jumlah_baris
        FROM sales_invoice_items
        WHERE invoice_id = $1
        ORDER BY line_number, id
        """

SQL_SJ_MUNGKIN = """
    SELECT EXISTS (
      SELECT 1 FROM sales_invoice_items sii
      LEFT JOIN products p ON p.id = sii.item_id AND p.tenant_id = $2
      JOIN invoice_fulfillment_items i ON i.sales_order_item_id = sii.sales_order_item_id
      JOIN invoice_fulfillments f ON f.id = i.fulfillment_id AND f.tenant_id = $2 AND f.asal = 'so'
           AND f.status <> 'voided' AND f.voided_at IS NULL
      WHERE sii.invoice_id = $1 AND sii.sales_order_item_id IS NOT NULL
        AND (COALESCE(p.track_inventory, false) OR COALESCE(sii.perlu_kirim, false))
        AND sii.quantity > COALESCE(sii.fulfilled_qty, 0)
        AND i.quantity > COALESCE((SELECT SUM(a.quantity) FROM sj_faktur_alokasi a
                                   WHERE a.fulfillment_item_id = i.id AND a.voided_at IS NULL), 0))"""

CATATAN_SEBAGIAN = "Pratinjau sebagian: HPP dan pengakuan barang terkirim dihitung saat faktur diterbitkan."


def _pesan(detail) -> str:
    return detail.get("message") if isinstance(detail, dict) and detail.get("message") else str(detail)


async def _tampil(conn, tid: str, baris: list) -> list:
    """Baris rencana -> bentuk tampil {account_code, account_name, debit, credit, memo} (akun dibaca, tanpa kunci)."""
    ids = [b["akun"] for b in baris if b["akun"] is not None]
    akun = {r["id"]: r for r in await conn.fetch(
        "SELECT id, account_code, name FROM chart_of_accounts WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tid, ids)} if ids else {}
    return [{"account_code": akun[b["akun"]]["account_code"] if b["akun"] in akun else None,
             "account_name": akun[b["akun"]]["name"] if b["akun"] in akun else None,
             "debit": float(b["debit"]), "credit": float(b["kredit"]), "memo": b["memo"]} for b in baris]


async def rencana_terbit_ro(conn, ctx: dict, invoice_id, skip_deposit_ids=()) -> Optional[dict]:
    """Pratinjau TERBIT faktur (POST /sales-invoices/{id}/post/preview) -- READ ONLY: tanpa advisory/FOR UPDATE/penomoran/tulis.
    Penentu SAMA dengan penulis (_internal_post_invoice + _auto_apply_so_deposits): baris_jv, alokasi_baris, pilih_cabang,
    pecah_pendapatan, jumlah_recog_jasa, baris_recog, UMR.baris_terapkan; blok berurutan seperti penulis. None = faktur tak ada.
    complete=False bila HPP/pengakuan barang terkirim (kirim otomatis / Surat Jalan dari pesanan) belum direncanakan (B2)."""
    from fastapi import HTTPException
    from ..routers import sales_invoices as SI
    from ..routers.customer_deposits import plan_so_deposit_application
    from . import gudang_bawaan as GB
    from . import tanggal_uang_muka as TUM
    from . import uang_muka_rencana as UMR
    from .deposit_jenis import label_deposit
    from .pkp_guard import tolak_ppn_bila_non_pkp
    from .role_resolver import (AccountRole, AccountRoleUnmappedError, resolve_account_id_by_role, resolve_account_id_by_role_if_pkp,
                                resolve_line_revenue_account)
    tid = ctx["tenant_id"]
    inv = await conn.fetchrow(
        """SELECT id, invoice_number, customer_id, customer_name, total_amount, tax_amount, invoice_date, warehouse_id, recognize_at,
                  shipping_amount, status FROM sales_invoices WHERE id = $1 AND tenant_id = $2""", invoice_id, tid)
    if inv is None:
        return None
    blocks, warnings, catatan, journals = [], [], [], []

    def blok(code, pesan, status=400, field=None):
        blocks.append({"code": code, "message": pesan, "status": status, **({"field": field} if field else {})})

    if inv["status"] != "draft":
        blok("SI_BUKAN_DRAF", "Hanya faktur berstatus Draf yang bisa diterbitkan.")
    try:
        await SI.check_period_is_open(conn, tid, inv["invoice_date"])
    except HTTPException as e:
        blok("PERIODE_TERTUTUP", _pesan(e.detail), e.status_code)
    try:
        await tolak_ppn_bila_non_pkp(conn, tid, inv["tax_amount"])
    except HTTPException as e:
        blok("NON_PKP", _pesan(e.detail), e.status_code)
    total, pajak, ongkir = _d(inv["total_amount"]), _d(inv["tax_amount"] or 0), _d(inv["shipping_amount"] or 0)
    akun_ar = akun_dimuka = akun_ppn = akun_ongkir = None
    try:
        akun_ar = await resolve_account_id_by_role(conn, tid, AccountRole.AR_TRADE)
        akun_dimuka = await SI._resolve_unearned_revenue(conn, tid)
        akun_ppn = await resolve_account_id_by_role_if_pkp(conn, tid, AccountRole.VAT_OUTPUT)
    except AccountRoleUnmappedError as e:
        blok("PERAN_AKUN_BELUM_DIPETAKAN", str(e), 500)
    if ongkir > 0:
        try:
            akun_ongkir = await resolve_account_id_by_role(conn, tid, AccountRole.REVENUE_SHIPPING)
        except AccountRoleUnmappedError:
            blok("PERAN_ONGKIR_BELUM_DIATUR", ("Akun Pendapatan Ongkos Kirim belum diatur untuk usaha ini "
                                               "(peran REVENUE_SHIPPING). Atur dulu sebelum memposting faktur "
                                               "berongkos kirim."), 422)
    if akun_ar is not None and akun_dimuka is not None:
        journals.append({"journal_number": None, "source_type": "INVOICE", "journal_date": inv["invoice_date"].isoformat(),
                         "description": f"Faktur Penjualan {inv['invoice_number']} - {inv['customer_name']}",
                         "lines": await _tampil(conn, tid, baris_jv(total, pajak, ongkir, akun_ar, akun_dimuka, akun_ongkir, akun_ppn,
                                                                     inv["invoice_number"]))})
    items = [dict(r) for r in await conn.fetch(SQL_ITEMS, invoice_id)]
    for it, a in zip(items, alokasi_baris(items, total, pajak, ongkir)):
        it["_allocated"] = a
    sj_mungkin = bool(await conn.fetchval(SQL_SJ_MUNGKIN, invoice_id, tid))
    ada_stok, semua_berbiaya, untuk_kirim = False, True, []
    for it in items:
        if not it.get("item_id"):
            continue
        p = await conn.fetchrow("SELECT id, track_inventory FROM products WHERE tenant_id=$1 AND id=$2", tid, it["item_id"])
        if not p or not p.get("track_inventory", True):
            continue
        ada_stok = True
        wac = await conn.fetchval("SELECT get_weighted_average_cost($1, $2)", tid, it["item_id"])
        if not wac or wac == 0:
            semua_berbiaya = False
        else:
            untuk_kirim.append(it["id"])
    kebijakan = inv["recognize_at"] or await conn.fetchval(
        "SELECT revenue_recognition_policy FROM tenant_config WHERE tenant_id=$1", tid) or "invoice"
    cabang = pilih_cabang(NOL, ada_stok, semua_berbiaya, bool(untuk_kirim), kebijakan)
    complete = not sj_mungkin
    if cabang == CABANG_KIRIM_OTOMATIS:
        complete = False
        if not inv["warehouse_id"] and await GB.bawaan(conn, tid) is None:
            blok(SI.KODE_GUDANG_WAJIB_POSTING, SI.PESAN_GUDANG_WAJIB_POSTING, 400, "warehouse_id")
    if cabang == CABANG_TANGGUH_WAC0:
        for it in items:
            if not it.get("item_id"):
                continue
            wac = await conn.fetchval("SELECT get_weighted_average_cost($1, $2)", tid, it["item_id"])
            if not wac or wac == 0:
                _desc = it.get("description") or it.get("item_code") or "item"
                warnings.append(f"Pendapatan ditangguhkan: {_desc} belum punya harga pokok (WAC=0). Catat penerimaan barang dulu, lalu "
                                f"kirim (fulfill) untuk mengakui pendapatan & HPP.")

    async def recog(debit, pasangan, memo_debit, deskripsi):
        barang, jasa = pecah_pendapatan(pasangan)
        baris = baris_recog(debit, barang, jasa, akun_dimuka, memo_debit, inv["invoice_number"])
        for b in baris[1:]:
            b["akun"] = await resolve_line_revenue_account(conn, tid, is_service=b["jasa"])
        journals.append({"journal_number": None, "source_type": "INVOICE_REVENUE", "journal_date": inv["invoice_date"].isoformat(),
                         "description": deskripsi, "lines": await _tampil(conn, tid, baris)})

    async def jenis(it):
        pid = it.get("item_id") or it.get("product_id")
        return await conn.fetchval("SELECT item_type FROM products WHERE id=($1)::uuid AND tenant_id=$2", pid, tid) if pid else None

    if akun_dimuka is not None and cabang == CABANG_JASA:
        debit = jumlah_recog_jasa([it["_allocated"] for it in items])
        if debit > 0:
            pas = [(it["_allocated"], await jenis(it)) for it in items if _d(it["_allocated"]) != 0]
            await recog(debit, pas, f"Dimuka → Penjualan {inv['invoice_number']}", f"Revenue Recognition {inv['invoice_number']} (Service)")
    if akun_dimuka is not None and ada_stok:
        nt = []
        for it in items:
            pid = it.get("item_id") or it.get("product_id")
            trk = False
            if pid:
                tp = await conn.fetchrow("SELECT track_inventory FROM products WHERE tenant_id=$1 AND id=$2", tid, pid)
                trk = bool(tp and tp["track_inventory"])
            if not trk and _d(it.get("_allocated") or 0) > 0:
                nt.append(it)
        nt_total = sum((_d(i["_allocated"]) for i in nt), NOL)
        if nt_total > 0:
            pas = [(it["_allocated"], await jenis(it)) for it in nt]
            await recog(nt_total, pas, f"Dimuka → Penjualan {inv['invoice_number']} (non-persediaan)",
                        f"Revenue Recognition {inv['invoice_number']} (baris non-persediaan)")
    if not complete:
        catatan.append(CATATAN_SEBAGIAN)
    # uang muka pesanan yang AKAN diterapkan (penentu SAMA dgn _auto_apply_so_deposits; draf: sisa = total, pola GET /deposit-plan)
    rencana = await plan_so_deposit_application(conn, tid, invoice_id, total, list(skip_deposit_ids or ()))
    deposit_apps, deposit_journals = [], []
    if any(p["planned_amount"] > 0 for p in rencana):
        akun_dp = await resolve_account_id_by_role(conn, tid, AccountRole.CUSTOMER_DEPOSIT_LIABILITY)
        for p in rencana:
            if p["planned_amount"] <= 0:
                continue
            tgl = TUM.tanggal_terap_otomatis(inv["invoice_date"], p["deposit_date"])
            label_k = await label_deposit(conn, tid, p["deposit_id"], kapital=True)
            baris = [{"akun": a, "debit": _d(d), "kredit": _d(k), "memo": m}
                     for a, d, k, m in UMR.baris_terapkan(akun_dp, akun_ar, p["planned_amount"], label_k, inv["invoice_number"],
                                                          p["deposit_number"])]
            deposit_apps.append({"deposit_id": p["deposit_id"], "deposit_number": p["deposit_number"],
                                 "amount": float(p["planned_amount"]), "application_date": tgl.isoformat()})
            deposit_journals.append({"journal_number": None, "source_type": "DEPOSIT_APPLICATION", "journal_date": tgl.isoformat(),
                                     "description": f"Apply Deposit {p['deposit_number']} to {inv['invoice_number']}",
                                     "lines": await _tampil(conn, tid, baris)})
    return {"invoice": inv, "blocks": blocks, "warnings": warnings, "notes": catatan, "complete": complete, "cabang": cabang,
            "journals": journals, "deposit_applications": deposit_apps, "deposit_application_journals": deposit_journals,
            "deposit_plan": rencana}
