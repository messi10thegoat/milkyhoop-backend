"""SATU SUMBER PENGINGAT dashboard (T1-BE, 22 Okt 2026; KONTRAK-dashboard-pengingat-20261022.md).

Pola NetSuite *Reminders*: pengingat = saved search yang SAMA + satu kriteria. Tiap pengingat dihitung dengan PREDIKAT YANG SAMA
dengan `?aksi=` + `/summary` daftar seragam modulnya (faktur_aksi.sql_aksi, proformas.aksi_semua_proforma, so_aksi.fakta,
tagihan_daftar.sql_aksi, po_daftar.sql_aksi) -- dashboard TIDAK menghitung ulang aturan. Paritas diuji (unit + harness nyata).

"Segera jatuh tempo" = SATU definisi (putusan pemilik LANGSUNG 22 Okt): TERBUKA ∧ hari_ini <= jatuh tempo efektif <= hari_ini + N,
N = accounting_settings.pengingat_segera_hari (V450; bawaan 7, pola Xero/QBO). `ambang_segera` = satu-satunya pembaca N.
"""
from datetime import date
from decimal import Decimal
from typing import Optional

NOL = Decimal("0")
AMBANG_BAWAAN = 7
PENGINGAT_VERSI = 1
ARAH = ("masuk", "keluar", "non_uang")
MODUL_DAFTAR = ("sales_invoices", "proformas", "sales_orders", "bills", "purchase_orders")  # enum `list.modul` (FE)

# (modul, kunci_aksi, label, arah, lane, izin-modul) -- urutan = urutan tampil. Kunci WAJIB anggota AKSI daftar modulnya.
PENGINGAT = (
    ("sales_invoices", "jatuh_tempo", "Faktur lewat jatuh tempo", "masuk", "now", "sales_invoice"),
    ("sales_invoices", "segera_tempo", "Faktur segera jatuh tempo", "masuk", "week", "sales_invoice"),
    ("proformas", "lewat_tempo", "Proforma lewat jatuh tempo", "masuk", "now", "proforma"),
    ("proformas", "segera_tempo", "Proforma segera jatuh tempo", "masuk", "week", "proforma"),
    # 23 Okt (MENUNGGU-PEMBAYARAN): anggota = menunggu_pembayaran (proforma terbit belum lunas apa pun jenisnya); KUNCI tetap alias usang
    # `menunggu_dp` selama transisi FE (list.aksi -> ?tugas= DaftarWorkspace hanya kenal kunci lama; WORKSPACE 23 Okt). Ganti ke
    # menunggu_pembayaran BERSAMA nilai aksi baris saat FE pindah terverifikasi (so_aksi.AKSI_BARIS_USANG), lalu alias dicabut.
    ("sales_orders", "menunggu_dp", "Menunggu bayar", "masuk", "any", "sales_order"),
    ("sales_orders", "perlu_minta_dp", "DP perlu diminta", "masuk", "any", "sales_order"),
    ("sales_orders", "telat_kirim", "Pesanan telat dikirim", "non_uang", "now", "sales_order"),
    ("sales_orders", "perlu_dikirim", "Pesanan perlu dikirim", "non_uang", "week", "sales_order"),
    ("bills", "lewat_jatuh_tempo", "Tagihan lewat jatuh tempo", "keluar", "now", "purchase_invoice"),
    ("bills", "segera_tempo", "Tagihan segera jatuh tempo", "keluar", "week", "purchase_invoice"),
    ("purchase_orders", "telat_terima", "Barang pesanan pembelian telat diterima", "non_uang", "now", "purchase_order"),
)


async def ambang_segera(conn, tenant_id: str) -> int:
    """N hari "segera jatuh tempo" tenant (accounting_settings.pengingat_segera_hari, V450). Tanpa baris pengaturan -> bawaan 7."""
    n = await conn.fetchval("SELECT pengingat_segera_hari FROM accounting_settings WHERE tenant_id = $1", tenant_id)
    return int(n) if n else AMBANG_BAWAAN


def uang(v) -> str:
    return f"{Decimal(str(v or 0)).quantize(Decimal('0.01')):.2f}"


async def _faktur(conn, tid: str, hari_ini: date, ambang: int) -> dict:
    from . import faktur_aksi as FA
    return await FA.ringkas_aksi(conn, tid, hari_ini, ambang)


async def _proforma(conn, tid: str, hari_ini: date, ambang: int) -> dict:
    from ..routers.proformas import aksi_semua_proforma
    peta = await aksi_semua_proforma(conn, tid, hari_ini, ambang=ambang)
    out = {}
    for a, v in peta.values():
        n, s = out.get(a, (0, NOL))
        out[a] = (n + 1, s + Decimal(str(v)))
    return {k: {"count": n, "amount": s} for k, (n, s) in out.items()}


