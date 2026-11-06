"""B8 Kas & Bank TULIS: penentu BERSAMA pratinjau dan tulis untuk Uang Masuk/Keluar manual, hitung fisik (penyesuaian), dan
transfer (buat + posting). Pratinjau = penentu yang sama di transaksi READ ONLY (NOL tulisan); tulis = blok pertama dilempar (Blok).

Putusan pemilik LANGSUNG di sesi BACKEND, 6 Nov 2026:
1. Uang keluar/transfer yang membuat saldo KAS (cash/petty_cash) minus -> DITOLAK (KAS_MINUS) + tawaran hitung fisik.
   Bank/e-wallet boleh minus pada Uang Keluar manual. Posting transfer dan penyesuaian tetap menolak saldo minus seperti dulu.
2. Hitung fisik: pengguna mengetik JUMLAH FISIK; server menghitung selisih terhadap saldo jurnal per tanggal penyesuaian.
3. X-Idempotency-Key opsional (services/idem_buat) -- dipasang di rute, bukan di sini.
Saldo = SUM(debit) - SUM(credit) jurnal POSTED pada coa_id rekening (Law 1/16), tenant eksplisit (Law 24)."""
from decimal import Decimal
from uuid import UUID

from . import teks_galat as tg
from .bayar_keluar_rencana import Blok, periode_terbuka
from .pagar_uang_muka import tolak_akun_lawan_bank
from .role_resolver import AccountRole, AccountRoleUnmappedError, resolve_account_id_by_role

TIPE_KAS = ("cash", "petty_cash")
SEN = Decimal("0.01")

SQL_AKUN = """SELECT ba.id, ba.account_name, ba.account_type, ba.coa_id, ba.is_active,
       COALESCE((SELECT SUM(jl.debit) - SUM(jl.credit) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
                 WHERE jl.account_id = ba.coa_id AND je.status = 'POSTED' AND je.tenant_id = ba.tenant_id
                   AND ($3::date IS NULL OR je.journal_date <= $3::date)), 0) AS saldo
FROM bank_accounts ba WHERE ba.id = $1 AND ba.tenant_id = $2"""


def _d(x) -> Decimal:
    return Decimal(str(x or 0))


def _uuid(bl: Blok, x, code: str, pesan: str):
    try:
        return UUID(str(x))
    except (ValueError, TypeError):
        bl.galat(code, 400, pesan)
        return None


async def akun(conn, tid: str, ba_id, per_tanggal=None):
    """Rekening + saldo jurnal (per_tanggal None = semua tanggal)."""
    return await conn.fetchrow(SQL_AKUN, ba_id, tid, per_tanggal)


SQL_AKUN_COA = SQL_AKUN.replace("WHERE ba.id = $1 AND ba.tenant_id = $2", "WHERE ba.coa_id = $1 AND ba.tenant_id = $2 AND ba.is_active = true")


async def tolak_kas_minus_coa(conn, tid: str, coa_id, keluar, bl: Blok = None) -> list:
    """Aturan kas minus untuk jalur yang hanya tahu CoA kredit (biaya: create_expense_journal). Tanpa bl = tulis (melempar KAS_MINUS);
    dengan Blok(True) = pratinjau (blok dikumpulkan). CoA bukan rekening kas -> tak ada blok."""
    bl = bl or Blok(False)
    a = await conn.fetchrow(SQL_AKUN_COA, coa_id, tid, None) if coa_id else None
    if a:
        _kas_minus(bl, a, _d(keluar))
    return bl.daftar


def _kas_minus(bl: Blok, a, keluar: Decimal) -> None:
    """Putusan 1: kas fisik tak mungkin minus -> catatan salah; tawarkan hitung fisik (kode KAS_MINUS = sinyal FE)."""
    if a["account_type"] in TIPE_KAS and _d(a["saldo"]) - keluar < 0:
        bl.galat("KAS_MINUS", 400, f"Saldo {a['account_name']} sekarang {tg.rp(a['saldo'])}, tidak cukup untuk {tg.rp(keluar)}; "
                                   "kas tidak bisa minus. Bila uang di laci berbeda dengan catatan, hitung fisik kas dulu.")


def _saldo(a, gerak: Decimal) -> dict:
    return {"bank_account_id": str(a["id"]), "account_name": a["account_name"], "sebelum": _d(a["saldo"]),
            "sesudah": _d(a["saldo"]) + gerak}


