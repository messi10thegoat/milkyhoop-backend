"""AS-G7c (24 Okt 2026, desain disetujui MASTER): penentu bersama aksi uang muka pelanggan -- terapkan / lepas penerapan /
kembalikan dana / batal. SATU fungsi per aksi dipakai PENULIS (routers/customer_deposits *_core, SESUDAH kunci advisory-nya)
DAN PRATINJAU (transaksi READ ONLY, tanpa kunci, tanpa penomoran, tanpa tulis).

Tiap rencana_* memeriksa dengan URUTAN, status, dan teks galat YANG SAMA dengan penulis lama (raise pertama = galat yang sama),
lalu mengembalikan rencana: baris jurnal (akun, debit, kredit, memo), cermin bank, baris uang muka. Penulis menulis DARI rencana;
pratinjau membentuk jurnal/bank/sesudah dari rencana (nomor jurnal = null: nomor final saat simpan, pola NetSuite).
Penerapan banyak faktur: efek baris sebelumnya (sisa faktur, "sudah diterapkan") disimulasikan supaya galat pertama tetap sama
dengan penulis yang menulis baris demi baris."""
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException

from . import teks_galat as tg
from .bank_sync import JENIS_PEMBALIK
from .deposit_jenis import label_deposit
from .pihak_helpers import normalisasi_pihak, pastikan_pihak_sama
from .role_resolver import AccountRole, resolve_account_id_by_role
from .status_uang_muka import status_detail_dp

NOL = Decimal("0")


def _cd():
    from ..routers import customer_deposits as CD  # malas: router mengimpor modul ini
    return CD


async def _uang_muka(conn, tid: str, deposit_id):
    dep = await conn.fetchrow("SELECT * FROM customer_deposits WHERE id = $1 AND tenant_id = $2", deposit_id, tid)
    if not dep:
        raise HTTPException(status_code=404, detail="Uang muka tidak ditemukan.")
    return dep


async def _periode(conn, tid: str, tanggal) -> None:
    r = await conn.fetchrow(
        "SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2", tid, tanggal)
    if r and r["status"] != "OPEN":
        raise HTTPException(status_code=400, detail=tg.periode_tertutup(None, r["status"]))


def _jurnal(source_type: str, description: str, journal_date, total, lines: list) -> dict:
    """lines = [(account_id, debit, credit, memo)] berurutan = line_number 1..n."""
    return {"source_type": source_type, "description": description, "journal_date": journal_date, "total": total,
            "lines": lines}


