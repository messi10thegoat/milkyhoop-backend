package executor

import (
	"context"
	"fmt"
	"time"
	
	"github.com/milkyhoop/flow-executor/internal/observer"
	"github.com/milkyhoop/flow-executor/internal/utils"
	"github.com/milkyhoop/flow-executor/internal/ragclient"
)

func ExecuteNode(flow FlowSpec, node Node, input map[string]interface{}) (map[string]interface{}, string, error) {
	start := time.Now()
	var output map[string]interface{}
	var nextID string

	switch node.Hoop {
	case "ShowMenu":
		var err error
		output, err = observer.DummyShowMenu(context.Background(), input)
		if err != nil {
			return nil, "", fmt.Errorf("node %s failed: %w", node.ID, err)
		}
		nextID = node.TruePath

	case "CreateOrder":
		var err error
		output, err = observer.DummyCreateOrder(context.Background(), input)
		if err != nil {
			return nil, "", fmt.Errorf("node %s failed: %w", node.ID, err)
		}
		nextID = node.TruePath

	case "SendNotification":
		var err error
		output, err = observer.DummySendNotification(context.Background(), input)
		if err != nil {
			return nil, "", fmt.Errorf("node %s failed: %w", node.ID, err)
		}
		nextID = node.TruePath

	case "LogComplaint":
		contextMap := flow.ContextToMap()
		rendered := RenderTemplate(node.Parameters, contextMap)
		if rendered["user_id"] == "{{user_id}}" {
			rendered["user_id"] = contextMap["user_id"]
		}
		if rendered["tenant_id"] == "{{tenant_id}}" {
			rendered["tenant_id"] = contextMap["tenant_id"]
		}

		node.Input = rendered

		utils.Log.Debug().Interface("rendered", rendered).Msg("🧪 Rendered result")

		userID, ok := rendered["user_id"].(string)
		if !ok {
			return nil, "", fmt.Errorf("node %s: invalid user_id", node.ID)
		}
		message, ok := rendered["message"].(string)
		if !ok {
			return nil, "", fmt.Errorf("node %s: invalid message", node.ID)
		}

		complaintID, err := observer.LogComplaint(userID, message)
		if err != nil {
			utils.Log.Error().Err(err).Msg("❌ Gagal log complaint")
			return nil, "", fmt.Errorf("node %s failed: %w", node.ID, err)
		}

		utils.Log.Info().Str("complaint_id", complaintID).Msg("✅ Complaint berhasil dikirim")

		rendered["complaint_id"] = complaintID
		output = rendered
		nextID = node.TruePath


	case "rag_query":
		contextMap := flow.ContextToMap()
		rendered := RenderTemplate(node.Parameters, contextMap)

		query, ok := rendered["query"].(string)
		if !ok {
			return nil, "", fmt.Errorf("node %s: invalid or missing query", node.ID)
		}
		tenantID, ok := rendered["tenant_id"].(string)
		if !ok {
			return nil, "", fmt.Errorf("node %s: invalid or missing tenant_id", node.ID)
		}

		utils.Log.Info().
			Str("query", query).
			Str("tenant_id", tenantID).
			Msg("🔍 Menjalankan RAG query")

		answer, err := observer.QueryRAG(query, tenantID)
		if err != nil {
			return nil, "", fmt.Errorf("node %s: RAG query failed: %w", node.ID, err)
		}

		output = map[string]interface{}{
			"answer": answer,
		}
		nextID = node.TruePath


	case "rag_search_faq":
        contextMap := flow.ContextToMap()
        rendered := RenderTemplate(node.Parameters, contextMap)
        query, ok := rendered["query"].(string)
        if !ok {
                return nil, "", fmt.Errorf("node %s: invalid or missing query", node.ID)
        }
        tenantID, ok := rendered["tenant_id"].(string)
        if !ok {
                return nil, "", fmt.Errorf("node %s: invalid or missing tenant_id", node.ID)
        }
        utils.Log.Info().
                Str("query", query).
                Str("tenant_id", tenantID).
                Msg("🔍 Searching FAQ database directly")
                
        // Use ragclient.QueryRAG yang search database langsung
        answer, err := ragclient.QueryRAG(query, tenantID)
        if err != nil {
                return nil, "", fmt.Errorf("node %s: FAQ search failed: %w", node.ID, err)
        }
        output = map[string]interface{}{
                "answer": answer,
        }
        nextID = node.TruePath



		
	case "rag_llm":
		contextMap := flow.ContextToMap()
		rendered := RenderTemplate(node.Parameters, contextMap)

		query, ok := rendered["query"].(string)
		if !ok {
			return nil, "", fmt.Errorf("node %s: invalid or missing query", node.ID)
		}
		tenantID, ok := rendered["tenant_id"].(string)
		if !ok {
			return nil, "", fmt.Errorf("node %s: invalid or missing tenant_id", node.ID)
		}

		utils.Log.Info().
			Str("query", query).
			Str("tenant_id", tenantID).
			Msg("🧠 Menjalankan RAG LLM")

		answer, err := observer.QueryRAGLLM(query, tenantID)
		if err != nil {
			return nil, "", fmt.Errorf("node %s: RAG LLM failed: %w", node.ID, err)
		}

		output = map[string]interface{}{
			"answer": answer,
		}
		nextID = node.TruePath




	case "rag_crud_update":
        contextMap := flow.ContextToMap()
        rendered := RenderTemplate(node.Parameters, contextMap)

        id, ok := rendered["id"].(float64) // JSON numbers come as float64
        if !ok {
                return nil, "", fmt.Errorf("node %s: invalid or missing id", node.ID)
        }
        title, ok := rendered["title"].(string)
        if !ok {
                return nil, "", fmt.Errorf("node %s: invalid or missing title", node.ID)
        }
        content, ok := rendered["content"].(string)
        if !ok {
                return nil, "", fmt.Errorf("node %s: invalid or missing content", node.ID)
        }

        utils.Log.Info().
                Int32("id", int32(id)).
                Str("title", title).
                Msg("🔄 Menjalankan RAG CRUD update")

        result, err := ragclient.UpdateRAGDocument(int32(id), title, content)
        if err != nil {
                return nil, "", fmt.Errorf("node %s: RAG CRUD update failed: %w", node.ID, err)
        }

        output = map[string]interface{}{
                "result": result,
        }
        nextID = node.TruePath



	case "rag_crud_delete":
        contextMap := flow.ContextToMap()
        rendered := RenderTemplate(node.Parameters, contextMap)

        id, ok := rendered["id"].(float64)
        if !ok {
                return nil, "", fmt.Errorf("node %s: invalid or missing id", node.ID)
        }

        utils.Log.Info().
                Int32("id", int32(id)).
                Msg("🗑️ Menjalankan RAG CRUD delete")

        result, err := ragclient.DeleteRAGDocument(int32(id))
        if err != nil {
                return nil, "", fmt.Errorf("node %s: RAG CRUD delete failed: %w", node.ID, err)
        }

        output = map[string]interface{}{
                "result": result,
        }
        nextID = node.TruePath


	case "rag_crud_update_search":
        contextMap := flow.ContextToMap()
        rendered := RenderTemplate(node.Parameters, contextMap)

        tenantID, ok := rendered["tenant_id"].(string)
        if !ok {
                return nil, "", fmt.Errorf("node %s: invalid or missing tenant_id", node.ID)
        }
        searchContent, ok := rendered["search_content"].(string)
        if !ok {
                return nil, "", fmt.Errorf("node %s: invalid or missing search_content", node.ID)
        }
        newContent, ok := rendered["new_content"].(string)
        if !ok {
                return nil, "", fmt.Errorf("node %s: invalid or missing new_content", node.ID)
        }

        utils.Log.Info().
                Str("tenant_id", tenantID).
                Str("search_content", searchContent).
                Msg("🔍 Menjalankan RAG CRUD update by search")

        result, err := ragclient.UpdateRAGDocumentBySearch(tenantID, searchContent, newContent)
        if err != nil {
                return nil, "", fmt.Errorf("node %s: RAG CRUD update by search failed: %w", node.ID, err)
        }

        output = map[string]interface{}{
                "result": result,
        }
        nextID = node.TruePath



	case "rag_crud_create":
		contextMap := flow.ContextToMap()
		rendered := RenderTemplate(node.Parameters, contextMap)

		tenantID, ok := rendered["tenant_id"].(string)
		if !ok {
			return nil, "", fmt.Errorf("node %s: invalid or missing tenant_id", node.ID)
		}
		title, ok := rendered["title"].(string)
		if !ok {
			return nil, "", fmt.Errorf("node %s: invalid or missing title", node.ID)
		}
		content, ok := rendered["content"].(string)
		if !ok {
			return nil, "", fmt.Errorf("node %s: invalid or missing content", node.ID)
		}

		utils.Log.Info().
			Str("tenant_id", tenantID).
			Str("title", title).
			Msg("📝 Menjalankan RAG CRUD create")

		result, err := ragclient.CreateRAGDocument(tenantID, title, content)
		if err != nil {
			return nil, "", fmt.Errorf("node %s: RAG CRUD create failed: %w", node.ID, err)
		}

		output = map[string]interface{}{
			"result": result,
		}
		nextID = node.TruePath













	
	
	
	
	
	
	case "SendBotReply":
		var err error
		output, err = observer.HandleSendBotReply(context.Background(), input)
		if err != nil {
			return nil, "", fmt.Errorf("node %s failed: %w", node.ID, err)
		}
		nextID = node.TruePath

	default:
		utils.Log.Warn().
			Str("hoop", node.Hoop).
			Msg("⚠️ Unknown hoop. Skipping...")
		return nil, "", fmt.Errorf("node %s: unknown hoop %s", node.ID, node.Hoop)
	}

	duration := time.Since(start).Seconds()
	observer.NodeExecutionDuration.WithLabelValues(node.ID, node.Hoop).Observe(duration)
	return output, nextID, nil
}

