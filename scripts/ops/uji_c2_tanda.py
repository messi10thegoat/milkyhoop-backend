"""Pembantu harness BF-1c2 (dipakai DUA pohon kode di salinan DB yang sama): urutan operasi Uang Masuk/Keluar + void deterministik, dan TANDA TANGAN (sidik) jurnal +
bank_transactions yang dinormalkan (nomor acak MT-/RV-MT- diganti pola). Pohon LAMA (master, handler inline) dan BARU (c2, penulis hasil ekstraksi) menjalankan operasi SAMA pada
rekening kembar; tanda tangan harus IDENTIK. Modul ini tak mengimpor `app` (diberi fungsi rute oleh pemanggil)."""
import json
import re
import uuid
from datetime import date
from decimal import Decimal as D

USER = "8b0f1c9e-2d4a-4e6b-9c1d-5a7e3f2b1c00"      # tetap: created_by/voided_by sama di kedua pohon
OPS = [("in", 1000000, date(2026, 10, 1), "4-90000", "setoran uji", "Budi"),
       ("out", 250000, date(2026, 10, 2), "5-20900", "beli lakban", None),
       ("in", 75500, date(2026, 10, 3), "3-10100", "tambah modal", None),
       ("out", 10000, date(2026, 10, 4), "5-20850", "biaya admin", "Bank")]
VOIDS = [(1, "salah catat"), (2, "uji batal")]       # indeks OPS yang di-void, urut


def norm(teks):
    return re.sub(r"(MT|RV-MT)-[0-9A-F]{8}", r"\1-*", str(teks)) if teks is not None else None


async def rekening_kembar(c, tid, nomor):
    coa = await c.fetchval("SELECT id FROM chart_of_accounts c WHERE tenant_id = $1 AND account_type = 'ASSET' AND NOT COALESCE(is_header, false) "
                           "AND NOT EXISTS (SELECT 1 FROM bank_accounts b WHERE b.coa_id = c.id) ORDER BY account_code LIMIT 1", tid)
    rid = uuid.uuid4()
    await c.execute("INSERT INTO bank_accounts (id, tenant_id, account_name, account_number, bank_name, coa_id, account_type, created_by) "
                    "VALUES ($1, $2, $3, $4, 'BCA', $5, 'bank', $6::uuid)", rid, tid, f"UJI C2 {uuid.uuid4().hex[:6]}", nomor, coa, USER)
    return rid, coa


async def kode_ke_id(c, tid, kode):
    return await c.fetchval("SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = $2", tid, kode)


async def tanda_bt(c, tid, bt_id, coa_bank):
    """Tanda tangan SATU bank_transaction: barisnya, jurnalnya, baris jurnal (kode akun / BANK, debit, kredit, memo), penanda batal."""
    bt = await c.fetchrow("SELECT transaction_date, transaction_type, amount, running_balance, description, payee_payer, origin_type, status, reference_type, reference_number, "
                          "source_module, void_reason, voided_by, COALESCE(is_reconciled, false) AS rekon, journal_id FROM bank_transactions WHERE tenant_id = $1 AND id = $2", tid, bt_id)
    je = await c.fetchrow("SELECT journal_date, description, source_type, status, total_debit, total_credit, reversal_of_id IS NOT NULL AS pembalik, reversed_by_id IS NOT NULL AS dibalik, "
                          "source_id = $3 AS sumber_bt, journal_number FROM journal_entries WHERE tenant_id = $1 AND id = $2", tid, bt["journal_id"], str(bt_id))
    garis = []
    for r in await c.fetch("SELECT jl.line_number, jl.account_id, jl.debit, jl.credit, jl.memo FROM journal_lines jl JOIN journal_entries e ON e.id = jl.journal_id "
                           "WHERE e.tenant_id = $1 AND e.id = $2 ORDER BY jl.line_number", tid, bt["journal_id"]):
        kode = "BANK" if r["account_id"] == coa_bank else await c.fetchval("SELECT account_code FROM chart_of_accounts WHERE tenant_id = $1 AND id = $2", tid, r["account_id"])
        garis.append([r["line_number"], kode, str(r["debit"]), str(r["credit"]), norm(r["memo"])])
    return {"bt": {k: (str(v) if k in ("amount", "running_balance", "transaction_date", "voided_by") and v is not None else norm(v) if isinstance(v, str) else v) for k, v in dict(bt).items() if k != "journal_id"},
            "je": {k: (str(v) if k in ("journal_date", "total_debit", "total_credit") else norm(v) if isinstance(v, str) else v) for k, v in dict(je).items()}, "garis": garis}


async def jalankan(pool, tid, coa_bank, rid, buat, batal, req, Response, BodyBuat, BodyBatal):
    """Menjalankan OPS lalu VOIDS lewat fungsi RUTE (buat/batal) di pohon yang sedang aktif; mengembalikan daftar tanda tangan berurutan."""
    id_bt, hasil = [], []
    for arah, jumlah, tgl, kode, desk, kontak in OPS:
        async with pool.acquire() as c:
            lawan = await kode_ke_id(c, tid, kode)
        r = await buat(req(tid), rid, BodyBuat(direction=arah, amount=jumlah, transaction_date=tgl, description=desk, contra_account_id=str(lawan), contact_name=kontak), Response())
        id_bt.append(uuid.UUID(r["data"]["id"]))
        hasil.append({"respons": {"direction": r["data"]["direction"], "amount": r["data"]["amount"], "transaction_date": r["data"]["transaction_date"], "description": r["data"]["description"],
                                  "nomor": norm(r["data"]["journal_number"])}})
    for i, alasan in VOIDS:
        r = await batal(req(tid), id_bt[i], BodyBatal(reason=alasan), Response())
        hasil.append({"void_respons": {"status": r["data"]["status"]}})
    async with pool.acquire() as c:
        for n, bt in enumerate(id_bt):
            hasil[n]["tanda"] = await tanda_bt(c, tid, bt, coa_bank)
        for i, _ in VOIDS:
            cermin = await c.fetchval("SELECT id FROM bank_transactions WHERE tenant_id = $1 AND reference_type = 'manual_void' AND reference_id = $2", tid, id_bt[i])
            hasil.append({"cermin_void": await tanda_bt(c, tid, cermin, coa_bank)})
    return hasil


def simpan(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True, default=str)


def muat(path):
    with open(path) as f:
        return json.load(f)
