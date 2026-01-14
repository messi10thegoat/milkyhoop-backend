package monitoring

import (
	"net/http"
	"os"

	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/rs/zerolog/log"
)

func StartMetricsServer() {
	port := ":9109"
	if val := os.Getenv("METRICS_PORT"); val != "" {
		port = ":" + val
	}

	http.Handle("/metrics", promhttp.Handler())

	go func() {
		log.Info().Str("port", port).Msg("📊 Prometheus metrics server running")
		if err := http.ListenAndServe(port, nil); err != nil {
			log.Error().Err(err).Msg("❌ Metrics server failed")
		}
	}()
}
