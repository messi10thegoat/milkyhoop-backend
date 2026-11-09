"""Kas & Bank KELOLA REKENING (buat/ubah/hapus): penentu BERSAMA pratinjau dan tulis (pola B8 services/kasbank_tulis; kontrak
KONTRAK-kasbank-kelola-rekening-cw-20261110.md). Pratinjau = penentu yang sama di transaksi READ ONLY lewat KT.pratinjau (nol tulisan);
tulis = blok pertama dilempar (Blok) dengan kode/status/kalimat penulis lama.

- Buat: CoA otomatis DIRENCANAKAN (kode berikut, nama, tipe, induk) -- pratinjau menampilkan, penulis menulis rencana yang sama.
  Saldo awal > 0 -> jurnal BANK_OPENING_BALANCE: aset Dr Rekening / Cr Modal Saldo Awal; kartu kredit Dr Modal Saldo Awal / Cr Kartu Kredit.
- Ubah: blok = pemeriksaan penulis lama; catatan = perubahan sebelum -> sesudah; PERINGATAN (bukan blok; perilaku tak diubah) saat
  menonaktifkan rekening utama / masih bersaldo.
- Hapus: akibat permanen | nonaktifkan (penentu KD, sama dengan DELETE dan aksi_tersedia.hapus). PUTUSAN A MASTER 10 Nov: rekening yang CoA-nya
  dipakai jurnal yang tak bisa dihapus (saldo awal BANK_OPENING_BALANCE / jurnal POSTED) -> 400 berkalimat (dulu 500: FK journal_lines /
  trg_prevent_posted_journal_delete, rollback penuh; hasil DB identik).
Saldo = jurnal POSTED (Law 1/16); tenant eksplisit (Law 24)."""
from decimal import Decimal
from uuid import UUID, uuid4

from . import kasbank_daftar as KD
from . import kasbank_tulis as KT
from . import teks_galat as tg
from .bayar_keluar_rencana import Blok, periode_terbuka
from .pagar_uang_muka import tolak_rekening_bank
from .resolve_account import resolve_account_id
from ..utils.tanggal_tenant import tanggal_dokumen

OPENING_BALANCE_EQUITY = "3-50000"  # Modal Saldo Awal
BANK_PARENT_CODE = "1-10200"  # Aset - Bank (bank, cash, petty_cash, e_wallet)
CREDIT_CARD_PARENT_CODE = "2-10600"  # Liabilitas - Hutang Kartu Kredit
KARTU = "credit_card"
NOL = Decimal(0)
LABEL_MEDAN = {
    "account_name": "Nama rekening", "account_number": "Nomor rekening", "bank_name": "Nama bank", "bank_branch": "Cabang",
    "bank_address": "Alamat cabang", "swift_code": "Kode SWIFT", "account_holder_name": "Nama pemilik rekening",
    "account_type": "Jenis", "is_active": "Status", "is_default": "Rekening utama", "notes": "Catatan",
}
SQL_REKENING = f"SELECT ba.*, {KD.sql_saldo('ba')} AS saldo FROM bank_accounts ba WHERE ba.id = $1 AND ba.tenant_id = $2"
MEDAN_TEKS = ("account_number", "bank_name", "bank_branch", "bank_address", "swift_code", "account_holder_name", "notes")


async def kode_coa_berikut(conn, tid: str, prefix: str):
    """1-10201..1-10299 / 2-10601..2-10699 (== get_next_coa_code lama). None = penuh."""
    last = await conn.fetchval("""SELECT account_code FROM chart_of_accounts WHERE tenant_id = $1 AND account_code LIKE $2
                                  AND LENGTH(account_code) = 7 ORDER BY account_code DESC LIMIT 1""", tid, f"{prefix}%")
    n = int(last[-2:]) + 1 if last else 1
    return f"{prefix}{n:02d}" if n <= 99 else None


