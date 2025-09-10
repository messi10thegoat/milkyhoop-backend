#!/bin/bash
# Script untuk memeriksa dan mencegah kebocoran API key, secret, atau token di Git

echo "Memeriksa kebocoran API keys, secrets, atau tokens di repositori..."

# Memindai file yang belum di-commit untuk kunci sensitif
git secrets --scan

# Jika menemukan secrets, berikan peringatan
if [ $? -eq 0 ]; then
  echo "Tidak ditemukan kebocoran kunci."
else
  echo "Peringatan: Kebocoran kunci ditemukan! Harap periksa dan perbaiki."
  exit 1
fi

echo "Pemeriksaan selesai."

