import requests

# Daftar endpoint health check untuk diuji
endpoints = [
    "/health",
    "/ready",
    "/metrics"
]

base_url = "http://localhost:8000"  # Ganti dengan URL API Gateway kamu jika berbeda

for endpoint in endpoints:
    url = f"{base_url}{endpoint}"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            print(f"Endpoint {endpoint} OK")
        else:
            print(f"Endpoint {endpoint} gagal dengan status {response.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"Error mengakses {endpoint}: {e}")

