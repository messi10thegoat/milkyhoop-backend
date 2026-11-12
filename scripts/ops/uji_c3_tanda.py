"""Pembantu harness BF-1c3 (dipakai DUA pohon kode di salinan DB yang sama): Transfer (buat+posting+void) dan Uang Muka Pelanggan (buat+auto_post+void) lewat RUTE LAMA, lalu
TANDA TANGAN jurnal + bank_transactions yang dinormalkan (nomor acak diganti pola, rekening/akun kembar diberi label). Pohon LAMA (handler inline) dan BARU (inti hasil ekstraksi) harus identik.
Modul ini tak mengimpor `app` (diberi fungsi rute oleh pemanggil)."""
import json
import re
import uuid
from datetime import date

USER = "8b0f1c9e-2d4a-4e6b-9c1d-5a7e3f2b1c00"
TGL = date(2026, 10, 5)


def norm(teks):
    teks = re.sub(r"UJI C3 [0-9a-f]{6}", "UJI C3 X", str(teks)) if teks is not None else None
    return re.sub(r"\b(?:RV-|VD-)?[A-Z]{2,5}-(?:\d{4}-)?[0-9A-F]{4,8}\b", "NOMOR", teks) if teks is not None else None


async def rekening_kembar(c, tid, nomor, tipe="bank", coa_baru=True, saldo=10000000):
    coa, kode = uuid.uuid4(), f"1-9{uuid.uuid4().int % 10000:04d}"
    await c.execute("INSERT INTO chart_of_accounts (id, tenant_id, account_code, name, account_type, normal_balance, is_active, is_header, is_detail, is_cash) "
                    "VALUES ($1, $2, $3, $4, 'ASSET', 'DEBIT', true, false, true, true)", coa, tid, kode, f"UJI C3 {kode}")
    rid = uuid.uuid4()
    await c.execute("INSERT INTO bank_accounts (id, tenant_id, account_name, account_number, bank_name, coa_id, account_type, created_by) "
                    "VALUES ($1, $2, $3, $4, 'BCA', $5, $7, $6::uuid)", rid, tid, f"UJI C3 {uuid.uuid4().hex[:6]}", nomor, coa, USER, tipe)
    if saldo:       # saldo awal: jurnal POSTED Dr rekening / Cr Modal Pemilik (3-10100), di salinan saja
        lawan = await c.fetchval("SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = '3-10100'", tid)
        je = uuid.uuid4()
        async with c.transaction():
            await c.execute("INSERT INTO journal_entries (id, tenant_id, journal_number, journal_date, description, source_type, source_id, status, total_debit, total_credit, created_by) "
                            "VALUES ($1, $2, $3, '2026-09-01', 'saldo awal uji c3', 'MANUAL', $4, 'POSTED', $5, $5, $6::uuid)", je, tid, f"UJI3-{je.hex[:8]}", str(je), saldo, USER)
            await c.execute("INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit) VALUES ($1, 1, $2, $4, 0), ($1, 2, $3, 0, $4)", je, coa, lawan, saldo)
    return rid, coa


async def pelanggan(c, tid):
    return await c.fetchrow("SELECT id, nama FROM customers WHERE tenant_id = $1 AND deleted_at IS NULL AND COALESCE(is_active, true) ORDER BY created_at, id LIMIT 1", tid)


async def tanda_jurnal(c, tid, jid, peta):
    """Satu jurnal: kepala, baris (akun -> label), bank_transactions yang menunjuknya; peta = {str(coa_id): label, str(rekening_id): label}."""
    je = await c.fetchrow("SELECT journal_date, description, source_type, status, total_debit, total_credit, reversal_of_id IS NOT NULL AS pembalik, reversed_by_id IS NOT NULL AS dibalik "
                          "FROM journal_entries WHERE tenant_id = $1 AND id = $2", tid, jid)
    garis = []
    for r in await c.fetch("SELECT jl.line_number, jl.account_id, jl.debit, jl.credit, jl.memo FROM journal_lines jl JOIN journal_entries e ON e.id = jl.journal_id "
                           "WHERE e.tenant_id = $1 AND e.id = $2 ORDER BY jl.line_number", tid, jid):
        lab = peta.get(str(r["account_id"])) or await c.fetchval("SELECT account_code FROM chart_of_accounts WHERE tenant_id = $1 AND id = $2", tid, r["account_id"])
        garis.append([r["line_number"], lab, str(r["debit"]), str(r["credit"]), norm(r["memo"])])
    bts = []
    for b in await c.fetch("SELECT bank_account_id, transaction_date, transaction_type, amount, running_balance, description, status, reference_type, origin_type, source_module, void_reason "
                           "FROM bank_transactions WHERE tenant_id = $1 AND journal_id = $2 ORDER BY transaction_type, amount", tid, jid):
        bts.append([peta.get(str(b["bank_account_id"]), "?"), str(b["transaction_date"]), b["transaction_type"], str(b["amount"]), str(b["running_balance"]), norm(b["description"]),
                    b["status"], b["reference_type"], b["origin_type"], b["source_module"], norm(b["void_reason"])])
    return {"je": [str(je["journal_date"]), norm(je["description"]), je["source_type"], je["status"], str(je["total_debit"]), str(je["total_credit"]), je["pembalik"], je["dibalik"]],
            "garis": garis, "bt": bts}


