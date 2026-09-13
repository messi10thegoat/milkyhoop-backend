# TEMUAN — dana satu pihak diterapkan ke dokumen pihak lain (13 Sep 2026)

**Kelas:** penerapan dana (uang muka, nota kredit, kredit vendor, pembayaran) ke dokumen
**tanpa memastikan pihaknya sama**. Dua bentuk cacat terukur: **tak membandingkan sama sekali**
dan **membandingkan dengan cara rusak**.

## Status ringkas

| # | jalur | status |
|---|---|---|
| 1 | DP pelanggan → faktur | **TUTUP `fe42de6a`** |
| 2 | nota kredit → faktur | **fitur MATI**; unit B menunggu — penghalang: 1 dari 2 CN posted ber-`customer_id` bukan UUID |
| 10 | pembayaran tagihan → alokasi tagihan | **lintas vendor TERBUKTI (draf)**; lintas tenant **DITOLAK SECARA KEBETULAN** — unit berikut |

## Tabel sapuan (S2) — enumerasi lewat penulis baris aplikasi/alokasi (INSERT)

Kontrol positif sapuan: pembanding CN `credit_notes.py:1507` tertangkap.
Tipe kolom pihak: `sales_invoices`/`receive_payments`/`bills`/`bill_payments_v2`/`vendor_credits`/
`vendor_deposits` = **uuid**; `credit_notes.customer_id`, `customer_deposits.customer_id` = **VARCHAR**.

| # | jalur (situs) | memeriksa pihak? | benar? | kelas bukti |
|---|---|---|---|---|
| 1 | DP pelanggan → faktur (`customer_deposits.py` apply) | **TIDAK** (sebelum `fe42de6a`) | — | eksekusi: lintas pelanggan 200 |
| 2 | nota kredit → faktur (`credit_notes.py:1507`) | ya: `invoice.customer_id != cn.customer_id` | **RUSAK** — uuid vs str selalu beda → selalu 400 | eksekusi |
| 3 | DP vendor → tagihan (`vendor_deposits.py:592`) | ya: SQL `bills WHERE id AND vendor_id` | benar (uuid-uuid) — tapi UPDATE memakai kolom hantu `paid_amount`/`total_amount` | baca; 500 = DUGAAN |
| 4 | kredit vendor → tagihan (`vendor_credits.py:1330`) | ya: `bill.vendor_id != vc.vendor_id` | benar bila `vc.vendor_id` terisi; **dilewati bila NULL** | baca; 0 subjek |
| 5 | buat penerimaan + alokasi (`receive_payments.py:1203,1253`) | ya: filter SQL + `str(invoice.customer_id) != body.customer_id` | benar untuk uuid huruf kecil; huruf besar → **tolak palsu (gagal-tertutup)** | baca |
| 6 | ubah penerimaan draf (`receive_payments.py:1482`) | ya: str vs str | benar | baca |
| 7 | bayar dari DP di penerimaan (`receive_payments.py:1202`) | ya: DP dicari `customer_id = body.customer_id` | benar di SQL | baca |
| 8 | bayar faktur shortcut (`sales_invoices.py:3325`) | pihak diturunkan dari faktur itu sendiri | tak ada ruang lintas pihak | baca |
| 9 | bayar tagihan shortcut (`bills_service.py:1692`) | pihak diturunkan dari tagihan itu sendiri | tak ada ruang lintas pihak | baca |
| 10 | buat pembayaran tagihan + alokasi (`bill_payments.py:1309`) | **TIDAK** (vendor), **TIDAK** (tenant) | — | eksekusi: lintas vendor 200 |

## 1 — DP pelanggan: TUTUP `fe42de6a`

- **Celah, eksekusi (handler nyata, ROLLBACK):** DP pelanggan `d2d6d24b…` → faktur INV-2609-0005
  milik `345468ea…` → **200**; jurnal DEPOSIT_APPLICATION (Dr 2-10500 kewajiban DP), outstanding
  faktur pelanggan lain −1.000, amount_paid +1.000. Kontrol pelanggan sama → 200 efek penuh.
