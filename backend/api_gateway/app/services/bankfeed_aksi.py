"""Aksi Bank Feed BF-1c1 (KONTRAK-bf1c1): COCOKKAN · KECUALIKAN · LEPASKAN + RIWAYAT. NOL jurnal baru: aksi ini hanya MENAUTKAN/MENANDAI baris rekening koran.
BF-1c3 (KONTRAK-bf1c3): CATAT juga untuk transfer · uang_muka_pelanggan · terima_pembayaran, lewat penulis modulnya (buat_transfer_core · buat_dp_core · buat_penerimaan);
BATALKAN-CATATAN generik: dokumen dibatalkan oleh penulis void MODULNYA menurut je.source_type. Izin modul tujuan diperiksa di sini (rute Bank Feed beda dari rute modul).
BF-1c2 (KONTRAK-bf1c2): CATAT (uang_masuk · uang_keluar · pribadi) dan BATALKAN-CATATAN. Jurnal HANYA lewat penulis Uang Masuk/Keluar yang ada (kasbank_tulis:
rencana_tx_manual + tulis_tx_manual; batal = rencana_void_manual + tulis_void_manual = jurnal PEMBALIK, Law 2); aksi ini tak menulis jurnal sendiri.
Pola B8 (sama dgn penulis lain): SATU fungsi rencana `rencana_<aksi>` dipakai pratinjau (transaksi READ ONLY) dan tulis (transaksi pemanggil); tulis = blok
pertama dilempar. Urutan kunci TETAP: idem (rute) -> pg_advisory_xact_lock('MUTASI_BANK:{id}') -> SELECT .. FOR UPDATE baris mutasi (status dibaca ULANG di dalam kunci)
-> pg_advisory_xact_lock('MUTASI_BT:{bt}') urut id (cocokkan) -> baca kapasitas bt. `versi` pratinjau (sha256 keadaan) beda saat tulis -> 409 RENCANA_BERUBAH.
Sumber kebenaran "bertaut" = mutasi_bank_tautan aktif (dilepas_pada IS NULL). Kolom bank_transactions.matched_statement_line_id milik rekonsiliasi LAMA dan TIDAK ditulis.
Nominal bt dari JURNAL (|debit−kredit| CoA rekening), bukan tanda amount. Tenant eksplisit di tiap SQL; Decimal; tanpa str(e)."""
import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import HTTPException

from . import bankfeed_daftar as BD
from . import bankfeed_tebak as TB
from . import guc_tenant as GUC
from . import kasbank_ringkas as KR
from . import kasbank_tulis as KT
from . import teks_galat as tg
from .bayar_keluar_rencana import Blok
from .galat_berkode import GalatBerkode
from .role_resolver import AccountRole, resolve_account_id_by_role
from .nama_pengguna import nama_untuk
from .so_riwayat import catat_riwayat
from ..middleware.permission_middleware import izin_rute_pengguna
from ..routers import bank_transfers as BTR
from ..routers import customer_deposits as CD
from ..routers import receive_payments as RP
from ..schemas.bank_accounts import CreateManualTransactionRequest
from ..schemas.bank_transfers import CreateBankTransferRequest
from ..schemas.customer_deposits import VoidCustomerDepositRequest
from ..utils.tanggal_tenant import zona_tenant

SEN = Decimal("0.01")
DITINJAU = ("untuk_ditinjau", "mungkin_duplikat")
MAKS_BT = 4                    # satu baris bank <= 4 dokumen (gabungan)
MAKS_BARIS_PER_BT = 3          # satu dokumen <= 3 baris bank
ALASAN_KECUALI = {"duplikat": "Duplikat", "bukan_transaksi": "Bukan transaksi", "lain": "Lain-lain"}
EVENT = {"cocokkan": "BANK_FEED_COCOKKAN", "kecualikan": "BANK_FEED_KECUALIKAN", "lepaskan": "BANK_FEED_LEPASKAN", "catat": "BANK_FEED_CATAT", "batalkan": "BANK_FEED_BATALKAN"}
AKSI_TEKS = {"BANK_FEED_COCOKKAN": ("cocokkan", "Dicocokkan"), "BANK_FEED_KECUALIKAN": ("kecualikan", "Dikecualikan"), "BANK_FEED_LEPASKAN": ("lepaskan", "Dilepas"),
             "BANK_FEED_CATAT": ("catat", "Dicatat"), "BANK_FEED_BATALKAN": ("batalkan", "Dibatalkan")}
JENIS_CATAT = ("uang_masuk", "uang_keluar", "pribadi", "transfer", "uang_muka_pelanggan", "terima_pembayaran")
JENIS_DOK = ("transfer", "uang_muka_pelanggan", "terima_pembayaran")         # BF-1c3: dokumen dibuat penulis MODULNYA
IZIN_BUAT = {"transfer": "/api/bank-transfers", "uang_muka_pelanggan": "/api/customer-deposits", "terima_pembayaran": "/api/receive-payments"}
IZIN_VOID = {"BANK_TRANSFER": "/api/bank-transfers/{}/void", "CUSTOMER_DEPOSIT": "/api/customer-deposits/{}/void", "RECEIVE_PAYMENT": "/api/receive-payments/{}/void"}
TIPE_DOK = ("BANK_TRANSACTION",) + tuple(IZIN_VOID)
# jenis yang penulisnya belum dipanggil Bank Feed (c3 / BF-1d): diblok berkode, bukan diam
JENIS_BELUM = ("bayar_tagihan",)
# Putusan pemilik LANGSUNG 12 Nov 2026: pribadi keluar Dr 3-40000 Prive; pribadi masuk Cr 3-10100 Modal Pemilik. Akun ditetapkan sistem (bukan pilihan pengguna).
AKUN_PRIBADI = {"keluar": "3-40000", "masuk": "3-10100"}
LABEL_JENIS = {"uang_masuk": "Uang masuk", "uang_keluar": "Uang keluar", "pribadi": "Pribadi", "transfer": "Transfer", "uang_muka_pelanggan": "Uang muka pelanggan",
               "terima_pembayaran": "Pembayaran pelanggan"}
ENTITAS = "mutasi_bank"

SQL_MUTASI = ("SELECT id, bank_account_id, tanggal, keterangan, arah, nominal, status, cara, alasan, diputus_oleh, diputus_pada, duplikat_dari, rekonsiliasi_id "
              "FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2 AND id = $3 AND status <> 'dipisah'")
SQL_TAUTAN_MUTASI = ("SELECT t.id, t.bank_transaction_id, t.nominal_alokasi, bt.transaction_number AS nomor, bt.transaction_type AS jenis, bt.is_reconciled AS rekon, "
                     "NULLIF(btrim(bt.payee_payer), '') AS kontak_nama FROM mutasi_bank_tautan t JOIN bank_transactions bt ON bt.id = t.bank_transaction_id AND bt.tenant_id = t.tenant_id "
                     "WHERE t.tenant_id = $1 AND t.mutasi_bank_id = $2 AND t.dilepas_pada IS NULL ORDER BY t.dibuat_pada, t.id")
SQL_PELANGGAN = "SELECT id, nama FROM customers WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL"
SQL_TAUTAN_JUMLAH = "SELECT count(*) FROM mutasi_bank_tautan WHERE tenant_id = $1 AND mutasi_bank_id = $2"      # termasuk yang dilepas: kunci idempotensi berurut
SQL_BT_JURNAL = "SELECT id FROM bank_transactions WHERE tenant_id = $1 AND journal_id = $2 AND bank_account_id = $3 AND status = 'POSTED'"
SQL_DOK_TAUTAN = ("SELECT bt.id AS bt_id, je.source_type, je.source_id, bt.reference_type, bt.reference_id FROM bank_transactions bt "
                  "JOIN journal_entries je ON je.id = bt.journal_id AND je.tenant_id = bt.tenant_id WHERE bt.tenant_id = $1 AND bt.id = $2")