async def rencana_tx_manual(conn, tid: str, ba_id, body, pratinjau: bool) -> dict:
    """POST /{id}/transactions (+/preview). Urutan blok = urutan pemeriksaan lama; KAS_MINUS (baru) terakhir."""
    bl = Blok(pratinjau)
    hasil = {"blocks": bl.daftar, "akun": None, "baris": [], "saldo": []}
    a = await akun(conn, tid, ba_id)
    if not a:
        bl.galat("REKENING_TIDAK_DITEMUKAN", 404, "Rekening tidak ditemukan di usaha ini.")
        return hasil
    hasil["akun"] = a
    if not a["is_active"]:
        bl.galat("REKENING_NONAKTIF", 400, "Rekening ini nonaktif.")
    if body.direction not in ("in", "out"):
        bl.galat("ARAH_TIDAK_SAH", 400, "Arah harus 'in' (uang masuk) atau 'out' (uang keluar).")
    lawan = _uuid(bl, body.contra_account_id, "AKUN_LAWAN_TIDAK_SAH", "Akun lawan tidak valid.")
    c = await conn.fetchrow("SELECT id, account_type FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2", lawan, tid) if lawan else None
    if lawan and not c:
        bl.galat("AKUN_LAWAN_TIDAK_DITEMUKAN", 400, "Akun lawan tidak ditemukan.")
    if c:
        if c["account_type"] in ("RECEIVABLE", "PAYABLE"):
            bl.galat("AKUN_LAWAN_PIUTANG_HUTANG", 400, "Gunakan modul Terima Pembayaran untuk piutang, atau Bayar Tagihan untuk hutang. (Law 29)")
        await bl.coba("AKUN_LAWAN_UANG_MUKA", tolak_akun_lawan_bank(conn, tid, c["id"]))
        # Dr X / Cr X pada akun yang sama seimbang TAPI ekonomis nol -> bank_transaction tanpa gerak saldo (kasus MT-C4057F2C).
        if str(c["id"]) == str(a["coa_id"]):
            bl.galat("AKUN_LAWAN_SAMA", 400, "Akun lawan tidak boleh sama dengan rekening ini. Pilih akun lawan, misalnya Modal Pemilik.")
    await periode_terbuka(conn, tid, body.transaction_date, bl)
    jumlah = _d(body.amount)
    if body.direction == "out":
        _kas_minus(bl, a, jumlah)
    masuk = body.direction == "in"
    bank, lwn = (a["coa_id"], jumlah, Decimal(0), body.description), (lawan, Decimal(0), jumlah, body.description)
    hasil["baris"] = [bank, lwn] if masuk else [(lawan, jumlah, Decimal(0), body.description), (a["coa_id"], Decimal(0), jumlah, body.description)]
    hasil["saldo"] = [_saldo(a, jumlah if masuk else -jumlah)]
    return hasil


async def rencana_hitung_fisik(conn, tid: str, ba_id, body, pratinjau: bool) -> dict:
    """POST /{id}/adjust (+/preview). jumlah_fisik (putusan 2) -> selisih = fisik - saldo jurnal s.d. adjustment_date;
    adjustment_amount (bentuk lama) -> selisih apa adanya terhadap saldo semua tanggal."""
    bl = Blok(pratinjau)
    hasil = {"blocks": bl.daftar, "akun": None, "baris": [], "saldo": [], "selisih": Decimal(0), "akun_penyesuaian": None}
    fisik = getattr(body, "jumlah_fisik", None)
    a = await akun(conn, tid, ba_id, body.adjustment_date if fisik is not None else None)
    if not a:
        bl.galat("REKENING_TIDAK_DITEMUKAN", 404, "Rekening tidak ditemukan di usaha ini.")
        return hasil
    hasil["akun"] = a
    if not a["is_active"]:
        bl.galat("REKENING_NONAKTIF", 400, "Rekening nonaktif tidak bisa disesuaikan.")
    selisih = _d(fisik) - _d(a["saldo"]) if fisik is not None else _d(body.adjustment_amount)
    hasil["selisih"] = selisih
    if selisih == 0:
        bl.galat("SELISIH_NOL", 400, "Uang fisik sama dengan saldo catatan; tidak ada yang perlu disesuaikan."
                 if fisik is not None else "Selisih penyesuaian tidak boleh nol.")
        return hasil
    if _d(a["saldo"]) + selisih < 0:
        bl.galat("SALDO_MINUS", 400, f"Penyesuaian membuat saldo {a['account_name']} minus ({tg.rp(_d(a['saldo']) + selisih)}).")
    await periode_terbuka(conn, tid, body.adjustment_date, bl)
    peran = AccountRole.BANK_ADJUSTMENT_GAIN if selisih > 0 else AccountRole.BANK_ADJUSTMENT_LOSS
    try:
        adj = await resolve_account_id_by_role(conn, tid, peran)
    except AccountRoleUnmappedError:
        bl.galat("AKUN_PENYESUAIAN_BELUM_DIATUR", 400, "Akun penyesuaian saldo (selisih lebih/kurang) belum diatur untuk usaha ini. "
                                                       "Atur akun Pendapatan/Beban Lain-lain dulu.")
        return hasil
    hasil["akun_penyesuaian"] = adj
    n, alasan = abs(selisih), body.reason
    # Rekening di satu sisi, akun peran penyesuaian di sisi lain -- tak pernah akun yang sama (test_ii_adjust_never_net_zero_same_account).
    hasil["baris"] = ([(a["coa_id"], n, Decimal(0), f"Penyesuaian Saldo - {alasan}"), (adj, Decimal(0), n, f"Koreksi Saldo (selisih lebih) - {alasan}")]
                      if selisih > 0 else
                      [(adj, n, Decimal(0), f"Koreksi Saldo (selisih kurang) - {alasan}"), (a["coa_id"], Decimal(0), n, f"Penyesuaian Saldo - {alasan}")])
    hasil["saldo"] = [_saldo(a, selisih)]
    return hasil


