"""Sisa WIP per WO SELESAI untuk dikuras month_end_reconcile ke 5-90200 (BUG-183b, putusan pemilik 12 Nov 2026).

FG diterima pada STANDAR BOM; selisih bahan/konversi tertinggal di WIP per WO. Tutup bulan memindahkan sisa WIP tiap WO SELESAI (penentu tunggal
services.produksi_terap_standar.wo_selesai_per) ke akun varian produksi di jurnal PRODUCTION_RECONCILE yang sama; WO belum selesai dibiarkan (WIP sah).
Idempoten lintas periode: baris kuras ditandai memo `Kuras sisa WIP WO <nomor> [<id WO>]` dan ikut dihitung (jurnal efektif saja; reconcile yang di-void tak terhitung),
jadi WO yang sudah dikuras (atau berWIP 0) TIDAK disentuh lagi."""
import re
from decimal import Decimal

from . import teks_galat as tg
from .produksi_terap_standar import wo_selesai_per

AWAL_MEMO = "Kuras sisa WIP WO "
_ID_DI_MEMO = re.compile(r"\[([0-9a-fA-F-]{36})\]\s*$")
Q = Decimal("0.01")


def memo_kuras(nomor, order_id) -> str:
    return f"{AWAL_MEMO}{nomor} [{order_id}]"


async def sisa_wip_selesai(conn, tenant_id: str, wip_id, akhir) -> list:
    """-> [{order_id, order_number, sisa}] hanya WO SELESAI per `akhir` dgn sisa WIP != 0 (sisa > 0 = debit WIP tertinggal)."""
    wo = await conn.fetch(
        """SELECT po.id, po.order_number, po.status, po.planned_quantity, po.actual_end_date,
                  COALESCE((SELECT SUM(pc.good_quantity) FROM production_completions pc
                            WHERE pc.production_order_id = po.id AND pc.completion_date <= $2), 0) AS baik
           FROM production_orders po WHERE po.tenant_id = $1 AND po.status <> 'cancelled'""",
        tenant_id, akhir)
    selesai = {str(r["id"]): r for r in wo if wo_selesai_per(r["status"], r["baik"], r["planned_quantity"], r["actual_end_date"], akhir)}
    if not selesai:
        return []
    saldo = {k: Decimal(0) for k in selesai}
    for r in await conn.fetch(
        """SELECT je.source_id::text AS wo, COALESCE(SUM(jl.debit - jl.credit), 0) AS s
           FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
           WHERE je.tenant_id = $1 AND jl.account_id = $2 AND je.journal_date <= $3 AND is_effective_journal(je.id)
             AND je.source_id::text = ANY($4::text[]) GROUP BY je.source_id""",
        tenant_id, wip_id, akhir, list(selesai)):
        saldo[r["wo"]] += Decimal(str(r["s"]))
    for r in await conn.fetch(
        """SELECT jl.memo, jl.debit, jl.credit FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
           WHERE je.tenant_id = $1 AND jl.account_id = $2 AND je.source_type = 'PRODUCTION_RECONCILE' AND je.journal_date <= $3
             AND is_effective_journal(je.id) AND jl.memo LIKE 'Kuras sisa WIP WO %'""",
        tenant_id, wip_id, akhir):
        m = _ID_DI_MEMO.search(r["memo"] or "")
        if m and m.group(1) in saldo:
            saldo[m.group(1)] += Decimal(str(r["debit"] or 0)) - Decimal(str(r["credit"] or 0))
    return [{"order_id": k, "order_number": selesai[k]["order_number"], "sisa": s.quantize(Q)} for k, s in saldo.items() if s.quantize(Q) != 0]


PENJELASAN_SELISIH = ("Selisih produksi = beban aktual − biaya yang diterapkan (labor/overhead applied) + sisa WIP WO selesai. "
                      "Positif = merugikan (Debit akun selisih); negatif = menguntungkan (Kredit akun selisih).")


def arah_selisih(nilai, kode_akun: str) -> dict:
    """BUG-190: teks ARAH dari server. nilai > 0 = merugikan (Debit akun selisih); < 0 = menguntungkan (Kredit); 0 = tak ada selisih.
    -> {nilai (bertanda, str), arah, teks}; contoh: -95000 -> 'Menguntungkan Rp 95.000 → 5-90200 Kredit'."""
    n = Decimal(str(nilai)).quantize(Q)
    if n > 0:
        return {"nilai": str(n), "arah": "merugikan", "teks": f"Merugikan {tg.rp(n)} → {kode_akun} Debit"}
    if n < 0:
        return {"nilai": str(n), "arah": "menguntungkan", "teks": f"Menguntungkan {tg.rp(-n)} → {kode_akun} Kredit"}
    return {"nilai": str(n), "arah": "nol", "teks": "Tidak ada selisih"}