SQL_JURNAL_BARIS = ("SELECT jl.account_id, jl.debit, jl.credit FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id WHERE je.tenant_id = $1 "
                    "AND je.id = (SELECT journal_id FROM bank_transactions WHERE tenant_id = $1 AND id = $2) ORDER BY jl.line_number")
SQL_TAUTAN_INSERT = ("INSERT INTO mutasi_bank_tautan (tenant_id, mutasi_bank_id, bank_transaction_id, nominal_alokasi, dibuat_oleh) VALUES ($1, $2, $3, $4, $5::uuid)")
SQL_TAUTAN_LEPAS = ("UPDATE mutasi_bank_tautan SET dilepas_oleh = $3::uuid, dilepas_pada = now() WHERE tenant_id = $1 AND mutasi_bank_id = $2 AND dilepas_pada IS NULL")
SQL_AKUN_KODE = ("SELECT id, account_code, name FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = $2 AND is_active = true AND is_header = false")
SQL_PUTUS_CATAT = ("UPDATE mutasi_bank SET status = $4, cara = $5, alasan = $6, akun_id = $8::uuid, diputus_oleh = $3::uuid, diputus_pada = now() "
                   "WHERE tenant_id = $1 AND id = $2 AND status = ANY($7::text[]) RETURNING id")
SQL_KEMBALI_CATAT = ("UPDATE mutasi_bank SET status = $3, cara = NULL, alasan = NULL, akun_id = NULL, diputus_oleh = NULL, diputus_pada = NULL "
                     "WHERE tenant_id = $1 AND id = $2 AND status = 'dicatat' AND cara IN ('catat', 'pribadi') RETURNING id")
SQL_PUTUS = ("UPDATE mutasi_bank SET status = $4, cara = $5, alasan = $6, diputus_oleh = $3::uuid, diputus_pada = now() "
             "WHERE tenant_id = $1 AND id = $2 AND status = ANY($7::text[]) RETURNING id")
SQL_KEMBALI = ("UPDATE mutasi_bank SET status = $3, cara = NULL, alasan = NULL, diputus_oleh = NULL, diputus_pada = NULL "
               "WHERE tenant_id = $1 AND id = $2 AND status IN ('dicatat', 'dikecualikan') AND cara IN ('cocok', 'kecualikan') RETURNING id")
SQL_RIWAYAT = ("SELECT \"userId\" AS user_id, \"eventType\" AS event, metadata, \"createdAt\" AS pada FROM audit_logs "
               "WHERE tenant_id = $1 AND entity_type = 'mutasi_bank' AND entity_id = $2::uuid ORDER BY \"createdAt\", id")
SQL_DIIMPOR = ("SELECT m.dibuat_pada AS pada, i.dibuat_oleh AS oleh FROM mutasi_bank m LEFT JOIN mutasi_bank_impor i ON i.id = m.impor_id AND i.tenant_id = m.tenant_id "
               "WHERE m.tenant_id = $1 AND m.id = $2")


def _rp(x) -> str:
    return tg.rp(x)


def _dec(x) -> Optional[Decimal]:
    try:
        d = Decimal(str(x))
    except Exception:  # noqa: BLE001 -- masukan pengguna: tak sah -> None, tanpa str(e)
        return None
    return d.quantize(SEN) if d.is_finite() else None


def _uuid(x) -> Optional[UUID]:
    try:
        return x if isinstance(x, UUID) else UUID(str(x))
    except (ValueError, AttributeError, TypeError):
        return None


def _versi(*bagian) -> str:
    return hashlib.sha256(json.dumps(bagian, default=str, sort_keys=True).encode("utf-8")).hexdigest()[:32]


async def _teks_diputus(conn, tid: str, m) -> Optional[str]:
    if not m["diputus_pada"]:
        return None
    nama = (await nama_untuk(conn, [m["diputus_oleh"]])).get(str(m["diputus_oleh"]), "pengguna") if m["diputus_oleh"] else "pengguna"
    kata = BD.CARA_KATA.get(m["cara"] or ("kecualikan" if m["status"] == "dikecualikan" else "catat"), "Dicatat")
    return f"{kata} oleh {nama} · {BD._waktu(await zona_tenant(conn, tid), m['diputus_pada'])}"


async def _muat(conn, tid: str, rekening_id, mutasi_id, bl: Blok, kunci: bool):
    """Baris mutasi (FOR UPDATE saat tulis). Tak ada -> blok 404."""
    m = await conn.fetchrow(SQL_MUTASI + (" FOR UPDATE" if kunci else ""), tid, rekening_id, mutasi_id)
    if not m:
        bl.galat("MUTASI_TAK_ADA", 404, "Mutasi bank tidak ditemukan.")
    return m


async def _tolak_bila_diputus(conn, tid: str, m, bl: Blok) -> bool:
    if m["status"] in DITINJAU:
        return False
    bl.galat("SUDAH_DIPUTUS", 409, f"Baris ini sudah diputus ({await _teks_diputus(conn, tid, m)}). Muat ulang daftar.")
    return True


def _ringkas_mutasi(m) -> dict:
    return {"id": str(m["id"]), "tanggal": m["tanggal"].isoformat(), "keterangan_ringkas": BD.ringkas_keterangan(m["keterangan"]), "arah": m["arah"],
            "nominal": str(m["nominal"]), "nominal_tampil": _rp(m["nominal"]), "status": m["status"], "status_text": BD.STATUS_TEKS.get(m["status"], m["status"])}


# ====================================================================== COCOKKAN
def _alokasi_eksplisit(body: dict, ids: list, bl: Blok) -> dict:
    hasil = {}
    for a in body.get("alokasi") or []:
        bt, n = _uuid((a or {}).get("bank_transaction_id")), _dec((a or {}).get("nominal"))
        if bt is None or n is None or n <= 0 or str(bt) not in ids or str(bt) in hasil:
            bl.galat("ALOKASI_TAK_SAH", 422, "Alokasi tidak sah: tiap dokumen yang dipilih paling banyak satu alokasi bernominal lebih dari nol.")
            return {}
        hasil[str(bt)] = n
    return hasil