async def _rencana_coa(conn, tid: str, body, bl: Blok) -> dict:
    """CoA rekening: tertaut (divalidasi) atau otomatis (direncanakan, belum ditulis)."""
    if body.coa_id:
        try:
            cid = UUID(str(body.coa_id))
        except (ValueError, TypeError):
            bl.galat("COA_TIDAK_SAH", 400, "Chart of Accounts entry not found")
            return {}
        coa = await conn.fetchrow("SELECT id, account_code, name, account_type FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2", cid, tid)
        if not coa:
            bl.galat("COA_TIDAK_DITEMUKAN", 400, "Chart of Accounts entry not found")
            return {}
        await bl.coba("COA_UANG_MUKA", tolak_rekening_bank(conn, tid, coa["id"]))
        if body.account_type == KARTU and coa["account_type"] != "LIABILITY":
            bl.galat("COA_TIPE_SALAH", 400, "Credit card account must be linked to a LIABILITY type CoA")
        elif body.account_type != KARTU and coa["account_type"] != "ASSET":
            bl.galat("COA_TIPE_SALAH", 400, "Bank account must be linked to an ASSET type CoA (e.g., Kas or Bank)")
        return {"id": coa["id"], "account_code": coa["account_code"], "name": coa["name"], "account_type": coa["account_type"], "baru": False}
    kartu = body.account_type == KARTU
    induk_kode, prefix = (CREDIT_CARD_PARENT_CODE, "2-106") if kartu else (BANK_PARENT_CODE, "1-102")
    induk = await conn.fetchrow("SELECT id, account_code, name FROM chart_of_accounts WHERE account_code = $1 AND tenant_id = $2", induk_kode, tid)
    if not induk:
        bl.galat("COA_INDUK_TIDAK_ADA", 400, f"Akun induk {induk_kode} belum ada di daftar akun usaha ini; akun perkiraan rekening tidak bisa dibuat.")
        return {}
    kode = await kode_coa_berikut(conn, tid, prefix)
    if not kode:
        bl.galat("COA_KODE_PENUH", 400, f"Kode akun perkiraan di bawah {induk_kode} sudah penuh (99); tautkan ke akun yang sudah ada.")
        return {}
    return {"id": None, "account_code": kode, "name": body.account_name, "account_type": "LIABILITY" if kartu else "ASSET",
            "normal_balance": "CREDIT" if kartu else "DEBIT", "parent_code": induk_kode, "induk_nama": induk["name"], "baru": True}


async def tulis_coa(conn, tid: str, coa: dict):
    """Penulis buat: menulis CoA RENCANA (kode/nama/tipe/induk yang sama dengan pratinjau; kolom == auto_create lama)."""
    cid = uuid4()
    await conn.execute(
        """INSERT INTO chart_of_accounts (id, tenant_id, account_code, name, description, is_cash, account_type, normal_balance, parent_code,
                                          is_header, is_active)
           VALUES ($1, $2, $3, $4, $5, true, $6, $7, $8, false, true)""",
        cid, tid, coa["account_code"], coa["name"], f"Auto-created for bank account: {coa['name']}", coa["account_type"],
        coa["normal_balance"], coa["parent_code"])
    return cid


