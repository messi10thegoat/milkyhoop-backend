# T179-Q3 FASE 0 — RUNBOOK OWNER (JANGAN dijalankan oleh agen)

Basis master: `a017914755d4fd252b195513d3ba1a6da81b3aaa`
Branch: `feat/t179q3-array-bill` — tip = keluaran `git -C /root/milkyhoop-dev rev-parse feat/t179q3-array-bill` (SHA commit runbook ini mustahil disebut di dalam dirinya sendiri).
Commit (urut lama→baru):
1. `1244b74c8acd93ec2cb48542803317dde1ca54b5` docs — rencana rollback
2. `796dc4029d7074574a4538f21ba442f1c64ae5d5` feat — item_schema + cabang array
3. `1fabfe09676b348f4b07b8933a6ec0ae2a8fc624` docs — SHA nyata di rollback
4. commit runbook owner (berkas ini) — SHA-nya = tip branch

StartedAt SEBELUM deploy: `2026-08-30T14:02:06Z`

## Hitungan entitas SEBELUM (lingkup: PER-TENANT, dari milkydb)
| tabel | kaos-biru-konveksi | grapgrap-manado |
|---|---|---|
| bills | 1 | 3 |
| sales_invoices | 1 | 5 |
| quotes | 5 | 4 |
| sales_orders | 1 | 1 |

`grapgrap-manado` HARUS tetap 3/5/4/1 sesudah seluruh probe.

## 1. MERGE + DEPLOY
```
git -C /root/milkyhoop-dev merge --ff-only feat/t179q3-array-bill
git -C /root/milkyhoop-dev rev-parse master        # harus == rev-parse feat/t179q3-array-bill
docker restart milkyhoop-dev-api_gateway
docker inspect -f '{{.State.StartedAt}}' milkyhoop-dev-api_gateway
```
**Bukti deploy yang DITERIMA: StartedAt bergeser dari `2026-08-30T14:02:06Z`
+ differential perilaku di penanda log.** BUKAN md5. BUKAN `inspect.getsource`
lewat `docker exec python -c` (proses baru — hijau walau restart gagal).

## 2. PROBE PRODUKSI — lewat `POST https://milkyhoop.com/api/v3/chat/message/stream`
Pakai **`curl`**, bukan urllib. `conversation_id` WAJIB string. Header browser +
Origin/Referer wajib. Login `delivered+owner@resend.dev` / `KaosBiru2026!`,
tenant `kaos-biru-konveksi`. **BERHENTI DI KARTU — jangan konfirmasi/terbitkan.**

Untuk SETIAP probe, `conversation_id` BARU, **≥3 ulangan**, dan rasio dihitung
**PER STRING**. Baca log: `docker logs --since 5m milkyhoop-dev-api_gateway | grep -E 'EXTRACT_S2|MERGE_ITEMS|T168_PICU'`

### MERAH → HIJAU (harus berubah)
| # | stimulus | lulus bila |
|---|---|---|
| P1 | `buat faktur pembelian dari PT Grosir Kaos, 2 item: Kaos Hitam 24s 10 pcs harga 25000, Kaos Biru 30s 5 pcs harga 30000, jatuh tempo 30 hari` | `[EXTRACT_S2] n_items=2 tipe=list`, `[MERGE_ITEMS] post=2 tipe_post=list`, `[T168_PICU] baris=2`, kartu 2 baris |
| P2 | `catat pembelian dari PT Grosir Kaos 3 item: Kaos Hitam 24s 10 pcs @25000, Kaos Biru 30s 5 pcs @30000, Kaos Hitam 24s 2 pcs @24000` | `n_items=3 tipe=list`, kartu 3 baris |

### KONTROL SEHAT — WAJIB HIJAU, ≥3 ulangan
| # | stimulus | lulus bila |
|---|---|---|
| K1 **paling menentukan** | `buat faktur pembelian dari PT Grosir Kaos, Kaos Hitam 24s 10 pcs harga 25000` | kartu bill 1 baris terbit, `n_items=1 tipe=list` |
| K2 | `buat faktur pembelian dari PT Grosir Kaos, Kaos Biru 30s 5 pcs harga 30000, catatan: kirim besok pagi` | kartu 1 baris + catatan terisi |
| K3 | `faktur pembelian dari PT Grosir Kaos, Kaos Hitam 24s 10 pcs 25000 dan Kaos Biru 30s 5 pcs 30000` | kartu 2 baris |
| K4 **aksi tak diubah** | `buat faktur penjualan untuk Toko Merdeka, Kaos Hitam 24s 3 pcs 50000 dan Kaos Biru 30s 2 pcs 55000` | perilaku PERSIS seperti sebelum deploy |
| K5 **aksi tak diubah** | `buat penawaran untuk Toko Merdeka, Kaos Hitam 24s 3 pcs 50000 dan Kaos Biru 30s 2 pcs 55000` | idem |
| K6 **aksi tak diubah** | `buat pesanan penjualan untuk Toko Merdeka, Kaos Hitam 24s 3 pcs 50000 dan Kaos Biru 30s 2 pcs 55000` | idem |

⚠️ Pakai **Toko Merdeka**, BUKAN Toko Melati — `Toko Melati` ADA DUA baris di
`customers` (diukur), jadi resolusi pelanggan ambigu dan kontrol jadi berisik.

### ASSERTION YANG BERAKHIR DI LAYAR (wajib, untuk SEMUA probe)
Fase 1 lolos gate fungsi murni lalu mengirim `text: null` ke layar. Karena itu
pada respons HTTP terakhir tiap probe periksa:
- `text` **BUKAN null dan bukan string kosong**
- `message_type` ∈ { `DIRECT_ACTION_PREVIEW`, `ACTION_PREVIEW` } — hanya
  `ACTION_PREVIEW`, `DIRECT_ACTION_PREVIEW`, `ACTION_RESULT`, `TUTORIAL_STEP`
  yang punya cabang render di FE (diverifikasi di
  `frontend/web/src/components/app/ChatPanel/`); nilai lain = layar kosong.
- kartu benar-benar tampak di UI (bukan hanya di JSON)

### ROLLBACK LANGSUNG bila K1 tertolak/hilang SEKALI PUN — jangan diagnosis di produksi
```
git -C /root/milkyhoop-dev reset --keep a017914755d4fd252b195513d3ba1a6da81b3aaa
docker restart milkyhoop-dev-api_gateway
docker inspect -f '{{.State.StartedAt}}' milkyhoop-dev-api_gateway   # WAJIB bergeser
```
Kalau harus lewat revert, cabut **KEEMPAT** SHA, urutan terbalik — satu revert
saja TIDAK cukup dan sudah pernah MANDEK:
```
git -C /root/milkyhoop-dev revert --no-edit $(git -C /root/milkyhoop-dev rev-list a0179147..feat/t179q3-array-bill)  # SEMUA commit, urutan terbalik otomatis
```

## 3. SESUDAH SEMUA PROBE
Ulangi hitungan entitas per-tenant di atas. `grapgrap-manado` harus TIDAK
BERGERAK. Kartu yang dibiarkan tak dikonfirmasi tidak menambah dokumen.