async def rencana_cocokkan(conn, tid: str, rekening_id, mutasi_id, body: dict, bl: Blok, kunci: bool, ctx: Optional[dict] = None) -> dict:
    h = {"blocks": bl.daftar, "mutasi": None, "pasangan": [], "alokasi": [], "versi": None, "kalimat": None}
    m = await _muat(conn, tid, rekening_id, mutasi_id, bl, kunci)
    if not m:
        return h
    h["mutasi"] = m
    if await _tolak_bila_diputus(conn, tid, m, bl):
        return h
    ids = []
    for x in body.get("bank_transaction_ids") or []:
        u = _uuid(x)
        if u is None:
            bl.galat("UUID_TIDAK_SAH", 422, "Pilihan dokumen tidak sah.")
            return h
        if str(u) not in ids:
            ids.append(str(u))
    if not 1 <= len(ids) <= MAKS_BT:
        bl.galat("DOKUMEN_JUMLAH", 422, f"Pilih 1 sampai {MAKS_BT} dokumen untuk dicocokkan.")
        return h
    eksplisit = _alokasi_eksplisit(body, ids, bl)
    if kunci:   # kunci per dokumen, urut id (cegah dua baris berbeda mengalokasikan dokumen sama serentak)
        for bid in sorted(ids):
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"MUTASI_BT:{tid}:{bid}")
    rows = {str(r["id"]): r for r in await conn.fetch(TB.SQL_BT_PILIHAN, tid, rekening_id, [UUID(i) for i in ids])}
    if len(rows) != len(ids):
        bl.galat("DOKUMEN_TAK_BISA_DICOCOKKAN", 422, "Ada dokumen yang tidak ditemukan, sudah dibatalkan, sudah direkonsiliasi, atau milik rekening lain.")
        return h
    pasangan, alokasi, jumlah = [], [], Decimal(0)
    for i in ids:
        r = rows[i]
        nominal_bt, sudah = Decimal(str(r["nominal"])), Decimal(str(r["sudah_alokasi"]))
        sisa = nominal_bt - sudah
        if r["arah"] != m["arah"]:
            bl.galat("ARAH_BEDA", 422, f"{r['nomor']} berlawanan arah dengan baris bank ({'uang masuk' if m['arah'] == 'masuk' else 'uang keluar'}).")
            return h
        if int(r["jumlah_tautan"]) >= MAKS_BARIS_PER_BT:
            bl.galat("DOKUMEN_PENUH", 422, f"{r['nomor']} sudah dialokasikan ke {MAKS_BARIS_PER_BT} baris bank.")
            return h
        a = eksplisit.get(i)
        if a is None:
            a = m["nominal"] if (len(ids) == 1 and sisa >= m["nominal"]) else sisa
        if a <= 0 or a > sisa:
            bl.galat("ALOKASI_MELEBIHI_SISA", 422, f"Alokasi untuk {r['nomor']} ({_rp(a)}) melebihi sisa dokumen ({_rp(sisa)}).")
            return h
        jumlah += a
        alokasi.append({"bank_transaction_id": i, "nominal": a})
        pasangan.append({"bank_transaction_id": i, "nomor": r["nomor"], "jenis": KR.LABEL_SUMBER.get(r["sumber"], r["sumber"]), "tanggal": r["tanggal"].isoformat(),
                         "nominal": str(nominal_bt), "nominal_tampil": _rp(nominal_bt), "alokasi": str(a), "alokasi_tampil": _rp(a),
                         "kontak": {"jenis": r["kontak_jenis"], "id": str(r["kontak_id"]) if r["kontak_id"] else None, "nama": r["kontak_nama"]} if r["kontak_nama"] else None,
                         "selisih_hari": (r["tanggal"] - m["tanggal"]).days})
    if jumlah != m["nominal"]:
        bl.galat("NOMINAL_BEDA", 422, f"Total dokumen {_rp(jumlah)} tidak sama dengan baris bank {_rp(m['nominal'])} (selisih {_rp(abs(m['nominal'] - jumlah))}). "
                                        "Pilih dokumen lain atau atur alokasinya.")
        return h
    h["pasangan"], h["alokasi"] = pasangan, alokasi
    h["versi"] = _versi("cocokkan", str(m["id"]), str(m["nominal"]), m["status"], [(a["bank_transaction_id"], str(a["nominal"]), str(rows[a["bank_transaction_id"]]["sudah_alokasi"])) for a in alokasi])
    nomor = ", ".join(p["nomor"] for p in pasangan)
    h["kalimat"] = f"Baris {_rp(m['nominal'])} ({m['tanggal'].day}/{m['tanggal'].month}) dicocokkan dengan {nomor}. Tidak ada jurnal baru."
    return h


async def _tulis_cocokkan(conn, ctx: dict, r: dict) -> dict:
    tid, uid, m = ctx["tenant_id"], ctx["user_id"], r["mutasi"]
    try:
        for a in r["alokasi"]:
            await conn.execute(SQL_TAUTAN_INSERT, tid, m["id"], UUID(a["bank_transaction_id"]), a["nominal"], str(uid))
    except asyncpg.UniqueViolationError as e:
        raise HTTPException(status_code=409, detail={"code": "SUDAH_DIPUTUS", "message": "Dokumen sudah ditautkan ke baris ini. Muat ulang daftar."}) from e
    if not await conn.fetchrow(SQL_PUTUS, tid, m["id"], str(uid), "dicatat", "cocok", None, list(DITINJAU)):
        raise HTTPException(status_code=409, detail={"code": "SUDAH_DIPUTUS", "message": "Baris ini sudah diputus. Muat ulang daftar."})
    await catat_riwayat(conn, tid, ENTITAS, m["id"], None, EVENT["cocokkan"], uid, f"Dicocokkan dengan {', '.join(p['nomor'] for p in r['pasangan'])}",
                        meta={"rekening_id": str(m["bank_account_id"]), "nominal": str(m["nominal"]),
                              "tautan": [{"bank_transaction_id": p["bank_transaction_id"], "nomor": p["nomor"], "alokasi": p["alokasi"]} for p in r["pasangan"]]})
    return {"pasangan": r["pasangan"]}


# ====================================================================== KECUALIKAN
async def rencana_kecualikan(conn, tid: str, rekening_id, mutasi_id, body: dict, bl: Blok, kunci: bool, ctx: Optional[dict] = None) -> dict:
    h = {"blocks": bl.daftar, "mutasi": None, "alasan": None, "catatan": None, "versi": None, "kalimat": None}
    m = await _muat(conn, tid, rekening_id, mutasi_id, bl, kunci)
    if not m:
        return h
    h["mutasi"] = m
    if await _tolak_bila_diputus(conn, tid, m, bl):
        return h
    alasan, catatan = body.get("alasan"), (body.get("catatan") or "").strip()
    if alasan not in ALASAN_KECUALI:
        bl.galat("ALASAN_TAK_SAH", 422, "Alasan harus salah satu: duplikat, bukan transaksi, atau lain-lain.")
        return h
    if alasan == "lain" and len(catatan) < 3:
        bl.galat("CATATAN_WAJIB", 422, "Tulis catatan singkat untuk alasan lain-lain.")
        return h
    h["alasan"], h["catatan"] = alasan, catatan or None
    h["versi"] = _versi("kecualikan", str(m["id"]), str(m["nominal"]), m["status"], alasan, catatan)
    h["kalimat"] = f"Baris {_rp(m['nominal'])} dikecualikan: {ALASAN_KECUALI[alasan]}{' — ' + catatan if catatan else ''}. Tidak ada jurnal dibuat."
    return h


async def _tulis_kecualikan(conn, ctx: dict, r: dict) -> dict:
    tid, uid, m = ctx["tenant_id"], ctx["user_id"], r["mutasi"]
    teks = r["alasan"] + (f": {r['catatan']}" if r["catatan"] else "")
    if not await conn.fetchrow(SQL_PUTUS, tid, m["id"], str(uid), "dikecualikan", "kecualikan", teks, list(DITINJAU)):
        raise HTTPException(status_code=409, detail={"code": "SUDAH_DIPUTUS", "message": "Baris ini sudah diputus. Muat ulang daftar."})
    await catat_riwayat(conn, tid, ENTITAS, m["id"], None, EVENT["kecualikan"], uid, f"Dikecualikan: {ALASAN_KECUALI[r['alasan']]}",
                        meta={"rekening_id": str(m["bank_account_id"]), "nominal": str(m["nominal"]), "alasan": r["alasan"], "catatan": r["catatan"]})
    return {}


