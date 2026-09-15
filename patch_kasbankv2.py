"""kasbank_v2: hapus 10 handler rute MATI (router tak dipasang; hanya void_transaction di-graft main.py:737).
Pertahankan helper + void_transaction. void_transaction tak memanggil handler mati (terverifikasi)."""
import io, sys
P = "/root/mh-law2/backend/api_gateway/app/routers/kasbank_v2.py"
t = io.open(P, encoding="utf-8").read()
H = '@router.get("/kasbank-v2/health")'
V = '@router.post("/bank-transactions/{transaction_id}/void", tags=["kasbank-v2"])'
X = '@router.post("/bank-transfers", tags=["kasbank-v2"])'
ih, iv, ix = t.find(H), t.find(V), t.find(X)
if not (0 < ih < iv < ix):
    print(f"GAGAL jangkar ih={ih} iv={iv} ix={ix}"); sys.exit(1)
# helper (..ih) + void_transaction (iv..ix)
new = t[:ih] + t[iv:ix].rstrip() + "\n"
io.open(P, "w", encoding="utf-8").write(new)
# sanity: void_transaction ada, handler mati hilang
assert "async def void_transaction(" in new
for dead in ("def list_accounts", "def create_manual_transaction", "def post_transaction",
             "def create_transfer", "def post_transfer", "def void_transfer", "def health_check",
             "def get_account_detail", "def list_transactions", "def get_transaction_detail"):
    assert dead not in new, f"SISA {dead}"
print("OK kasbank_v2: 10 handler mati dihapus, void_transaction + helper tetap")
