"""Gerbang gudang-wajib -- DUA SISI, NOL PENULISAN ke basis data.

  SISI  -- "lama" (harus MERAH) atau "baru" (harus HIJAU)

Gerbang ini TIDAK menulis satu baris pun ke `inventory_ledger`. Menguji
"apa yang terjadi kalau gudang kosong" dengan benar-benar menulisnya berarti
menaruh baris cacat di pembukuan yang sedang kita bersihkan. Jadi:

  [S] STRUKTUR  -- ke-17 titik `INSERT INTO inventory_ledger` menyebut
                   `warehouse_id` di daftar kolomnya. Ini klaim tentang KODE,
                   dan disebut begitu -- ia tak membuktikan perilaku.
  [P] PERILAKU  -- `record_inventory_inbound`/`_outbound` dipanggil dengan
                   `warehouse_id=None` memakai KONEKSI PALSU yang meledak
                   kalau disentuh. Sisi baru harus menolak SEBELUM menyentuh
                   DB; sisi lama akan menyentuhnya, dan ledakan koneksi palsu
                   itulah buktinya.

`KoneksiPalsu` adalah SENTINEL -- milik alat, bukan produk. Setiap laporan
yang menyebutnya harus menyebutnya sebagai sentinel.
"""
import asyncio, os, re, sys

SISI = os.environ["SISI"]
assert SISI in ("lama", "baru"), SISI
AKAR = os.environ.get("AKAR", "/root/mh-audit") + "/backend/"
BERKAS = """api_gateway/app/routers/inventory.py
api_gateway/app/routers/items.py
api_gateway/app/routers/production.py
api_gateway/app/routers/sales_invoices.py
api_gateway/app/routers/sales_receipts.py
api_gateway/app/routers/stock_adjustments.py
api_gateway/app/routers/stock_transfers.py
api_gateway/app/routers/transactions.py
api_gateway/app/services/inventory_helpers.py
api_gateway/app/services/kernel_document_executor.py""".split()


class DisentuhnyaDB(Exception):
    """SENTINEL: helper menyentuh basis data padahal gudang kosong."""


class KoneksiPalsu:
    """SENTINEL -- milik gerbang, bukan produk. Meledak kalau disentuh."""

    def __getattr__(self, nama):
        async def meledak(*a, **k):
            raise DisentuhnyaDB(nama)
        return meledak


def struktur():
    total, tanpa = 0, []
    for f in BERKAS:
        s = open(AKAR + f).read()
        for m in re.finditer(r"INSERT INTO inventory_ledger(.{0,900})", s, re.S):
            blok = m.group(1)
            tutup = blok.find(")")
            kolom = blok[:tutup] if tutup > 0 else blok
            total += 1
            if "warehouse_id" not in kolom:
                tanpa.append(f"{f}:{s[:m.start()].count(chr(10)) + 1}")
    return total, tanpa


async def perilaku():
    sys.path.insert(0, AKAR.rstrip("/").rsplit("/", 1)[0])
    from backend.api_gateway.app.services.inventory_helpers import (
        record_inventory_inbound,
        record_inventory_outbound,
    )
    from decimal import Decimal
    from uuid import uuid4

    umum = dict(
        tenant_id="kaos-biru-konveksi", product_id=uuid4(), product_code="X",
        product_name="X", warehouse_id=None, quantity=Decimal("1"),
        source_type="UJI", source_id=uuid4(), source_number="UJI-0",
        user_id=None, notes="uji gerbang",
    )
    hasil = {}
    for nama, fn, tambah in (
        ("inbound", record_inventory_inbound, {"unit_cost": Decimal("1")}),
        ("outbound", record_inventory_outbound, {}),
    ):
        try:
            await fn(KoneksiPalsu(), **umum, **tambah)
            hasil[nama] = "LOLOS TANPA KEBERATAN"
        except ValueError as e:
            hasil[nama] = "menolak" if "warehouse_id wajib" in str(e) else f"ValueError lain: {e}"
        except DisentuhnyaDB as e:
            hasil[nama] = f"menyentuh DB (sentinel: .{e})"
        except Exception as e:
            hasil[nama] = f"{type(e).__name__}: {e}"
    return hasil


def main():
    total, tanpa = struktur()
    ok_s = not tanpa
    hasil = asyncio.run(perilaku())
    ok_p = all(v == "menolak" for v in hasil.values())

    print(f"=== {SISI.upper()} @ {AKAR}")
    print(f"  [S] {'HIJAU' if ok_s else 'MERAH'}  {total} titik INSERT; "
          f"tanpa warehouse_id: {tanpa or 'nihil'}")
    print(f"  [P] {'HIJAU' if ok_p else 'MERAH'}  " +
          "; ".join(f"{k}={v}" for k, v in hasil.items()))
    if SISI == "baru":
        print("PUTUSAN:", "LULUS" if (ok_s and ok_p) else "GAGAL")
        return 0 if (ok_s and ok_p) else 1
    merah = (not ok_s) and (not ok_p)
    print("PUTUSAN:", "KONTROL MERAH SAH" if merah else
          "KONTROL TIDAK MEMERAH -- gerbang tak membuktikan apa pun")
    return 0 if merah else 1


sys.exit(main())