async def _so(conn, tid: str, hari_ini: date, ambang: int) -> dict:
    from . import so_aksi
    r = so_aksi.ringkasan(await so_aksi.fakta(conn, tid, hari_ini))
    # + alias usang (menunggu_dp == menunggu_pembayaran) -- kunci registri selama transisi FE
    return {k: {"count": r[f"{k}_count"], "amount": Decimal(str(r[f"{k}_value"]))} for k in so_aksi.AKSI + tuple(so_aksi.AKSI_USANG)}


async def _tagihan(conn, tid: str, hari_ini: date, ambang: int) -> dict:
    from . import tagihan_daftar as TD
    out = {}
    for k in ("lewat_jatuh_tempo", "segera_tempo"):
        pred, par = TD.sql_aksi_berparam(k, 2, hari_ini, ambang)  # segera_tempo = N hari tenant (sama dengan ?aksi= daftar tagihan)
        r = await conn.fetchrow(
            f"""SELECT COUNT(*) AS n, COALESCE(SUM({TD.sql_hutang('b')}), 0) AS v
                FROM bills b {TD.join_rincian()} WHERE b.tenant_id = $1 AND {pred}""", tid, *par)
        out[k] = {"count": int(r["n"]), "amount": Decimal(str(r["v"]))}
    return out


async def _po(conn, tid: str, hari_ini: date, ambang: int) -> dict:
    from . import po_daftar as PD
    r = await conn.fetchrow(
        f"""SELECT {", ".join(f"COUNT(*) FILTER (WHERE {PD.sql_aksi(k, '$2')}) AS {k}" for k in PD.AKSI)}
            FROM purchase_orders po {PD.SQL_AGREGAT} WHERE po.tenant_id = $1""", tid, hari_ini)
    return {k: {"count": int(r[k]), "amount": None} for k in PD.AKSI}


PENGHITUNG = {"sales_invoices": _faktur, "proformas": _proforma, "sales_orders": _so, "bills": _tagihan, "purchase_orders": _po}


async def hitung(conn, tenant_id: str, hari_ini: date) -> list:
    """Semua pengingat tenant (belum disaring izin) -- yang count > 0 saja. Ini yang di-cache bersama tasks."""
    ambang = await ambang_segera(conn, tenant_id)
    per_modul = {}
    for modul in dict.fromkeys(m for m, *_ in PENGINGAT):
        per_modul[modul] = await PENGHITUNG[modul](conn, tenant_id, hari_ini, ambang)
    out = []
    for modul, kunci, label, arah, lane, izin in PENGINGAT:
        h = per_modul[modul].get(kunci) or {"count": 0, "amount": NOL}
        if not h["count"]:
            continue
        amount = None if arah == "non_uang" else uang(h["amount"])
        out.append({"key": f"{modul}:{kunci}:{h['count']}:{amount or '-'}", "kunci_aksi": kunci, "modul": modul, "label": label,
                    "count": int(h["count"]), "amount": amount, "arah": arah, "lane": lane, "izin": izin,
                    "list": {"modul": modul, "aksi": kunci}})
    return out


# Pola QBO/Xero: piutang = yang SUDAH ditagih; DP yang belum diminta = tugas, bukan uang (MASTER 23 Okt, temuan kaos 91% sum_in).
KUNCI_BELUM_DITAGIH = (("sales_orders", "perlu_minta_dp"),)


def rangkum(pengingat: list) -> dict:
    """{reminders, sum_in, sum_in_ditagih, sum_in_dp_belum_ditagih, sum_out, pengingat_versi} -- sesudah penyaringan izin.
    Invarian: sum_in == sum_in_ditagih + sum_in_dp_belum_ditagih (aditif; bentuk lama tak berubah -> versi tetap)."""
    masuk = [p for p in pengingat if p["arah"] == "masuk"]
    belum = sum((Decimal(p["amount"]) for p in masuk if (p.get("modul"), p.get("kunci_aksi")) in KUNCI_BELUM_DITAGIH), NOL)
    ditagih = sum((Decimal(p["amount"]) for p in masuk if (p.get("modul"), p.get("kunci_aksi")) not in KUNCI_BELUM_DITAGIH), NOL)
    s_out = sum((Decimal(p["amount"]) for p in pengingat if p["arah"] == "keluar"), NOL)
    return {"reminders": [{k: v for k, v in p.items() if k != "izin"} for p in pengingat],
            "sum_in": uang(ditagih + belum), "sum_in_ditagih": uang(ditagih), "sum_in_dp_belum_ditagih": uang(belum),
            "sum_out": uang(s_out), "pengingat_versi": PENGINGAT_VERSI}