# ====================================================================== LEPASKAN
async def rencana_lepaskan(conn, tid: str, rekening_id, mutasi_id, body: dict, bl: Blok, kunci: bool, ctx: Optional[dict] = None) -> dict:
    h = {"blocks": bl.daftar, "mutasi": None, "tautan": [], "kembali_ke": None, "versi": None, "kalimat": None}
    m = await _muat(conn, tid, rekening_id, mutasi_id, bl, kunci)
    if not m:
        return h
    h["mutasi"] = m
    if m["status"] not in ("dicatat", "dikecualikan"):
        bl.galat("BELUM_DIPUTUS", 409, "Baris ini belum diputus, tidak ada yang dilepas.")
        return h
    if m["cara"] in ("catat", "pribadi"):
        bl.galat("PAKAI_BATALKAN", 409, "Baris ini dicatat sebagai transaksi. Gunakan Batalkan catatan untuk membatalkannya.")
        return h
    if m["rekonsiliasi_id"]:
        bl.galat("SUDAH_DIREKONSILIASI", 409, "Baris ini sudah direkonsiliasi, tidak bisa dilepas.")
        return h
    tautan = list(await conn.fetch(SQL_TAUTAN_MUTASI, tid, m["id"])) if m["cara"] == "cocok" else []
    if any(t["rekon"] for t in tautan):
        bl.galat("SUDAH_DIREKONSILIASI", 409, "Dokumen yang dicocokkan sudah direkonsiliasi, tidak bisa dilepas.")
        return h
    h["tautan"] = [{"bank_transaction_id": str(t["bank_transaction_id"]), "nomor": t["nomor"], "alokasi": str(t["nominal_alokasi"]), "alokasi_tampil": _rp(t["nominal_alokasi"])} for t in tautan]
    h["kembali_ke"] = "mungkin_duplikat" if m["duplikat_dari"] else "untuk_ditinjau"
    h["versi"] = _versi("lepaskan", str(m["id"]), m["status"], m["cara"], [(t["bank_transaction_id"], t["alokasi"]) for t in h["tautan"]])
    sisa = f" {', '.join(t['nomor'] for t in h['tautan'])} tetap tercatat." if h["tautan"] else ""
    h["kalimat"] = f"Baris kembali ke {BD.STATUS_TEKS[h['kembali_ke']]}.{sisa}"
    return h


async def _tulis_lepaskan(conn, ctx: dict, r: dict) -> dict:
    tid, uid, m = ctx["tenant_id"], ctx["user_id"], r["mutasi"]
    await conn.execute(SQL_TAUTAN_LEPAS, tid, m["id"], str(uid))
    if not await conn.fetchrow(SQL_KEMBALI, tid, m["id"], r["kembali_ke"]):
        raise HTTPException(status_code=409, detail={"code": "BELUM_DIPUTUS", "message": "Keputusan baris ini sudah berubah. Muat ulang daftar."})
    await catat_riwayat(conn, tid, ENTITAS, m["id"], None, EVENT["lepaskan"], uid, f"Keputusan dilepas ({m['cara']})",
                        meta={"rekening_id": str(m["bank_account_id"]), "cara_sebelum": m["cara"], "tautan": r["tautan"]})
    return {}




# ====================================================================== CATAT (BF-1c2): uang_masuk · uang_keluar · pribadi
def _bank_net(baris, coa_bank) -> Decimal:
    return sum((Decimal(str(d)) - Decimal(str(k)) for akun, d, k, _ in baris if str(akun) == str(coa_bank)), Decimal(0))


async def rencana_catat(conn, tid: str, rekening_id, mutasi_id, body: dict, bl: Blok, kunci: bool, ctx: Optional[dict] = None) -> dict:
    h = {"blocks": bl.daftar, "mutasi": None, "jenis": None, "akun": None, "baris": [], "saldo": [], "versi": None, "kalimat": None, "body_manual": None, "rencana_bank": None, "catatan": None,
         "dok": None, "akun_id": None}
    m = await _muat(conn, tid, rekening_id, mutasi_id, bl, kunci)
    if not m:
        return h
    h["mutasi"] = m
    if await _tolak_bila_diputus(conn, tid, m, bl):
        return h
    jenis = body.get("jenis")
    if jenis in JENIS_BELUM:
        bl.galat("JENIS_BELUM_TERSEDIA", 422, "Bayar tagihan belum bisa dicatat dari Bank Feed. Catat lewat modul Pembayaran dulu.")
        return h
    if jenis not in JENIS_CATAT:
        bl.galat("JENIS_TAK_SAH", 422, "Jenis catatan tidak dikenal.")
        return h
    h["jenis"] = jenis
    wajib_arah = {"uang_masuk": "masuk", "uang_keluar": "keluar"}.get(jenis)
    if wajib_arah and m["arah"] != wajib_arah:
        bl.galat("JENIS_TAK_SESUAI_ARAH", 422, f"Baris ini {'uang masuk' if m['arah'] == 'masuk' else 'uang keluar'}; pilih jenis yang sesuai arahnya.")
        return h
    if m["nominal"] != m["nominal"].to_integral_value():
        bl.galat("NOMINAL_BERSEN", 422, f"Nominal {_rp(m['nominal'])} berkoma; Uang masuk/keluar hanya menerima rupiah bulat. Atur selisih sen lewat Pisah baris.")
        return h
    if jenis in JENIS_DOK:
        return await _rencana_dokumen(conn, tid, rekening_id, h, m, jenis, body, bl, kunci, ctx)
    if jenis == "pribadi":
        if body.get("akun_id"):
            bl.galat("AKUN_TETAP", 422, "Akun untuk Pribadi ditetapkan sistem (Prive untuk uang keluar, Modal Pemilik untuk setoran); tidak bisa dipilih.")
            return h
        kode = AKUN_PRIBADI[m["arah"]]
        a = await conn.fetchrow(SQL_AKUN_KODE, tid, kode)
        if not a:
            bl.galat("AKUN_PRIVE_TAK_ADA" if m["arah"] == "keluar" else "AKUN_SETORAN_TAK_ADA", 422,
                     f"Akun {kode} ({'Prive' if m['arah'] == 'keluar' else 'Modal Pemilik'}) tidak ada atau nonaktif di usaha ini; tambahkan akunnya lebih dulu.")
            return h
        h["akun"] = {"id": str(a["id"]), "kode": a["account_code"], "nama": a["name"]}
        lawan = a["id"]
    else:
        lawan = _uuid(body.get("akun_id"))
        if lawan is None:
            bl.galat("AKUN_WAJIB", 422, "Pilih akun lawan untuk transaksi ini.")
            return h
    catatan = (body.get("catatan") or "").strip()
    h["catatan"] = catatan or None
    bm = CreateManualTransactionRequest(direction="in" if m["arah"] == "masuk" else "out", amount=int(m["nominal"]), transaction_date=m["tanggal"],
                                        description=(catatan or BD.ringkas_keterangan(m["keterangan"], 500))[:500] or "Mutasi bank", contra_account_id=str(lawan),
                                        contact_name=(body.get("kontak") or "").strip() or None)
    h["body_manual"] = bm
    if kunci:   # urutan kunci TETAP: MUTASI_BANK -> BANK_TX_MANUAL (sama dgn rute Uang Masuk/Keluar) -> kas per CoA (di dalam penentu)
        await KT.kunci_tx_manual(conn, rekening_id)
    r = await KT.rencana_tx_manual(conn, tid, rekening_id, bm, bl.pratinjau)
    for b in r["blocks"]:
        bl.daftar.append(b)
    h["rencana_bank"], h["baris"], h["saldo"] = r, r["baris"], r["saldo"]
    harap = m["nominal"] if m["arah"] == "masuk" else -m["nominal"]
    if _bank_net(r["baris"], r["akun"]["coa_id"]) != harap:      # nominal diverifikasi ULANG terhadap jurnal yang akan ditulis
        bl.galat("NOMINAL_JURNAL_TAK_SAMA", 409, "Jurnal yang akan ditulis tidak sama dengan nominal baris bank; dibatalkan demi keamanan.")
        return h
    label = {"uang_masuk": "Uang masuk", "uang_keluar": "Uang keluar", "pribadi": "Pribadi (setoran pemilik)" if m["arah"] == "masuk" else "Pribadi (prive)"}[jenis]
    ke = f" ke {h['akun']['nama']} ({h['akun']['kode']})" if h["akun"] else ""
    h["kalimat"] = f"{label} {_rp(m['nominal'])} ({m['tanggal'].day}/{m['tanggal'].month}) akan dicatat{ke}. Jurnal ada di bawah."
    h["versi"] = _versi("catat", str(m["id"]), str(m["nominal"]), m["status"], jenis, str(lawan), bm.contact_name, bm.description, [(str(a), str(d), str(k)) for a, d, k, _ in r["baris"]])
    return h


