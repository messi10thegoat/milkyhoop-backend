"""Catat biaya sederhana untuk Milkybook v2 (03 §3 "Catat biaya (sederhana)", 02-ALUR §F tugas satu kalimat).

Putusan pemilik LANGSUNG 2 Nov 2026 ("Belajar dari riwayat") + MASTER: GENERIK, nol nama akun di kode (nama yang tampil
hanya dari data tenant). Urutan pilihan (riwayat = HARI_RIWAYAT hari, bukan void):
- Dari: kas/bank yang paling sering membayar biaya SERUPA (catatan memuat kata yang sama) -> paling sering membayar biaya
  apa pun -> rekening tertaut peran CASH_PETTY lalu CASH_GENERAL -> satu-satunya rekening petty_cash/cash -> kosong.
- Akun beban: akun biaya SERUPA -> akun berperan PERAN_BEBAN_LAIN (account_roles, bila terpetakan) -> kosong.
  "Paling sering dipakai" BUKAN tebakan akun beban. Kosong = pengguna memilih di kartu; keduanya bisa diganti.
Simpan = jalur POST /expenses yang SAMA (expenses.buat_biaya_inti, status posted: jurnal + transaksi bank) di dalam
transaksi idempotensi (Law 14, X-Idempotency-Key wajib per kartu). Tidak ada jalur tulis kedua."""
from __future__ import annotations

import re
from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from .cari_barang import rupiah

HARI_RIWAYAT = 90
PERAN_KAS = ("CASH_PETTY", "CASH_GENERAL")  # urutan = prioritas
PERAN_BEBAN_LAIN = "EXPENSE_OTHER"  # belum di katalog/CHECK V149: tanpa pemetaan = kosong (bukan galat)
MAKS_PILIHAN = 6
MAKS_JUMLAH = 10_000_000_000
PREFIX_IDEM = "MB2_EXPENSE_CREATE"

SQL_DARI = """
SELECT ba.id, ba.account_name AS nama, ba.account_type AS jenis, COALESCE(p.n, 0) AS dipakai, COALESCE(p.serupa, 0) AS serupa,
  (SELECT min(array_position($4::text[], ar.role_key)) FROM account_roles ar
    WHERE ar.tenant_id = $1 AND ar.account_id = ba.coa_id AND ar.role_key = ANY($4::text[])) AS peran
FROM bank_accounts ba
LEFT JOIN LATERAL (
  SELECT count(*) AS n, count(*) FILTER (WHERE $3::text[] <> '{}' AND lower(e.notes) LIKE ANY($3::text[])) AS serupa
  FROM expenses e
  WHERE e.tenant_id = $1 AND e.paid_through_id = ba.id AND e.status <> 'void'
    AND e.expense_date >= CURRENT_DATE - $2::int) p ON true
WHERE ba.tenant_id = $1 AND ba.is_active AND ba.coa_id IS NOT NULL
ORDER BY serupa DESC, dipakai DESC, peran NULLS LAST, (ba.account_type IN ('petty_cash', 'cash')) DESC, ba.account_name
"""

SQL_AKUN = """
SELECT coa.id, coa.name AS nama, COALESCE(p.n, 0) AS dipakai, COALESCE(p.serupa, 0) AS serupa,
  EXISTS (SELECT 1 FROM account_roles ar WHERE ar.tenant_id = $1 AND ar.account_id = coa.id AND ar.role_key = $4) AS peran
FROM chart_of_accounts coa
LEFT JOIN LATERAL (
  SELECT count(*) AS n, count(*) FILTER (WHERE $3::text[] <> '{}' AND lower(e.notes) LIKE ANY($3::text[])) AS serupa
  FROM expenses e
  WHERE e.tenant_id = $1 AND e.account_id = coa.id AND e.status <> 'void'
    AND e.expense_date >= CURRENT_DATE - $2::int) p ON true
WHERE coa.tenant_id = $1 AND coa.account_type IN ('EXPENSE', 'OTHER_EXPENSE') AND NOT COALESCE(coa.is_header, false)
ORDER BY serupa DESC, peran DESC, dipakai DESC, coa.name
"""


def _pola(untuk: str) -> list[str]:
    """Kata >= 3 huruf dari 'untuk' -> pola LIKE pada catatan biaya lama (huruf kecil)."""
    return [f"%{w}%" for w in re.findall(r"[a-z0-9]{3,}", (untuk or "").lower())]


def _bersih(teks: str) -> str:
    return re.sub(r"\s+", " ", teks or "").strip()


class CatatBiaya(BaseModel):
    """Isi kartu tinjau yang disetujui pengguna. dari_id/akun_id = yang tampil di kartu (pratinjau atau pilihan)."""
    untuk: str = Field(..., min_length=1, max_length=100)
    jumlah: int = Field(..., gt=0, le=MAKS_JUMLAH)
    dari_id: Optional[UUID] = None  # kosong = kartu belum siap -> 422 berkode, bukan bawaan diam
    akun_id: Optional[UUID] = None
    tanggal: Optional[date] = None

    @field_validator("untuk")
    @classmethod
    def _untuk(cls, v):
        v = _bersih(v)
        if not v:
            raise ValueError("untuk wajib diisi")
        return v


def _ke_pilihan(baris) -> dict:
    return {"id": str(baris["id"]), "nama": baris["nama"]}


