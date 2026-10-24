"""Uang Muka Pembelian -- PENENTU per aksi (kontrak CW penuh, 22 Okt 2026; kembar uang muka pelanggan de24dcdb/d7dd4fd7).

SATU definisi aturan + RENCANA jurnal per aksi (buat, posting, terapkan, lepas, kembalikan, batal, hapus), dipakai DUA jalur:
  * endpoint TULIS: dipanggil SESUDAH kunci (advisory + FOR UPDATE); blok pertama -> galat HTTP (pesan lama dipertahankan);
  * endpoint PRATINJAU: transaksi READ ONLY tanpa kunci (pola NetSuite preview = baca, commit = validasi ulang di bawah lock);
    SEMUA blok, 200; jurnal yang AKAN dibuat (baris akun dari PERAN, Law 27) tanpa nomor (nomor lahir saat tulis).
Angka = TURUNAN JURNAL (Law 1/16/29): sisa/terpakai/dikembalikan dari uang_muka_vendor_daftar.fakta_vd; sisa tagihan dari
compute_ap_outstanding (sumber yang SAMA dengan penjaga /apply). Uang Decimal 2dp HALF_UP; respons float.

Blok: {"code", "message", "status"} -- `status` = kode HTTP endpoint tulis (dibuang dari respons pratinjau)."""
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException

from . import po_daftar as PD
from . import uang_muka_vendor as UMV
from . import uang_muka_vendor_daftar as UMD
from .role_resolver import AccountRole

NOL = Decimal("0")
STATUS_TERBIT = ("posted", "partial")  # boleh diterapkan / dikembalikan (status 'applied' = sisa 0)
SQL_SISA_TAGIHAN = "SELECT COALESCE(SUM(outstanding), 0) FROM compute_ap_outstanding($1) WHERE bill_id = $2"


async def sisa_tagihan(conn, tid: str, bill_id) -> Decimal:
    """Sisa tagihan dari JURNAL lewat compute_ap_outstanding (arap Rule 5; SATU sumber dgn segarkan_cache_hutang_tagihan). Hilang = 0."""
    return max(NOL, _d(await conn.fetchval(SQL_SISA_TAGIHAN, tid, bill_id)))


def blok(code: str, message: str, status: int = 400) -> dict:
    return {"code": code, "message": message, "status": status}


def untuk_respons(bloks: list) -> list:
    return [{"code": b["code"], "message": b["message"]} for b in bloks]


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else NOL


def _f(v):
    return float(PD.r2(v)) if v is not None else None


async def _akun(conn, tid: str, peran: str, bloks: list):
    try:
        return await UMV.akun_peran(conn, tid, peran)
    except HTTPException as e:
        d = e.detail if isinstance(e.detail, dict) else {"code": "PERAN_AKUN_BELUM_DIPETAKAN", "message": str(e.detail)}
        if not any(b["code"] == d["code"] and b["message"] == d["message"] for b in bloks):
            bloks.append(blok(d["code"], d["message"], e.status_code))
        return None


async def _baris(conn, tid: str, akun, debit, kredit) -> dict:
    r = await conn.fetchrow("SELECT account_code, name FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2", akun, tid) if akun else None
    return {"account_code": r["account_code"] if r else None, "account_name": r["name"] if r else None,
            "debit": _f(debit), "credit": _f(kredit)}


async def _periode(conn, tid: str, tgl, bloks: list) -> None:
    """Law 5. Pesan == jalur tulis lama ("Periode akuntansi sudah {status}")."""
    r = await conn.fetchrow("SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2", tid, tgl)
    if r and r["status"] != "OPEN":
        bloks.append(blok("PERIODE_TERTUTUP", f"Periode akuntansi sudah {r['status']}"))


async def keadaan(conn, tid: str, deposit_id) -> dict:
    """before/after: {exists, status, amount, amount_applied, amount_refunded, remaining} -- angka dari JURNAL (fakta_vd)."""
    r = await conn.fetchrow("SELECT id, status, amount FROM vendor_deposits WHERE id = $1 AND tenant_id = $2", deposit_id, tid)
    if not r:
        return {"exists": False}
    akun = await UMD.akun_baca(conn, tid)
    f = (await UMD.fakta_vd(conn, tid, akun, [r["id"]]))[r["id"]]
    return {"exists": True, "status": r["status"], "amount": _f(r["amount"]), "amount_applied": _f(f["terpakai"]),
            "amount_refunded": _f(f["dikembalikan"]), "remaining": _f(f["sisa"]) if f["sisa"] is not None else None,
            "position_text": f["position_text"]}


