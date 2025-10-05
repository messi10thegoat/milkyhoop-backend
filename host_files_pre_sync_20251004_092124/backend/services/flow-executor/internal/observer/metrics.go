package observer

import (
	"github.com/prometheus/client_golang/prometheus"
)

var (
	FlowExecutionCount = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "flow_execution_total",
			Help: "Total number of flows executed",
		},
		[]string{"flow_id", "status"},
	)

	NodeExecutionDuration = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "node_execution_duration_seconds",
			Help:    "Duration of each node execution in seconds",
			Buckets: prometheus.DefBuckets,
		},
		[]string{"node_id", "hoop"},
	)
)

func RegisterMetrics() {
	prometheus.MustRegister(FlowExecutionCount)
	prometheus.MustRegister(NodeExecutionDuration)
}
