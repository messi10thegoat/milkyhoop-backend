"""B8 Kas & Bank TULIS: penentu BERSAMA pratinjau dan tulis untuk Uang Masuk/Keluar manual, hitung fisik (penyesuaian), dan
transfer (buat + posting). Pratinjau = penentu yang sama di transaksi READ ONLY (NOL tulisan); tulis = blok pertama dilempar (Blok).

Putusan pemilik LANGSUNG di sesi BACKEND, 6 Nov 2026:
1. Uang keluar/transfer yang membuat saldo KAS (cash/petty_cash) minus -> DITOLAK (KAS_MINUS) + tawaran hitung fisik.
   Bank/e-wallet boleh minus pada Uang Keluar manual. Posting transfer dan penyesuaian tetap menolak saldo minus seperti dulu.
2. Hitung fisik: pengguna mengetik JUMLAH FISIK; server menghitung selisih terhadap saldo jurnal per tanggal penyesuaian.
3. X-Idempotency-Key opsional (services/idem_buat) -- dipasang di rute, bukan di sini.
Saldo = SUM(debit) - SUM(credit) jurnal POSTED pada coa_id rekening (Law 1/16), tenant eksplisit (Law 24).

K1 kas-negatif (6 Nov 2026, putusan pemilik LANGSUNG di sesi BACKEND2 + lingkup MASTER, pola Accurate "kas tidak boleh minus" untuk
dokumen KAS KELUAR; void/koreksi tidak dikunci, pola NetSuite/SAP): SATU aturan untuk Uang Keluar, transfer, biaya, bayar tagihan,
uang muka vendor, refund nota kredit, refund uang muka pelanggan. Tulis = kunci advisory per CoA kas (kunci_kas, `KAS_KELUAR:{tenant}:
{coa}`) SEBELUM saldo dibaca -> dua kas keluar serentak tak bisa sama-sama lolos. Pratinjau tanpa kunci."""
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException

from . import teks_galat as tg
from .bayar_keluar_rencana import Blok, periode_terbuka
from .jaga_rekonsiliasi import tolak_void_bila_terekonsiliasi
from .pagar_uang_muka import tolak_akun_lawan_bank
from .role_resolver import AccountRole, AccountRoleUnmappedError, resolve_account_id_by_role
from ..utils.tanggal_tenant import tanggal_dokumen

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


async def kunci_kas(conn, tid: str, coa_id) -> None:
    """K1: kunci transaksi per CoA kas/bank (Law 13) -- SEMUA jalur kas keluar memakai kunci ini SEBELUM membaca saldo."""
    if coa_id:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"KAS_KELUAR:{tid}:{coa_id}")


SQL_TANDAI_VOID = """UPDATE bank_transactions SET status = 'VOIDED', voided_by = $2, voided_at = NOW(), void_reason = $3
                     WHERE tenant_id = $1 AND status <> 'VOIDED' AND """


async def tandai_baris_void(conn, tid: str, user_id, alasan: str, *, tx_id=None, referensi: tuple = None) -> str:
    """ROW (n): SATU penanda baris mutasi bank batal -- status VOIDED (CHECK V110) + aktor + waktu + alasan -- untuk void manual
    (kasbank_v2), void biaya, dan void transfer. Per baris (`tx_id`) atau per dokumen asal (`referensi` = (reference_type, id)).
    Dulu UPDATE disalin tiga kali; void transfer tak mengisi aktor dan alasan."""
    if tx_id is not None:
        return await conn.execute(SQL_TANDAI_VOID + "id = $4", tid, user_id, alasan, tx_id)
    jenis, ref_id = referensi
    return await conn.execute(SQL_TANDAI_VOID + "reference_type = $4 AND reference_id = $5", tid, user_id, alasan, jenis, ref_id)


async def tolak_kas_minus_coa(conn, tid: str, coa_id, keluar, bl: Blok = None, kunci: bool = None) -> list:
    """Aturan kas minus untuk jalur yang hanya tahu CoA kredit (biaya, bayar tagihan, uang muka vendor, refund NK/UM). Tanpa bl = tulis
    (melempar KAS_MINUS); dengan Blok(True) = pratinjau (blok dikumpulkan). CoA bukan rekening kas -> tak ada blok.
    kunci (bawaan: tulis) -> kunci_kas DULU, baru saldo dibaca."""
    bl = bl or Blok(False)
    if (not bl.pratinjau) if kunci is None else kunci:
        await kunci_kas(conn, tid, coa_id)
    a = await conn.fetchrow(SQL_AKUN_COA, coa_id, tid, None) if coa_id else None
    if a:
        _kas_minus(bl, a, _d(keluar))
    return bl.daftar


