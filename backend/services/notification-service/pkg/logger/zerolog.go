package logger

import (
	"context"
	"os"
	"time"

	"github.com/rs/zerolog"
)

var Log zerolog.Logger

func InitLogger() {
	Log = zerolog.New(os.Stdout).
		With().
		Timestamp().
		Logger().
		Level(zerolog.InfoLevel).
		Output(zerolog.ConsoleWriter{Out: os.Stdout, TimeFormat: time.RFC3339})
}

func WithContext(ctx context.Context) *zerolog.Event {
	return Log.Info().
		Str("trace_id", GetTraceID(ctx)).
		Str("request_id", GetRequestID(ctx))
}