async def rencana_terapkan(conn, ctx: dict, deposit_id, body) -> dict:
    """Terapkan uang muka ke faktur (apply_deposit_core)."""
    CD, tid = _cd(), ctx["tenant_id"]
    dep = await _uang_muka(conn, tid, deposit_id)
    if dep["status"] not in ("posted", "partial"):
        raise HTTPException(
            status_code=400,
            detail=f"{await label_deposit(conn, tid, deposit_id)} berstatus {CD._status_dp(status_detail_dp(dep['status'], dep['amount_applied'], dep['amount_refunded']))} tidak bisa diterapkan.",
        )
    # PIHAK SAMA (13 Sep 2026): semua faktur diperiksa SEBELUM tulis apa pun.
    pelanggan_dp = normalisasi_pihak(dep["customer_id"], "Pelanggan uang muka")
    for app in body.applications:
        inv = await conn.fetchrow("SELECT invoice_number, customer_id FROM sales_invoices WHERE id = $1 AND tenant_id = $2",
                                  UUID(app.invoice_id), tid)
        if not inv:
            raise HTTPException(status_code=400, detail="Faktur tidak ditemukan.")
        pastikan_pihak_sama(pelanggan_dp, inv["customer_id"], f"Faktur {inv['invoice_number']}")
    remaining = await CD.compute_deposit_remaining(conn, tid, deposit_id)
    total = sum(app.amount for app in body.applications)
    if total > remaining:
        raise HTTPException(
            status_code=400,
            detail=f"Nilai penerapan {CD._rp_dp(total)} melebihi sisa {(await label_deposit(conn, tid, deposit_id)).lower()} {CD._rp_dp(remaining)}.",
        )
    application_date = body.application_date or await CD.tanggal_dokumen(conn, tid)
    akun_dp = await resolve_account_id_by_role(conn, tid, AccountRole.CUSTOMER_DEPOSIT_LIABILITY)
    akun_ar = await resolve_account_id_by_role(conn, tid, AccountRole.AR_TRADE)
    await CD._assert_ar_side_is_ar_trade(conn, tid, akun_ar)
    label_k = await label_deposit(conn, tid, deposit_id, kapital=True)
    dipakai, items = {}, []
    for app in body.applications:
        inv_id = UUID(app.invoice_id)
        invoice = await conn.fetchrow(
            """SELECT id, customer_id, customer_name, invoice_number, total_amount, status
               FROM sales_invoices WHERE id = $1 AND tenant_id = $2""", inv_id, tid)
        if not invoice:
            raise HTTPException(status_code=400, detail="Faktur tidak ditemukan.")
        # penulis menulis baris demi baris: baris sebelumnya ke faktur yang sama sudah memotong sisanya
        sisa = await CD.get_invoice_remaining_from_journal(conn, tid, inv_id) - dipakai.get(inv_id, NOL)
        if app.amount > sisa:
            raise HTTPException(status_code=400, detail="Nilai penerapan melebihi sisa tagihan faktur.")
        ada = inv_id in dipakai or await conn.fetchval(
            """SELECT id FROM customer_deposit_applications
               WHERE deposit_id = $1 AND invoice_id = $2 AND COALESCE(status, 'active') <> 'reversed'""",
            deposit_id, inv_id)
        if ada:
            raise HTTPException(status_code=400, detail="Uang muka ini sudah diterapkan ke faktur itu.")
        dipakai[inv_id] = dipakai.get(inv_id, NOL) + Decimal(str(app.amount))  # auto-apply SI: SimpleNamespace, bukan skema
        items.append({
            "invoice": invoice, "invoice_id": inv_id, "invoice_key": app.invoice_id, "amount": app.amount,
            "jurnal": _jurnal("DEPOSIT_APPLICATION", f"Apply Deposit {dep['deposit_number']} to {invoice['invoice_number']}",
                              application_date, app.amount,
                              [(akun_dp, app.amount, 0, f"Aplikasi {label_k} - {invoice['invoice_number']}"),
                               (akun_ar, 0, app.amount, f"Pelunasan dari Deposit - {dep['deposit_number']}")]),
        })
    return {"dep": dep, "application_date": application_date, "total": total, "items": items}


async def rencana_lepas(conn, ctx: dict, deposit_id, application_id) -> dict:
    """Lepas satu penerapan (reverse_deposit_application_core). sudah=True = sudah dilepas (penulis membalas sukses lama)."""
    CD, tid = _cd(), ctx["tenant_id"]
    app_row = await conn.fetchrow(
        "SELECT * FROM customer_deposit_applications WHERE id = $1 AND deposit_id = $2 AND tenant_id = $3",
        application_id, deposit_id, tid)
    if not app_row:
        raise HTTPException(status_code=404, detail="Penerapan uang muka tidak ditemukan.")
    if (app_row["status"] or "active") == "reversed" or app_row["reversed_by_id"]:
        return {"sudah": True, "app_row": app_row}
    asal = app_row["journal_id"]
    if not asal:
        raise HTTPException(status_code=400, detail="Penerapan uang muka ini tidak punya jurnal untuk dibalik.")
    hari_ini = await CD.tanggal_dokumen(conn, tid)
    await _periode(conn, tid, hari_ini)
    orig_je = await conn.fetchrow("SELECT id, reversed_by_id, status FROM journal_entries WHERE id = $1 AND tenant_id = $2",
                                  asal, tid)
    if orig_je and orig_je["reversed_by_id"]:
        raise HTTPException(status_code=400, detail="Jurnal penerapan uang muka ini sudah dibalik.")
    baris = await conn.fetch("SELECT * FROM journal_lines WHERE journal_id = $1 ORDER BY line_number", asal)
    if not baris:
        raise HTTPException(status_code=400, detail="Jurnal penerapan uang muka ini kosong.")
    sisi_ar = next((ln for ln in baris if (ln["credit"] or 0) > 0), None)
    if sisi_ar is None:
        raise HTTPException(status_code=500, detail="Invariant #7: original apply journal missing AR-side credit line")
    await CD._assert_ar_side_is_ar_trade(conn, tid, sisi_ar["account_id"])
    jumlah = app_row["amount_applied"]
    return {"sudah": False, "app_row": app_row, "original_journal_id": asal, "amount": jumlah, "hari_ini": hari_ini,
            "jurnal": _jurnal("DEPOSIT_APPLICATION", f"Un-apply Deposit application {app_row['invoice_number'] or application_id}",
                              hari_ini, jumlah,
                              [(ln["account_id"], ln["credit"], ln["debit"], f"Reversal - {ln['memo'] or ''}") for ln in baris])}