async def tolak_kas_minus_baris(conn, tid: str, baris, bl: Blok = None, kunci: bool = None) -> list:
    """K1: kas keluar dari RENCANA baris jurnal [(akun, debit, kredit, ...)] -- bersih (kredit - debit) per CoA > 0 diperiksa; urutan CoA
    tetap (kunci tanpa deadlock). Satu penentu untuk pratinjau dan penulis jalur yang sudah punya baris jurnal."""
    bl = bl or Blok(False)
    bersih, asli = {}, {}
    for b in baris or []:
        if b[0]:
            k = str(b[0])
            asli[k] = b[0]
            bersih[k] = bersih.get(k, Decimal(0)) + _d(b[2]) - _d(b[1])
    for k in sorted(bersih):
        if bersih[k] > 0:
            await tolak_kas_minus_coa(conn, tid, asli[k], bersih[k], bl, kunci)
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
        if not pratinjau:  # K1: kunci per CoA lalu saldo dibaca ULANG (dibaca di atas tanpa kunci)
            await kunci_kas(conn, tid, a["coa_id"])
            a = await akun(conn, tid, ba_id)
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


def keterangan_transfer(t, arah: str) -> str:
    """BUG-083: keterangan ketikan pengguna (bank_transfers.notes) bila ada, selain itu teks bawaan per arah. Satu penentu untuk memo baris
    jurnal (mutasi B2) dan deskripsi bank_transactions (mutasi lama); data lama dibaca lewat COALESCE notes di pembacanya."""
    ket = (t.get("notes") or "").strip()
    if ket:
        return ket
    return f"Transfer masuk dari {t['from_name']}" if arah == "masuk" else f"Transfer keluar ke {t['to_name']}"


async def rencana_posting_transfer(conn, tid: str, t, bl: Blok) -> dict:
    """Posting transfer dari baris t (bank_transfers + nama/coa/saldo/tipe rekening asal). Dipakai _post_transfer dan pratinjau."""
    hasil = {"baris": [], "saldo": [], "akun_biaya": None}
    if t["status"] != "draft":
        bl.galat("TRANSFER_BUKAN_DRAF", 400, f"Transfer berstatus '{t['status']}' tidak bisa diposting.")
    total, jumlah, biaya = _d(t["total_amount"]), _d(t["amount"]), _d(t["fee_amount"])
    asal = {"id": t["from_bank_id"], "account_name": t["from_name"], "account_type": t["from_type"], "saldo": t["from_balance"]}
    if not bl.pratinjau:  # K1: kunci per CoA sumber lalu saldo dibaca ULANG (t dibaca pemanggil tanpa kunci)
        await kunci_kas(conn, tid, t["from_coa_id"])
        segar = await akun(conn, tid, t["from_bank_id"])
        if segar:
            asal["saldo"] = segar["saldo"]
    if t["from_type"] in TIPE_KAS:
        _kas_minus(bl, asal, total)
    elif _d(asal["saldo"]) < total:
        bl.galat("SALDO_TIDAK_CUKUP", 400, f"Saldo {t['from_name']} {tg.rp(asal['saldo'])} tidak cukup untuk {tg.rp(total)}.")
    await periode_terbuka(conn, tid, t["transfer_date"], bl)
    if biaya > 0:
        try:
            hasil["akun_biaya"] = await resolve_account_id_by_role(conn, tid, AccountRole.BANK_FEE)
        except AccountRoleUnmappedError:
            bl.galat("AKUN_BIAYA_TRANSFER_BELUM_DIATUR", 400, "Akun biaya transfer bank belum diatur untuk usaha ini.")
    hasil["baris"] = [(t["to_coa_id"], jumlah, Decimal(0), keterangan_transfer(t, "masuk"))]
    if biaya > 0 and hasil["akun_biaya"]:
        hasil["baris"].append((hasil["akun_biaya"], biaya, Decimal(0), f"Biaya transfer - {t['transfer_number']}"))
    hasil["baris"].append((t["from_coa_id"], Decimal(0), total, keterangan_transfer(t, "keluar")))
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
             "to_bank_id": t["id"], "to_name": t["account_name"], "to_coa_id": t["coa_id"], "to_balance": t["saldo"], "notes": body.notes}
    # Draf tanpa auto_post tak menggerakkan saldo -> pemeriksaan posting hanya bila auto_post; jurnal tetap ditampilkan.
    p = await rencana_posting_transfer(conn, tid, baris, bl if body.auto_post else Blok(True))
    hasil["baris"], hasil["saldo"] = p["baris"], p["saldo"]
    return hasil