def sesudah(sebelum: dict, **ubah) -> dict:
    """after = before + perubahan; position_text DIHITUNG ULANG dari angka sesudah (penentu sama dgn daftar/detail, UMD.keadaan_vd) --
    dulu disalin dari before -> pratinjau batal tetap "Belum diterapkan" (temuan uji nyata WORKSPACE 23 Okt)."""
    out = dict(sebelum)
    out.update(ubah)
    if out.get("exists"):
        out["position_text"] = UMD.position_text(UMD.keadaan_vd(out.get("status"), out.get("amount"), out.get("amount_applied"),
                                                                out.get("amount_refunded"), out.get("remaining")))
    return out


async def akun_kas(conn, tid: str, bank_account_id, account_id, bloks: list, wajib: bool):
    """(coa_id kas/bank, bank_account_id cermin|None). bank_account_id -> CoA rekening (tenant eksplisit, Law 24); account_id -> CoA
    kas/bank ASSET is_cash milik tenant (kembar uang muka pelanggan); keduanya kosong -> wajib ? blok REKENING_WAJIB : peran Kas."""
    if bank_account_id:
        r = await conn.fetchrow("SELECT id, coa_id FROM bank_accounts WHERE id = $1 AND tenant_id = $2", _uuid(bank_account_id), tid)
        if not r or not r["coa_id"]:
            bloks.append(blok("REKENING_TIDAK_SAH", "Rekening bank tidak ditemukan di usaha ini.", 422))
            return None, None
        return r["coa_id"], r["id"]
    if account_id:
        r = await conn.fetchrow(
            """SELECT id FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2 AND account_type = 'ASSET'
               AND COALESCE(is_cash, false) AND COALESCE(is_active, true) AND NOT COALESCE(is_header, false)""", _uuid(account_id), tid)
        if not r:
            bloks.append(blok("AKUN_KAS_TIDAK_SAH", "Akun kas/bank tidak ditemukan di usaha ini.", 422))
            return None, None
        ba = await conn.fetchval("SELECT id FROM bank_accounts WHERE coa_id = $1 AND tenant_id = $2", r["id"], tid)
        return r["id"], ba
    if wajib:
        bloks.append(blok("REKENING_WAJIB", "Pilih rekening bank atau akun kas.", 422))
        return None, None
    coa = await _akun(conn, tid, AccountRole.CASH_GENERAL, bloks)
    ba = await conn.fetchval("SELECT id FROM bank_accounts WHERE coa_id = $1 AND tenant_id = $2", coa, tid) if coa else None
    return coa, ba


def _uuid(v):
    try:
        return UUID(str(v))
    except (ValueError, TypeError):
        return UUID(int=0)


def _rencana_jurnal(source_type: str, deskripsi: str, tgl, baris: list) -> dict:
    return {"journal_number": None, "source_type": source_type, "description": deskripsi,
            "journal_date": tgl.isoformat() if tgl else None, "lines": baris}


async def _bank(conn, tid: str, ba, jumlah, jenis: str) -> list:
    if not ba:
        return []
    nama = await conn.fetchval("SELECT account_name FROM bank_accounts WHERE id = $1 AND tenant_id = $2", ba, tid)
    return [{"bank_account": nama, "amount": _f(jumlah), "transaction_type": jenis}]