# ====================================================================== CATAT (BF-1c3): transfer · uang_muka_pelanggan · terima_pembayaran (penulis MODULNYA)
async def _izin(ctx, jalur: str, bl: Blok, kata: str) -> bool:
    """Rute Bank Feed beda dari rute modul: izin modul tujuan diperiksa DI SINI (tanpa konteks pengguna = unit orkestrasi, tak diperiksa)."""
    user = (ctx or {}).get("user")
    if not user:
        return True
    if (await izin_rute_pengguna(user, [jalur], "POST")).get(jalur):
        return True
    bl.galat("IZIN_DITOLAK", 403, f"Anda tidak punya izin untuk {kata}. Minta pemilik usaha membukanya.")
    return False


def _aduh(bl: Blok, blocks) -> None:
    for b in blocks:
        bl.galat(b["code"], b.get("status", 400), b["message"])


def _total(baris):
    return sum((Decimal(str(d)) for _, d, _, _ in baris), Decimal(0)), sum((Decimal(str(k)) for _, _, k, _ in baris), Decimal(0))


async def _pelanggan(conn, tid: str, body: dict, bl: Blok):
    kid = _uuid(body.get("kontak"))
    if kid is None:
        bl.galat("KONTAK_WAJIB", 422, "Pilih pelanggannya.")
        return None
    p = await conn.fetchrow(SQL_PELANGGAN, tid, kid)
    if not p:
        bl.galat("PELANGGAN_TAK_ADA", 422, "Pelanggan tidak ditemukan.")
        return None
    return p


async def _rencana_dokumen(conn, tid: str, rekening_id, h: dict, m, jenis: str, body: dict, bl: Blok, kunci: bool, ctx) -> dict:
    if jenis != "transfer" and m["arah"] != "masuk":
        bl.galat("JENIS_TAK_SESUAI_ARAH", 422, f"{LABEL_JENIS[jenis]} hanya untuk uang masuk; baris ini uang keluar.")
        return h
    if not await _izin(ctx, IZIN_BUAT[jenis], bl, f"membuat {LABEL_JENIS[jenis].lower()}"):
        return h
    rek = await KT.akun(conn, tid, rekening_id)
    if not rek:
        bl.galat("REKENING_TAK_ADA", 404, "Rekening tidak ditemukan.")
        return h
    catatan = (body.get("catatan") or "").strip()
    h["catatan"] = catatan or None
    isi = catatan or BD.ringkas_keterangan(m["keterangan"], 200)
    nominal, tgl = m["nominal"], m["tanggal"]
    harap = nominal if m["arah"] == "masuk" else -nominal
    waktu = f"({tgl.day}/{tgl.month})"
    if jenis == "transfer":
        lawan = _uuid(body.get("rekening_lawan"))
        if lawan is None:
            bl.galat("TRANSFER_LAWAN_WAJIB", 422, "Pilih rekening lawan transfernya.")
            return h
        if str(lawan) == str(rekening_id):
            bl.galat("TRANSFER_LAWAN_SAMA", 422, "Rekening lawan tidak boleh sama dengan rekening ini.")
            return h
        dari, ke = (str(rekening_id), str(lawan)) if m["arah"] == "keluar" else (str(lawan), str(rekening_id))
        dok = CreateBankTransferRequest(from_bank_id=dari, to_bank_id=ke, amount=int(nominal), fee_amount=0, transfer_date=tgl, ref_no=None, notes=isi[:500] or None, auto_post=True)
        r = await KT.rencana_buat_transfer(conn, tid, dok, bl.pratinjau)
        _aduh(bl, r["blocks"])
        h["baris"], h["saldo"] = r["baris"], r["saldo"]
        d, k = _total(r["baris"])
        if d != nominal or k != nominal or _bank_net(r["baris"], rek["coa_id"]) != harap:       # nominal diverifikasi ULANG terhadap jurnal yang akan ditulis
            bl.galat("NOMINAL_JURNAL_TAK_SAMA", 409, "Jurnal yang akan ditulis tidak sama dengan nominal baris bank; dibatalkan demi keamanan.")
            return h
        h["dok"], h["akun_id"] = dok, ((r["tujuan"] if m["arah"] == "keluar" else r["asal"]) or {}).get("coa_id")
        nama_lawan = ((r["tujuan"] if m["arah"] == "keluar" else r["asal"]) or {}).get("account_name") or "rekening lawan"
        h["kalimat"] = f"Transfer {_rp(nominal)} {waktu} {'ke' if m['arah'] == 'keluar' else 'dari'} {nama_lawan} akan dicatat. Jurnal ada di bawah. Sisi rekening lawan dicocokkan dari barisnya sendiri."
        h["versi"] = _versi("catat", str(m["id"]), str(nominal), m["status"], jenis, dari, ke, dok.notes, [(str(a), str(dd), str(kk)) for a, dd, kk, _ in r["baris"]])
        return h
    p = await _pelanggan(conn, tid, body, bl) if jenis == "uang_muka_pelanggan" else None
    if jenis == "uang_muka_pelanggan" and not p:
        return h
    n_lama = int(await conn.fetchval(SQL_TAUTAN_JUMLAH, tid, m["id"]) or 0)
    kunci_idem = f"BANKFEED:{m['id']}:{n_lama + 1}"                  # berurut: dicatat ulang setelah batal tak bentrok dengan dokumen lama
    ctx_eff = ctx or {"tenant_id": tid, "user_id": None}
    await GUC.pasang_tenant(conn, tid)
    metode = "cash" if rek["account_type"] == "cash" else "transfer"
    if jenis == "uang_muka_pelanggan":
        so = _uuid(body.get("so_id"))
        dok = CD.CreateCustomerDepositRequest(customer_id=str(p["id"]), customer_name=p["nama"], amount=int(nominal), deposit_date=tgl, payment_method=metode,
                                              account_id=str(rek["coa_id"]), bank_account_id=str(rek["id"]), reference=None, notes=isi[:500] or None, auto_post=True,
                                              sales_order_id=str(so) if so else None, idempotency_key=kunci_idem)
        rb = await CD.rencana_buat_dp(conn, ctx_eff, dok, bl.pratinjau)
        _aduh(bl, rb["blocks"])
        akun_dp = rb.get("akun_dp") or await resolve_account_id_by_role(conn, tid, AccountRole.CUSTOMER_DEPOSIT_LIABILITY)
        h["baris"] = [(rek["coa_id"], nominal, Decimal(0), "Uang muka diterima"), (akun_dp, Decimal(0), nominal, "Uang muka pelanggan")]
        h["dok"], h["rb"], h["akun_id"] = dok, rb, rek["coa_id"]
        h["kalimat"] = f"Uang muka {_rp(nominal)} {waktu} dari {p['nama']} akan dicatat sebagai uang muka pelanggan. Jurnal ada di bawah."
        h["versi"] = _versi("catat", str(m["id"]), str(nominal), m["status"], jenis, str(p["id"]), dok.notes, str(dok.sales_order_id), str(rek["coa_id"]), str(akun_dp))
        return h
    # terima_pembayaran: alokasi EKSPLISIT (Q2); kelebihan mengikuti aturan Terima Pembayaran yang ada (jadi uang muka pelanggan) dan DITAMPILKAN (Q4)
    mentah = body.get("alokasi") or []
    if not mentah:
        bl.galat("ALOKASI_WAJIB", 422, "Pilih faktur yang dibayar beserta nominalnya.")
        return h
    alok = []
    for a in mentah:
        iid, n = _uuid(a.get("invoice_id")), _dec(a.get("nominal"))
        if iid is None or n is None or n <= 0:
            bl.galat("ALOKASI_TAK_SAH", 422, "Alokasi faktur tidak sah (faktur atau nominal salah).")
            return h
        alok.append(RP.AllocationInput(invoice_id=str(iid), amount_applied=n))
    if sum((a.amount_applied for a in alok), Decimal(0)) > nominal:
        bl.galat("ALOKASI_MELEBIHI_NOMINAL", 422, f"Jumlah alokasi melebihi nominal baris bank {_rp(nominal)}.")
        return h
    p = await _pelanggan(conn, tid, body, bl)
    if not p:
        return h
    dok = RP.CreateReceivePaymentRequest(customer_id=str(p["id"]), customer_name=p["nama"], payment_date=tgl, payment_method="bank_transfer", bank_account_id=str(rek["coa_id"]),
                                         bank_account_name=rek["account_name"], total_amount=nominal, source_type="cash", notes=isi[:500] or None, allocations=alok,
                                         save_as_draft=False, idempotency_key=kunci_idem)
    ro = await RP.rencana_penerimaan_ro(conn, ctx_eff, dok)
    _aduh(bl, ro["blocks"])
    sisa = Decimal(str((ro.get("cek") or {}).get("unapplied_amount") or 0))
    h["kelebihan"] = {"jumlah": str(sisa.quantize(SEN)), "nomor": ro.get("overpayment_deposit_number")} if sisa > 0 else None
    h["faktur_sesudah"] = ro.get("invoices") or []
    h["jurnal_ringkas"] = [x for j in ro.get("journals") or [] for x in j["lines"]]
    h["dok"], h["akun_id"] = dok, rek["coa_id"]
    lebih = f" Kelebihan {_rp(sisa)} menjadi uang muka pelanggan ({ro.get('overpayment_deposit_number') or 'nomor terbit saat disimpan'})." if sisa > 0 else ""
    h["kalimat"] = f"Pembayaran {_rp(nominal)} {waktu} dari {p['nama']} akan dialokasikan ke {len(alok)} faktur.{lebih}"
    h["versi"] = _versi("catat", str(m["id"]), str(nominal), m["status"], jenis, str(p["id"]), [(a.invoice_id, str(a.amount_applied)) for a in alok], str(sisa), dok.notes)
    return h


