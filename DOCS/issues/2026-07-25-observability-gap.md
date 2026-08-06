# Issue (LARGER): degraded >1 day, zero alerts — observability gap

---

## ⚖️ KEPUTUSAN URUTAN (2026-08-06): ALERTING MENDAHULUI PERBAIKAN LOGGING

**Bukan catatan — ini menentukan urutan kerja, dan ia MEMBALIK urutan yang tampak wajar.**

Temuan terkait (`2026-08-06-stdlib-logging-never-configured.md`): stdlib logging tak pernah
dikonfigurasi, sehingga **522 pemanggilan level INFO** (357 routers + 165 services) tak pernah
terlihat. Refleks wajarnya: "perbaiki logging dulu supaya kita bisa melihat".

**Refleks itu salah untuk kasus ini.**

Bukti dari `/ready` 503: sinyalnya **ADA sepanjang waktu**, di tempat yang benar, **berulang** sejak
2026-07-24 —
```
Redis connection failed: Port could not be cast to integer value as 'x66dii8PjJ7ADL094'
```
dan **tetap nol yang bertindak selama lebih dari seminggu**. Degradasinya bukan tak terlihat; ia
**tak terbaca**.

**Konsekuensi:** menyalakan 522 baris INFO ke aliran yang **sudah tidak dibaca siapa pun** akan
menambah **kebisingan**, bukan **kemampuan melihat**. Lebih buruk: volume yang naik membuat sinyal
yang benar-benar penting makin tenggelam, sehingga perbaikan logging tanpa alerting justru
**memperburuk** keadaan yang hendak diperbaiki.

**Urutan yang benar:**
1. **Alerting dulu** — `/ready != 200` → Alertmanager (stack Prometheus/Grafana/Loki SUDAH jalan).
   Setidaknya satu probe terpantau harus memakai `/ready`, bukan hanya `/healthz`.
2. **Baru** konfigurasi logging, bertahap per-modul, setelah audit isi pesan
   (sebagian `logger.info` mungkin mencetak payload sensitif yang selama ini "aman karena tak
   terlihat").

**Aturan umum yang bisa dipakai ulang:** *visibilitas tanpa pembaca bukan observability.* Sebelum
menambah sinyal, pastikan ada yang membaca sinyal yang sudah ada. Kalau tidak ada, menambah sinyal
adalah pekerjaan yang terasa produktif tetapi nol dampak.

---

## Observation
/ready returned 503 (redis down) continuously since 2026-07-24 with NOBODY notified, while
Prometheus/Grafana/Loki are running. The system ran degraded and undetected. Worse: the compose
healthcheck probes /healthz (which ignores redis and returns 200), so the container reports
"healthy" while /ready says not-ready — the one signal that knew was the one nothing watched.

## Why this is the bigger ticket
The redis parse bug is one defect. The fact that a day-long degradation passed silently is a
systemic gap: no alert wires /ready (or redis health) to the running monitoring stack. Any future
degradation of an in-memory-fallback dependency will likewise pass unnoticed and surface as
intermittent, hard-to-diagnose behavior (exactly the harness-flakiness risk).

## Proposal (not implemented)
- Alert on /ready != 200 (or redis:false) via the existing Prometheus/Alertmanager.
- Reconsider using /ready (not just /healthz) for at least one monitored probe so "healthy while
  degraded" cannot recur.


## PROBE MAP + gate correction (appended 2026-08-03, during deploy #2)
Live gateway (8001->8000) probe truth table:
- `/healthz` -> 200 unauth `{"status":"healthy","phase":"2","middleware":"active"}` = the compose healthcheck (`curl -f :8000/healthz`); container State.Health=healthy LEGITIMATELY.
- `/health` -> 200 unauth.
- `/api/health`, `/api/healthz`, `/api/ready` -> 401 (auth middleware) -> UNUSABLE as gates.
- `/ready` -> 503 `{"status":"not_ready","checks":{"db":true,"redis":false}}` -> STILL failing 2026-08-03. Root: redis health check FALSE (db ok). Cross-ref 2026-07-25-redis-password-as-port.md + memory redis-misconf-capdrop-20260725.
Two-layer gap (one ticket): a broken readiness probe (redis:false) AND zero alarm. Separately, the deploy runbook gated on `/api/health -> 200`, which is IMPOSSIBLE (auth-gated) — corrected to `/healthz`. NOT fixed here (diagnosis only, per owner): the /api/* auth-gating and the redis:false readiness.
