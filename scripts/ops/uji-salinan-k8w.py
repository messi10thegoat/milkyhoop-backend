"""K8-W uji DB di SALINAN (DRAF, belum pernah dijalankan; perbaiki di slot): services/pemindahan_dua_langkah.pratinjau_terima ASLI atas klon (BACA-SAJA), tenant kaos + nyawit.
Untuk tiap pemindahan in_transit di klon (sampai 5): 
A Terima SEMUA (baris None): peringatan == [] bila tak ada selisih lagi (tuntas) -- tuntas True <=> tanpa peringatan
B Terima SEBAGIAN (separuh qty tertunda barang pertama, 0 untuk lainnya bila ada): tiap barang ber-tertunda_sesudah > 0 punya tepat satu peringatan PB_TERIMA_KURANG; selisih == tertunda_sesudah; kalimat memuat nama barang
C pratinjau tak menulis (jumlah baris ledger + header status sebelum == sesudah)
Bila klon tak punya pemindahan in_transit: CATATAN (bukan FAIL) -- uji tulis penuh ada di unit + gerbang."""
import asyncio
import sys
from decimal import Decimal as D

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import stock_transfers as ST  # noqa: E402
from app.services import pemindahan_dua_langkah as D2  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def main():
    pool = await ST.get_pool()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            trf = await c.fetch("/* tenant_id */ SELECT id, transfer_number FROM stock_transfers WHERE tenant_id = $1 AND status = 'in_transit' ORDER BY created_at DESC LIMIT 5", tid)
            if not trf:
                print(f"CATATAN {tid}: tak ada pemindahan in_transit di klon, A-C dilewati", flush=True)
                continue
            uid = await c.fetchval("/* tenant_id */ SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND utr.status = 'ACTIVE' AND r.name ILIKE '%owner%' LIMIT 1", tid)
            ctx = {"tenant_id": tid, "user_id": str(uid)}
            for t in trf:
                n0 = await c.fetchval("/* tenant_id */ SELECT count(*) FROM inventory_ledger WHERE tenant_id = $1", tid)
                semua = await D2.pratinjau_terima(c, ctx, t["id"], None)
                cek(f"A {tid} {t['transfer_number']}: Terima semua: tuntas {semua['tuntas']} <=> tanpa peringatan ({len(semua['peringatan'])})", semua["tuntas"] == (len(semua["peringatan"]) == 0))
                items = [ln for ln in semua["lines"] if ln["tertunda"] > 0]
                if not items:
                    continue
                satu = items[0]
                sebagian = await D2.pratinjau_terima(c, ctx, t["id"], [{"item_id": satu["item_id"], "qty": satu["tertunda"] / 2}])
                tersisa = [ln for ln in sebagian["lines"] if ln["tertunda_sesudah"] > 0]
                kode = [w for w in sebagian["peringatan"] if w["code"] == "PB_TERIMA_KURANG"]
                cek(f"B {tid} {t['transfer_number']}: sebagian -> {len(tersisa)} barang tersisa == {len(kode)} peringatan, selisih sama, kalimat memuat nama",
                    len(tersisa) == len(kode) and all(any(w["item_id"] == str(ln["item_id"]) and abs(D(str(w["selisih"])) - ln["tertunda_sesudah"]) < D("0.0001") and ln["name"] in w["message"] for w in kode) for ln in tersisa))
                n1 = await c.fetchval("/* tenant_id */ SELECT count(*) FROM inventory_ledger WHERE tenant_id = $1", tid)
                cek(f"C {tid} {t['transfer_number']}: pratinjau tak menulis ledger ({n0} == {n1})", n0 == n1)
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
