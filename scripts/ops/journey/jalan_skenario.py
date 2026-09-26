"""python /h/jalan_skenario.py <skenario_nama> <A|B> — memuat /h/skenario_<nama>.py (fungsi async jalankan(J))."""
import asyncio, importlib, sys
sys.path.insert(0, "/h")
from journey_lib import Journey   # noqa: E402


async def main():
    nama, lengan = sys.argv[1], sys.argv[2]
    sk = importlib.import_module(f"skenario_{nama}")
    J = Journey(nama, lengan, getattr(sk, "DIKENAL", {}))
    await J.buka()
    try:
        await sk.jalankan(J)
    except SystemExit:
        raise
    except Exception as e:   # skenario patah = FAIL tercatat, bukan hijau diam
        import traceback
        traceback.print_exc()
        J.gagal("skenario_patah", f"{type(e).__name__}: {e}")
    ring = await J.tutup()
    sys.exit(1 if ring["FAIL"] else 0)


asyncio.run(main())
