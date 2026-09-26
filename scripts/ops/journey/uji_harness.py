"""Uji HARNESS (bukan skenario): pagar hijau di pool aplikasi + jalur koneksi-sendiri, MERAH di 4 kontrol,
jaringan terisolasi. mh-journey.sh menolak menjalankan skenario kecuali baris akhir = 'HARNESS OK'."""
import asyncio, os, socket, sys
sys.path.insert(0, "/wt/backend/api_gateway")
sys.path.insert(0, "/h")
import asyncpg
import journey_pagar as P


async def main():
    hijau, merah, bocor = 0, 0, 0
    from app.services.db_pool import get_db_pool
    pool = await get_db_pool()
    async with pool.acquire() as c:
        info = await P.periksa(c, "pool aplikasi")
        hijau += 1
        print("HIJAU pool aplikasi", info)
    from app.config import settings
    c2 = await asyncpg.connect(**{k: v for k, v in settings.get_db_config().items() if k != "ssl"})
    print("HIJAU settings.get_db_config", await P.periksa(c2, "config"))
    hijau += 1
    await c2.close()

    pw = os.environ["DB_PASSWORD"]
    for db, label in (("postgres", "db tanpa penanda"), ("milkydb", "db bernama prod (dengan penanda)")):
        c = await asyncpg.connect(host=os.environ["DB_HOST"], user="postgres", password=pw, database=db)
        try:
            await P.periksa(c, label)
            print("KONTROL MERAH GAGAL (pagar diam):", label)
        except SystemExit as e:
            merah += 1
            print("MERAH ok:", e)
        await c.close()
    async with pool.acquire() as c:
        try:
            await P.periksa(c, "sysid prod ditiru", sysid_prod=info["sysid"])
            print("KONTROL MERAH GAGAL (sysid)")
        except SystemExit as e:
            merah += 1
            print("MERAH ok:", e)
    simpan = os.environ.pop("JOURNEY_PROD_SYSID", None)
    try:
        async with pool.acquire() as c:
            await P.periksa(c, "env sysid kosong")
        print("KONTROL MERAH GAGAL (env kosong)")
    except SystemExit as e:
        merah += 1
        print("MERAH ok:", e)
    if simpan:
        os.environ["JOURNEY_PROD_SYSID"] = simpan

    for host, port in (("milkyhoop-dev-postgres-1", 5432), ("postgres", 5432), ("159.89.202.160", 5432),
                       ("redis", 6379), ("minio", 9000), ("api.resend.com", 443), ("1.1.1.1", 443)):
        try:
            s = socket.create_connection((host, port), timeout=3)
            s.close()
            bocor += 1
            print("BOCOR JARINGAN:", host, port)
        except OSError as e:
            print(f"tertutup {host}:{port} ({type(e).__name__})")
    print(f"RINGKAS hijau={hijau}/2 merah={merah}/4 bocor={bocor}")
    print("HARNESS OK" if (hijau, merah, bocor) == (2, 4, 0) else "HARNESS GAGAL")


asyncio.run(main())
