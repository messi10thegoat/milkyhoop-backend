package delivery

import (
	"log"
	"net/http"

	"github.com/prometheus/client_golang/prometheus/promhttp"
)

func StartMetricsServer() {
	http.Handle("/metrics", promhttp.Handler())

	go func() {
		log.Println("📊 Starting Prometheus metrics server at :8080")
		if err := http.ListenAndServe(":8080", nil); err != nil {
			log.Fatalf("❌ Metrics server failed: %v", err)
		}
	}()
}