# ---------------------------------------------------------------- BATAL (void) -- Kas&Bank standar SO, 9 Nov 2026
# Penentu BERSAMA pratinjau /void/preview dan tulis /void (mutasi manual + transfer). Urutan blok = urutan pemeriksaan lama.
# K3 (lingkup MASTER 6 Nov): void/koreksi TIDAK dikunci saldo -- saldo[].minus hanya isyarat, bukan blok. Tanpa aturan uang baru.

# Mutasi manual SAH (daftar /api/bank-transactions == yang boleh di-void manual): asal MANUAL, tanpa dokumen rujukan, jurnalnya milik
# baris itu sendiri. SATU predikat SQL untuk daftar (kasbank_daftar) dan void (dulu `asal_sah` Python di kasbank_v2).
ASAL_MANUAL = ("(bt.origin_type = 'MANUAL' AND bt.reference_type IS NULL AND bt.reference_id IS NULL AND EXISTS ("
               "SELECT 1 FROM journal_entries ja WHERE ja.id = bt.journal_id AND ja.tenant_id = bt.tenant_id "
               "AND ja.source_type = 'BANK_TRANSACTION' AND ja.source_id = bt.id))")

SQL_MUTASI_VOID = f"""SELECT bt.*, ba.coa_id AS bank_coa_id, {ASAL_MANUAL} AS asal_sah
FROM bank_transactions bt JOIN bank_accounts ba ON ba.id = bt.bank_account_id AND ba.tenant_id = bt.tenant_id
WHERE bt.id = $1 AND bt.tenant_id = $2"""


async def _coba_rekon(bl: Blok, conn, tid: str, source_id) -> None:
    """Penjaga rekonsiliasi (409, detail {code, message}); pratinjau -> blok berkode SAMA, tulis -> lempar apa adanya."""
    try:
        await tolak_void_bila_terekonsiliasi(conn, tid, source_id)
    except HTTPException as e:
        if not bl.pratinjau:
            raise
        d = e.detail if isinstance(e.detail, dict) else {"code": "TEREKONSILIASI", "message": str(e.detail)}
        bl.daftar.append({"code": d.get("code"), "message": d.get("message"), "status": e.status_code})


def _balik(lines) -> list:
    """Baris jurnal pembalik: debit <-> kredit, memo "Reversal - …" (sama dengan tulis lama)."""
    return [(x["account_id"], _d(x["credit"]), _d(x["debit"]), f"Reversal - {x['memo'] or ''}") for x in lines]


async def rencana_void_manual(conn, tid: str, tx_id, alasan, bl: Blok, kunci: bool = False) -> dict:
    """POST /api/bank-transactions/{id}/void (+/preview). kunci=True (tulis) -> FOR UPDATE baris + jurnal (pratinjau READ ONLY tanpa)."""
    hasil = {"blocks": bl.daftar, "baris": [], "saldo": [], "tx": None, "je": None, "jumlah": Decimal(0)}
    await _coba_rekon(bl, conn, tid, tx_id)
    tx = await conn.fetchrow(SQL_MUTASI_VOID + (" FOR UPDATE OF bt" if kunci else ""), tx_id, tid)
    if not tx:
        bl.galat("MUTASI_TIDAK_DITEMUKAN", 404, "Mutasi tidak ditemukan di usaha ini.")
        return hasil
    je = await conn.fetchrow("SELECT id, journal_number, source_type, source_id, reversed_by_id FROM journal_entries "
                             "WHERE id = $1 AND tenant_id = $2" + (" FOR UPDATE" if kunci else ""), tx["journal_id"], tid) if tx["journal_id"] else None
    hasil["tx"], hasil["je"] = tx, je
    if not tx["asal_sah"] or je is None:
        bl.galat("BUKAN_MUTASI_MANUAL", 400, "Transaksi ini dibuat oleh modul lain. Batalkan dari dokumen asalnya.")
        return hasil
    if tx["status"] == "VOIDED":
        bl.galat("MUTASI_SUDAH_DIBATALKAN", 400, "Mutasi ini sudah dibatalkan.")
        return hasil
    if tx["transaction_type"] == "opening":
        bl.galat("SALDO_AWAL", 400, "Saldo awal tidak bisa di-void. Gunakan Edit Akun untuk mengubah saldo awal.")
        return hasil
    if tx["reconciliation_status"] == "RECONCILED":
        bl.galat("TEREKONSILIASI", 400, "Mutasi yang sudah direkonsiliasi tidak bisa dibatalkan.")
    await periode_terbuka(conn, tid, tx["transaction_date"], bl)
    hari_ini = await tanggal_dokumen(conn, tid)  # t10-tanggal-bisnis: jurnal pembalik bertanggal hari ini
    await periode_terbuka(conn, tid, hari_ini, bl)
    if je["reversed_by_id"]:
        bl.galat("JURNAL_SUDAH_DIBALIK", 409, "Jurnal mutasi ini sudah dibalik.")
    lines = await conn.fetch("SELECT * FROM journal_lines WHERE journal_id = $1 ORDER BY line_number", je["id"])
    if not lines:
        bl.galat("JURNAL_TANPA_BARIS", 500, "Baris jurnal mutasi ini tidak ditemukan.")
        return hasil
    hasil["baris"], hasil["jumlah"] = _balik(lines), sum((_d(x["debit"]) for x in lines), Decimal(0))
    a = await akun(conn, tid, tx["bank_account_id"])
    gerak = -sum((_d(x["debit"]) - _d(x["credit"]) for x in lines if x["account_id"] == tx["bank_coa_id"]), Decimal(0))
    hasil["saldo"] = [_saldo(a, gerak)] if a else []
    return hasil


