package loader

import (
	"bytes"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
)

// CompileJSON mengirim file JSON ke visualhoop-compiler dan menerima file .pb sebagai output
func CompileJSON(jsonPath, outputPath string) error {
	compilerURL := os.Getenv("VISUAL_COMPILER_URL")
	if compilerURL == "" {
		compilerURL = "http://visualhoop-compiler:5009/compile"
	}

	file, err := os.Open(filepath.Join("flows/global", jsonPath))
	if err != nil {
		return fmt.Errorf("failed to open JSON file: %w", err)
	}
	defer file.Close()

	var buf bytes.Buffer
	writer := multipart.NewWriter(&buf)
	part, err := writer.CreateFormFile("file", filepath.Base(jsonPath))
	if err != nil {
		return fmt.Errorf("failed to create form file: %w", err)
	}
	if _, err := io.Copy(part, file); err != nil {
		return fmt.Errorf("failed to copy file to buffer: %w", err)
	}
	writer.Close()

	resp, err := http.Post(compilerURL, writer.FormDataContentType(), &buf)
	if err != nil {
		return fmt.Errorf("failed to send request to compiler: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("compiler error: %s", string(body))
	}

	out, err := os.Create(outputPath)
	if err != nil {
		return fmt.Errorf("failed to create output .pb file: %w", err)
	}
	defer out.Close()

	if _, err := io.Copy(out, resp.Body); err != nil {
		return fmt.Errorf("failed to write .pb file: %w", err)
	}

	return nil
}