- **Data historis:** 5 aplikasi, **5/5 pelanggan sama**, 21.584.400. Celah nyata, **belum pernah dipakai**.
- **Perbaikan:** `services/pihak_helpers.py` (`normalisasi_pihak` uuid/varchar → UUID kanonik;
  NULL/bukan uuid → 400; `pastikan_pihak_sama`). Di dalam lock `DEPOSIT:{id}`, **semua faktur
  diperiksa sebelum tulis apa pun**.
- **Prasyarat terukur:** `customer_deposits` dapat-diterapkan gagal parse UUID **0** (posted 14/14 sah;
  kontrol sampah tertangkap 1/1); DP tanpa pelanggan hanya 43 baris **void**; faktur ber-outstanding
  tanpa pelanggan 0.
- **Gerbang `scripts/gerbang_dp_pihak.py` 9/9** (lama vs baru, data sama): merah lama BEDA 200
  menulis · baru BEDA 400 nol jurnal/aplikasi/amount_paid · SAMA 200 efek penuh · DP tanpa
  pelanggan 400 · multi [SAMA, BEDA] 400 **nol tulis** · sabotase helper no-op → lolos · nol menetap.
- **Hidup:** skrip bukti celah yang sama, **tak diubah**, atas kode live: BEDA 200 → **400**.

## 10 — pembayaran tagihan: lintas vendor TERBUKTI, lintas tenant DITOLAK KEBETULAN

Handler nyata `create_bill_payment`, konteks tenant kaos-biru, ROLLBACK, 8/8, nol menetap di KEDUA tenant.

- **Kontrol** vendor sama → tagihan sendiri → 200, alokasi tertulis.
- **Lintas VENDOR — TERBUKTI (draf):** pembayaran vendor `2a18aff0…` → tagihan vendor `1f2ba957…`
  → **200, alokasi tertulis**. Efek POST (AP, amount_paid) belum diukur → sisi merah unit berikut.
- **Lintas TENANT — DITOLAK SECARA KEBETULAN:** pembayaran tenant A → tagihan grapgrap `652aa18a…`
  → 400 "Amount exceeds remaining", nol tulis. **Lapis penolak BUKAN pemeriksaan tenant**, melainkan
  `get_bill_remaining_from_journal(conn, ctx_tenant, bill_id)` (`bill_payments.py:132`) yang
  menjumlahkan jurnal tenant A → 0. **Pagar tenant TIDAK ADA**: kalau fungsi itu diubah (dicache,
  disederhanakan, dibuat tenant-agnostik), jalur lintas tenant terbuka tanpa satu baris pun berubah
  di handler pembayaran.
- **Yang tetap bocor:** `SELECT … FROM bills WHERE id = $1::uuid FOR UPDATE` tanpa `tenant_id`
  (`bill_payments.py:1309`, dan serupa di `post_bill_payment` ~1596) **mengunci baris tenant lain**
  dan **membocorkan keberadaan** (400 "exceeds remaining" untuk id tenant lain vs 400 "not found"
  untuk id karangan). Kebocoran keberadaan + kunci, bukan uang. Paparan hari ini ≈0 (semua tenant
  milik pemilik) — **wajib lunas sebelum pelanggan pertama**.

## 2 — nota kredit: penghalang unit B

1 dari 2 `credit_notes` posted ber-`customer_id` **bukan UUID** (terukur, kontrol sampah tertangkap).
Helper akan menolak nota kredit itu. Sebelum B: ukur nilainya, jalur pembuatnya, dan apakah jalur itu
masih hidup — kalau pembuat masih menulis id bukan-UUID, **perbaikannya di pembuat, bukan di penerap**.
Lihat `TIKET-nota-kredit-apply-mati-20260913.md`.