async def rencana_posting_transfer(conn, tid: str, t, bl: Blok) -> dict:
    """Posting transfer dari baris t (bank_transfers + nama/coa/saldo/tipe rekening asal). Dipakai _post_transfer dan pratinjau."""
    hasil = {"baris": [], "saldo": [], "akun_biaya": None}
    if t["status"] != "draft":
        bl.galat("TRANSFER_BUKAN_DRAF", 400, f"Transfer berstatus '{t['status']}' tidak bisa diposting.")
    total, jumlah, biaya = _d(t["total_amount"]), _d(t["amount"]), _d(t["fee_amount"])
    asal = {"id": t["from_bank_id"], "account_name": t["from_name"], "account_type": t["from_type"], "saldo": t["from_balance"]}
    if t["from_type"] in TIPE_KAS:
        _kas_minus(bl, asal, total)
    elif _d(t["from_balance"]) < total:
        bl.galat("SALDO_TIDAK_CUKUP", 400, f"Saldo {t['from_name']} {tg.rp(t['from_balance'])} tidak cukup untuk {tg.rp(total)}.")
    await periode_terbuka(conn, tid, t["transfer_date"], bl)
    if biaya > 0:
        try:
            hasil["akun_biaya"] = await resolve_account_id_by_role(conn, tid, AccountRole.BANK_FEE)
        except AccountRoleUnmappedError:
            bl.galat("AKUN_BIAYA_TRANSFER_BELUM_DIATUR", 400, "Akun biaya transfer bank belum diatur untuk usaha ini.")
    hasil["baris"] = [(t["to_coa_id"], jumlah, Decimal(0), f"Transfer masuk dari {t['from_name']}")]
    if biaya > 0 and hasil["akun_biaya"]:
        hasil["baris"].append((hasil["akun_biaya"], biaya, Decimal(0), f"Biaya transfer - {t['transfer_number']}"))
    hasil["baris"].append((t["from_coa_id"], Decimal(0), total, f"Transfer keluar ke {t['to_name']}"))
    tujuan_saldo = t.get("to_balance")
    hasil["saldo"] = [_saldo(asal, -total)] + ([_saldo({"id": t["to_bank_id"], "account_name": t["to_name"], "saldo": tujuan_saldo}, jumlah)]
                                               if tujuan_saldo is not None else [])
    return hasil


SQL_TRANSFER_POSTING = """SELECT bt.*, fb.account_name AS from_name, fb.coa_id AS from_coa_id, fb.account_type AS from_type,
       COALESCE((SELECT SUM(jl.debit) - SUM(jl.credit) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
                 WHERE jl.account_id = fb.coa_id AND je.status = 'POSTED' AND je.tenant_id = bt.tenant_id), 0) AS from_balance,
       tb.account_name AS to_name, tb.coa_id AS to_coa_id,
       COALESCE((SELECT SUM(jl.debit) - SUM(jl.credit) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
                 WHERE jl.account_id = tb.coa_id AND je.status = 'POSTED' AND je.tenant_id = bt.tenant_id), 0) AS to_balance
FROM bank_transfers bt
LEFT JOIN bank_accounts fb ON bt.from_bank_id = fb.id
LEFT JOIN bank_accounts tb ON bt.to_bank_id = tb.id
WHERE bt.id = $1 AND bt.tenant_id = $2"""


