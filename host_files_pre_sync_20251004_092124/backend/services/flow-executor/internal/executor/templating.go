package executor

import (
	"fmt"
	"regexp"
	"strings"
)

// RenderTemplate mengganti placeholder seperti {{input.message}} menjadi value dari input map.
// Bisa menangani nested key seperti input.message → dicari di data["input"]["message"].
func RenderTemplate(input map[string]interface{}, data map[string]interface{}) map[string]interface{} {
	// DEBUG: Print context and template
	fmt.Printf("DEBUG RenderTemplate - Input: %+v\n", input)
	fmt.Printf("DEBUG RenderTemplate - Data: %+v\n", data)
	
	re := regexp.MustCompile(`\{\{\s*([a-zA-Z0-9_\.]+)\s*\}\}`)
	rendered := make(map[string]interface{})
	for key, val := range input {
		switch str := val.(type) {
		case string:
			matches := re.FindAllStringSubmatch(str, -1)
			newVal := str
			for _, match := range matches {
				if len(match) == 2 {
					lookupPath := match[1]
					if replacement, ok := getNestedValue(data, lookupPath); ok {
						newVal = strings.ReplaceAll(newVal, match[0], fmt.Sprintf("%v", replacement))
					}
				}
			}
			rendered[key] = newVal
		default:
			rendered[key] = val
		}
	}
	return rendered
}

// getNestedValue mencari nilai berdasarkan path seperti "input.message" dalam map bersarang.
func getNestedValue(data map[string]interface{}, path string) (interface{}, bool) {
	fmt.Printf("DEBUG getNestedValue - Path: %s\n", path)
	fmt.Printf("DEBUG getNestedValue - Data keys: %v\n", getMapKeys(data))
	
	keys := strings.Split(path, ".")
	var current interface{} = data
	for i, key := range keys {
		fmt.Printf("DEBUG getNestedValue - Step %d, looking for key: %s\n", i, key)
		if m, ok := current.(map[string]interface{}); ok {
			if val, exists := m[key]; exists {
				fmt.Printf("DEBUG getNestedValue - Found: %v\n", val)
				current = val
			} else {
				fmt.Printf("DEBUG getNestedValue - Key not found: %s\n", key)
				return nil, false
			}
		} else {
			fmt.Printf("DEBUG getNestedValue - Not a map: %T\n", current)
			return nil, false
		}
	}
	return current, true
}

func getMapKeys(m map[string]interface{}) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	return keys
}