"""Aksi Bank Feed BF-1c1 (KONTRAK-bf1c1): COCOKKAN · KECUALIKAN · LEPASKAN + RIWAYAT. NOL jurnal baru: aksi ini hanya MENAUTKAN/MENANDAI baris rekening koran.
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
from . import kasbank_ringkas as KR
from . import kasbank_tulis as KT
from . import teks_galat as tg
from .bayar_keluar_rencana import Blok
from .nama_pengguna import nama_untuk
from .so_riwayat import catat_riwayat
from ..schemas.bank_accounts import CreateManualTransactionRequest
from ..utils.tanggal_tenant import zona_tenant

SEN = Decimal("0.01")
DITINJAU = ("untuk_ditinjau", "mungkin_duplikat")
MAKS_BT = 4                    # satu baris bank <= 4 dokumen (gabungan)
MAKS_BARIS_PER_BT = 3          # satu dokumen <= 3 baris bank
ALASAN_KECUALI = {"duplikat": "Duplikat", "bukan_transaksi": "Bukan transaksi", "lain": "Lain-lain"}
EVENT = {"cocokkan": "BANK_FEED_COCOKKAN", "kecualikan": "BANK_FEED_KECUALIKAN", "lepaskan": "BANK_FEED_LEPASKAN", "catat": "BANK_FEED_CATAT", "batalkan": "BANK_FEED_BATALKAN"}
AKSI_TEKS = {"BANK_FEED_COCOKKAN": ("cocokkan", "Dicocokkan"), "BANK_FEED_KECUALIKAN": ("kecualikan", "Dikecualikan"), "BANK_FEED_LEPASKAN": ("lepaskan", "Dilepas"),
             "BANK_FEED_CATAT": ("catat", "Dicatat"), "BANK_FEED_BATALKAN": ("batalkan", "Dibatalkan")}
JENIS_CATAT = ("uang_masuk", "uang_keluar", "pribadi")
# jenis yang penulisnya belum dipanggil Bank Feed (c3 / BF-1d): diblok berkode, bukan diam
JENIS_BELUM = ("bayar_tagihan", "terima_pembayaran", "transfer", "uang_muka_pelanggan")
# Putusan pemilik LANGSUNG 12 Nov 2026: pribadi keluar Dr 3-40000 Prive; pribadi masuk Cr 3-10100 Modal Pemilik. Akun ditetapkan sistem (bukan pilihan pengguna).
AKUN_PRIBADI = {"keluar": "3-40000", "masuk": "3-10100"}
LABEL_JENIS = {"uang_masuk": "Uang masuk", "uang_keluar": "Uang keluar", "pribadi": "Pribadi"}
ENTITAS = "mutasi_bank"

SQL_MUTASI = ("SELECT id, bank_account_id, tanggal, keterangan, arah, nominal, status, cara, alasan, diputus_oleh, diputus_pada, duplikat_dari, rekonsiliasi_id "
              "FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2 AND id = $3 AND status <> 'dipisah'")
SQL_TAUTAN_MUTASI = ("SELECT t.id, t.bank_transaction_id, t.nominal_alokasi, bt.transaction_number AS nomor, bt.transaction_type AS jenis, bt.is_reconciled AS rekon, "
                     "NULLIF(btrim(bt.payee_payer), '') AS kontak_nama FROM mutasi_bank_tautan t JOIN bank_transactions bt ON bt.id = t.bank_transaction_id AND bt.tenant_id = t.tenant_id "
                     "WHERE t.tenant_id = $1 AND t.mutasi_bank_id = $2 AND t.dilepas_pada IS NULL ORDER BY t.dibuat_pada, t.id")
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


async def rencana_cocokkan(conn, tid: str, rekening_id, mutasi_id, body: dict, bl: Blok, kunci: bool) -> dict:
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
async def rencana_kecualikan(conn, tid: str, rekening_id, mutasi_id, body: dict, bl: Blok, kunci: bool) -> dict:
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
async def rencana_lepaskan(conn, tid: str, rekening_id, mutasi_id, body: dict, bl: Blok, kunci: bool) -> dict:
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


async def rencana_catat(conn, tid: str, rekening_id, mutasi_id, body: dict, bl: Blok, kunci: bool) -> dict:
    h = {"blocks": bl.daftar, "mutasi": None, "jenis": None, "akun": None, "baris": [], "saldo": [], "versi": None, "kalimat": None, "body_manual": None, "rencana_bank": None, "catatan": None}
    m = await _muat(conn, tid, rekening_id, mutasi_id, bl, kunci)
    if not m:
        return h
    h["mutasi"] = m
    if await _tolak_bila_diputus(conn, tid, m, bl):
        return h
    jenis = body.get("jenis")
    if jenis in JENIS_BELUM:
        bl.galat("JENIS_BELUM_TERSEDIA", 422, "Jenis ini belum bisa dicatat dari Bank Feed. Pilih Uang masuk, Uang keluar, atau Pribadi, atau catat lewat modulnya dulu.")
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


async def _tulis_catat(conn, ctx: dict, r: dict) -> dict:
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
async def rencana_batalkan(conn, tid: str, rekening_id, mutasi_id, body: dict, bl: Blok, kunci: bool) -> dict:
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
    h["versi"] = _versi("batalkan", str(m["id"]), m["status"], m["cara"], h["tautan"], alasan)
    if not kunci:   # pratinjau: penentu void yang SAMA (READ ONLY). Tautan baris ini dilepas lebih dulu saat tulis, jadi BANK_TX_BERTAUT-nya bukan penghalang.
        bl2 = Blok(True)
        rv = await KT.rencana_void_manual(conn, tid, h["bt_id"], alasan, bl2, kunci=False)
        for b in bl2.daftar:
            if b["code"] != "BANK_TX_BERTAUT":
                bl.daftar.append(b)
        h["baris"], h["saldo"] = rv.get("baris") or [], rv.get("saldo") or []
        nomor = (rv.get("je") or {}).get("journal_number") or tautan[0]["nomor"]
        h["kalimat_tiga"] = [f"Dokumen {nomor} dibatalkan", f"Jurnal pembalik RV-{nomor} dibuat", f"Baris kembali ke {BD.STATUS_TEKS[h['kembali_ke']]}"]
        h["kalimat"] = "; ".join(h["kalimat_tiga"]) + "."
    return h


async def _tulis_batalkan(conn, ctx: dict, r: dict) -> dict:
    tid, uid, m = ctx["tenant_id"], ctx["user_id"], r["mutasi"]
    await conn.execute(SQL_TAUTAN_LEPAS, tid, m["id"], str(uid))        # tautan DILEPAS dulu: penjaga void menghitung tautan aktif (BANK_TX_BERTAUT)
    await KT.kunci_void_manual(conn, r["bt_id"])
    rv = await KT.rencana_void_manual(conn, tid, r["bt_id"], r["alasan"], Blok(False), kunci=True)       # blok pertama dilempar (rollback seluruh transaksi)
    v = await KT.tulis_void_manual(conn, ctx, r["bt_id"], r["alasan"], rv)
    if not await conn.fetchrow(SQL_KEMBALI_CATAT, tid, m["id"], r["kembali_ke"]):
        raise HTTPException(status_code=409, detail={"code": "BELUM_DIPUTUS", "message": "Keputusan baris ini sudah berubah. Muat ulang daftar."})
    nomor = rv["je"]["journal_number"]
    await catat_riwayat(conn, tid, ENTITAS, m["id"], None, EVENT["batalkan"], uid, f"Catatan {nomor} dibatalkan",
                        meta={"rekening_id": str(m["bank_account_id"]), "nominal": str(m["nominal"]), "bank_transaction_id": str(r["bt_id"]), "alasan": r["alasan"],
                              "jurnal_pembalik": v["reversal_number"]})
    return {"jurnal_pembalik": v["reversal_number"], "kalimat": f"Dokumen {nomor} dibatalkan; jurnal pembalik {v['reversal_number']} dibuat; baris kembali ke {BD.STATUS_TEKS[r['kembali_ke']]}."}


RENCANA = {"cocokkan": rencana_cocokkan, "kecualikan": rencana_kecualikan, "lepaskan": rencana_lepaskan, "catat": rencana_catat, "batalkan": rencana_batalkan}
TULIS = {"cocokkan": _tulis_cocokkan, "kecualikan": _tulis_kecualikan, "lepaskan": _tulis_lepaskan, "catat": _tulis_catat, "batalkan": _tulis_batalkan}


def _keluaran(r: dict, aksi: str, body: dict) -> dict:
    blocks = [{"code": b["code"], "message": b["message"]} for b in r["blocks"]]
    data = {"ok": not blocks, "blocks": blocks, "aksi": aksi, "mutasi": _ringkas_mutasi(r["mutasi"]) if r["mutasi"] else None, "jurnal": [], "kalimat": r["kalimat"],
            "payload": {**{k: v for k, v in body.items() if k != "versi"}, "versi": r["versi"]}}
    for k in ("pasangan", "tautan", "kembali_ke", "alasan", "jenis", "akun", "kalimat_tiga"):
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


async def pratinjau(pool, tid: str, rekening_id, mutasi_id, aksi: str, body: dict) -> dict:
    """Pratinjau B8: transaksi READ ONLY; penentu SAMA dengan tulis; jurnal selalu [] (NOL jurnal)."""
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            r = await RENCANA[aksi](conn, tid, rekening_id, mutasi_id, body, Blok(True), False)
            return {"success": True, "data": await _keluaran_async(conn, tid, r, aksi, body)}


async def tulis(conn, ctx: dict, rekening_id, mutasi_id, aksi: str, body: dict) -> dict:
    """Tulis di transaksi PEMANGGIL (idem sudah di rute): MUTASI_BANK:{id} -> penentu yang SAMA (FOR UPDATE, status dibaca ulang) -> versi -> tulis + riwayat."""
    tid = ctx["tenant_id"]
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"MUTASI_BANK:{mutasi_id}")
    r = await RENCANA[aksi](conn, tid, rekening_id, mutasi_id, body, Blok(False), True)
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