async def rencana_buat_rekening(conn, tid: str, body, pratinjau: bool) -> dict:
    """POST /api/bank-accounts (+/preview). Urutan blok = urutan pemeriksaan penulis lama; periode tutup (dulu galat trigger) terakhir."""
    bl = Blok(pratinjau)
    hasil = {"blocks": bl.daftar, "baris": [], "saldo": [], "coa": {}, "ekuitas": None, "tanggal": None, "catatan": None, "nama_lain": {}}
    coa = await _rencana_coa(conn, tid, body, bl)
    hasil["coa"] = coa
    if await conn.fetchval("SELECT id FROM bank_accounts WHERE tenant_id = $1 AND account_name = $2", tid, body.account_name):
        bl.galat("NAMA_REKENING_DIPAKAI", 400, f"Bank account with name '{body.account_name}' already exists")
    if body.coa_id and coa.get("id") and await conn.fetchval("SELECT id FROM bank_accounts WHERE tenant_id = $1 AND coa_id = $2", tid, coa["id"]):
        bl.galat("COA_SUDAH_TERTAUT", 400, "This CoA is already linked to another bank account")
    kartu = body.account_type == KARTU
    n = Decimal(body.opening_balance or 0)
    if n > 0:
        hasil["ekuitas"] = await resolve_account_id(conn, tid, OPENING_BALANCE_EQUITY)
        if not hasil["ekuitas"]:
            bl.galat("MODAL_SALDO_AWAL_TIDAK_ADA", 500, "Opening Balance Equity account not found in CoA")
        hasil["tanggal"] = body.opening_date or await tanggal_dokumen(conn, tid)
        await periode_terbuka(conn, tid, hasil["tanggal"], bl)
        rek = coa.get("id") or "COA_BARU"
        if not coa.get("id") and coa:
            hasil["nama_lain"]["COA_BARU"] = f"{coa['account_code']} {coa['name']} (baru)"
        eq, nama = hasil["ekuitas"], body.account_name
        hasil["baris"] = ([(eq, n, NOL, f"Modal Saldo Awal - {nama}"), (rek, NOL, n, f"Saldo Awal Hutang - {nama}")] if kartu else
                          [(rek, n, NOL, f"Saldo Awal - {nama}"), (eq, NOL, n, f"Modal Saldo Awal - {nama}")])
    hasil["saldo"] = [{"bank_account_id": None, "account_name": body.account_name, "sebelum": NOL, "sesudah": -n if kartu else n}]
    if coa.get("baru"):
        hasil["catatan"] = f"Akun perkiraan baru {coa['account_code']} '{coa['name']}' akan dibuat di bawah {coa['parent_code']} {coa['induk_nama']}."
    elif coa:
        hasil["catatan"] = f"Rekening ditautkan ke akun perkiraan {coa['account_code']} {coa['name']}."
    return hasil


async def rencana_ubah_rekening(conn, tid: str, ba_id, body, pratinjau: bool) -> dict:
    """PATCH /{id} (+/preview). Blok = pemeriksaan penulis lama; catatan = perubahan; peringatan bukan blok."""
    bl = Blok(pratinjau)
    hasil = {"blocks": bl.daftar, "baris": [], "saldo": [], "ba": None, "perubahan": [], "peringatan": [], "catatan": None}
    ba = await conn.fetchrow(SQL_REKENING, ba_id, tid)
    if not ba:
        bl.galat("REKENING_TIDAK_DITEMUKAN", 404, "Bank account not found")
        return hasil
    hasil["ba"] = ba
    dikirim = body.model_fields_set
    if "account_name" in dikirim and not (body.account_name or "").strip():
        bl.galat("NAMA_REKENING_KOSONG", 422, "account_name tidak boleh kosong")
    elif body.account_name is not None and await conn.fetchval(
            "SELECT id FROM bank_accounts WHERE tenant_id = $1 AND account_name = $2 AND id != $3", tid, body.account_name, ba_id):
        bl.galat("NAMA_REKENING_DIPAKAI", 400, f"Bank account with name '{body.account_name}' already exists")
    if "account_type" in dikirim and body.account_type is not None:
        if (body.account_type == KARTU) != (ba["account_type"] == KARTU):
            bl.galat("TIPE_KARTU_KREDIT", 400, "Ubah tipe ke/dari kartu kredit tidak didukung — buat akun baru")
        else:
            perlu = "LIABILITY" if body.account_type == KARTU else "ASSET"
            if await conn.fetchval("SELECT account_type FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2", ba["coa_id"], tid) != perlu:
                bl.galat("TIPE_COA_TIDAK_KONSISTEN", 400, f"Tipe akun tidak konsisten dengan CoA tertaut (butuh {perlu})")
    for m in LABEL_MEDAN:
        if m not in dikirim or getattr(body, m) is None and m not in MEDAN_TEKS:
            continue
        baru = getattr(body, m)
        if m in MEDAN_TEKS and isinstance(baru, str):
            baru = baru.strip() or None
        if baru != ba[m]:
            hasil["perubahan"].append({"medan": m, "label": LABEL_MEDAN[m], "sebelum": ba[m], "sesudah": baru})
    saldo = Decimal(str(ba["saldo"] or 0))
    if body.is_active is False and ba["is_active"]:
        if ba["is_default"]:
            hasil["peringatan"].append("Ini rekening utama; sesudah dinonaktifkan, pilih rekening utama lain.")
        if saldo != 0:
            hasil["peringatan"].append(f"Saldo {tg.rp(abs(saldo))} tetap tercatat; rekening nonaktif tidak bisa dipakai transaksi baru.")
    hasil["saldo"] = [{"bank_account_id": str(ba["id"]), "account_name": ba["account_name"], "sebelum": saldo, "sesudah": saldo}]
    hasil["catatan"] = None if hasil["perubahan"] else "Tidak ada perubahan."
    return hasil