func ExecuteIfNode(flow FlowSpec, node Node, input map[string]interface{}, outputs map[string]map[string]interface{}) (string, error) {
	field, ok := input["field"].(string)
	if !ok {
		return "", fmt.Errorf("IfNode %s: invalid field type", node.ID)
	}
	operator, ok := input["operator"].(string)
	if !ok {
		return "", fmt.Errorf("IfNode %s: invalid operator type", node.ID)
	}
	value, ok := input["value"]
	if !ok {
		return "", fmt.Errorf("IfNode %s: missing value", node.ID)
	}

	refOutput, ok := outputs[node.InputFrom]
	if !ok {
		return "", fmt.Errorf("IfNode %s: missing input from node %s", node.ID, node.InputFrom)
	}
	compareVal, exists := refOutput[field]
	if !exists {
		return "", fmt.Errorf("IfNode %s: field %s not found in input from node %s", node.ID, field, node.InputFrom)
	}

	switch operator {
	case "==":
		if compareVal == value {
			return node.TruePath, nil
		}
		return node.FalsePath, nil
	case ">":
		cf, ok1 := compareVal.(float64)
		vf, ok2 := value.(float64)
		if !ok1 || !ok2 {
			return "", fmt.Errorf("IfNode %s: non-numeric value for operator >", node.ID)
		}
		if cf > vf {
			return node.TruePath, nil
		}
		return node.FalsePath, nil
	default:
		utils.Log.Warn().
			Str("operator", operator).
			Msg("⚠️ Unknown operator in IfNode")
		return node.FalsePath, nil
	}
}
