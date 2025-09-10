#!/bin/bash
# Simple Docker build audit script

echo "Menganalisis ukuran image Docker dan layer cache..."

# Memeriksa ukuran image Docker
docker images --format "table {{.Repository}}\t{{.Size}}"

# Memeriksa layer cache pada build Docker
docker system df

echo "Audit Docker selesai!"

