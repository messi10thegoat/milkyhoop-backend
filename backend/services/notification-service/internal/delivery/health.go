package delivery

import (
	"google.golang.org/grpc/health"
	"google.golang.org/grpc/health/grpc_health_v1"
)

var healthServer *health.Server

func InitHealthCheck() grpc_health_v1.HealthServer {
	healthServer = health.NewServer()
	healthServer.SetServingStatus("", grpc_health_v1.HealthCheckResponse_SERVING)
	return healthServer
}

func SetHealthStatus(status grpc_health_v1.HealthCheckResponse_ServingStatus) {
	if healthServer != nil {
		healthServer.SetServingStatus("", status)
	}
}