async def rencana_posting_transfer_id(conn, tid: str, transfer_id) -> dict:
    """POST /bank-transfers/{id}/post/preview: baris SAMA dengan _post_transfer (SQL_TRANSFER_POSTING) + penentu posting."""
    bl = Blok(True)
    t = await conn.fetchrow(SQL_TRANSFER_POSTING, transfer_id, tid)
    if not t:
        bl.galat("TRANSFER_TIDAK_DITEMUKAN", 404, "Transfer tidak ditemukan di usaha ini.")
        return {"blocks": bl.daftar, "baris": [], "saldo": []}
    return {"blocks": bl.daftar, **await rencana_posting_transfer(conn, tid, t, bl)}


async def rencana_buat_transfer(conn, tid: str, body, pratinjau: bool) -> dict:
    """POST /bank-transfers (+/preview): rekening asal/tujuan milik tenant + aktif; auto_post -> penentu posting juga (blok ikut)."""
    bl = Blok(pratinjau)
    hasil = {"blocks": bl.daftar, "asal": None, "tujuan": None, "baris": [], "saldo": []}
    i_asal = _uuid(bl, body.from_bank_id, "REKENING_ASAL_TIDAK_DITEMUKAN", "Rekening asal tidak ditemukan.")
    f = await akun(conn, tid, i_asal) if i_asal else None
    if i_asal and not f:
        bl.galat("REKENING_ASAL_TIDAK_DITEMUKAN", 400, "Rekening asal tidak ditemukan.")
    elif f and not f["is_active"]:
        bl.galat("REKENING_ASAL_NONAKTIF", 400, "Rekening asal nonaktif.")
    i_tujuan = _uuid(bl, body.to_bank_id, "REKENING_TUJUAN_TIDAK_DITEMUKAN", "Rekening tujuan tidak ditemukan.")
    t = await akun(conn, tid, i_tujuan) if i_tujuan else None
    if i_tujuan and not t:
        bl.galat("REKENING_TUJUAN_TIDAK_DITEMUKAN", 400, "Rekening tujuan tidak ditemukan.")
    elif t and not t["is_active"]:
        bl.galat("REKENING_TUJUAN_NONAKTIF", 400, "Rekening tujuan nonaktif.")
    hasil["asal"], hasil["tujuan"] = f, t
    if not (f and t):
        return hasil
    baris = {"status": "draft", "amount": body.amount, "fee_amount": body.fee_amount, "total_amount": _d(body.amount) + _d(body.fee_amount),
             "transfer_date": body.transfer_date, "transfer_number": "(nomor terbit saat disimpan)", "from_bank_id": f["id"],
             "from_name": f["account_name"], "from_type": f["account_type"], "from_balance": f["saldo"], "from_coa_id": f["coa_id"],
             "to_bank_id": t["id"], "to_name": t["account_name"], "to_coa_id": t["coa_id"], "to_balance": t["saldo"]}
    # Draf tanpa auto_post tak menggerakkan saldo -> pemeriksaan posting hanya bila auto_post; jurnal tetap ditampilkan.
    p = await rencana_posting_transfer(conn, tid, baris, bl if body.auto_post else Blok(True))
    hasil["baris"], hasil["saldo"] = p["baris"], p["saldo"]
    return hasil


def _rp(x) -> str:
    return str(_d(x).quantize(SEN))


async def keluaran(conn, tid: str, r: dict, payload) -> dict:
    """Bentuk pratinjau: {ok, blocks, jurnal[{account_id, account_name, debit, credit, memo}], saldo[{…, sebelum, sesudah}], payload}."""
    ids = [b[0] for b in r["baris"] if b[0]]
    nama = {x["id"]: x["account_name"] for x in await conn.fetch(
        "SELECT id, account_name FROM chart_of_accounts WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tid, ids)} if ids else {}
    blocks = [{k: v for k, v in b.items() if k != "status"} for b in r["blocks"]]
    tambahan = {"selisih": _rp(r["selisih"])} if "selisih" in r else {}
    return {"ok": not blocks, "blocks": blocks, **tambahan,
            "jurnal": [{"account_id": str(i) if i else None, "account_name": nama.get(i), "debit": _rp(d), "credit": _rp(k), "memo": m}
                       for i, d, k, m in r["baris"]],
            "saldo": [{**s, "sebelum": _rp(s["sebelum"]), "sesudah": _rp(s["sesudah"]), "minus": s["sesudah"] < 0} for s in r["saldo"]],
            "payload": payload}


async def pratinjau(pool, tid: str, rencana, payload) -> dict:
    """Satu jalur pratinjau untuk keempat rute */preview: transaksi READ ONLY (penentu tak bisa menulis), bentuk keluaran()."""
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            r = await rencana(conn)
            return {"success": True, "data": await keluaran(conn, tid, r, payload)}
