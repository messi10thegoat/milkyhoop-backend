"""Hapus service action_executor + env gateway-nya dari docker-compose.yml. Anchored, NOL bila tak cocok."""
import io
import re
import sys

P = "docker-compose.yml"
t = io.open(P, encoding="utf-8").read()
galat = []

# 1) blok service: dari '  action_executor:' sampai (tak termasuk) service berikutnya '  minio:'
m = re.search(r"\n  action_executor:\n(?:.*\n)*?(?=  minio:\n)", t)
if not m:
    galat.append("blok service action_executor tak ditemukan (anchor: sampai '  minio:')")
else:
    blok = m.group(0)
    if "grpc_health_probe" not in blok or blok.count("\n  ") > 3:  # sanity: satu service saja
        # blok.count('\n  ') menghitung baris ber-indent-2 (kunci service lain) -> harus hanya action_executor
        pass
    t = t[:m.start()] + "\n" + t[m.end():]

# 2) env gateway
for env in ("    - ACTION_EXECUTOR_GRPC_HOST=action_executor\n", "    - ACTION_EXECUTOR_GRPC_PORT=5092\n"):
    if t.count(env) != 1:
        galat.append(f"env {env.strip()!r} cocok {t.count(env)}x")
    else:
        t = t.replace(env, "")

if galat:
    print("GAGAL — NOL ditulis:\n  " + "\n  ".join(galat)); sys.exit(1)
if "action_executor:" in t or "ACTION_EXECUTOR_GRPC" in t:
    print("GAGAL — sisa referensi action_executor di compose:", [l for l in t.splitlines() if "action_executor" in l.lower() or "ACTION_EXECUTOR" in l]); sys.exit(1)
io.open(P, "w", encoding="utf-8").write(t)
print("service action_executor + env dihapus dari compose")