async def tanda_dengan_pembalik(c, tid, jid, peta):
    s = {"asli": await tanda_jurnal(c, tid, jid, peta)}
    rv = await c.fetchval("SELECT id FROM journal_entries WHERE tenant_id = $1 AND reversal_of_id = $2", tid, jid)
    s["pembalik"] = await tanda_jurnal(c, tid, rv, peta) if rv else None
    return s


async def jalankan(pool, tid, req, Response, BTR, CD, BodyTrf, BodyVoidTrf, BodyDp, BodyVoidDp):
    """Operasi deterministik lewat RUTE LAMA: transfer A->B 300000 (+ void), transfer B->A 50000 (tetap), uang muka 500000 (+ void). -> daftar tanda tangan berurutan."""
    async with pool.acquire() as c:
        a, coa_a = await rekening_kembar(c, tid, "8888880301")
        b, coa_b = await rekening_kembar(c, tid, "8888880302")
        p = await pelanggan(c, tid)
    peta = {str(coa_a): "A", str(coa_b): "B", str(a): "A", str(b): "B"}
    hasil, jids = [], []
    for dari, ke, n in ((a, b, 300000), (b, a, 50000)):
        r = await BTR.create_bank_transfer(req(tid), BodyTrf(from_bank_id=str(dari), to_bank_id=str(ke), amount=n, fee_amount=0, transfer_date=TGL, ref_no="R", notes="uji", auto_post=True), Response())
        jids.append((r["data"]["id"], uuid.UUID(str(r["data"]["journal_id"]))))
        hasil.append({"respons": {"status": r["data"]["status"], "nomor": norm(r["data"]["transfer_number"]), "pesan": r["message"]}})
    v = await BTR.void_bank_transfer(req(tid), uuid.UUID(jids[0][0]), BodyVoidTrf(reason="uji batal transfer"), Response())
    hasil.append({"void_transfer": {"status": v["data"]["status"] if isinstance(v.get("data"), dict) and "status" in v["data"] else str(sorted((v.get("data") or {}).keys()))}})
    d = await CD.create_customer_deposit(req(tid), BodyDp(customer_id=str(p["id"]), customer_name=p["nama"], amount=500000, deposit_date=TGL, payment_method="transfer",
                                                         account_id=str(coa_a), bank_account_id=str(a), reference="R", notes="uji", auto_post=True))
    dj = uuid.UUID(str(d["data"]["journal_id"]))
    hasil.append({"respons_dp": {"status": d["data"]["status"], "pesan": d["message"], "nomor": norm(d["data"]["deposit_number"])}})
    v2 = await CD.void_customer_deposit(req(tid), uuid.UUID(d["data"]["id"]), BodyVoidDp(reason="uji batal dp"), Response())
    hasil.append({"void_dp": {"kunci": sorted((v2.get("data") or {}).keys()) if isinstance(v2, dict) else str(type(v2))}})
    async with pool.acquire() as c:
        for _, j in jids:
            hasil.append({"tanda_transfer": await tanda_dengan_pembalik(c, tid, j, peta)})
        hasil.append({"tanda_dp": await tanda_dengan_pembalik(c, tid, dj, peta)})
        hasil.append({"saldo": [str(await c.fetchval("SELECT COALESCE(SUM(jl.debit - jl.credit), 0) FROM journal_lines jl JOIN journal_entries e ON e.id = jl.journal_id "
                                                      "WHERE e.tenant_id = $1 AND e.status = 'POSTED' AND jl.account_id = $2", tid, x)) for x in (coa_a, coa_b)]})
    return hasil


def simpan(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True, default=str)


def muat(path):
    with open(path) as f:
        return json.load(f)
