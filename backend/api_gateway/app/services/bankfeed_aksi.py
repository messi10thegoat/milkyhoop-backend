"""Aksi Bank Feed BF-1c1 (KONTRAK-bf1c1): COCOKKAN · KECUALIKAN · LEPASKAN + RIWAYAT. NOL jurnal baru: aksi ini hanya MENAUTKAN/MENANDAI baris rekening koran.
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
from . import teks_galat as tg
from .bayar_keluar_rencana import Blok
from .nama_pengguna import nama_untuk
from .so_riwayat import catat_riwayat
from ..utils.tanggal_tenant import zona_tenant

SEN = Decimal("0.01")
DITINJAU = ("untuk_ditinjau", "mungkin_duplikat")
MAKS_BT = 4                    # satu baris bank <= 4 dokumen (gabungan)
MAKS_BARIS_PER_BT = 3          # satu dokumen <= 3 baris bank
ALASAN_KECUALI = {"duplikat": "Duplikat", "bukan_transaksi": "Bukan transaksi", "lain": "Lain-lain"}
EVENT = {"cocokkan": "BANK_FEED_COCOKKAN", "kecualikan": "BANK_FEED_KECUALIKAN", "lepaskan": "BANK_FEED_LEPASKAN"}
AKSI_TEKS = {"BANK_FEED_COCOKKAN": ("cocokkan", "Dicocokkan"), "BANK_FEED_KECUALIKAN": ("kecualikan", "Dikecualikan"), "BANK_FEED_LEPASKAN": ("lepaskan", "Dilepas")}
ENTITAS = "mutasi_bank"

SQL_MUTASI = ("SELECT id, bank_account_id, tanggal, keterangan, arah, nominal, status, cara, alasan, diputus_oleh, diputus_pada, duplikat_dari, rekonsiliasi_id "
              "FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2 AND id = $3 AND status <> 'dipisah'")
SQL_TAUTAN_MUTASI = ("SELECT t.id, t.bank_transaction_id, t.nominal_alokasi, bt.transaction_number AS nomor, bt.transaction_type AS jenis, bt.is_reconciled AS rekon, "
                     "NULLIF(btrim(bt.payee_payer), '') AS kontak_nama FROM mutasi_bank_tautan t JOIN bank_transactions bt ON bt.id = t.bank_transaction_id AND bt.tenant_id = t.tenant_id "
                     "WHERE t.tenant_id = $1 AND t.mutasi_bank_id = $2 AND t.dilepas_pada IS NULL ORDER BY t.dibuat_pada, t.id")
SQL_TAUTAN_INSERT = ("INSERT INTO mutasi_bank_tautan (tenant_id, mutasi_bank_id, bank_transaction_id, nominal_alokasi, dibuat_oleh) VALUES ($1, $2, $3, $4, $5::uuid)")
SQL_TAUTAN_LEPAS = ("UPDATE mutasi_bank_tautan SET dilepas_oleh = $3::uuid, dilepas_pada = now() WHERE tenant_id = $1 AND mutasi_bank_id = $2 AND dilepas_pada IS NULL")
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


RENCANA = {"cocokkan": rencana_cocokkan, "kecualikan": rencana_kecualikan, "lepaskan": rencana_lepaskan}
TULIS = {"cocokkan": _tulis_cocokkan, "kecualikan": _tulis_kecualikan, "lepaskan": _tulis_lepaskan}


def _keluaran(r: dict, aksi: str, body: dict) -> dict:
    blocks = [{"code": b["code"], "message": b["message"]} for b in r["blocks"]]
    data = {"ok": not blocks, "blocks": blocks, "aksi": aksi, "mutasi": _ringkas_mutasi(r["mutasi"]) if r["mutasi"] else None, "jurnal": [], "kalimat": r["kalimat"],
            "payload": {**{k: v for k, v in body.items() if k != "versi"}, "versi": r["versi"]}}
    for k in ("pasangan", "tautan", "kembali_ke", "alasan"):
        if k in r:
            data[k] = r[k]
    return data


async def pratinjau(pool, tid: str, rekening_id, mutasi_id, aksi: str, body: dict) -> dict:
    """Pratinjau B8: transaksi READ ONLY; penentu SAMA dengan tulis; jurnal selalu [] (NOL jurnal)."""
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            r = await RENCANA[aksi](conn, tid, rekening_id, mutasi_id, body, Blok(True), False)
            return {"success": True, "data": _keluaran(r, aksi, body)}


async def tulis(conn, ctx: dict, rekening_id, mutasi_id, aksi: str, body: dict) -> dict:
    """Tulis di transaksi PEMANGGIL (idem sudah di rute): MUTASI_BANK:{id} -> penentu yang SAMA (FOR UPDATE, status dibaca ulang) -> versi -> tulis + riwayat."""
    tid = ctx["tenant_id"]
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"MUTASI_BANK:{mutasi_id}")
    r = await RENCANA[aksi](conn, tid, rekening_id, mutasi_id, body, Blok(False), True)
    if body.get("versi") and body["versi"] != r["versi"]:
        raise HTTPException(status_code=409, detail={"code": "RENCANA_BERUBAH", "message": "Keadaan baris atau dokumen berubah sejak pratinjau. Tinjau ulang sebelum menyimpan."})
    ekstra = await TULIS[aksi](conn, ctx, r)
    m = await conn.fetchrow(SQL_MUTASI, tid, rekening_id, mutasi_id)
    return {"id": str(m["id"]), "status": m["status"], "status_text": BD.STATUS_TEKS.get(m["status"], m["status"]), "diputus_text": await _teks_diputus(conn, tid, m),
            "kalimat": r["kalimat"], **ekstra}


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
