"""GERBANG unit 2 — void SATU transaksi bank manual. Dua sisi, KODE LAMA vs BARU, nol baris menetap.

Cara kerja:
  - SATU koneksi, satu transaksi LUAR yang di-ROLLBACK di akhir.
  - get_pool modul di-patch -> handler memakai koneksi itu; conn.transaction()
    handler menjadi SAVEPOINT. Tiap skenario dibungkus savepoint sendiri dan
    dibatalkan sesudah diukur (hasil disimpan di MEMORI Python, bukan tabel --
    pelajaran V242: ROLLBACK TO SAVEPOINT menghapus catatan hasil di tabel).
  - Transaksi manual SINTETIS dibuat dgn bentuk PERSIS pembuat hidup
    (bank_accounts.py ~1833: MT-<hex8>, DRAFT -> baris -> POSTED, btx MANUAL tanpa
    ref, transaction_number NULL, source_id jurnal = id btx).
  - Transaksi manual NYATA (Rp 10 jt, 5 Sep) TIDAK dipilih di sisi mana pun:
    semua sasaran "manual" adalah sintetis, dan kueri sasaran nyata MENGECUALIKAN
    baris ber-origin MANUAL.
  - Tanpa sleep, tanpa prompt: FOR UPDATE atas baris nyata ditahan sesingkat mungkin.

Jalankan di kontainer: cd /app/backend/api_gateway && python3 /tmp/gerbang_void_transaksi_bank.py
"""
import asyncio
import importlib.util
import os
import sys
import uuid

sys.path.insert(0, "/app/backend/api_gateway")

import asyncpg  # noqa: E402
from fastapi import HTTPException  # noqa: E402

TENANT = "kaos-biru-konveksi"
BARU = "/tmp/kasbank_v2_baru.py"
hasil = []  # (sisi, nama, ok, keterangan)
HARAP_CACAH = 18


def catat(sisi, nama, ok, ket=""):
    hasil.append((sisi, nama, bool(ok), ket))


def muat(nama_modul, path):
    spec = importlib.util.spec_from_file_location("app.routers." + nama_modul, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules["app.routers." + nama_modul] = m
    spec.loader.exec_module(m)
    return m


class Req:
    class state:
        user = None


class Body:
    def __init__(self, reason):
        self.reason = reason


async def r9(conn, bank_account_id):
    """R9 RESMI, bentuk PERSIS check_3 / skill banksync.

    ⚠️ Terukur 13 Sep 2026: `je.status='POSTED'` di dalam ON sebuah LEFT JOIN
    TIDAK membuang baris jurnal non-POSTED -- R9 resmi menjumlahkan baris jurnal
    berstatus APA PUN (termasuk 16 jurnal VOID lama). Dipakai apa adanya karena
    ini definisi yang disepakati; pergeseran versi POSTED-saja diukur terpisah.
    """
    await conn.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT)
    return await conn.fetchval(
        """
        WITH bank_coa AS (SELECT ba.id AS bank_account_id, ba.coa_id FROM bank_accounts ba WHERE ba.id = $1),
        journal_balance AS (
            SELECT bc.bank_account_id, COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) AS ledger_balance
            FROM bank_coa bc
            LEFT JOIN journal_lines jl ON jl.account_id = bc.coa_id
            LEFT JOIN journal_entries je ON je.id = jl.journal_id AND je.status = 'POSTED'
            GROUP BY bc.bank_account_id),
        bank_txn_bal AS (
            SELECT bank_account_id, COALESCE(SUM(amount), 0) AS txn_balance
            FROM bank_transactions WHERE tenant_id = $2 GROUP BY bank_account_id)
        SELECT COALESCE(jb.ledger_balance, 0) - COALESCE(btb.txn_balance, 0)
        FROM bank_coa bc
        LEFT JOIN journal_balance jb ON jb.bank_account_id = bc.bank_account_id
        LEFT JOIN bank_txn_bal btb ON btb.bank_account_id = bc.bank_account_id
        """,
        bank_account_id, TENANT,
    )