async def rencana_kembalikan(conn, ctx: dict, deposit_id, body) -> dict:
    """Kembalikan dana uang muka (refund_deposit_core)."""
    CD, tid = _cd(), ctx["tenant_id"]
    dep = await _uang_muka(conn, tid, deposit_id)
    if dep["status"] not in ("posted", "partial"):
        raise HTTPException(
            status_code=400,
            detail=f"{await label_deposit(conn, tid, deposit_id)} berstatus {CD._status_dp(status_detail_dp(dep['status'], dep['amount_applied'], dep['amount_refunded']))} tidak bisa dikembalikan.",
        )
    remaining = await CD.compute_deposit_remaining(conn, tid, deposit_id)
    if body.amount > remaining:
        raise HTTPException(
            status_code=400,
            detail=f"Pengembalian {CD._rp_dp(body.amount)} melebihi sisa {(await label_deposit(conn, tid, deposit_id)).lower()} {CD._rp_dp(remaining)}.",
        )
    akun_kas = UUID(body.account_id)
    account = await conn.fetchrow(
        "SELECT id, account_code, name, account_type FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2", akun_kas, tid)
    if not account:
        raise HTTPException(status_code=400, detail="Rekening pengembalian tidak ditemukan.")
    if account["account_type"] != "ASSET":
        raise HTTPException(status_code=400, detail="Rekening pengembalian harus akun kas/bank.")
    akun_dp = await resolve_account_id_by_role(conn, tid, AccountRole.CUSTOMER_DEPOSIT_LIABILITY)
    await _periode(conn, tid, body.refund_date)
    label_k = await label_deposit(conn, tid, deposit_id, kapital=True)
    # FIX_R9_REFUND: cermin diturunkan dari CoA KREDIT jurnal (akun_kas), bukan body.bank_account_id
    ba = await conn.fetchrow(
        "SELECT id, account_name FROM bank_accounts WHERE tenant_id = $1 AND coa_id = $2 AND is_active = true", tid, akun_kas)
    return {"dep": dep, "akun_kas": akun_kas,
            "jurnal": _jurnal("DEPOSIT_REFUND", f"Refund Deposit {dep['deposit_number']} - {dep['customer_name']}",
                              body.refund_date, body.amount,
                              [(akun_dp, body.amount, 0, f"Refund {label_k} - {dep['deposit_number']}"),
                               (akun_kas, 0, body.amount, f"Bayar Refund - {dep['customer_name']}")]),
            "bank": ({"bank_account_id": ba["id"], "bank_account": ba["account_name"], "amount": -body.amount,
                      "transaction_type": "withdrawal"} if ba else None)}


