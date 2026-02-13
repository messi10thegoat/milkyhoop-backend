# action_planner - MilkyHoop Agentic Accounting Sprint 2

gRPC microservice that generates ActionPlans from user text via LLM classification and parsing.

## Iron Law Compliance

- **Law 0**: This service is a planner, NOT an executor. All writes go through Kernel services.
- **Law 10**: LLM NEVER writes accounting data. Only classifies and parses.

## Architecture

```
User Text --> ClassifyIntent --> GeneratePlan --> ActionPlan (JSON)
                                      |
                              ParseDocumentText
                                      |
                              GenerateResponse
```

## RPCs

| RPC                 | Description                                    | Port |
|---------------------|------------------------------------------------|------|
| ClassifyIntent      | Classify intent: ACTION/READ/CONFIRM/CANCEL    | 5090 |
| GeneratePlan        | Generate structured ActionPlan from intent      | 5090 |
| ParseDocumentText   | Parse free text into invoice/document structure | 5090 |
| GenerateResponse    | Natural conversational response as "Milky"      | 5090 |
| HealthCheck         | Service health + OpenAI connectivity status     | 5090 |

## Environment Variables

| Variable         | Default        | Description              |
|------------------|----------------|--------------------------|
| GRPC_PORT        | 5090           | gRPC server port         |
| OPENAI_API_KEY   | (required)     | OpenAI API key           |
| OPENAI_MODEL     | gpt-4o-mini    | Model for LLM calls      |
| LOG_LEVEL        | INFO           | Logging level            |

## Development

```bash
# Run locally
cd /root/milkyhoop-dev/backend/services/action_planner
python -m app.grpc_server

# Docker
docker build -t action_planner .
docker run -e OPENAI_API_KEY=sk-xxx -p 5090:5090 action_planner
```

## Prompt Versioning

Prompts are versioned in `app/prompts/system_prompt.py` via `PROMPT_REGISTRY`.
Switch active version by changing the `"active"` key. No code changes needed.