async def r9_posted_saja(conn, bank_account_id):
    """Pembanding: hanya jurnal POSTED. Tak harus 0 (data VOID lama), tapi void TAK BOLEH menggesernya."""
    return await conn.fetchval(
        """
        WITH ba AS (SELECT id, coa_id FROM bank_accounts WHERE id = $1)
        SELECT (SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)
                  FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
                 WHERE jl.account_id = (SELECT coa_id FROM ba) AND je.status = 'POSTED'
                   AND je.tenant_id = $2)
             - (SELECT COALESCE(SUM(amount), 0) FROM bank_transactions
                 WHERE bank_account_id = $1 AND tenant_id = $2)
        """,
        bank_account_id, TENANT,
    )


async def buat_sintetis(conn, user_id, amount=12345):
    """Bentuk PERSIS pembuat hidup, arah Uang Keluar."""
    ba = await conn.fetchrow(
        "SELECT ba.id, ba.coa_id FROM bank_accounts ba WHERE ba.tenant_id = $1 AND ba.is_active ORDER BY ba.account_name LIMIT 1",
        TENANT,
    )
    contra = await conn.fetchval(
        """SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_type = 'EXPENSE'
           AND COALESCE(is_header, false) = false AND COALESCE(is_active, true) ORDER BY account_code LIMIT 1""",
        TENANT,
    )
    jid = uuid.uuid4()
    jnum = f"MT-{uuid.uuid4().hex[:8].upper()}"
    await conn.execute(
        """INSERT INTO journal_entries (id, tenant_id, journal_number, journal_date, description,
               source_type, source_id, status, total_debit, total_credit, created_by)
           VALUES ($1,$2,$3,CURRENT_DATE,'Uang Keluar - gerbang sintetis','BANK_TRANSACTION',$4,'DRAFT',$5,$5,$6)""",
        jid, TENANT, jnum, str(ba["id"]), amount, user_id,
    )
    await conn.execute(
        "INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo) VALUES ($1,$2,1,$3,$4,0,'sintetis')",
        uuid.uuid4(), jid, contra, amount,
    )
    await conn.execute(
        "INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo) VALUES ($1,$2,2,$3,0,$4,'sintetis')",
        uuid.uuid4(), jid, ba["coa_id"], amount,
    )
    await conn.execute("UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", jid)
    btx = uuid.uuid4()
    await conn.execute(
        """INSERT INTO bank_transactions (id, tenant_id, bank_account_id, transaction_date, transaction_type,
               amount, running_balance, description, payee_payer, origin_type, status, journal_id, created_by)
           VALUES ($1,$2,$3,CURRENT_DATE,'withdrawal',$4,0,'gerbang sintetis',NULL,'MANUAL','POSTED',$5,$6)""",
        btx, TENANT, ba["id"], -amount, jid, user_id,
    )
    await conn.execute("UPDATE journal_entries SET source_id = $1 WHERE id = $2", str(btx), jid)
    return btx, jid, jnum, ba["id"]


async def panggil(mod, btx_id, user_id, reason="uji gerbang"):
    req = Req()
    req.state = type("S", (), {})()
    req.state.user = {"tenant_id": TENANT, "user_id": str(user_id)}
    try:
        r = await mod.void_transaction(req, btx_id, Body(reason))
        return 200, r
    except HTTPException as e:
        return e.status_code, e.detail