def pilih_dari(rows) -> Optional[dict]:
    """Baris sudah terurut SQL_DARI (serupa, dipakai, peran). Tanpa riwayat & tanpa peran -> satu-satunya kas; lainnya None."""
    if rows and (rows[0]["serupa"] > 0 or rows[0]["dipakai"] > 0 or rows[0]["peran"] is not None):
        return _ke_pilihan(rows[0])
    kas = [r for r in rows if r["jenis"] in ("petty_cash", "cash")]
    return _ke_pilihan(kas[0]) if len(kas) == 1 else None


def pilih_akun(rows) -> Optional[dict]:
    """Baris terurut SQL_AKUN: biaya serupa -> akun berperan PERAN_BEBAN_LAIN -> None (tidak menebak akun lain)."""
    if rows and (rows[0]["serupa"] > 0 or rows[0]["peran"]):
        return _ke_pilihan(rows[0])
    return None


async def pratinjau(conn, tenant_id: str, untuk: str, jumlah: Optional[int], tanggal: date) -> dict:
    """Kartu tinjau (BACA saja): Untuk · Dari · Akun · Tanggal · Jumlah + pilihan pengganti. siap = boleh Simpan."""
    untuk = _bersih(untuk)
    pola = _pola(untuk)
    dari_rows = await conn.fetch(SQL_DARI, tenant_id, HARI_RIWAYAT, pola, list(PERAN_KAS))
    akun_rows = await conn.fetch(SQL_AKUN, tenant_id, HARI_RIWAYAT, pola, PERAN_BEBAN_LAIN)
    dari, akun = pilih_dari(dari_rows), pilih_akun(akun_rows)
    kurang = [n for n, v in (("untuk", untuk), ("jumlah", jumlah), ("dari", dari), ("akun", akun)) if not v]
    return {"untuk": untuk, "jumlah": rupiah(jumlah) if jumlah else None, "tanggal": tanggal.isoformat(),
            "dari": dari, "akun": akun, "kurang": kurang, "siap": not kurang,
            "pilihan_dari": [_ke_pilihan(r) for r in dari_rows[:MAKS_PILIHAN]],
            "pilihan_akun": [_ke_pilihan(r) for r in akun_rows[:MAKS_PILIHAN]]}


SQL_AKUN_SAH = ("SELECT id, name FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2 "
                "AND account_type IN ('EXPENSE', 'OTHER_EXPENSE') AND NOT COALESCE(is_header, false)")


def pesan_tercatat(untuk: str, jumlah: int, dari_nama: str) -> str:
    angka = f"{jumlah:,}".replace(",", ".")
    return f"Biaya {untuk} Rp{angka} tercatat dari {dari_nama}."


async def catat(conn, user: dict, body: CatatBiaya, kunci: Optional[str], tanggal_bawaan: date, *, izin_fn,
                response=None) -> dict:
    """Satu transaksi: idem (lock + replay) -> akun beban sah milik tenant -> buat_biaya_inti (posted)."""
    from ...routers.expenses import buat_biaya_inti
    from ...schemas.expenses import CreateExpenseRequest
    from ...services import idem_buat

    kurang = [n for n, v in (("dari", body.dari_id), ("akun", body.akun_id)) if v is None]
    if kurang:  # MASTER 2 Nov: Dari/Akun kosong = pengguna WAJIB memilih di kartu; tak pernah diisi diam-diam
        raise HTTPException(status_code=422, detail={"code": "PILIH_DARI_AKUN_DULU", "kurang": kurang,
                                                     "message": "Pilih kas dan akun beban dulu di kartu"})
    if not kunci:
        raise HTTPException(status_code=422, detail={"code": "IDEMPOTENCY_KEY_WAJIB",
                                                     "message": "Kunci idempotensi per kartu wajib (header X-Idempotency-Key)"})
    # status posted = jurnal langsung: butuh izin Posting pengeluaran juga, bukan hanya Buat (peta rute yang sama)
    izin = await izin_fn(user, ["/api/expenses/_/post"], "POST")
    if not izin.get("/api/expenses/_/post"):
        raise HTTPException(status_code=403, detail={"code": "IZIN_POSTING_BIAYA",
                                                     "message": "Tidak punya izin memposting pengeluaran"})
    ctx = {"tenant_id": user["tenant_id"], "user_id": user["user_id"]}
    async with conn.transaction():
        kp, sd, lama = await idem_buat.mulai(conn, ctx, kunci, PREFIX_IDEM, body, "expenses", "expense", response)
        if lama is not None:
            return lama
        akun = await conn.fetchrow(SQL_AKUN_SAH, body.akun_id, ctx["tenant_id"])
        if not akun:
            raise HTTPException(status_code=400, detail={"code": "AKUN_BEBAN_TIDAK_SAH",
                                                         "message": "Akun beban tidak ditemukan"})
        req = CreateExpenseRequest(expense_date=body.tanggal or tanggal_bawaan, paid_through_id=body.dari_id,
                                   account_id=akun["id"], account_name=akun["name"], amount=body.jumlah,
                                   notes=body.untuk, status="posted")
        hasil = await buat_biaya_inti(conn, ctx, req)
        d = hasil["data"]
        return await idem_buat.simpan(conn, ctx, kp, sd, PREFIX_IDEM, {
            "success": True,
            "message": pesan_tercatat(body.untuk, body.jumlah, d.get("paid_through_name") or ""),
            "data": {"id": str(d["id"]), "expense_number": d.get("expense_number"),
                     "expense_date": d.get("expense_date"), "total_amount": rupiah(d.get("total_amount")),
                     "dari": d.get("paid_through_name"), "akun": d.get("account_name"), "untuk": body.untuk}},
            d["id"])
