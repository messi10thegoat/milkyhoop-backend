"""PENGINGAT ANOMALI -- GET /api/anomalies (read-only, per tenant).

Lahir dari kasus Rahayu Umar (grapgrap, 23 Sep 2026): draf faktur dari SO dihapus, SO tetap
"63/63 difakturkan", ditandai selesai, dan uang mukanya menganggur -- ketahuan seminggu
kemudian secara kebetulan. Endpoint ini memunculkan kelas kasus itu hari yang sama.

Pemeriksaan (nama kunci = kontrak FE panel "Perlu Perhatian"):
  so_invoiced_mismatch   baris SO: quantity_invoiced != SIGMA qty baris faktur non-void yang
                         TERTAUT (invariant 8a)
  so_closed_uninvoiced   SO selesai/ditutup tanpa satu pun faktur non-void
  deposit_idle           uang muka pelanggan (bukan draf/void) dengan sisa > 0 dan menganggur
                         > DEPOSIT_IDLE_DAYS. Sisa = compute_deposit_remaining() -- fungsi
                         yang SAMA dengan apply/refund modul uang muka (Law 1/16: satu turunan)
  stale_draft            draf faktur / SO lebih tua dari STALE_DRAFT_DAYS

"DIPERIKSA, NIHIL" != "PEMERIKSAAN GAGAL": tiap pemeriksaan melapor status "ok" atau "error"
sendiri; satu pemeriksaan yang gagal TIDAK pernah tampil sebagai daftar kosong. Pemeriksaan
yang modulnya tak boleh dibaca pengguna dilaporkan "skipped", bukan dihilangkan diam-diam.
Nol tulis. Semua kueri disaring tenant_id pemanggil.

UMUR dihitung dari "hari ini" MENURUT ZONA TENANT (utils/tanggal_tenant), bukan CURRENT_DATE
basis data (UTC): terukur 23 Sep, pukul pagi WITA basis data masih 22 Sep, sehingga uang
muka 8 hari terbaca 7 hari dan lolos dari ambang "> 7 hari".
"""
import logging
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request

from ..services.db_pool import get_db_pool
from ..services.policy_engine_client import get_policy_engine
from ..utils.tanggal_tenant import tanggal_dokumen, zona_tenant
from .customer_deposits import compute_deposit_remaining

router = APIRouter()
logger = logging.getLogger(__name__)

# Ambang -- konstanta bernama, bukan angka ajaib (tiket G.17).
DEPOSIT_IDLE_DAYS = 7
STALE_DRAFT_DAYS = 14

# kunci pemeriksaan -> modul (nama middleware) yang wajib boleh DIBACA pemanggil
CHECK_MODULE = {
    "so_invoiced_mismatch": "sales_order",
    "so_closed_uninvoiced": "sales_order",
    "deposit_idle": "customer_deposit",
    "stale_draft": "sales_invoice",
}
CHECK_LABEL = {
    "so_invoiced_mismatch": "Jumlah terfakturkan SO tidak cocok dengan fakturnya",
    "so_closed_uninvoiced": "SO selesai tanpa faktur",
    "deposit_idle": f"Uang muka menganggur lebih dari {DEPOSIT_IDLE_DAYS} hari",
    "stale_draft": f"Draf lebih tua dari {STALE_DRAFT_DAYS} hari",
}


def _rp(v) -> str:
    return "Rp" + f"{Decimal(v):,.0f}".replace(",", ".")


def _n(v) -> str:
    d = Decimal(str(v))
    return f"{d.quantize(Decimal('1')) if d == d.to_integral_value() else d.normalize()}".replace(".", ",")


def _qty(v, unit) -> str:
    """BUG-002: jumlah bersatuan ("63 pcs"), bukan angka telanjang. Satuan = kolom baris SO;
    kosong -> angka saja (tak menebak satuan)."""
    return f"{_n(v)} {unit}" if unit else _n(v)