async def rencana_batal(conn, ctx: dict, deposit_id, body) -> dict:
    """Batalkan uang muka (void_deposit_core, SESUDAH kunci DEPOSIT_POST -> DEPOSIT). Draf = hapus=True."""
    CD, tid = _cd(), ctx["tenant_id"]
    if not (body.reason or "").strip():
        raise HTTPException(status_code=422, detail="Alasan pembatalan wajib diisi.")
    await CD.tolak_void_bila_terekonsiliasi(conn, tid, deposit_id)  # rekon bank: 409 sebelum tulisan
    dep = await _uang_muka(conn, tid, deposit_id)
    if dep["status"] == "void":
        raise HTTPException(status_code=400, detail="Uang muka sudah dibatalkan.")
    if dep["status"] == "draft":
        return {"dep": dep, "hapus": True}
    if (dep["amount_applied"] or 0) > 0:
        raise HTTPException(status_code=400, detail="Uang muka sudah diterapkan ke faktur. Lepas penerapannya dulu.")
    if (dep["amount_refunded"] or 0) > 0:
        raise HTTPException(status_code=400,
                            detail="Uang muka sudah dikembalikan (sebagian) ke pelanggan, jadi tidak bisa dibatalkan.")
    rp = await conn.fetchrow("SELECT payment_number FROM receive_payments WHERE tenant_id = $1 AND created_deposit_id = $2",
                             tid, deposit_id)
    if rp:
        raise HTTPException(status_code=400, detail=(
            f"Uang muka ini berasal dari kelebihan bayar pembayaran {rp['payment_number']}. Gunakan pengembalian dana, atau "
            "batalkan pembayarannya."))
    hari_ini = await CD.tanggal_dokumen(conn, tid)
    await _periode(conn, tid, hari_ini)
    jurnal, bank = None, None
    if dep["journal_id"]:
        baris = await conn.fetch("SELECT * FROM journal_lines WHERE journal_id = $1 ORDER BY line_number", dep["journal_id"])
        jurnal = _jurnal("CUSTOMER_DEPOSIT", f"Void {dep['deposit_number']} - {dep['customer_name']}", hari_ini, dep["amount"],
                         [(ln["account_id"], ln["credit"], ln["debit"], f"Reversal - {ln['memo'] or ''}") for ln in baris])
        asal = await conn.fetchrow(
            """SELECT bt.id, bt.amount, bt.transaction_type, ba.account_name FROM bank_transactions bt
               LEFT JOIN bank_accounts ba ON ba.id = bt.bank_account_id AND ba.tenant_id = bt.tenant_id
               WHERE bt.journal_id = $1 AND bt.tenant_id = $2 ORDER BY bt.created_at ASC LIMIT 1""", dep["journal_id"], tid)
        if asal:
            bank = {"original_bank_transaction_id": asal["id"], "bank_account": asal["account_name"], "amount": -asal["amount"],
                    "transaction_type": JENIS_PEMBALIK.get(asal["transaction_type"], "adjustment")}
    return {"dep": dep, "hapus": False, "hari_ini": hari_ini, "jurnal": jurnal, "bank": bank}


async def tulis_baris(conn, journal_id, lines: list) -> None:
    """Baris jurnal dari rencana (line_number = urutan rencana) -- dipakai keempat penulis."""
    import uuid as _uuid
    for n, (akun, debit, kredit, memo) in enumerate(lines, 1):
        await conn.execute(
            """INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""", _uuid.uuid4(), journal_id, n, akun, debit, kredit, memo)


# ---------------- pratinjau: bentuk keluaran dari rencana (bentuk SAMA dengan pratinjau lama) ----------------

def _jurnal_rencana(aksi: str, p: dict) -> list:
    if aksi == "apply":
        return [it["jurnal"] for it in p["items"]]
    return [p["jurnal"]] if p.get("jurnal") else []


