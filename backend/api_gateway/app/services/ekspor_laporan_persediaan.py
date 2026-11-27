"""LP-4: ekspor xlsx/csv Laporan Nilai Persediaan & Mutasi Persediaan. BACA saja; angka/teks = hasil loader laporan yang SAMA dengan GET JSON-nya
(muat_nilai_persediaan / muat_mutasi_persediaan) -> berkas == layar. Baris total + blok rekonsiliasi GL ikut."""
import csv
import io

MAKS_BARIS = 20000


def tabel_nilai(d: dict) -> tuple:
    """-> (judul_kolom, baris, bawah[])"""
    per_gudang = d["kelompok"] == "gudang"
    kolom = (["Gudang"] if per_gudang else []) + ["Kode", "Barang", "Kategori", "Satuan", "Qty", "HPP satuan", "Nilai"]
    baris = []
    for x in d["baris"]:
        baris.append(([(x.get("gudang") or {}).get("nama")] if per_gudang else []) +
                     [x["kode"], x["nama"], x["kategori"], x["satuan"], x["qty_text"], x["hpp_satuan_text"], x["nilai_text"]])
    bawah = [[], ["Total nilai", d["total_nilai_text"]], [f"Per tanggal {d['per_tanggal']}", d["satuan_text"]]]
    if d.get("dalam_perjalanan"):
        bawah.append(["Dalam perjalanan (Transit)", d["dalam_perjalanan"]["qty_text"], d["dalam_perjalanan"]["nilai_text"]])
    rk = d["rekonsiliasi"]
    bawah += [[], ["Rekonsiliasi dengan buku besar", rk.get("status_text")], ["Sub-buku", rk.get("sub_ledger_text")], ["Buku besar", rk.get("gl_text")], ["Selisih", rk.get("selisih_text")]]
    return kolom, baris, bawah


def tabel_mutasi(d: dict) -> tuple:
    per_gudang = d["kelompok"] == "gudang"
    ks = list(d["kolom_text"].keys())
    kolom = (["Gudang"] if per_gudang else []) + ["Kode", "Barang", "Satuan"]
    for k in ks:
        kolom += [f"{d['kolom_text'][k]} (qty)", f"{d['kolom_text'][k]} (nilai)"]
    baris = []
    for x in d["baris"]:
        r = ([(x.get("gudang") or {}).get("nama")] if per_gudang else []) + [x["kode"], x["nama"], x["satuan"]]
        for k in ks:
            r += [x[k]["qty_text"], x[k]["nilai_text"]]
        baris.append(r)
    tot = ["Total nilai"] + [""] * (len(kolom) - 1 - 2 * len(ks)) 
    for k in ks:
        tot += ["", d["total"][k]["nilai_text"]]
    rk = d["rekonsiliasi"]
    bawah = [[], tot, [d["periode_text"], d["satuan_text"]]]
    if d.get("dalam_perjalanan"):
        bawah.append(["Dalam perjalanan (Transit)", d["dalam_perjalanan"]["qty_text"], d["dalam_perjalanan"]["nilai_text"]])
    bawah += [[], ["Rekonsiliasi dengan buku besar", rk.get("status_text")]]
    for k in ks:
        v = rk.get(k)
        if isinstance(v, dict):
            bawah.append([d["kolom_text"][k], "Sub-buku " + str(v.get("ledger_text")), "Buku besar " + str(v.get("gl_text")), "Selisih " + str(v.get("selisih_text"))])
    return kolom, baris, bawah


def tabel_kartu(d: dict) -> tuple:
    """d = respons GET /items/{id}/history (halaman digabung: data[] urut tampilan = terbaru dulu; berkas dibalik jadi kronologis)."""
    kolom = ["Tanggal", "Jenis", "No. dokumen", "Gudang", "Masuk", "Keluar", "Saldo", "HPP satuan", "Total biaya"]
    baris = [[m.get("tanggal_text"), m.get("jenis_text"), m.get("source_number"), m.get("warehouse_name"), m.get("quantity_in_text"), m.get("quantity_out_text"),
              m.get("quantity_balance_text"), m.get("unit_cost_text"), m.get("total_cost_text")] for m in reversed(d["data"])]
    it = d["item"]
    bawah = [[], ["Barang", f"{it.get('code') or ''} {it.get('name') or ''}".strip()], ["Periode", d.get("periode_text") or "Semua tanggal"],
             ["Saldo awal", d.get("saldo_awal_text")]]
    rg = d.get("ringkasan")
    if rg:
        bawah += [["Total masuk", rg["total_masuk_text"]], ["Total keluar", rg["total_keluar_text"]], ["Saldo akhir", rg["saldo_akhir_text"]]]
    if d.get("dalam_perjalanan"):
        bawah.append(["Dalam perjalanan (Transit)", d["dalam_perjalanan"]["qty_text"], d["dalam_perjalanan"]["nilai_text"]])
    return kolom, baris, bawah


def tabel_laba_barang(d: dict) -> tuple:
    """d = `data` GET /reports/profitability/items (halaman digabung)."""
    kolom = ["Barang", "Qty terjual", "Pendapatan", "Diskon", "HPP", "Laba kotor", "Margin", "Faktur", "Retur"]
    baris = [[x.get("product_name"), x["qty_sold_text"], x["revenue_gross_text"], x["total_discount_text"], x["cogs_text"], x["gross_profit_text"],
              x["margin_text"], x["invoice_count_text"], x["retur_value_text"]] for x in d["items"]]
    s, rk = d["summary"], d.get("reconciliation") or {}
    bawah = [[], ["Total", s["total_qty_sold_text"], s["total_revenue_gross_text"], s["total_discount_text"], s["total_cogs_text"], s["total_gross_profit_text"], s["overall_margin_text"]],
             ["Periode", d["periode_text"], d["satuan_text"]], [], ["Rekonsiliasi dengan laba rugi", rk.get("status_text"), rk.get("drift_amount_text")]]
    return kolom, baris, bawah


def bangun_csv(kolom: list, baris: list, bawah: list) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(kolom)
    for r in baris + bawah:
        w.writerow(["" if v is None else v for v in r])
    return ("﻿" + buf.getvalue()).encode("utf-8")   # BOM: Excel Windows membaca UTF-8


def bangun_xlsx(judul: str, kolom: list, baris: list, bawah: list) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = judul[:31]
    ws.append(kolom)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in baris + bawah:
        ws.append(["" if v is None else v for v in r])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