# ---------------------------------------------------------------- buat (+ simpan & posting)
async def rencana_buat(conn, ctx, body) -> dict:
    """Blok buat: vendor milik tenant · PO milik tenant, vendor SAMA, tak dibatalkan · rekening sah. body.simpan_dan_posting -> + rencana
    posting (periode tanggal uang muka, peran akun, jurnal Dr Uang Muka Pembelian / Cr Kas-Bank)."""
    tid = ctx["tenant_id"]
    bloks = []
    v = await conn.fetchrow("SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2", body.vendor_id, tid)
    if not v:
        bloks.append(blok("VENDOR_TIDAK_DITEMUKAN", "Vendor tidak ditemukan.", 422))
    if body.purchase_order_id:
        po = await conn.fetchrow("SELECT vendor_id, status FROM purchase_orders WHERE id = $1 AND tenant_id = $2", body.purchase_order_id, tid)
        if not po:
            bloks.append(blok("PO_TIDAK_DITEMUKAN", "Pesanan pembelian tidak ditemukan di usaha ini.", 422))
        elif po["vendor_id"] and po["vendor_id"] != body.vendor_id:
            bloks.append(blok("PO_BEDA_VENDOR", "Pesanan pembelian itu milik vendor lain.", 422))
        elif po["status"] == "cancelled":
            bloks.append(blok("PO_DIBATALKAN", "Pesanan pembelian itu sudah dibatalkan.", 422))
    coa_kas, ba = await akun_kas(conn, tid, body.bank_account_id, None, bloks, wajib=False)
    jumlah = _d(body.amount)
    jurnal, bank = [], []
    status = "draft"
    if getattr(body, "simpan_dan_posting", False):
        status = "posted"
        await _periode(conn, tid, body.deposit_date, bloks)
        um = await _akun(conn, tid, AccountRole.AP_PREPAID, bloks)
        if um and coa_kas:
            jurnal = [_rencana_jurnal("VENDOR_DEPOSIT", "Vendor Deposit - (nomor saat disimpan)", body.deposit_date,
                              [await _baris(conn, tid, um, jumlah, 0), await _baris(conn, tid, coa_kas, 0, jumlah)])]
            bank = await _bank(conn, tid, ba, -jumlah, "withdrawal")
    after = {"exists": True, "status": status, "amount": _f(jumlah), "amount_applied": 0.0, "amount_refunded": 0.0,
             "remaining": _f(jumlah) if status == "posted" else None}
    return {"blocks": bloks, "before": {"exists": False}, "after": after, "journals": jurnal, "bank_transactions": bank, "bills": []}


# ---------------------------------------------------------------- posting
async def rencana_posting(conn, ctx, vd, pratinjau: bool = False) -> dict:
    tid = ctx["tenant_id"]
    bloks = []
    if vd["status"] != "draft":
        bloks.append(blok("SUDAH_DIPOSTING", "Uang muka ini sudah diposting."))
    um = await _akun(conn, tid, AccountRole.AP_PREPAID, bloks)
    coa_kas, ba = await akun_kas(conn, tid, vd["bank_account_id"], None, bloks, wajib=False)
    await _periode(conn, tid, vd["deposit_date"], bloks)
    jumlah = _d(vd["amount"])
    jurnal = [_rencana_jurnal("VENDOR_DEPOSIT", f"Vendor Deposit - {vd['deposit_number']}", vd["deposit_date"],
                      [await _baris(conn, tid, um, jumlah, 0), await _baris(conn, tid, coa_kas, 0, jumlah)])] if um and coa_kas else []
    sebelum = await keadaan(conn, tid, vd["id"]) if pratinjau else {}
    return {"blocks": bloks, "before": sebelum, "after": sesudah(sebelum, status="posted", remaining=_f(jumlah)),
            "journals": jurnal, "bank_transactions": await _bank(conn, tid, ba, -jumlah, "withdrawal") if jurnal else [], "bills": []}


