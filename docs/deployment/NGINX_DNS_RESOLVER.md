# Nginx DNS Resolver untuk Docker

## Problem

Nginx secara default **cache DNS selamanya** saat startup. Jika backend container restart dan mendapat IP baru, nginx tetap mengarah ke IP lama → **502 Bad Gateway**.

## Solution

Gunakan `resolver` directive dengan variable-based `proxy_pass`.

### Konfigurasi di `nginx-ssl.conf`

```nginx
server {
    # Docker's embedded DNS server dengan TTL 10 detik
    resolver 127.0.0.11 valid=10s ipv6=off;
    resolver_timeout 5s;

    # WAJIB: Gunakan variable untuk hostname
    set $backend_api milkyhoop-dev-api_gateway;
    set $backend_barcode milkyhoop-dev-barcode_service-1;

    location /api/ {
        # PENTING: Jangan sertakan path saat pakai variable
        proxy_pass http://$backend_api:8000;
        # ...
    }
}
```

## Aturan Penting

### 1. Wajib Pakai Variable

```nginx
# SALAH - DNS di-cache selamanya
proxy_pass http://milkyhoop-dev-api_gateway:8000;

# BENAR - DNS di-refresh setiap 10 detik
set $backend_api milkyhoop-dev-api_gateway;
proxy_pass http://$backend_api:8000;
```

### 2. Jangan Sertakan Path di proxy_pass

```nginx
# SALAH - URI jadi double (/api//api/items)
proxy_pass http://$backend_api:8000/api/;

# BENAR - URI diteruskan as-is
proxy_pass http://$backend_api:8000;
```

### 3. Docker DNS Server

- IP: `127.0.0.11` (embedded DNS di setiap container)
- Hostname yang bisa di-resolve: nama container atau service name

## Troubleshooting

### Cek DNS Resolution dari Container

```bash
docker exec milkyhoop-frontend-1 nslookup milkyhoop-dev-api_gateway
```

### Cek Konektivitas

```bash
docker exec milkyhoop-frontend-1 wget -qO- http://milkyhoop-dev-api_gateway:8000/healthz
```

### Cek Nginx Error Log

```bash
docker logs milkyhoop-frontend-1 --tail 50 2>&1 | grep -E "error|502|503|refused"
```

### Jika Dapat 404 (bukan 502)

Kemungkinan `proxy_pass` masih pakai path. Pastikan format:
```nginx
proxy_pass http://$backend_api:8000;  # Tanpa trailing path
```

## Reference

- [NGINX Blog: DNS Service Discovery](https://www.nginx.com/blog/dns-service-discovery-nginx-plus/)
- [Docker DNS Resolution](https://docs.docker.com/config/containers/container-networking/#dns-services)

---

*Last updated: 2026-01-15*