async def jurnal_dan_bank(conn, tid: str, aksi: str, p: dict) -> tuple:
    """-> (journals, bank_transactions) seperti _jurnal_tx_ini lama; journal_number = None (belum diberi nomor)."""
    js = _jurnal_rencana(aksi, p)
    ids = list({ln[0] for j in js for ln in j["lines"]})
    coa = {r["id"]: r for r in await conn.fetch(
        "SELECT id, account_code, name FROM chart_of_accounts WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tid, ids)} if ids else {}
    jurnal = [{"journal_number": None, "source_type": j["source_type"], "description": j["description"],
               "journal_date": j["journal_date"].isoformat(),
               "lines": [{"account_code": coa[a]["account_code"], "account_name": coa[a]["name"], "debit": float(d or 0),
                          "credit": float(k or 0)} for a, d, k, _ in j["lines"] if a in coa]} for j in js]
    b = p.get("bank")
    bank = [{"bank_account": b["bank_account"], "amount": float(b["amount"] or 0), "transaction_type": b["transaction_type"]}] \
        if b and b.get("bank_account") is not None else []
    return jurnal, bank


async def _neto_liabilitas(conn, tid: str, journal_id) -> Decimal:
    """Kontribusi satu jurnal EFEKTIF ke sisa uang muka (kredit - debit akun CUSTOMER_DEPOSIT_LIABILITY)."""
    akun = await resolve_account_id_by_role(conn, tid, AccountRole.CUSTOMER_DEPOSIT_LIABILITY)
    v = await conn.fetchval(
        """SELECT COALESCE(SUM(jl.credit) - SUM(jl.debit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
           WHERE je.id = $1 AND je.tenant_id = $2 AND jl.account_id = $3 AND is_effective_journal(je.id)""", journal_id, tid, akun)
    return Decimal(str(v or 0))


async def keadaan_sesudah(conn, tid: str, deposit_id, aksi: str, p: dict) -> dict:
    """Bentuk _keadaan_dp SESUDAH aksi, dihitung tanpa menulis: cache = aturan trigger update_customer_deposit_status (V384),
    sisa = sisa jurnal sekarang +/- jurnal rencana (journal-derived, Law 16)."""
    CD = _cd()
    if aksi == "void" and p.get("hapus"):
        return {"exists": False}
    r = await conn.fetchrow(
        "SELECT status, amount, amount_applied, amount_refunded FROM customer_deposits WHERE id = $1 AND tenant_id = $2",
        deposit_id, tid)
    sisa = await CD.compute_deposit_remaining(conn, tid, deposit_id)
    if aksi == "reverse" and p.get("sudah"):
        return await CD._keadaan_dp(conn, tid, deposit_id)
    if aksi == "void":
        st, dipakai, kembali = "void", r["amount_applied"], r["amount_refunded"]  # trigger tak jalan: hanya status berubah
        if p.get("jurnal"):
            sisa -= await _neto_liabilitas(conn, tid, p["dep"]["journal_id"])
    else:
        dipakai = Decimal(str(await conn.fetchval(
            "SELECT COALESCE(SUM(amount_applied), 0) FROM customer_deposit_applications WHERE deposit_id = $1 "
            "AND COALESCE(status, 'active') <> 'reversed'", deposit_id)))
        kembali = Decimal(str(await conn.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM customer_deposit_refunds WHERE deposit_id = $1", deposit_id)))
        if aksi == "apply":
            dipakai, sisa = dipakai + p["total"], sisa - p["total"]
        elif aksi == "refund":
            kembali, sisa = kembali + p["jurnal"]["total"], sisa - p["jurnal"]["total"]
        elif aksi == "reverse":
            dipakai, sisa = dipakai - p["amount"], sisa - await _neto_liabilitas(conn, tid, p["original_journal_id"])
        st = r["status"]
        if st not in ("draft", "void"):
            jumlah = Decimal(str(r["amount"] or 0))
            st = "applied" if dipakai + kembali >= jumlah else ("partial" if dipakai + kembali > 0 else "posted")
        else:
            dipakai, kembali = r["amount_applied"], r["amount_refunded"]
    return {"exists": True, "status": st, "status_detail": status_detail_dp(st, dipakai, kembali), "amount": float(r["amount"] or 0),
            "amount_applied": float(dipakai or 0), "amount_refunded": float(kembali or 0), "remaining": float(sisa)}


def sisa_faktur_sesudah(aksi: str, p: dict, f0: dict) -> dict:
    """f0 = {kunci faktur: Decimal sisa sebelum} -> {kunci: Decimal sisa sesudah} (jurnal rencana memotong/mengembalikan piutang)."""
    f1 = dict(f0)
    if aksi == "apply":
        for it in p["items"]:
            if it["invoice_key"] in f1:
                f1[it["invoice_key"]] -= it["amount"]
    elif aksi == "reverse" and not p.get("sudah"):
        k = str(p["app_row"]["invoice_id"])
        if k in f1:
            f1[k] += p["amount"]
    return f1
