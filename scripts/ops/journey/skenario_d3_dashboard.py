"""D3 dashboard v2 (28 Sep 2026) lewat app PENUH di salinan prod: rute terpasang di belakang pagar nyata,
respons valid terhadap kontrak (4 periode + tugas), angka = fungsi laporan pada DB salinan yang sama,
dismiss per PENGGUNA (hilang untuk OWNER, tetap ada untuk KOLAB) lalu urungkan -> kembali, kunci liar 422."""
import sys
from urllib.parse import quote

from journey_lib import KOLAB, OWNER, T

sys.path.insert(0, "/wt/backend/api_gateway")
from tests.unit.kontrak_json import _cocok, _skema  # noqa: E402


async def jalankan(J):
    sk_s, sk_t = _skema("dashboard-summary.schema.json"), _skema("dashboard-tasks.schema.json")
    for per in ("day", "week", "month", "year"):
        _, s = await J.langkah(f"summary_{per}", "GET", f"/api/dashboard/v2/summary?period={per}", user=OWNER)
        g = _cocok(s or {}, sk_s, sk_s)
        if g:
            J.gagal(f"kontrak_summary_{per}", str(g[:3]))
        if per == "month" and s:
            async with J.pool.acquire() as c:
                kas = await c.fetchval(
                    """SELECT COALESCE(SUM(jl.debit - jl.credit), 0) FROM journal_lines jl
                       JOIN journal_entries je ON je.id = jl.journal_id JOIN chart_of_accounts coa ON coa.id = jl.account_id
                       WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND coa.is_cash = TRUE
                         AND je.journal_date <= tanggal_bisnis($1)""", T)
                ar = await c.fetchval("SELECT COALESCE(SUM(outstanding), 0) FROM compute_ar_outstanding($1)", T)
            print(f"summary month: kas {s['cash']['total']} (DB {kas}) piutang {s['receivables']['total']} (DB {ar})"
                  f" laba {s['pnl']['profit']} omitted={s.get('omitted')}", flush=True)
            if f"{kas:.2f}" != s["cash"]["total"] or f"{ar:.2f}" != s["receivables"]["total"]:
                J.gagal("angka_sama_dengan_ledger", f"kas {s['cash']['total']} vs {kas}; AR {s['receivables']['total']} vs {ar}")
    await J.langkah("period_liar_422", "GET", "/api/dashboard/v2/summary?period=7d", user=OWNER, harap=(422,))

    _, t1 = await J.langkah("tasks_owner", "GET", "/api/dashboard/tasks", user=OWNER)
    g = _cocok(t1 or {}, sk_t, sk_t)
    if g:
        J.gagal("kontrak_tasks", str(g[:3]))
    tugas = (t1 or {}).get("tasks") or []
    print(f"tasks owner: {len(tugas)} {sorted({x['type'] for x in tugas})} sum_now={(t1 or {}).get('sum_now')}", flush=True)
    if not tugas:
        J.gagal("ada_tugas", "tenant uji tanpa tugas — dismiss tak teruji")
        return
    k = tugas[0]["key"]
    jalur = f"/api/dashboard/tasks/{quote(k, safe='')}/dismiss"
    await J.langkah("dismiss_owner", "POST", jalur, user=OWNER)
    await J.langkah("dismiss_ulang_idempoten", "POST", jalur, user=OWNER)
    _, t2 = await J.langkah("tasks_owner_sesudah", "GET", "/api/dashboard/tasks", user=OWNER)
    if k in [x["key"] for x in (t2 or {}).get("tasks", [])]:
        J.gagal("hilang_untuk_owner", k)
    if (t2 or {}).get("done_today", 0) < 1:
        J.gagal("done_today_naik", str((t2 or {}).get("done_today")))
    _, t3 = await J.langkah("tasks_kolab", "GET", "/api/dashboard/tasks", user=KOLAB)
    kolab_keys = [x["key"] for x in (t3 or {}).get("tasks", [])]
    tipe_k = tugas[0]["type"]
    print(f"kolab melihat {len(kolab_keys)} tugas; kunci di-dismiss owner ada untuk kolab: {k in kolab_keys} (jenis {tipe_k})", flush=True)
    if (t3 or {}).get("tasks") and any(x["type"] == tipe_k for x in t3["tasks"]) and k not in kolab_keys:
        J.gagal("tetap_untuk_pengguna_lain", k)
    await J.langkah("urungkan", "DELETE", jalur, user=OWNER)
    _, t4 = await J.langkah("tasks_owner_urung", "GET", "/api/dashboard/tasks", user=OWNER)
    if k not in [x["key"] for x in (t4 or {}).get("tasks", [])]:
        J.gagal("kembali_sesudah_urungkan", k)
    await J.langkah("kunci_liar_422", "POST", "/api/dashboard/tasks/hapus:semua/dismiss", user=OWNER, harap=(422,))
    async with J.pool.acquire() as c:
        n = await c.fetchval("SELECT count(*) FROM dashboard_task_state WHERE tenant_id = $1", T)
    if n != 0:
        J.gagal("tabel_bersih_sesudah_urungkan", str(n))