async def _check_so_invoiced_mismatch(conn, tid, today, tz):
    rows = await conn.fetch(
        """
        WITH linked AS (
            SELECT sii.sales_order_item_id AS soi, SUM(sii.quantity) AS q
            FROM sales_invoice_items sii
            JOIN sales_invoices si ON si.id = sii.invoice_id
            WHERE si.tenant_id = $1 AND si.status <> 'void'
              AND sii.sales_order_item_id IS NOT NULL
            GROUP BY 1
        )
        SELECT so.id, so.order_number, so.customer_name, so.status,
               ($2::date - so.order_date) AS age_days,
               soi.description, soi.quantity, soi.quantity_invoiced, soi.unit,
               COALESCE(l.q, 0) AS linked_q, soi.unit_price,
               COALESCE(soi.discount_percent, 0) AS discount_percent
        FROM sales_order_items soi
        JOIN sales_orders so ON so.id = soi.sales_order_id
        LEFT JOIN linked l ON l.soi = soi.id
        WHERE so.tenant_id = $1
          AND soi.quantity_invoiced <> COALESCE(l.q, 0)
        ORDER BY so.order_date, so.order_number, soi.sort_order
        """,
        tid,
        today,
    )
    by_so = {}
    for r in rows:
        f = by_so.setdefault(r["id"], {"r": r, "lines": [], "amount": Decimal("0")})
        gap = Decimal(r["quantity_invoiced"]) - Decimal(r["linked_q"])
        f["lines"].append({
            "description": r["description"],
            "quantity_ordered": float(r["quantity"]),
            "quantity_invoiced_recorded": float(r["quantity_invoiced"]),
            "quantity_on_invoices": float(r["linked_q"]),
            "unit": (r["unit"] or "").strip() or None,
        })
        f["amount"] += abs(gap) * Decimal(r["unit_price"]) * (1 - Decimal(r["discount_percent"]) / 100)
    out = []
    for so_id, f in by_so.items():
        r = f["r"]
        ln = f["lines"][0]
        over = ln["quantity_invoiced_recorded"] > ln["quantity_on_invoices"]
        out.append({
            "check": "so_invoiced_mismatch",
            "severity": "high",
            "document_type": "sales_order",
            "document_id": str(so_id),
            "document_number": r["order_number"],
            "customer_name": r["customer_name"],
            "amount": float(round(f["amount"], 2)),
            "age_days": int(r["age_days"] or 0),
            "message": (
                f"SO {r['order_number']}: {_qty(ln['quantity_invoiced_recorded'], ln['unit'])} tercatat "
                f"sudah difakturkan, tetapi faktur yang ada hanya {_qty(ln['quantity_on_invoices'], ln['unit'])}."
                + (f" (+{len(f['lines']) - 1} baris lain)" if len(f["lines"]) > 1 else "")
            ),
            "suggested_action": (
                "Kemungkinan faktur dari SO ini dihapus/dibatalkan tanpa melepas SO. Periksa, "
                "lalu buat ulang fakturnya dari SO." if over else
                "Faktur melebihi catatan SO. Periksa faktur yang terhubung ke SO ini."
            ),
            "details": {"lines": f["lines"], "so_status": r["status"]},
        })
    return out


async def _check_so_closed_uninvoiced(conn, tid, today, tz):
    rows = await conn.fetch(
        """
        SELECT so.id, so.order_number, so.customer_name, so.status, so.total_amount,
               ($2::date - so.order_date) AS age_days
        FROM sales_orders so
        WHERE so.tenant_id = $1 AND so.status IN ('completed', 'closed')
          AND NOT EXISTS (SELECT 1 FROM sales_invoices si
                          WHERE si.tenant_id = $1 AND si.sales_order_id = so.id
                            AND si.status <> 'void')
        ORDER BY so.order_date, so.order_number
        """,
        tid,
        today,
    )
    return [{
        "check": "so_closed_uninvoiced",
        "severity": "high",
        "document_type": "sales_order",
        "document_id": str(r["id"]),
        "document_number": r["order_number"],
        "customer_name": r["customer_name"],
        "amount": float(r["total_amount"] or 0),
        "age_days": int(r["age_days"] or 0),
        "message": (
            f"SO {r['order_number']} berstatus selesai, tetapi tidak ada satu pun faktur "
            f"untuknya -- penjualan {_rp(r['total_amount'] or 0)} belum tercatat sebagai pendapatan."
        ),
        "suggested_action": "Buat faktur dari SO ini, atau batalkan SO bila memang tidak jadi.",
        "details": {"so_status": r["status"]},
    } for r in rows]


