# Issue (LARGER): degraded >1 day, zero alerts — observability gap

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
