# Jadwal MilkyHoop (`/etc/cron.d/`)

Berkas di sini adalah **salinan** dari yang terpasang di server. Ia di-commit
supaya jadwalnya tidak lenyap kalau server dibangun ulang — droplet lama pernah
mati, dan pekerjaan yang hanya hidup di satu disk hilang tanpa jejak.

## Pasang

```bash
sudo cp monitoring/cron.d/milkyhoop-accounting /etc/cron.d/
sudo chmod 644 /etc/cron.d/milkyhoop-accounting
```

## ⚠️ Terpasang ≠ berjalan

`crontab -l` atau `ls /etc/cron.d/` **bukan bukti**. Buktinya adalah berkas log
dari jalan terjadwal SUNGGUHAN, plus jejak di syslog:

```bash
grep -a accounting_health_check /var/log/syslog | tail -3
# CRON[...]: (root) CMD (/root/milkyhoop-dev/monitoring/accounting_health_check.sh ...)
ls -lt /var/log/milkyhoop/health_check_*.log | head -3
```

Cara membuktikannya saat memasang: tambahkan entri sementara `* * * * *`,
tunggu satu menit, pastikan berkas log baru muncul DAN syslog menunjukkan CRON
yang memanggilnya, lalu **cabut entri sementara itu**. Menjalankan skripnya
dengan tangan tidak membuktikan apa pun tentang jadwalnya.

## Sejarah

Dipasang 12 Sep 2026. Sebelum itu **tak ada satu pun pekerjaan MilkyHoop yang
terjadwal di server ini** — `accounting_health_check.sh` sudah benar, sudah
meng-assert, dan sudah `exit 2`, tapi tak ada yang menjalankannya. Ia menemukan
`CRITICAL [2] Hash Chain: 2 broken chain links` pada jalan pertamanya.