async def _tulis_dokumen(conn, ctx: dict, r: dict) -> dict:
    tid, uid, m, jenis, dok = ctx["tenant_id"], ctx["user_id"], r["mutasi"], r["jenis"], r["dok"]
    if jenis == "transfer":
        t = await BTR.buat_transfer_core(conn, ctx, dok)
        p = t["post"]
        bt_id = UUID(str(p["from_transaction_id"] if m["arah"] == "keluar" else p["to_transaction_id"]))       # HANYA sisi rekening ini; sisi lawan tak ditautkan
        nomor, jurnal_id = t["transfer_number"], p["journal_id"]
    else:
        if jenis == "uang_muka_pelanggan":
            d = (await CD.buat_dp_core(conn, ctx, dok, r["rb"]))["data"]
            nomor = d["deposit_number"]
        else:
            d = (await RP.buat_penerimaan(conn, ctx, dok))["data"]
            nomor = d["payment_number"]
        jurnal_id = d["journal_id"]
        bts = list(await conn.fetch(SQL_BT_JURNAL, tid, UUID(str(jurnal_id)), m["bank_account_id"]))
        if len(bts) != 1:
            raise HTTPException(status_code=409, detail={"code": "BT_HASIL_TAK_DITEMUKAN", "message": "Baris bank hasil dokumen tidak ditemukan; dibatalkan demi keamanan."})
        bt_id = bts[0]["id"]
    await conn.execute(SQL_TAUTAN_INSERT, tid, m["id"], bt_id, m["nominal"], str(uid))
    if not await conn.fetchrow(SQL_PUTUS_CATAT, tid, m["id"], str(uid), "dicatat", "catat", r["catatan"], list(DITINJAU), UUID(str(r["akun_id"])) if r["akun_id"] else None):
        raise HTTPException(status_code=409, detail={"code": "SUDAH_DIPUTUS", "message": "Baris ini sudah diputus. Muat ulang daftar."})
    await catat_riwayat(conn, tid, ENTITAS, m["id"], None, EVENT["catat"], uid, f"Dicatat sebagai {nomor} ({jenis})",
                        meta={"rekening_id": str(m["bank_account_id"]), "nominal": str(m["nominal"]), "jenis": jenis, "bank_transaction_id": str(bt_id), "journal_number": nomor})
    return {"bank_transaction_id": str(bt_id), "journal_id": str(jurnal_id), "journal_number": nomor, "kalimat": f"{LABEL_JENIS[jenis]} {_rp(m['nominal'])} dicatat sebagai {nomor}."}


async def _tulis_catat(conn, ctx: dict, r: dict) -> dict:
    if r["jenis"] in JENIS_DOK:
        return await _tulis_dokumen(conn, ctx, r)
    tid, uid, m = ctx["tenant_id"], ctx["user_id"], r["mutasi"]
    t = await KT.tulis_tx_manual(conn, ctx, m["bank_account_id"], r["body_manual"], r["rencana_bank"])
    await conn.execute(SQL_TAUTAN_INSERT, tid, m["id"], t["bank_txn_id"], m["nominal"], str(uid))
    cara = "pribadi" if r["jenis"] == "pribadi" else "catat"
    if not await conn.fetchrow(SQL_PUTUS_CATAT, tid, m["id"], str(uid), "dicatat", cara, r["catatan"], list(DITINJAU), UUID(str(r["body_manual"].contra_account_id))):
        raise HTTPException(status_code=409, detail={"code": "SUDAH_DIPUTUS", "message": "Baris ini sudah diputus. Muat ulang daftar."})
    await catat_riwayat(conn, tid, ENTITAS, m["id"], None, EVENT["catat"], uid, f"Dicatat sebagai {t['journal_number']} ({r['jenis']})",
                        meta={"rekening_id": str(m["bank_account_id"]), "nominal": str(m["nominal"]), "jenis": r["jenis"], "bank_transaction_id": str(t["bank_txn_id"]),
                              "journal_number": t["journal_number"], "akun_id": str(r["body_manual"].contra_account_id)})
    return {"bank_transaction_id": str(t["bank_txn_id"]), "journal_id": str(t["journal_id"]), "journal_number": t["journal_number"],
            "kalimat": f"{LABEL_JENIS[r['jenis']]} {_rp(m['nominal'])} dicatat sebagai {t['journal_number']}."}