async def _check_deposit_idle(conn, tid, today, tz):
    deps = await conn.fetch(
        """
        SELECT d.id, d.deposit_number, d.customer_name, d.amount, d.status,
               ($2::date - GREATEST(
                    d.deposit_date,
                    COALESCE((SELECT MAX(a.application_date) FROM customer_deposit_applications a
                              WHERE a.deposit_id = d.id), d.deposit_date),
                    COALESCE((SELECT MAX(f.refund_date) FROM customer_deposit_refunds f
                              WHERE f.deposit_id = d.id), d.deposit_date)
               )) AS idle_days
        FROM customer_deposits d
        WHERE d.tenant_id = $1 AND d.status NOT IN ('draft', 'void')
        ORDER BY d.deposit_date, d.deposit_number
        """,
        tid,
        today,
    )
    out = []
    for d in deps:
        if int(d["idle_days"] or 0) <= DEPOSIT_IDLE_DAYS:
            continue
        remaining = await compute_deposit_remaining(conn, tid, d["id"])
        if remaining <= 0:
            continue
        out.append({
            "check": "deposit_idle",
            "severity": "medium",
            "document_type": "customer_deposit",
            "document_id": str(d["id"]),
            "document_number": d["deposit_number"],
            "customer_name": d["customer_name"],
            "amount": float(remaining),
            "age_days": int(d["idle_days"]),
            "message": (
                f"Uang muka {d['deposit_number']} masih tersisa {_rp(remaining)} dan belum "
                f"dipakai selama {int(d['idle_days'])} hari."
            ),
            "suggested_action": "Terapkan ke faktur pelanggan ini, atau kembalikan bila pesanan batal.",
            "details": {"deposit_amount": float(d["amount"]), "status": d["status"]},
        })
    return out


async def _check_stale_draft(conn, tid, today, tz):
    rows = await conn.fetch(
        """
        SELECT 'sales_invoice' AS dt, id, invoice_number AS num, customer_name, total_amount,
               ($3::date - (created_at AT TIME ZONE $4)::date) AS age_days
        FROM sales_invoices WHERE tenant_id = $1 AND status = 'draft'
          AND ($3::date - (created_at AT TIME ZONE $4)::date) > $2::int
        UNION ALL
        SELECT 'sales_order', id, order_number, customer_name, total_amount,
               ($3::date - (created_at AT TIME ZONE $4)::date)
        FROM sales_orders WHERE tenant_id = $1 AND status = 'draft'
          AND ($3::date - (created_at AT TIME ZONE $4)::date) > $2::int
        ORDER BY 6 DESC
        """,
        tid,
        STALE_DRAFT_DAYS,
        today,
        tz,
    )
    label = {"sales_invoice": "Draf faktur", "sales_order": "Draf SO"}
    return [{
        "check": "stale_draft",
        "severity": "low",
        "document_type": r["dt"],
        "document_id": str(r["id"]),
        "document_number": r["num"],
        "customer_name": r["customer_name"],
        "amount": float(r["total_amount"] or 0),
        "age_days": int(r["age_days"] or 0),
        "message": f"{label[r['dt']]} {r['num']} belum diterbitkan selama {int(r['age_days'])} hari.",
        "suggested_action": "Terbitkan bila sudah final, atau hapus bila tidak dipakai.",
        "details": {},
    } for r in rows]


CHECKS = {
    "so_invoiced_mismatch": _check_so_invoiced_mismatch,
    "so_closed_uninvoiced": _check_so_closed_uninvoiced,
    "deposit_idle": _check_deposit_idle,
    "stale_draft": _check_stale_draft,
}


_SEV = {"high": 3, "medium": 2, "low": 1}


