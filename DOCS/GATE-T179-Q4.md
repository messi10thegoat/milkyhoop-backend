# T179 Q4 — Pagar daftar-kependekan · berkas gate

Master sebelum: `6a707cb22f3b0c282c68697e2b1a7f016f74e565`
Tenant: `kaos-biru-konveksi` · prefix `ZZTest T179` · sesi BARU tiap probe
Harness: `/root/t179q4/probe.sh` + `run.sh` (UA browser + Origin/Referer milkyhoop.com)

## GATE MERAH — terbukti SEBELUM menulis kode

### M1 — varian V5, 11 percobaan, gejala TIDAK terpicu secara alami
Model non-deterministik. Metode: n kecil per string, banyak string.
Hasil 11 stimulus berbeda:
- 4x parse GAGAL total -> jalur T174 (sudah bersuara). Contoh log:
  `[T144_BULK] items string gagal di-parse: err=Expecting value ... head='ZZTest T179 Beta, ZZTest T179 Gamma'`
- 5x parse BERHASIL dan LENGKAP (3/3, 4/4) -> tak ada kependekan.
- 1x salah-rute ke query list.
- 1x klarifikasi slot.
Tak sekali pun "JSON sah tapi satu elemen hilang" muncul dalam ~11 percobaan.

### M2 — SINTETIS (DINYATAKAN SINTETIS)
Karena M1 tak terpicu dalam >8 percobaan, kasusnya disintesis lewat JALUR YANG SAMA:
stimulus yang memuat 3 token-harga tetapi secara sah hanya menghasilkan 2 elemen.
Yang sintetis adalah KEKURANGANNYA (tak ada barang yang benar-benar hilang);
stimulusnya sendiri kalimat wajar dan jalur kodenya identik.

Stimulus M2:
```
buatkan barang ZZTest T179 Meja (harga jual 500.000), ZZTest T179 Kursi (harga jual 300.000). Total belanja saya 800.000.
```

MERAH (master 6a707cb2, sebelum fix) — [HTTP] + [LOG]:
```
TYPE: DIRECT_ACTION_PREVIEW
TEXT: 'Ada 2 barang di pesan ini. Saya tampilkan satu per satu supaya tiap barang bisa
       dicek — dan dilewati — sendiri-sendiri.\n\n
       ⚠ 1. ZZTest T179 Meja — Jual Rp 500.000 · Beli - · pcs · persediaan  — belum ada: harga beli\n
       ⚠ 2. ZZTest T179 Kursi — Jual Rp 300.000 · Beli - · pcs · persediaan  — belum ada: harga beli\n\n
       Barang 1 dari 2: **ZZTest T179 Meja**'
PAYLOAD (baseline, _batch_id dibuang):
{"_batch_dilewati_awal": [], "_batch_index": 1,
 "_batch_queue": [{"base_unit":"pcs","item_type":"persediaan","nama_produk":"ZZTest T179 Kursi","sales_price":300000.0}],
 "_batch_total": 2, "base_unit": "pcs", "date": "2026-08-29", "item_type": "goods",
 "name": "ZZTest T179 Meja", "sales_price": 500000.0}
LOG:
[T144_BULK] jalur bulk create_item AKTIF: 2 baris, nama=['ZZTest T179 Meja', 'ZZTest T179 Kursi']
[T144_BULK] jalur bulk create_item AKTIF: 2 baris, nama=['ZZTest T179 Meja', 'ZZTest T179 Kursi']   <-- DUA KALI
[T171_SLIDE] pemecahan AKTIF: batch=... total=2
```
=> 2 baris tersusun, NOL kalimat peringatan, NOL `[T179_KEPENDEKAN]`. **MERAH.**

`items` MENTAH dari model (bukti jalur bulk benar) — dari `[T144_BULK] ... AKTIF`:
`[{'nama_produk':'ZZTest T179 Meja','item_type':'persediaan','base_unit':'pcs','sales_price':500000.0},
  {'nama_produk':'ZZTest T179 Kursi','item_type':'persediaan','base_unit':'pcs','sales_price':300000.0}]`
(terbaca di payload: baris-1 top-level + baris-2 di `_batch_queue`)

### Temuan tambahan yang MENGUBAH RANCANGAN (terukur, sebelum menulis)
Stimulus SEHAT dua-jenis-harga:
```
daftarkan barang baru ZZTest T179 Hoodie harga jual 250.000 harga beli 180.000,
ZZTest T179 Sweater harga jual 210.000 harga beli 150.000
```
-> 2 elemen LENGKAP, 4 token-harga. Pagar naif (n_harga > n_items) akan MENYALA di
jalur yang benar-benar sehat. Karena itu ditambahkan `_t179_faktor_harga`:
faktor 2 bila pesan menyebut jual DAN beli. Pagar yang menyala di jalur sehat lebih
merusak daripada gejala yang ditutupnya.

## PENGUKUR BISA MENYALA DAN BISA DIAM (kontrol positif + negatif)
Unit-check `_t179_hitung_token_harga` / `_t179_faktor_harga` atas string gate:
```
M2 meja/kursi/total        nh=3 faktor=1 n_items=2 -> NYALA
G1 celana/kemeja/dasi      nh=3 faktor=1 n_items=3 -> diam
G1b hoodie/sweater 2harga  nh=4 faktor=2 n_items=2 -> diam
G2 satu barang             nh=1 faktor=1 n_items=1 -> diam
combed 30s/24s             nh=3 faktor=1 n_items=3 -> diam
rb/jt/k                    nh=3 faktor=1 n_items=3 -> diam
kg bukan k                 nh=0 faktor=1 n_items=1 -> diam
dieja (titik buta)         nh=0 faktor=1 n_items=2 -> diam   <-- TITIK BUTA DINYATAKAN
kaos S/M/L/XL              nh=4 faktor=1 n_items=4 -> diam
```

## SITUS — MELAWAN PREMIS PROMPT (Aturan 12)
Prompt menunjuk `orchestrator.py:3193-3205`. Baris itu adalah jalur PIL ENTITY
(`_ep_items`), dan komentar yang sudah ada di repo (L1422-1425) menyatakan jalur itu
**tak pernah dilewati `create_item`** (nol slot vendor/customer -> nol ambiguitas).
Pagar yang dipasang di sana akan DIAM selamanya.
Situs yang benar = situs panggilan `_t144_normalisasi_items` di cabang `create_item`
(jalur non-pil), yaitu tempat `[T144_BULK] ... AKTIF` terbit.

## PENANDA TERBIT SEKALI
`_t144_normalisasi_items` dipanggil DUA KALI (L1429 normalisasi ke-1, L~4165 ke-2),
dan karenanya `[T144_BULK]` terbit dua kali per kejadian (terukur di log di atas).
Pagar T179 sengaja dipasang di SITUS PANGGILAN ke-1 SAJA, bukan di dalam fungsi,
supaya `[T179_KEPENDEKAN]` terbit SEKALI dan bisa dihitung apa adanya untuk Q3.

## TITIK BUTA YANG DINYATAKAN DAN DITERIMA
Angka yang DIEJA ("dua ratus ribu") -> nol token-harga -> pagar DIAM. Tidak dikejar.
