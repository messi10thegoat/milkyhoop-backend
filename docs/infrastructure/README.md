# Dokumentasi Infrastruktur MilkyHoop

## 1. Pendahuluan
MilkyHoop menggunakan arsitektur **cloud-native** berbasis **microservices**, dikelola dengan **Docker, Kubernetes, dan Terraform** untuk memastikan skalabilitas dan keandalan.

## 2. Arsitektur Infrastruktur
infra/ ├── kubernetes/ # Konfigurasi Kubernetes untuk layanan MilkyHoop │ ├── deployments/ │ │ ├── auth-service.yaml │ │ ├── chatbot_service.yaml │ │ ├── api_gateway.yaml │ ├── services/ │ │ ├── redis-service.yaml │ │ ├── kafka-service.yaml │ │ ├── postgres-service.yaml │ ├── ingress.yaml # Load balancer untuk trafik eksternal ├── terraform/ # Infrastructure as Code (IaC) menggunakan Terraform │ ├── provider.tf # Konfigurasi penyedia cloud (GCP/AWS) │ ├── main.tf # Konfigurasi utama infrastruktur │ ├── variables.tf # Variabel-variabel untuk konfigurasi ├── service-mesh/ # Konfigurasi Istio untuk komunikasi antar layanan │ ├── istio-gateway.yaml │ ├── istio-virtual-service.yaml ├── high_availability/ # Pengaturan failover & load balancing │ ├── multi_region/ │ ├── failover/ │ ├── load_balancing/ ├── secret-management/ # Penyimpanan kredensial sensitif │ ├── README.md


## 3. Teknologi Infrastruktur
| Komponen            | Teknologi                  | Deskripsi |
|--------------------|---------------------------|------------|
| **Orkestrasi**    | Kubernetes (k8s)          | Deployment layanan |
| **Containerization** | Docker                    | Mengelola container |
| **Database**      | PostgreSQL, Redis         | Database utama & caching |
| **Event Streaming** | Kafka                     | Sinkronisasi data antar layanan |
| **Monitoring**    | Prometheus, Grafana       | Pemantauan real-time |
| **Logging**      | ELK Stack (Elasticsearch, Logstash, Kibana) | Logging & analitik |
| **IaC**          | Terraform                  | Automatisasi infrastruktur |

## 4. Deployment Layanan
### 4.1. Menjalankan Layanan dengan Docker Compose
```bash
docker-compose up -d
Untuk menjalankan layanan spesifik:

docker-compose up -d auth-service chatbot_service
Cek status layanan:

docker ps | grep backend
4.2. Deployment di Kubernetes
kubectl apply -f infra/kubernetes/deployments/
kubectl apply -f infra/kubernetes/services/
kubectl apply -f infra/kubernetes/ingress.yaml
Cek status pod:

kubectl get pods
4.3. Setup Istio untuk Service Mesh
Istio digunakan untuk komunikasi antar layanan:

kubectl apply -f infra/service-mesh/istio-gateway.yaml
kubectl apply -f infra/service-mesh/istio-virtual-service.yaml
Cek status service mesh:

kubectl get svc -n istio-system
5. Manajemen Database & Caching

MilkyHoop menggunakan PostgreSQL untuk penyimpanan utama, Redis untuk caching, dan Kafka untuk event streaming.

5.1. Menjalankan PostgreSQL
docker-compose up -d postgres
Cek koneksi ke PostgreSQL:

docker exec -it postgres psql -U milkyhoop -d milkyhoop_db
5.2. Menjalankan Redis
docker-compose up -d redis
Cek koneksi:

redis-cli
127.0.0.1:6379> PING
PONG
5.3. Menjalankan Kafka
docker-compose up -d kafka
Cek topik Kafka:

docker exec -it kafka /opt/kafka/bin/kafka-topics.sh --list --bootstrap-server localhost:9092