async def rencana_void_transfer(conn, tid: str, transfer_id, alasan, bl: Blok, kunci: bool = False) -> dict:
    """POST /api/bank-transfers/{id}/void (+/preview). Draf -> dihapus (perilaku lama), tanpa jurnal. Saldo asal/tujuan dibaca lewat
    SQL_TRANSFER_POSTING (bersaring tenant; dulu subkueri jurnal tanpa tenant)."""
    hasil = {"blocks": bl.daftar, "baris": [], "saldo": [], "t": None, "hapus_draf": False}
    await _coba_rekon(bl, conn, tid, transfer_id)
    t = await conn.fetchrow(SQL_TRANSFER_POSTING + (" FOR UPDATE OF bt" if kunci else ""), transfer_id, tid)
    if not t:
        bl.galat("TRANSFER_TIDAK_DITEMUKAN", 404, "Transfer tidak ditemukan di usaha ini.")
        return hasil
    hasil["t"] = t
    if t["status"] == "void":
        bl.galat("TRANSFER_SUDAH_DIBATALKAN", 400, "Transfer ini sudah dibatalkan.")
        return hasil
    if t["status"] == "draft":
        hasil["hapus_draf"], hasil["catatan"] = True, "Draf akan dihapus (belum berjurnal)."
        return hasil
    await periode_terbuka(conn, tid, t["transfer_date"], bl)
    hari_ini = await tanggal_dokumen(conn, tid)  # t10-tanggal-bisnis: jurnal pembalik bertanggal hari ini
    await periode_terbuka(conn, tid, hari_ini, bl)
    lines = await conn.fetch("SELECT * FROM journal_lines WHERE journal_id = $1 ORDER BY line_number", t["journal_id"]) if t["journal_id"] else []
    hasil["baris"] = _balik(lines)
    total, jumlah = _d(t["total_amount"]), _d(t["amount"])
    hasil["saldo"] = [_saldo({"id": t["from_bank_id"], "account_name": t["from_name"], "saldo": t["from_balance"]}, total),
                      _saldo({"id": t["to_bank_id"], "account_name": t["to_name"], "saldo": t["to_balance"]}, -jumlah)]
    return hasil


def _rp(x) -> str:
    return str(_d(x).quantize(SEN))


async def keluaran(conn, tid: str, r: dict, payload) -> dict:
    """Bentuk pratinjau: {ok, blocks, jurnal[{account_id, account_name, debit, credit, memo}], saldo[{…, sebelum, sesudah}], payload}
    (+selisih hitung fisik, +catatan void draf)."""
    ids = [b[0] for b in r["baris"] if b[0]]
    nama = {x["id"]: x["account_name"] for x in await conn.fetch(
        "SELECT id, name AS account_name FROM chart_of_accounts WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tid, ids)} if ids else {}
    blocks = [{k: v for k, v in b.items() if k != "status"} for b in r["blocks"]]
    tambahan = {"selisih": _rp(r["selisih"])} if "selisih" in r else {}
    if r.get("catatan"):
        tambahan["catatan"] = r["catatan"]
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
