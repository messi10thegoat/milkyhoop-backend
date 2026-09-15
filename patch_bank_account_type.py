"""(1) PATCH bank account_type (label-only, larang ke/dari credit_card, validasi CoA)
(2) perbaiki split kartu Kas&Bank: cash+petty_cash->Kas, bank+e_wallet->Bank, credit_card dikecualikan.
Jangkar tepat; NOL bila tak cocok."""
import io, sys

# --- (1a) schema: account_type di UpdateBankAccountRequest ---
SCH = "/root/mh-law2/backend/api_gateway/app/schemas/bank_accounts.py"
s = io.open(SCH, encoding="utf-8").read()
A1 = '''class UpdateBankAccountRequest(BaseModel):
    """Request body for updating a bank account."""

    account_name: Optional[str] = Field(None, max_length=100)'''
B1 = '''class UpdateBankAccountRequest(BaseModel):
    """Request body for updating a bank account."""

    account_name: Optional[str] = Field(None, max_length=100)
    account_type: Optional[
        Literal["bank", "cash", "petty_cash", "e_wallet", "credit_card"]
    ] = None'''
if s.count(A1) != 1:
    print("GAGAL schema jangkar", s.count(A1)); sys.exit(1)
io.open(SCH, "w", encoding="utf-8").write(s.replace(A1, B1))
print("OK (1a) schema account_type ditambah")

# --- (1b) handler update_bank_account: terapkan account_type ---
R = "/root/mh-law2/backend/api_gateway/app/routers/bank_accounts.py"
t = io.open(R, encoding="utf-8").read()
A2 = '''                if "swift_code" in _dikirim:
                    updates.append(f"swift_code = ${param_idx}")
                    params.append(_bersih("swift_code"))
                    param_idx += 1

                if body.is_active is not None:'''
B2 = '''                if "swift_code" in _dikirim:
                    updates.append(f"swift_code = ${param_idx}")
                    params.append(_bersih("swift_code"))
                    param_idx += 1

                # account_type: LABEL-only (tak sentuh jurnal/CoA). Larang transisi ke/dari
                # credit_card -> butuh ganti tipe CoA LIABILITY<->ASSET yang Law 18 blokir bila
                # sudah ada jurnal. Selain itu (cash/petty_cash/bank/e_wallet) semuanya ASSET.
                if "account_type" in _dikirim and body.account_type is not None:
                    _new_t = body.account_type
                    _old_t = ba["account_type"]
                    if (_new_t == "credit_card") != (_old_t == "credit_card"):
                        raise HTTPException(
                            status_code=400,
                            detail="Ubah tipe ke/dari kartu kredit tidak didukung — buat akun baru",
                        )
                    _coa = await conn.fetchrow(
                        "SELECT account_type FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2",
                        ba["coa_id"],
                        ctx["tenant_id"],
                    )
                    _need = "LIABILITY" if _new_t == "credit_card" else "ASSET"
                    if not _coa or _coa["account_type"] != _need:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Tipe akun tidak konsisten dengan CoA tertaut (butuh {_need})",
                        )
                    updates.append(f"account_type = ${param_idx}")
                    params.append(_new_t)
                    param_idx += 1

                if body.is_active is not None:'''
if t.count(A2) != 1:
    print("GAGAL handler jangkar", t.count(A2)); sys.exit(1)
t = t.replace(A2, B2)
io.open(R, "w", encoding="utf-8").write(t)
print("OK (1b) handler account_type ditambah")

# --- (2) kasbank card split: konstanta + dua situs ---
K = "/root/mh-law2/backend/api_gateway/app/routers/kasbank.py"
k = io.open(K, encoding="utf-8").read()

# konstanta modul (setelah baris 'router = APIRouter(' block? -> sisipkan setelah import get_user_context)
# sisipkan sebelum def pertama yang butuh; taruh setelah 'logger =' bila ada, else setelah import blok.
anchor_const = "\nrouter = APIRouter("
CONST = ('\n# Pemetaan tipe akun -> kartu ringkasan Kas&Bank. Dulu hanya "cash"/"bank" yang dihitung,\n'
         '# sehingga saldo "petty_cash"/"e_wallet" JATUH ke NOL kartu (hilang dari ringkasan).\n'
         '# credit_card SENGAJA dikecualikan dari kedua kartu (liabilitas, bukan kas/bank).\n'
         'KARTU_KAS_TYPES = ("cash", "petty_cash")\n'
         'KARTU_BANK_TYPES = ("bank", "e_wallet")\n\nrouter = APIRouter(')
if k.count(anchor_const) != 1:
    print("GAGAL kasbank router jangkar", k.count(anchor_const)); sys.exit(1)
k = k.replace(anchor_const, CONST)

# situs 1 (python /summary)
A3 = '''            cash_total = sum(a["balance"] or 0 for a in accounts if a["type"] == "cash")
            bank_total = sum(a["balance"] or 0 for a in accounts if a["type"] == "bank")'''
B3 = '''            cash_total = sum(a["balance"] or 0 for a in accounts if a["type"] in KARTU_KAS_TYPES)
            bank_total = sum(a["balance"] or 0 for a in accounts if a["type"] in KARTU_BANK_TYPES)'''
if k.count(A3) != 1:
    print("GAGAL kasbank situs1 jangkar", k.count(A3)); sys.exit(1)
k = k.replace(A3, B3)

# situs 2 (SQL /stats)
A4 = '''                    COALESCE(SUM(CASE WHEN ba.account_type = 'cash' THEN jb.balance ELSE 0 END), 0) as cash_total,
                    COALESCE(SUM(CASE WHEN ba.account_type = 'bank' THEN jb.balance ELSE 0 END), 0) as bank_total,'''
B4 = '''                    COALESCE(SUM(CASE WHEN ba.account_type IN ('cash', 'petty_cash') THEN jb.balance ELSE 0 END), 0) as cash_total,
                    COALESCE(SUM(CASE WHEN ba.account_type IN ('bank', 'e_wallet') THEN jb.balance ELSE 0 END), 0) as bank_total,'''
if k.count(A4) != 1:
    print("GAGAL kasbank situs2 jangkar", k.count(A4)); sys.exit(1)
k = k.replace(A4, B4)

io.open(K, "w", encoding="utf-8").write(k)
print("OK (2) kasbank split diperbaiki (2 situs + konstanta)")