# ====================================================================== BATALKAN-CATATAN (BF-1c2): void lewat penulis yang ada = jurnal PEMBALIK
async def rencana_batalkan(conn, tid: str, rekening_id, mutasi_id, body: dict, bl: Blok, kunci: bool, ctx: Optional[dict] = None) -> dict:
    h = {"blocks": bl.daftar, "mutasi": None, "tautan": [], "baris": [], "saldo": [], "versi": None, "kalimat": None, "kalimat_tiga": [], "bt_id": None, "alasan": None, "kembali_ke": None}
    m = await _muat(conn, tid, rekening_id, mutasi_id, bl, kunci)
    if not m:
        return h
    h["mutasi"] = m
    if m["status"] not in ("dicatat", "dikecualikan"):
        bl.galat("BELUM_DIPUTUS", 409, "Baris ini belum diputus, tidak ada yang dibatalkan.")
        return h
    if m["cara"] not in ("catat", "pribadi"):
        bl.galat("PAKAI_LEPASKAN", 409, "Baris ini bukan hasil Catat. Gunakan Lepaskan untuk membatalkan keputusannya.")
        return h
    alasan = (body.get("alasan") or "").strip()
    if len(alasan) < 3:
        bl.galat("ALASAN_WAJIB", 422, "Tulis alasan pembatalan (paling sedikit 3 huruf).")
        return h
    tautan = list(await conn.fetch(SQL_TAUTAN_MUTASI, tid, m["id"]))
    if not tautan:
        bl.galat("CATATAN_TANPA_DOKUMEN", 409, "Dokumen hasil catatan ini tidak ditemukan; batalkan dari modul asalnya.")
        return h
    h["alasan"], h["bt_id"], h["tautan"] = alasan, tautan[0]["bank_transaction_id"], [str(t["bank_transaction_id"]) for t in tautan]
    h["kembali_ke"] = "mungkin_duplikat" if m["duplikat_dari"] else "untuk_ditinjau"
    dok = await conn.fetchrow(SQL_DOK_TAUTAN, tid, h["bt_id"])
    tipe = dok["source_type"] if dok else None
    if tipe not in TIPE_DOK:
        bl.galat("JENIS_DOKUMEN_TAK_DIKENAL", 409, "Dokumen hasil catatan ini bukan jenis yang bisa dibatalkan dari Bank Feed; batalkan dari modul asalnya.")
        return h
    h["dok"] = {"tipe": tipe, "id": str(dok["source_id"]), "nomor": tautan[0]["nomor"]}
    h["versi"] = _versi("batalkan", str(m["id"]), m["status"], m["cara"], h["tautan"], alasan, tipe, h["dok"]["id"])
    if tipe in IZIN_VOID and not await _izin(ctx, IZIN_VOID[tipe].format(h["dok"]["id"]), bl, "membatalkan dokumen ini"):
        return h
    if not kunci:   # pratinjau: penentu void yang SAMA (READ ONLY). Tautan baris ini dilepas lebih dulu saat tulis, jadi BANK_TX_BERTAUT-nya bukan penghalang.
        bl2 = Blok(True)
        nomor, pembalik = tautan[0]["nomor"], None
        if tipe == "BANK_TRANSACTION":
            rv = await KT.rencana_void_manual(conn, tid, h["bt_id"], alasan, bl2, kunci=False)
            h["baris"], h["saldo"] = rv.get("baris") or [], rv.get("saldo") or []
            nomor = (rv.get("je") or {}).get("journal_number") or nomor
        elif tipe == "BANK_TRANSFER":
            rv = await KT.rencana_void_transfer(conn, tid, h["dok"]["id"], alasan, bl2, kunci=False)
            h["baris"], h["saldo"] = rv.get("baris") or [], rv.get("saldo") or []
            nomor = (rv.get("t") or {}).get("transfer_number") or nomor
        else:
            ctx_eff = ctx or {"tenant_id": tid, "user_id": None}
            try:
                if tipe == "CUSTOMER_DEPOSIT":
                    bl2.daftar.extend(await CD._rencana_dp(conn, ctx_eff, h["dok"]["id"], "void", VoidCustomerDepositRequest(reason=alasan)))
                else:
                    rv = await RP._rencana_void_pembayaran(conn, ctx_eff, h["dok"]["id"], alasan, wajib_alasan=False, kunci=False)
                    bl2.daftar.extend(rv["blocks"])
                    nomor = (rv.get("payment") or {}).get("payment_number") or nomor
            except HTTPException as e:       # penjaga lama melempar (mis. BANK_TX_BERTAUT): tampilkan sebagai blok, kecuali yang memang dilepas lebih dulu saat tulis
                kode = getattr(e, "code", None) or (e.detail.get("code") if isinstance(e.detail, dict) else None) or "TIDAK_BISA_DIBATALKAN"
                bl2.daftar.append({"code": kode, "message": e.detail["message"] if isinstance(e.detail, dict) else str(e.detail)})
            # jurnal pembalik pratinjau = jurnal asli dengan debit/kredit DITUKAR (diverifikasi di harness terhadap pembalik sungguhan)
            h["baris"] = [(x["account_id"], x["credit"], x["debit"], "Pembalik") for x in await conn.fetch(SQL_JURNAL_BARIS, tid, h["bt_id"])]
        for b in bl2.daftar:
            if b["code"] != "BANK_TX_BERTAUT":
                bl.daftar.append(b)
        h["kalimat_tiga"] = [f"Dokumen {nomor} dibatalkan", f"Jurnal pembalik RV-{nomor} dibuat", f"Baris kembali ke {BD.STATUS_TEKS[h['kembali_ke']]}"]
        h["kalimat"] = "; ".join(h["kalimat_tiga"]) + "."
    return h


async def _tulis_batalkan(conn, ctx: dict, r: dict) -> dict:
    tid, uid, m = ctx["tenant_id"], ctx["user_id"], r["mutasi"]
    await conn.execute(SQL_TAUTAN_LEPAS, tid, m["id"], str(uid))        # tautan DILEPAS dulu: penjaga void menghitung tautan aktif (BANK_TX_BERTAUT)
    tipe, did, alasan = r["dok"]["tipe"], r["dok"]["id"], r["alasan"]
    if tipe == "BANK_TRANSACTION":
        await KT.kunci_void_manual(conn, r["bt_id"])
        rv = await KT.rencana_void_manual(conn, tid, r["bt_id"], alasan, Blok(False), kunci=True)       # blok pertama dilempar (rollback seluruh transaksi)
        v = await KT.tulis_void_manual(conn, ctx, r["bt_id"], alasan, rv)
        nomor, pembalik = rv["je"]["journal_number"], v["reversal_number"]
    elif tipe == "BANK_TRANSFER":
        await BTR.kunci_void_transfer(conn, did)
        rv = await KT.rencana_void_transfer(conn, tid, did, alasan, Blok(False), kunci=True)
        await BTR.void_transfer_core(conn, ctx, did, alasan, rv)
        nomor = rv["t"]["transfer_number"]
        pembalik = f"RV-{nomor}"
    elif tipe == "CUSTOMER_DEPOSIT":
        await CD.void_deposit_core(conn, ctx, did, VoidCustomerDepositRequest(reason=alasan))
        nomor = r["dok"]["nomor"]
        pembalik = f"RV-{nomor}"
    else:
        rv = await RP._rencana_void_pembayaran(conn, ctx, did, alasan, wajib_alasan=False, kunci=True)
        if rv["blocks"]:
            b = rv["blocks"][0]
            raise GalatBerkode(400, b["code"], b["message"])
        await RP._tulis_void_pembayaran(conn, ctx, did, alasan, rv)
        nomor = (rv.get("payment") or {}).get("payment_number") or r["dok"]["nomor"]
        pembalik = f"RV-{nomor}"
    if not await conn.fetchrow(SQL_KEMBALI_CATAT, tid, m["id"], r["kembali_ke"]):
        raise HTTPException(status_code=409, detail={"code": "BELUM_DIPUTUS", "message": "Keputusan baris ini sudah berubah. Muat ulang daftar."})
    await catat_riwayat(conn, tid, ENTITAS, m["id"], None, EVENT["batalkan"], uid, f"Catatan {nomor} dibatalkan",
                        meta={"rekening_id": str(m["bank_account_id"]), "nominal": str(m["nominal"]), "bank_transaction_id": str(r["bt_id"]), "alasan": r["alasan"],
                              "jurnal_pembalik": pembalik, "tipe_dokumen": tipe})
    return {"jurnal_pembalik": pembalik, "kalimat": f"Dokumen {nomor} dibatalkan; jurnal pembalik {pembalik} dibuat; baris kembali ke {BD.STATUS_TEKS[r['kembali_ke']]}."}


