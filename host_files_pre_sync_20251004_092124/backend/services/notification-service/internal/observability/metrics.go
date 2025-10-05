package observability

import (
	"github.com/prometheus/client_golang/prometheus"
)

var KafkaMessagesConsumed = prometheus.NewCounterVec(
	prometheus.CounterOpts{
		Name: "kafka_messages_consumed_total",
		Help: "Total Kafka messages consumed by topic",
	},
	[]string{"topic"},
)

func InitMetrics() {
	prometheus.MustRegister(KafkaMessagesConsumed)
}