def _gabung_per_dokumen(findings: list) -> list:
    """BUG-002: SATU kartu per dokumen. SO 001-09-26 (grapgrap) muncul dua kali -- "63 tercatat
    difakturkan, faktur 0" DAN "selesai tanpa faktur" -- padahal akarnya satu. Temuan untuk
    (document_type, document_id) yang sama digabung: `check`/`severity` = yang terberat,
    `checks` = semua kunci, `reasons` = tiap temuan asli (check, message, suggested_action,
    details), `message` = satu kalimat "<SO x>: isi-1; isi-2." Urutan kartu = kemunculan pertama.
    `checks[].count` di tingkat pemeriksaan TIDAK berubah (tetap per pemeriksaan)."""
    grup = {}
    for f in findings:
        grup.setdefault((f["document_type"], f["document_id"]), []).append(f)
    out = []
    for fs in grup.values():
        for f in fs:
            f["checks"] = [f["check"]]
            f["reasons"] = [{k: f[k] for k in ("check", "message", "suggested_action", "details")}]
        if len(fs) == 1:
            out.append(fs[0])
            continue
        utama = max(fs, key=lambda f: _SEV.get(f["severity"], 0))  # seri -> yang pertama
        num = utama["document_number"] or ""
        i = utama["message"].find(num) if num else -1
        awalan = utama["message"][: i + len(num)] if i >= 0 else ""
        isi = []
        for f in fs:
            m = f["message"]
            if awalan and m.startswith(awalan):
                m = m[len(awalan):].lstrip(": ")
            isi.append(m.rstrip(". "))
        saran = []
        for f in fs:
            if f["suggested_action"] and f["suggested_action"] not in saran:
                saran.append(f["suggested_action"])
        out.append({
            **utama,
            "amount": max(f["amount"] for f in fs),
            "age_days": max(f["age_days"] for f in fs),
            "message": (f"{awalan}: " if awalan else "") + "; ".join(isi) + ".",
            "suggested_action": " ".join(saran),
            "checks": [f["check"] for f in fs],
            "reasons": [r for f in fs for r in f["reasons"]],
        })
    return out


async def _readable_modules(user: dict) -> set:
    """Modul yang boleh DIBACA pemanggil -- engine can() yang SAMA dengan middleware.
    Gagal menilai izin = modul itu TIDAK dianggap boleh (fail-closed)."""
    pe = get_policy_engine()
    ctx = await pe.get_user_context(user.get("user_id"), user.get("tenant_id"), user.get("role", "USER"))
    ok = set()
    for m in set(CHECK_MODULE.values()):
        try:
            if await pe.can(ctx, "R", m):
                ok.add(m)
        except Exception as e:  # noqa: BLE001
            logger.error("anomalies can(%s) error: %s", m, e)
    return ok


@router.get("")
async def list_anomalies(request: Request):
    """Temuan anomali tenant pemanggil. Nol tulis."""
    user = getattr(request.state, "user", None)
    if not user or not user.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Authentication required")
    tid = user["tenant_id"]
    readable = await _readable_modules(user)
    checks, findings = [], []
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        today = await tanggal_dokumen(conn, tid)
        tz = (await zona_tenant(conn, tid)).key
        for key, fn in CHECKS.items():
            entry = {"key": key, "label": CHECK_LABEL[key], "status": "ok", "count": 0, "error": None}
            if CHECK_MODULE[key] not in readable:
                entry["status"] = "skipped"
                entry["error"] = "Tidak ada izin membaca modul ini."
                checks.append(entry)
                continue
            try:
                got = await fn(conn, tid, today, tz)
                entry["count"] = len(got)
                findings.extend(got)
            except Exception as e:  # noqa: BLE001 -- dilaporkan, TIDAK ditelan jadi daftar kosong
                logger.error("anomaly check %s failed for %s: %s", key, tid, e, exc_info=True)
                entry["status"] = "error"
                entry["error"] = "Pemeriksaan gagal dijalankan."
            checks.append(entry)
    errored = [c for c in checks if c["status"] == "error"]
    ran = [c for c in checks if c["status"] == "ok"]
    status = "error" if errored and not ran else ("partial" if errored else "ok")
    return {
        "success": True,
        "data": {
            "status": status,  # ok | partial | error -- "ok" + findings [] = diperiksa, nihil
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "as_of_date": today.isoformat(),  # "hari ini" menurut zona tenant
            "thresholds": {"deposit_idle_days": DEPOSIT_IDLE_DAYS, "stale_draft_days": STALE_DRAFT_DAYS},
            "checks": checks,
            "findings": _gabung_per_dokumen(findings),
        },
    }