RENCANA = {"cocokkan": rencana_cocokkan, "kecualikan": rencana_kecualikan, "lepaskan": rencana_lepaskan, "catat": rencana_catat, "batalkan": rencana_batalkan}
TULIS = {"cocokkan": _tulis_cocokkan, "kecualikan": _tulis_kecualikan, "lepaskan": _tulis_lepaskan, "catat": _tulis_catat, "batalkan": _tulis_batalkan}


def _keluaran(r: dict, aksi: str, body: dict) -> dict:
    blocks = [{"code": b["code"], "message": b["message"]} for b in r["blocks"]]
    data = {"ok": not blocks, "blocks": blocks, "aksi": aksi, "mutasi": _ringkas_mutasi(r["mutasi"]) if r["mutasi"] else None, "jurnal": [], "kalimat": r["kalimat"],
            "payload": {**{k: v for k, v in body.items() if k != "versi"}, "versi": r["versi"]}}
    for k in ("pasangan", "tautan", "kembali_ke", "alasan", "jenis", "akun", "kalimat_tiga", "kelebihan", "faktur_sesudah", "jurnal_ringkas"):
        if k in r:
            data[k] = r[k]
    return data


async def _keluaran_async(conn, tid: str, r: dict, aksi: str, body: dict) -> dict:
    """Bentuk pratinjau; untuk aksi yang MENULIS jurnal (catat, batalkan) jurnal + saldo diformat oleh KT.keluaran (SATU bentuk dengan penulis Kas & Bank lain)."""
    data = _keluaran(r, aksi, body)
    if aksi in ("catat", "batalkan"):
        k = await KT.keluaran(conn, tid, {"blocks": r["blocks"], "baris": r.get("baris") or [], "saldo": r.get("saldo") or []}, data["payload"])
        data["jurnal"], data["saldo"] = k["jurnal"], k["saldo"]
    return data


async def siapkan(pool, tid: str, aksi: str, body: dict) -> None:
    """Prasyarat peran akun modul tujuan (butuh pool, di LUAR transaksi) -- dipanggil rute sebelum pratinjau/tulis jenis dokumen modul."""
    if aksi != "catat":
        return
    if body.get("jenis") == "uang_muka_pelanggan":
        await CD._ensure_role_preconditions(pool, tid)
    elif body.get("jenis") == "terima_pembayaran":
        await RP._ensure_receive_payments_role_preconditions(pool, tid)
    elif body.get("jenis") == "transfer":
        await BTR._ensure_bank_transfers_role_preconditions(pool, tid)


async def pratinjau(pool, tid: str, rekening_id, mutasi_id, aksi: str, body: dict, ctx: Optional[dict] = None) -> dict:
    """Pratinjau B8: transaksi READ ONLY; penentu SAMA dengan tulis; jurnal selalu [] (NOL jurnal)."""
    await siapkan(pool, tid, aksi, body)
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            r = await RENCANA[aksi](conn, tid, rekening_id, mutasi_id, body, Blok(True), False, ctx)
            return {"success": True, "data": await _keluaran_async(conn, tid, r, aksi, body)}


async def tulis(conn, ctx: dict, rekening_id, mutasi_id, aksi: str, body: dict) -> dict:
    """Tulis di transaksi PEMANGGIL (idem sudah di rute): MUTASI_BANK:{id} -> penentu yang SAMA (FOR UPDATE, status dibaca ulang) -> versi -> tulis + riwayat."""
    tid = ctx["tenant_id"]
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"MUTASI_BANK:{mutasi_id}")
    r = await RENCANA[aksi](conn, tid, rekening_id, mutasi_id, body, Blok(False), True, ctx)
    if body.get("versi") and body["versi"] != r["versi"]:
        raise HTTPException(status_code=409, detail={"code": "RENCANA_BERUBAH", "message": "Keadaan baris atau dokumen berubah sejak pratinjau. Tinjau ulang sebelum menyimpan."})
    ekstra = await TULIS[aksi](conn, ctx, r)
    m = await conn.fetchrow(SQL_MUTASI, tid, rekening_id, mutasi_id)
    kalimat = ekstra.pop("kalimat", r["kalimat"])
    return {"id": str(m["id"]), "status": m["status"], "status_text": BD.STATUS_TEKS.get(m["status"], m["status"]), "diputus_text": await _teks_diputus(conn, tid, m),
            "kalimat": kalimat, **ekstra}


# ====================================================================== RIWAYAT
async def riwayat(conn, tid: str, rekening_id, mutasi_id) -> list:
    """Jejak per baris dari audit_logs (append-only, dijaga trigger) + penanda 'Diimpor'. Urut waktu naik."""
    m = await conn.fetchrow(SQL_MUTASI, tid, rekening_id, mutasi_id)
    if not m:
        raise HTTPException(status_code=404, detail={"code": "MUTASI_TAK_ADA", "message": "Mutasi bank tidak ditemukan."})
    awal = await conn.fetchrow(SQL_DIIMPOR, tid, mutasi_id)
    rows = list(await conn.fetch(SQL_RIWAYAT, tid, mutasi_id))
    ids = [x["user_id"] for x in rows if x["user_id"]] + ([awal["oleh"]] if awal and awal["oleh"] else [])
    nama = await nama_untuk(conn, [UUID(str(i)) for i in ids if _uuid(i)]) if ids else {}
    zona = await zona_tenant(conn, tid)
    hasil = []
    if awal:
        hasil.append({"aksi": "diimpor", "aksi_text": "Diimpor", "oleh_text": nama.get(str(awal["oleh"]), "pengguna") if awal["oleh"] else "pengguna",
                      "pada": awal["pada"].isoformat(), "pada_text": BD._waktu(zona, awal["pada"]), "ringkas": "Baris masuk dari berkas rekening koran"})
    for x in rows:
        kunci, teks = AKSI_TEKS.get(x["event"], (x["event"].lower(), x["event"]))
        meta = json.loads(x["metadata"]) if isinstance(x["metadata"], str) else (x["metadata"] or {})
        hasil.append({"aksi": kunci, "aksi_text": teks, "oleh_text": nama.get(str(x["user_id"]), "pengguna") if x["user_id"] else "pengguna",
                      "pada": x["pada"].isoformat(), "pada_text": BD._waktu(zona, x["pada"]), "ringkas": meta.get("ringkas")})
    return hasil
