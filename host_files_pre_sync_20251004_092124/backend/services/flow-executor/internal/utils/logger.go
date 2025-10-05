package utils

import (
	"os"

	"github.com/rs/zerolog"
)

var Log zerolog.Logger

func InitLogger(service string) {
	Log = zerolog.New(os.Stdout).
		With().
		Timestamp().
		Str("service", service).
		Logger()
}
