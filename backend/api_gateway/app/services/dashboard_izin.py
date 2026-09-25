"""Izin widget dashboard — SATU sumber pemetaan widget -> modul (25 Sep 2026, audit izin usul C).

Latar (diukur, master 8c27c189): /api/dashboard/* dilewati PermissionMiddleware
(SKIP `^/api/dashboard`); pagarnya hanya require_active_membership. FCLMiddleware
yang disebut komentar SKIP ("Dashboard has own FCL rules") TIDAK terpasang di
main.py. Akibat: anggota aktif mana pun melihat saldo kas, piutang, hutang, laba
rugi, beban teratas — tanpa izin modulnya.

Putusan (pemilik via MASTER 25 Sep): widget disaring per izin BACA anggota.
  - /all dan /summary: widget tanpa izin DIHILANGKAN (bukan 0) + daftar `omitted`
    [{widget, modules}] supaya FE jujur menulis "tidak ada akses".
  - rute tunggal: 403 {code: PERMISSION_DENIED} (kode yang dirender FE r105).
  - OWNER lolos (bypass can()). Galat pemeriksa = TIDAK boleh (gagal tertutup).
Keputusan izin lewat PolicyEngine yang SAMA dengan PermissionMiddleware (can()).
"""
import logging
from typing import Dict, Iterable, List, Tuple

from fastapi import HTTPException, Request

from .policy_engine_client import get_policy_engine

logger = logging.getLogger(__name__)

AKSI = "R"

# Widget di /all dan bagian /summary -> modul yang SEMUANYA wajib dibaca.
# `expenses` (beban teratas) = baris jurnal akun beban dari SELURUH buku (termasuk
# tagihan & gaji), bukan tabel expenses -> REPORT, bukan EXPENSE.
WIDGET_MODUL: Dict[str, Tuple[str, ...]] = {
    "summary.laba_rugi": ("report",),
    "summary.kpi": ("report",),
    "summary.piutang": ("sales_invoice",),
    "summary.hutang": ("purchase_invoice",),
    "summary.kas_bank": ("kas_bank",),
    "cashFlow": ("kas_bank",),
    "expenses": ("report",),
    "overdueInvoices": ("sales_invoice",),
    "overdueBills": ("purchase_invoice",),
    "salesToday": ("sales_invoice",),
}

# upcomingDue & daily-transactions: disaring PER BARIS menurut jenis dokumen.
JENIS_MODUL: Dict[str, str] = {
    "invoice": "sales_invoice",
    "bill": "purchase_invoice",
    "sales_invoices": "sales_invoice",
    "bills": "purchase_invoice",
    "expenses": "expense",
    "receive_payments": "receive_payment",
    "bill_payments": "send_payment",
}

# Rute tunggal -> modul yang SEMUANYA wajib dibaca (lain: 403).
RUTE_MODUL: Dict[str, Tuple[str, ...]] = {
    "/piutang": ("sales_invoice",),
    "/hutang": ("purchase_invoice",),
    "/kas-bank": ("kas_bank",),
    "/cash-flow-trends": ("kas_bank",),
    "/top-expenses": ("report",),
    "/overdue-invoices": ("sales_invoice",),
    "/overdue-bills": ("purchase_invoice",),
    "/reconciliation-status": ("purchase_invoice",),
    # saldo kas + piutang + hutang sekaligus -> ketiganya (hak paling sempit)
    "/cash-flow-projection": ("kas_bank", "sales_invoice", "purchase_invoice"),
    "/sales-daily": ("sales_invoice",),
    "/sales-today": ("sales_invoice",),
}


async def boleh_baca(request: Request, modul: str) -> bool:
    """can(R, modul) untuk pemanggil; konteks + jawaban di-cache per permintaan."""
    st = request.state
    cache = getattr(st, "_izin_dashboard", None)
    if cache is None:
        cache = {}
        st._izin_dashboard = cache
    if modul in cache:
        return cache[modul]
    hasil = False
    try:
        user = getattr(st, "user", None) or {}
        eng = get_policy_engine()
        ctx = cache.get("__ctx__")
        if ctx is None:
            ctx = await eng.get_user_context(
                user_id=user["user_id"],
                tenant_id=user["tenant_id"],
                subscription_role=user.get("role", "USER"),
            )
            cache["__ctx__"] = ctx
        hasil = bool(await eng.can(ctx, AKSI, modul))
    except Exception as e:  # noqa: BLE001 — gagal TERTUTUP
        logger.error(f"[DASHBOARD_IZIN] pemeriksaan gagal -> tidak boleh: modul={modul} err={type(e).__name__}: {e}")
        hasil = False
    cache[modul] = hasil
    return hasil


async def boleh_semua(request: Request, modul: Iterable[str]) -> bool:
    for m in modul:
        if not await boleh_baca(request, m):
            return False
    return True


async def widget_boleh(request: Request, widget: str) -> bool:
    # Widget tak terpetakan = TIDAK boleh (gagal tertutup, bukan terbuka diam-diam).
    modul = WIDGET_MODUL.get(widget)
    if not modul:
        return False
    return await boleh_semua(request, modul)


def catat_omitted(omitted: List[dict], widget: str) -> None:
    omitted.append({"widget": widget, "modules": list(WIDGET_MODUL.get(widget, ()))})


async def saring_bagian_summary(request: Request, bagian: dict) -> List[dict]:
    """Bagian summary tanpa izin -> None (di tempat) + dicatat. Mengembalikan `omitted`."""
    omitted: List[dict] = []
    for nama in list(bagian):
        widget = "summary." + nama
        if not await widget_boleh(request, widget):
            bagian[nama] = None
            catat_omitted(omitted, widget)
    return omitted


def wajib_baca_rute(jalur: str):
    """Dependensi FastAPI untuk rute tunggal dashboard: tanpa izin -> 403 PERMISSION_DENIED."""
    modul = RUTE_MODUL[jalur]

    async def _dep(request: Request):
        for m in modul:
            if not await boleh_baca(request, m):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "PERMISSION_DENIED",
                        "message": f"You don't have permission to view {m.replace('_', ' ')}",
                        "required_module": m,
                        "required_action": AKSI,
                    },
                )

    return _dep
