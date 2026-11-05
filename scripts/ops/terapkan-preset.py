"""Terapkan PRESET flag kategori usaha ke satu tenant (services/preset_kategori = satu sumber). Dipanggil lewat terapkan-preset.sh.

    terapkan-preset.sh <tenant> <kategori>              # KERING (bawaan): cetak rencana, nol tulis
    terapkan-preset.sh <tenant> <kategori> --jalankan   # tulis (perubahan flag = izin pemilik langsung)
    terapkan-preset.sh <tenant> <kategori> --uji        # tulis di transaksi lalu ROLLBACK: rencana == hasil, net 0
MERAH=preset (hanya --uji): kunci terakhir YANG DIUBAH dibuang dari pembanding -> WAJIB merah; tak ada yang diubah = kontrol
buta -> GAGAL (dulu kunci[-1] tetap: sudah aktif di tenant = PASS palsu). Tenant tak ada = galat."""
import asyncio
import os
import sys

import asyncpg

sys.path.insert(0, "/app/backend/api_gateway")


async def main(tid, kategori, mode):
    from app.services import preset_kategori as PK
    from app.services.tenant_features import fitur_aktif
    try:
        kunci = PK.kunci_preset(kategori)
    except ValueError as e:
        print(f"GALAT: {e}")
        return 2
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        if not await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', tid):
            print(f"GALAT: tenant {tid} tidak ada.")
            return 2
        r = await PK.rencana_preset(c, tid, kategori)
        print(f"rencana {tid} / {kategori}: tambah={r['tambah']} nyalakan={r['nyalakan']} sudah={r['sudah']}")
        if mode == "kering":
            print("KERING: nol tulis.")
            return 0
        tx = c.transaction()
        await tx.start()
        try:
            sebelum = set(await fitur_aktif(c, tid))
            await PK.terapkan_preset(c, tid, kategori)
            sesudah = set(await fitur_aktif(c, tid))
            harap = set(kunci)
            if os.environ.get("MERAH") == "preset" and mode == "uji":
                berubah = r["tambah"] + r["nyalakan"]
                if not berubah:
                    print("GAGAL kontrol merah buta: preset sudah aktif penuh di tenant ini, tak ada kunci yang diubah.")
                    await tx.rollback()
                    return 1
                harap -= {berubah[-1]}
            ok = sesudah - sebelum == set(r["tambah"] + r["nyalakan"]) and sesudah == sebelum | harap and sebelum <= sesudah
            ulang = await PK.rencana_preset(c, tid, kategori)  # idempoten: kedua kali tak ada yang diubah
            ok = ok and not ulang["tambah"] and not ulang["nyalakan"]
            print(("PASS" if ok else "GAGAL") + f" hasil: +{sorted(sesudah - sebelum)}; flag lain tak tersentuh; ulang = nol ubah")
            if mode == "uji" or not ok:
                await tx.rollback()
                kembali = set(await fitur_aktif(c, tid))
                ok = ok and kembali == sebelum
                print(("PASS" if kembali == sebelum else "GAGAL") + " net 0 sesudah ROLLBACK")
            else:
                await tx.commit()
                print(f"DITERAPKAN: {tid} / {kategori}")
        except BaseException:
            await tx.rollback()
            raise
        return 0 if ok else 1
    finally:
        await c.close()


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 2 or (len(a) > 2 and a[2] not in ("--jalankan", "--uji")):
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main(a[0], a[1], {"--jalankan": "jalankan", "--uji": "uji"}.get(a[2] if len(a) > 2 else "", "kering"))))