async def rencana_hapus_rekening(conn, tid: str, ba_id, pratinjau: bool) -> dict:
    """DELETE /{id} (+/delete/preview). Penentu == KD.alasan_hapus_rekening (aksi_tersedia.hapus)."""
    bl = Blok(pratinjau)
    hasil = {"blocks": bl.daftar, "baris": [], "saldo": [], "ba": None, "akibat": None, "mutasi": 0, "catatan": None}
    ba = await conn.fetchrow(SQL_REKENING, ba_id, tid)
    if not ba:
        bl.galat("REKENING_TIDAK_DITEMUKAN", 404, "Bank account not found")
        return hasil
    hasil["ba"] = ba
    n = await KD.jumlah_mutasi_nyata(conn, tid, ba_id)
    terbuku = n == 0 and await KD.coa_terbukukan(conn, tid, ba)
    hasil["mutasi"] = n
    alasan = KD.alasan_hapus_rekening(ba["is_active"], n, terbuku)
    if alasan:
        bl.galat("REKENING_BERSALDO_AWAL" if terbuku else "REKENING_NONAKTIF_BERMUTASI", 400, alasan)
        return hasil
    saldo = Decimal(str(ba["saldo"] or 0))
    hasil["saldo"] = [{"bank_account_id": str(ba["id"]), "account_name": ba["account_name"], "sebelum": saldo, "sesudah": saldo}]
    if n > 0:
        hasil["akibat"] = "nonaktifkan"
        hasil["catatan"] = f"Rekening punya {n} mutasi; tidak dihapus, hanya dinonaktifkan (riwayat tetap tersimpan)."
    else:
        coa = await conn.fetchrow("SELECT account_code, name FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2", ba["coa_id"], tid)
        sesi = await conn.fetchval("SELECT count(*) FROM reconciliation_sessions WHERE account_id = $1 AND tenant_id = $2", ba_id, tid)
        hasil["akibat"] = "permanen"
        hasil["catatan"] = (f"Rekening {ba['account_name']}" + (f", akun perkiraan {coa['account_code']} {coa['name']}" if coa else "")
                            + (f", dan {sesi} sesi rekonsiliasi" if sesi else "") + " DIHAPUS PERMANEN dan tidak bisa dibatalkan.")
    return hasil


async def keluaran(conn, tid: str, r: dict, payload) -> dict:
    """Bentuk B8 (KT.keluaran) + akibat/perubahan/peringatan/coa; nama akun baru (belum tertulis) dari rencana."""
    out = await KT.keluaran(conn, tid, {**r, "baris": [(None if b[0] == "COA_BARU" else b[0], *b[1:]) for b in r["baris"]]}, payload)
    for j, b in zip(out["jurnal"], r["baris"]):
        if b[0] == "COA_BARU":
            j["account_name"] = r["nama_lain"]["COA_BARU"]
    for k in ("akibat", "perubahan", "peringatan"):
        if k in r:
            out[k] = r[k]
    if r.get("coa"):
        out["coa"] = {k: (str(v) if isinstance(v, UUID) else v) for k, v in r["coa"].items()}
    return out


async def pratinjau(pool, tid: str, rencana, payload) -> dict:
    """== KT.pratinjau (READ ONLY) dengan keluaran rekening."""
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            return {"success": True, "data": await keluaran(conn, tid, await rencana(conn), payload)}