async def main():
    dsn = os.environ.get("DATABASE_URL")
    conn = await asyncpg.connect(dsn)  # koneksi GERBANG sendiri (bukan jalur aplikasi)
    lama = muat("kasbank_v2_lama", "/app/backend/api_gateway/app/routers/kasbank_v2.py")
    baru = muat("kasbank_v2_baru", BARU)

    class FakePool:
        def acquire(self):
            class Ctx:
                async def __aenter__(s):
                    return conn
                async def __aexit__(s, *a):
                    return False
            return Ctx()

    async def fake_get_pool():
        return FakePool()

    lama.get_pool = fake_get_pool
    baru.get_pool = fake_get_pool

    user_id = await conn.fetchval(
        "SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", TENANT)

    btx_total0 = await conn.fetchval("SELECT count(*) FROM bank_transactions")
    je_total0 = await conn.fetchval("SELECT count(*) FROM journal_entries")

    luar = conn.transaction()
    await luar.start()
    try:
        # sasaran NYATA berreferensi (bukan MANUAL -> transaksi Rp 10 jt tak mungkin terpilih)
        sasaran = {}
        for ref in ("CUSTOMER_DEPOSIT", "EXPENSE"):
            sasaran[ref] = await conn.fetchval(
                """SELECT id FROM bank_transactions WHERE tenant_id=$1 AND reference_type=$2
                   AND status='POSTED' AND reconciliation_status='UNRECONCILED' AND origin_type <> 'MANUAL'
                   ORDER BY created_at DESC LIMIT 1""",
                TENANT, ref,
            )

        # ---- MERAH pada KODE LAMA: pintu itu NYATA (void berreferensi BERHASIL) ----
        for ref, tid in sasaran.items():
            sp = conn.transaction(); await sp.start()
            code, _ = await panggil(lama, tid, user_id)
            st = await conn.fetchval("SELECT status FROM bank_transactions WHERE id=$1", tid)
            await sp.rollback()
            catat("MERAH lama", f"void {ref} nyata DITERIMA kode lama", code == 200 and st == "VOIDED", f"http={code} status={st}")

        # ---- kode LAMA: void manual kedua -> 500 unik (RV-None) ----
        sp = conn.transaction(); await sp.start()
        a, *_ = await buat_sintetis(conn, user_id)
        b, *_ = await buat_sintetis(conn, user_id)
        c1, _ = await panggil(lama, a, user_id)
        c2, d2 = await panggil(lama, b, user_id)
        await sp.rollback()
        catat("MERAH lama", "void manual pertama 200, KEDUA gagal (RV-None unik)", c1 == 200 and c2 == 500, f"{c1}/{c2}")

        # ---- BARU: berreferensi DITOLAK, nol mutasi ----
        for ref, tid in sasaran.items():
            sp = conn.transaction(); await sp.start()
            je0 = await conn.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id=$1", TENANT)
            code, det = await panggil(baru, tid, user_id)
            st = await conn.fetchval("SELECT status FROM bank_transactions WHERE id=$1", tid)
            je1 = await conn.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id=$1", TENANT)
            await sp.rollback()
            catat("TOLAK baru", f"void {ref} nyata -> 400, tetap POSTED, nol jurnal baru",
                  code == 400 and st == "POSTED" and je0 == je1 and "dokumen asalnya" in str(det),
                  f"http={code} status={st} jurnal {je0}->{je1}")

        # ---- BARU: HIJAU manual sintetis ----
        sp = conn.transaction(); await sp.start()
        btx, jid, jnum, ba_id = await buat_sintetis(conn, user_id)
        r9_0 = await r9(conn, ba_id)
        p_0 = await r9_posted_saja(conn, ba_id)
        code, _ = await panggil(baru, btx, user_id, reason="salah catat")
        asli = await conn.fetchrow("SELECT status, reversed_by_id, reversed_at FROM journal_entries WHERE id=$1", jid)
        rev = await conn.fetchrow("SELECT journal_number, status, reversal_of_id FROM journal_entries WHERE id=$1", asli["reversed_by_id"]) if asli["reversed_by_id"] else None
        baris_ok = await conn.fetchval(
            """SELECT bool_and(r.debit = o.credit AND r.credit = o.debit AND r.account_id = o.account_id)
               FROM journal_lines o JOIN journal_lines r ON r.line_number = o.line_number
               WHERE o.journal_id = $1 AND r.journal_id = $2""", jid, asli["reversed_by_id"]) if rev else False
        mir = await conn.fetchrow("SELECT amount, origin_type, journal_id FROM bank_transactions WHERE reference_id=$1 AND reference_type='manual_void'", btx)
        bt = await conn.fetchrow("SELECT status, voided_by, void_reason, amount FROM bank_transactions WHERE id=$1", btx)
        r9_1 = await r9(conn, ba_id)
        p_1 = await r9_posted_saja(conn, ba_id)
        await sp.rollback()
        catat("HIJAU baru", "HTTP 200", code == 200, str(code))
        catat("HIJAU baru", "pembalik RV-<nomor asli> POSTED + reversal_of_id", rev is not None and rev["journal_number"] == f"RV-{jnum}" and rev["status"] == "POSTED" and rev["reversal_of_id"] == jid, str(rev and dict(rev)))
        catat("HIJAU baru", "baris pembalik = baris asli ditukar", baris_ok is True, str(baris_ok))
        catat("HIJAU baru", "asli TETAP POSTED + reversed_by_id + reversed_at", asli["status"] == "POSTED" and asli["reversed_by_id"] and asli["reversed_at"], str(dict(asli)))
        catat("HIJAU baru", "mirror SYSTEM, amount dinegasi, terikat pembalik", mir is not None and mir["origin_type"] == "SYSTEM" and mir["amount"] == -bt["amount"] and mir["journal_id"] == asli["reversed_by_id"], str(mir and dict(mir)))
        catat("HIJAU baru", "btx VOIDED + voided_by + void_reason", bt["status"] == "VOIDED" and bt["voided_by"] and bt["void_reason"] == "salah catat", str(dict(bt)))
        catat("HIJAU baru", "R9 resmi rekening itu 0,00 sebelum DAN sesudah", r9_0 == 0 and r9_1 == 0, f"{r9_0} -> {r9_1}")
        catat("HIJAU baru", "R9 POSTED-saja TIDAK bergeser oleh void", p_0 == p_1, f"{p_0} -> {p_1}")

        # ---- BARU: void manual KEDUA sukses ----
        sp = conn.transaction(); await sp.start()
        a, *_ = await buat_sintetis(conn, user_id)
        b, *_ = await buat_sintetis(conn, user_id)
        c1, _ = await panggil(baru, a, user_id)
        c2, d2 = await panggil(baru, b, user_id)
        await sp.rollback()
        catat("HIJAU baru", "void manual pertama DAN kedua 200", c1 == 200 and c2 == 200, f"{c1}/{c2} {d2 if c2 != 200 else ''}")

        # ---- BARU: jurnal sudah dibalik lewat pintu lain -> 409 ----
        sp = conn.transaction(); await sp.start()
        btx, jid, jnum, _ = await buat_sintetis(conn, user_id)
        palsu, *_ = await buat_sintetis(conn, user_id, amount=1)
        palsu_j = await conn.fetchval("SELECT journal_id FROM bank_transactions WHERE id=$1", palsu)
        await conn.execute("UPDATE journal_entries SET reversed_by_id=$2 WHERE id=$1", jid, palsu_j)
        code, det = await panggil(baru, btx, user_id)
        await sp.rollback()
        catat("TOLAK baru", "jurnal sudah dibalik -> 409", code == 409, f"{code} {det}")

        # ---- SABOTASE: penyaring asal dicabut dari kode baru -> sisi TOLAK harus memerah ----
        src = open(BARU, encoding="utf-8").read()
        if src.count("if not asal_sah:") != 1:
            catat("SABOTASE", "jangkar penyaring ditemukan 1x", False, str(src.count("if not asal_sah:")))
        else:
            open("/tmp/kasbank_v2_sabotase.py", "w", encoding="utf-8").write(src.replace("if not asal_sah:", "if False:"))
            sab = muat("kasbank_v2_sabotase", "/tmp/kasbank_v2_sabotase.py")
            sab.get_pool = fake_get_pool
            tid = sasaran["CUSTOMER_DEPOSIT"]
            sp = conn.transaction(); await sp.start()
            code, _ = await panggil(sab, tid, user_id)
            await sp.rollback()
            catat("SABOTASE", "tanpa penyaring, void DP nyata LOLOS (gerbang bisa memerah)", code == 200, str(code))
    finally:
        await luar.rollback()

    btx_total1 = await conn.fetchval("SELECT count(*) FROM bank_transactions")
    je_total1 = await conn.fetchval("SELECT count(*) FROM journal_entries")
    r9_semua = await conn.fetch(
        "SELECT id, account_name FROM bank_accounts WHERE tenant_id=$1 ORDER BY account_name", TENANT)
    gaps = [(r["account_name"], await r9(conn, r["id"])) for r in r9_semua]
    await conn.close()
    catat("KONTROL", "nol baris menetap (btx & jurnal sama)", btx_total0 == btx_total1 and je_total0 == je_total1,
          f"btx {btx_total0}->{btx_total1} je {je_total0}->{je_total1}")
    catat("KONTROL", "R9 resmi 4 rekening 0,00 sesudah ROLLBACK", all(g == 0 for _, g in gaps), str(gaps))

    print()
    for sisi, nama, ok, ket in hasil:
        print(("[H] " if ok else "[X] ") + f"{sisi:11} {nama}  | {ket}")
    gagal = sum(1 for h in hasil if not h[2])
    lengkap = len(hasil) == HARAP_CACAH
    print(f"\ngagal={gagal} total={len(hasil)} harap={HARAP_CACAH} -> {'LENGKAP' if lengkap else 'GERBANG TAK SAH: cacah hasil salah'}")
    sys.exit(0 if gagal == 0 and lengkap else 1)


asyncio.run(main())
