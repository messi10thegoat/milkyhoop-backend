# Source Code Backup Tutorial

Panduan backup source code MilkyHoop ke laptop lokal.

## Prerequisites
- Akses SSH ke server (password atau SSH key)
- Terminal di Mac/Linux
- ~50MB free space di laptop

---

## Step 1: Buat Backup di Server

SSH ke server atau jalankan via Claude Code:

```bash
# Set timestamp Jakarta
export TZ="Asia/Jakarta"
TIMESTAMP=$(date +"%Y%m%d_%H%M")

# Buat folder backup
mkdir -p /root/source_backup_$TIMESTAMP

# Backup Frontend (exclude node_modules, build, dll)
rsync -av --exclude='node_modules' --exclude='build' --exclude='.git' --exclude='*.log' --exclude='backups' --exclude='snapshots' \
  /root/milkyhoop/frontend/web/ /root/source_backup_$TIMESTAMP/frontend/

# Backup Backend (exclude __pycache__, venv, dll)
rsync -av --exclude='node_modules' --exclude='__pycache__' --exclude='.git' --exclude='*.log' --exclude='backups' --exclude='.venv' --exclude='venv' \
  /root/milkyhoop-dev/backend/ /root/source_backup_$TIMESTAMP/backend/

# Backup configs & docs
cp /root/milkyhoop-dev/docker-compose.yml /root/source_backup_$TIMESTAMP/ 2>/dev/null || true
cp -r /root/milkyhoop-dev/docs /root/source_backup_$TIMESTAMP/ 2>/dev/null || true

# Compress
cd /root && tar -czvf milkyhoop_source_$TIMESTAMP.tar.gz source_backup_$TIMESTAMP/

# Verify
echo "✅ Backup complete!"
ls -lh /root/milkyhoop_source_$TIMESTAMP.tar.gz
```

**Expected output:** File ~7MB di `/root/milkyhoop_source_YYYYMMDD_HHMM.tar.gz`

---

## Step 2: Download ke Mac

Buka **Terminal di Mac**, jalankan:

```bash
# Ganti TIMESTAMP dengan timestamp dari Step 1
scp root@159.89.197.131:/root/milkyhoop_source_YYYYMMDD_HHMM.tar.gz ~/Downloads/
```

Masukkan password server saat diminta.

**Contoh:**
```bash
scp root@159.89.197.131:/root/milkyhoop_source_20251218_0022.tar.gz ~/Downloads/
```

---

## Step 3: Extract di Mac (Optional)

```bash
cd ~/Downloads
tar -xzvf milkyhoop_source_*.tar.gz
```

---

## One-Liner untuk Server

Copy-paste langsung:

```bash
export TZ="Asia/Jakarta" && TIMESTAMP=$(date +"%Y%m%d_%H%M") && mkdir -p /root/source_backup_$TIMESTAMP && rsync -av --exclude='node_modules' --exclude='build' --exclude='.git' --exclude='*.log' --exclude='backups' --exclude='snapshots' /root/milkyhoop/frontend/web/ /root/source_backup_$TIMESTAMP/frontend/ && rsync -av --exclude='node_modules' --exclude='__pycache__' --exclude='.git' --exclude='*.log' --exclude='backups' --exclude='.venv' /root/milkyhoop-dev/backend/ /root/source_backup_$TIMESTAMP/backend/ && cp /root/milkyhoop-dev/docker-compose.yml /root/source_backup_$TIMESTAMP/ 2>/dev/null; cp -r /root/milkyhoop-dev/docs /root/source_backup_$TIMESTAMP/ 2>/dev/null; cd /root && tar -czvf milkyhoop_source_$TIMESTAMP.tar.gz source_backup_$TIMESTAMP/ && ls -lh /root/milkyhoop_source_$TIMESTAMP.tar.gz
```

---

## Cleanup Old Backups

Hapus backup lama di server:

```bash
# List semua backup
ls -lh /root/milkyhoop_source_*.tar.gz
ls -d /root/source_backup_*

# Hapus yang lama (contoh: yang lebih dari 7 hari)
find /root -maxdepth 1 -name "milkyhoop_source_*.tar.gz" -mtime +7 -delete
find /root -maxdepth 1 -type d -name "source_backup_*" -mtime +7 -exec rm -rf {} \;
```

---

## Troubleshooting

### Password SSH tidak tahu
1. Buka DigitalOcean Console: https://cloud.digitalocean.com
2. Klik droplet → Access → Reset Root Password
3. Cek email untuk password baru

### Permission denied
```bash
# Pastikan file ada
ls -la /root/milkyhoop_source_*.tar.gz
```

### File terlalu besar
Tambahkan exclude untuk folder besar:
```bash
--exclude='*.sql' --exclude='*.age'
```

---

## Server Info

| Item | Value |
|------|-------|
| IP | 159.89.197.131 |
| User | root |
| Provider | DigitalOcean |
| Domain | milkyhoop.com |

---

*Last updated: 2025-12-18*
