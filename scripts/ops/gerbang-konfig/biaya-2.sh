# Konfig gerbang unit Biaya BE gelombang 2 (B2: pratinjau buat POST /expenses/preview + ubah POST /{id}/edit/preview, SATU kalkulator, inti ubah diekstrak). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-biaya2-r2; CABANG=feat/biaya-b2-r2; NAMA_JENDELA=biaya-2
POLA_UNIT='biaya|expense|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-biaya-gel2.sh
PENANDA_BERKAS=routers/expenses.py; PENANDA='async def ubah_biaya_inti'
A=backend/api_gateway/app; T=tests/unit/test_biaya_gel2.py; EXR=$A/routers/expenses.py
SAB=(
 "h1 pratinjau tak di-rollback|$EXR|s#await tr.rollback()   \# SELALU: pratinjau tak pernah menulis#await tr.commit()#|$T"
 "h2 kalkulator pajak beda dari tulis|$EXR|s#    tax = (s \\* Decimal(str(tax_rate or 0)) / Decimal(\"100\")).quantize(Decimal(\"1\"))#    tax = (s * Decimal(str(tax_rate or 0)) / Decimal(\"100\")).quantize(Decimal(\"0.01\"))#|$T"
 "h3 ubah bukan draf lolos|$EXR|s#if existing\\[\"status\"\\] != \"draft\":#if existing[\"status\"] == \"void\":#|$T"
 "h4 pratinjau buat menelan blok jadi sukses|$EXR|s#\"can_proceed\": not h\\[\"blocks\"\\], \"blocks\": h\\[\"blocks\"\\], \"journals\": h\\[\"journals\"\\],\$#\"can_proceed\": True, \"blocks\": h[\"blocks\"], \"journals\": h[\"journals\"],#|$T"
 "h5 PATCH tak lewat penulis bersama|$EXR|s#_sebelum, expense = await ubah_biaya_inti(conn, ctx, expense_id, body)#_sebelum, expense = None, None#|$T"
)
