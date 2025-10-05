package observer

import (
	"os"
	"strings"

	"github.com/sirupsen/logrus"
	"gopkg.in/yaml.v2"
)

var Logger *logrus.Logger
var Log *logrus.Entry

type AppConfig struct {
	LogLevel string `yaml:"log_level"`
}

func InitLogger(component string) {
	Logger = logrus.New()
	Logger.SetOutput(os.Stdout)
	Logger.SetFormatter(&logrus.TextFormatter{
		FullTimestamp: true,
	})

	// Default level
	level := logrus.InfoLevel

	// ✅ Coba baca dari config YAML
	configPath := "backend/services/flow-executor/config/app_config.yaml"
	if content, err := os.ReadFile(configPath); err == nil {
		var cfg AppConfig
		if yamlErr := yaml.Unmarshal(content, &cfg); yamlErr == nil {
			if parsed, err := logrus.ParseLevel(strings.ToLower(cfg.LogLevel)); err == nil {
				level = parsed
			}
		}
	}

	// ⛳ Jika ada ENV LOG_LEVEL, override config
	if levelStr := os.Getenv("LOG_LEVEL"); levelStr != "" {
		if parsed, err := logrus.ParseLevel(strings.ToLower(levelStr)); err == nil {
			level = parsed
		}
	}

	Logger.SetLevel(level)
	Log = Logger.WithFields(logrus.Fields{
		"component": component,
	})
}
