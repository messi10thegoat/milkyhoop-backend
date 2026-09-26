"""Pagar harness journey: WAJIB lolos sebelum satu tulisan pun. Gagal = SystemExit (bukan peringatan).

system_identifier prod DIUKUR tiap jalan oleh mh-journey.sh (docker exec ke postgres prod, baca-saja) dan dioper
lewat env JOURNEY_PROD_SYSID. Env kosong -> pagar MENOLAK (gagal-tertutup), bukan melewatkan pemeriksaan.
"""
import os

PROD_DB = "milkydb"


def prod_sysid():
    v = (os.environ.get("JOURNEY_PROD_SYSID") or "").strip()
    if not v.isdigit():
        raise SystemExit("PAGAR MENOLAK: JOURNEY_PROD_SYSID kosong/tak sah (jalankan lewat mh-journey.sh)")
    return v


async def periksa(conn, label="pool", sysid_prod=None):
    sysid_prod = sysid_prod or prod_sysid()
    db = await conn.fetchval("select current_database()")
    sysid = str(await conn.fetchval("select system_identifier from pg_control_system()"))
    sentinel = await conn.fetchval("select to_regclass('public._journey_scratch') is not null")
    alasan = []
    if db == PROD_DB:
        alasan.append(f"current_database()={db} (nama prod)")
    if sysid == sysid_prod:
        alasan.append("system_identifier == PROD")
    if not sentinel:
        alasan.append("tabel penanda _journey_scratch tak ada")
    if alasan:
        raise SystemExit(f"PAGAR MENOLAK [{label}]: " + "; ".join(alasan))
    return {"db": db, "sysid": sysid, "host_env": os.environ.get("DB_HOST")}