# ---------------------------------------------------------------- terapkan
async def rencana_terapkan(conn, ctx, vd, body, hari_ini, pratinjau: bool = False) -> dict:
    """Pesan & urutan == /apply: status · saldo (jurnal) · tagihan milik vendor · tagihan terposting · sisa tagihan · periode."""
    tid = ctx["tenant_id"]
    bloks = []
    jumlah = _d(body.amount)
    if vd["status"] not in STATUS_TERBIT:
        bloks.append(blok("BELUM_DIPOSTING", "Uang muka harus diposting dulu sebelum diterapkan."))
    sisa = await UMV.sisa_jurnal(conn, tid, vd["id"]) if vd["status"] in STATUS_TERBIT else NOL
    if vd["status"] in STATUS_TERBIT and jumlah > sisa:
        bloks.append(blok("SALDO_TIDAK_CUKUP", f"Saldo uang muka tidak cukup. Tersedia: {sisa}"))
    bill = await conn.fetchrow("SELECT id, invoice_number, status FROM bills WHERE id = $1 AND vendor_id = $2 AND tenant_id = $3",
                               body.bill_id, vd["vendor_id"], tid)
    sisa_t = NOL
    if not bill:
        bloks.append(blok("TAGIHAN_TIDAK_DITEMUKAN", "Tagihan tidak ditemukan atau bukan milik vendor ini."))
    else:
        if bill["status"] not in ("posted", "partial"):
            bloks.append(blok("TAGIHAN_BELUM_DIPOSTING", "Tagihan harus sudah diposting."))
        sisa_t = await sisa_tagihan(conn, tid, bill["id"])
        if jumlah > sisa_t:
            bloks.append(blok("MELEBIHI_SISA_TAGIHAN", f"Jumlah melebihi sisa tagihan. Sisa tagihan: {sisa_t}"))
    tgl = body.applied_date or hari_ini
    ap = await _akun(conn, tid, AccountRole.AP_TRADE, bloks)
    um = await _akun(conn, tid, AccountRole.AP_PREPAID, bloks)
    await _periode(conn, tid, tgl, bloks)
    nomor_tagihan = bill["invoice_number"] if bill else "-"
    jurnal = [_rencana_jurnal("DEPOSIT_APPLICATION", f"Apply Deposit {vd['deposit_number']} to Bill {nomor_tagihan}", tgl,
                      [await _baris(conn, tid, ap, jumlah, 0), await _baris(conn, tid, um, 0, jumlah)])] if ap and um else []
    sebelum = await keadaan(conn, tid, vd["id"]) if pratinjau else {}
    after = sesudah(sebelum, amount_applied=_f(_d(sebelum.get("amount_applied")) + jumlah),
                    remaining=_f(_d(sebelum.get("remaining")) - jumlah) if sebelum.get("remaining") is not None else None)
    tag = [{"bill_id": str(bill["id"]), "bill_number": bill["invoice_number"], "remaining_before": _f(sisa_t),
            "remaining_after": _f(sisa_t - jumlah)}] if bill else []
    return {"blocks": bloks, "before": sebelum, "after": after, "journals": jurnal, "bank_transactions": [], "bills": tag}


# ---------------------------------------------------------------- lepas
async def rencana_lepas(conn, ctx, vd, application_id, hari_ini, kunci: bool = True, pratinjau: bool = False) -> dict:
    """Pesan & urutan == /unapply. Penerapan sudah dilepas = blok SUDAH_DILEPAS (tulis menjawab 200 idempoten; pratinjau melaporkannya)."""
    tid = ctx["tenant_id"]
    bloks = []
    a = await conn.fetchrow(
        """SELECT a.id, a.bill_id, a.amount, a.journal_id, a.status, b.invoice_number AS bill_number
           FROM vendor_deposit_applications a LEFT JOIN bills b ON b.id = a.bill_id AND b.tenant_id = $3
           WHERE a.id = $1 AND a.vendor_deposit_id = $2""" + (" FOR UPDATE OF a" if kunci else ""), application_id, vd["id"], tid)
    if not a:
        return {"blocks": [blok("PENERAPAN_TIDAK_DITEMUKAN", "Penerapan uang muka tidak ditemukan.", 404)], "before": await keadaan(conn, tid, vd["id"]) if pratinjau else {},
                "after": None, "journals": [], "bank_transactions": [], "bills": [], "penerapan": None, "baris_asli": []}
    if a["status"] == "reversed":
        bloks.append(blok("SUDAH_DILEPAS", "Penerapan uang muka sudah dilepas sebelumnya.", 409))
    asli = await conn.fetchrow("SELECT id, status, reversed_by_id FROM journal_entries WHERE id = $1 AND tenant_id = $2", a["journal_id"], tid)
    if a["status"] != "reversed":
        if not asli or asli["status"] != "POSTED":
            bloks.append(blok("JURNAL_PENERAPAN_TIDAK_ADA", "Jurnal penerapan ini tidak ditemukan; tak bisa dilepas.", 409))
        elif asli["reversed_by_id"]:
            bloks.append(blok("JURNAL_PENERAPAN_SUDAH_DIBALIK", "Jurnal penerapan ini sudah dibalik oleh proses lain.", 409))
    r = await conn.fetchrow("SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2", tid, hari_ini)
    if r and r["status"] != "OPEN":
        bloks.append(blok("PERIODE_TERTUTUP", f"Periode akuntansi sudah {r['status']}.", 409))
    baris_asli = await conn.fetch(
        "SELECT account_id, debit, credit, memo FROM journal_lines WHERE journal_id = $1 ORDER BY line_number", a["journal_id"]) if asli else []
    jumlah = _d(a["amount"])
    jurnal = [_rencana_jurnal("DEPOSIT_APPLICATION", f"Lepas penerapan {vd['deposit_number']} dari tagihan {a['bill_number'] or ''}".strip(), hari_ini,
                      [await _baris(conn, tid, b["account_id"], b["credit"], b["debit"]) for b in baris_asli])] if baris_asli and not bloks else []
    sisa_t = await sisa_tagihan(conn, tid, a["bill_id"]) if a["bill_id"] else NOL
    sebelum = await keadaan(conn, tid, vd["id"]) if pratinjau else {}
    after = sesudah(sebelum, amount_applied=_f(_d(sebelum.get("amount_applied")) - jumlah),
                    remaining=_f(_d(sebelum.get("remaining")) + jumlah) if sebelum.get("remaining") is not None else None) if not bloks else sebelum
    tag = [{"bill_id": str(a["bill_id"]), "bill_number": a["bill_number"], "remaining_before": _f(sisa_t),
            "remaining_after": _f(sisa_t + jumlah if not bloks else sisa_t)}] if a["bill_id"] else []
    return {"blocks": bloks, "before": sebelum, "after": after, "journals": jurnal, "bank_transactions": [], "bills": tag,
            "penerapan": a, "baris_asli": baris_asli, "jurnal_asli": asli}


# ---------------------------------------------------------------- kembalikan (refund)
async def rencana_kembali(conn, ctx, vd, body, pratinjau: bool = False) -> dict:
    """Pesan & urutan == /refund: status · saldo (jurnal) · rekening/akun kas WAJIB (kembar pelanggan) · periode. Jurnal Dr Kas-Bank /
    Cr Uang Muka Pembelian; cermin bank bila akun kas itu rekening (BankSync R1)."""
    tid = ctx["tenant_id"]
    bloks = []
    jumlah = _d(body.amount)
    if vd["status"] not in STATUS_TERBIT:
        bloks.append(blok("BELUM_DIPOSTING", "Uang muka harus sudah diposting."))
    sisa = await UMV.sisa_jurnal(conn, tid, vd["id"]) if vd["status"] in STATUS_TERBIT else NOL
    if vd["status"] in STATUS_TERBIT and jumlah > sisa:
        bloks.append(blok("SALDO_TIDAK_CUKUP", f"Saldo uang muka tidak cukup. Tersedia: {sisa}"))
    um = await _akun(conn, tid, AccountRole.AP_PREPAID, bloks)
    coa_kas, ba = await akun_kas(conn, tid, body.bank_account_id, getattr(body, "account_id", None), bloks, wajib=True)
    await _periode(conn, tid, body.refund_date, bloks)
    jurnal = [_rencana_jurnal("DEPOSIT_REFUND", f"Refund Deposit {vd['deposit_number']}", body.refund_date,
                      [await _baris(conn, tid, coa_kas, jumlah, 0), await _baris(conn, tid, um, 0, jumlah)])] if um and coa_kas else []
    sebelum = await keadaan(conn, tid, vd["id"]) if pratinjau else {}
    after = sesudah(sebelum, amount_refunded=_f(_d(sebelum.get("amount_refunded")) + jumlah),
                    remaining=_f(_d(sebelum.get("remaining")) - jumlah) if sebelum.get("remaining") is not None else None)
    return {"blocks": bloks, "before": sebelum, "after": after, "journals": jurnal,
            "bank_transactions": await _bank(conn, tid, ba, jumlah, "deposit") if jurnal else [], "bills": [], "akun_kas": coa_kas}


# ---------------------------------------------------------------- batal (void)
SQL_BERGANTUNG = """SELECT (SELECT count(*) FROM vendor_deposit_applications WHERE vendor_deposit_id = $1 AND status = 'active') AS n_terap,
                           (SELECT count(*) FROM vendor_deposit_refunds WHERE vendor_deposit_id = $1) AS n_refund"""


async def rencana_batal(conn, ctx, vd, alasan: Optional[str], hari_ini, pratinjau: bool = False) -> dict:
    """Pesan & urutan == /void: sudah batal (tulis = 200 idempoten; pratinjau: blok SUDAH_DIBATALKAN) · draf (hapus, bukan batal) ·
    alasan wajib (kembar pelanggan VOID_REASON_REQUIRED) · penerapan aktif/refund · periode hari ini. Jurnal = pembalik jurnal posting."""
    from .jaga_rekonsiliasi import tolak_void_bila_terekonsiliasi
    tid = ctx["tenant_id"]
    bloks = []
    if vd["status"] == "void":
        bloks.append(blok("SUDAH_DIBATALKAN", "Uang muka ini sudah dibatalkan.", 409))
    elif vd["status"] == "draft":
        bloks.append(blok("DRAF_DIHAPUS", "Uang muka draf tidak dibatalkan; hapus saja."))
    if not (alasan or "").strip():
        bloks.append(blok("VOID_REASON_REQUIRED", "Alasan pembatalan wajib diisi.", 422))
    try:
        await tolak_void_bila_terekonsiliasi(conn, tid, vd["id"])
    except HTTPException as e:
        d = e.detail if isinstance(e.detail, dict) else {"code": "SUDAH_DIREKONSILIASI", "message": str(e.detail)}
        bloks.append(blok(d.get("code") or "SUDAH_DIREKONSILIASI", d.get("message") or str(e.detail), e.status_code))
    g = await conn.fetchrow(SQL_BERGANTUNG, vd["id"])
    if g["n_terap"] or g["n_refund"]:
        bloks.append(blok("UANG_MUKA_MASIH_DIPAKAI", "Uang muka ini masih diterapkan ke tagihan atau sudah dikembalikan. Lepas penerapannya dulu.", 409))
    await _periode(conn, tid, hari_ini, bloks)
    jurnal, bank = [], []
    if vd["journal_id"] and vd["status"] not in ("void", "draft"):
        baris = await conn.fetch("SELECT account_id, debit, credit FROM journal_lines WHERE journal_id = $1 ORDER BY line_number", vd["journal_id"])
        jurnal = [_rencana_jurnal("VENDOR_DEPOSIT", f"Void {vd['deposit_number']}", hari_ini,
                          [await _baris(conn, tid, b["account_id"], b["credit"], b["debit"]) for b in baris])]
        bt = await conn.fetchrow("""SELECT ba.account_name, bt.amount FROM bank_transactions bt JOIN bank_accounts ba ON ba.id = bt.bank_account_id
                                    WHERE bt.journal_id = $1 AND bt.tenant_id = $2 ORDER BY bt.created_at LIMIT 1""", vd["journal_id"], tid)
        if bt:
            bank = [{"bank_account": bt["account_name"], "amount": _f(-_d(bt["amount"])), "transaction_type": "deposit"}]
    sebelum = await keadaan(conn, tid, vd["id"]) if pratinjau else {}
    return {"blocks": bloks, "before": sebelum, "after": sesudah(sebelum, status="void", remaining=None), "journals": jurnal,
            "bank_transactions": bank, "bills": []}


# ---------------------------------------------------------------- hapus (draf)
async def rencana_ubah(conn, ctx, vd, perubahan: dict, pratinjau: bool = False) -> dict:
    """AS-GP4 (24 Okt 2026): ubah uang muka DRAF -- penentu SATU untuk PATCH /{id} (sesudah kunci VENDOR_DEPOSIT + FOR UPDATE, urutan SAMA
    dengan posting) dan POST /{id}/edit/preview (READ ONLY). Blok status = pesan lama (400 "Can only update draft deposits")."""
    bloks = [] if vd["status"] == "draft" else [blok("BUKAN_DRAF", "Can only update draft deposits")]
    sebelum = await keadaan(conn, ctx["tenant_id"], vd["id"]) if pratinjau else {}
    jadi = {k: (str(v) if v is not None and not isinstance(v, (int, float, str, bool)) else v) for k, v in perubahan.items()}
    return {"blocks": bloks, "before": sebelum, "after": ({**sebelum, **({"amount": _f(perubahan["amount"])} if "amount" in perubahan else {})}
                                                         if not bloks else sebelum), "changes": jadi,
            "journals": [], "bank_transactions": [], "bills": []}


async def rencana_hapus(conn, ctx, vd, pratinjau: bool = False) -> dict:
    bloks = [] if vd["status"] == "draft" else [blok("BUKAN_DRAF", "Hanya uang muka draf yang bisa dihapus.")]
    sebelum = await keadaan(conn, ctx["tenant_id"], vd["id"]) if pratinjau else {}
    return {"blocks": bloks, "before": sebelum, "after": {"exists": False} if not bloks else sebelum, "journals": [], "bank_transactions": [], "bills": []}
