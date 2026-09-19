# LLM Serving Backend

Private OpenAI-compatible FastAPI gateway for a Windows-hosted local LLM stack.

Target setup:

- Windows + Docker Desktop / WSL2
- RTX 3060 12 GB
- Prism ML Ternary-Bonsai-2-27B GGUF
- Prism llama.cpp fork running natively on Windows
- Open WebUI in Docker
- PostgreSQL + pgvector in Docker
- Tailscale Serve for private browser access

## Architecture

~~~text
Dad's browser
      |
      | Tailscale HTTPS
      v
Open WebUI :3000
      |
      | OpenAI-compatible API
      v
FastAPI gateway :8000
      |
      +---- PostgreSQL + pgvector
      |       - users and conversations
      |       - message history
      |       - rolling summaries
      |       - long-term memory
      |       - RAG documents/chunks
      |
      +---- system prompt / per-user instructions
      +---- tool allowlist
      +---- JSON logs
      |
      v
Prism llama.cpp :8080
      |
      v
Ternary-Bonsai-2-27B
      |
      v
RTX 3060 12 GB
~~~

Only Open WebUI should be exposed through Tailscale. FastAPI, PostgreSQL, and llama.cpp stay private.

## What the backend does

For a normal chat request the gateway authenticates Open WebUI, reads the Open WebUI user/chat identity, stores the latest user message, retrieves semantic memory and RAG context, refreshes a rolling summary when needed, injects the base and per-user instructions, forwards the request to llama.cpp, streams SSE tokens back to Open WebUI, and stores the assistant response.

OpenAI tools and tool_choice are forwarded. The gateway can enforce a function-name allowlist, but intentionally does not execute arbitrary model-generated tools itself.

## 1. Start Bonsai 2 on Windows

~~~powershell
git clone https://github.com/PrismML-Eng/Bonsai-demo.git
cd Bonsai-demo
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\setup.ps1
$env:BONSAI_CTX = "16384"
.\scripts\start_llama_server.ps1
~~~

Verify llama.cpp:

~~~powershell
curl.exe http://127.0.0.1:8080/v1/models
~~~

Leave that server running.

## 2. Configure this repository

~~~powershell
git clone https://github.com/anatwork14/llm-serving-backend.git
cd llm-serving-backend
Copy-Item .env.example .env
~~~

Edit .env and replace the secrets:

~~~dotenv
BACKEND_API_KEY=use-a-long-random-secret
ADMIN_API_KEY=use-another-long-random-secret
WEBUI_SECRET_KEY=use-another-long-random-secret
POSTGRES_PASSWORD=use-a-long-db-password
DATABASE_URL=postgresql+asyncpg://llm:YOUR_DB_PASSWORD@db:5432/llm
~~~

Optional secret generator:

~~~powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
~~~

The default upstream URL is already correct for Docker Desktop calling llama.cpp on the Windows host:

~~~dotenv
LLAMA_BASE_URL=http://host.docker.internal:8080/v1
~~~

## 3. Start PostgreSQL, FastAPI, and Open WebUI

~~~powershell
docker compose up --build -d
docker compose ps
~~~

Health checks:

~~~powershell
curl.exe http://127.0.0.1:8000/health/live
curl.exe http://127.0.0.1:8000/health/ready
~~~

Open this in your browser and create the first Open WebUI admin account:

~~~text
http://127.0.0.1:3000
~~~

## Open WebUI connection

Docker Compose preconfigures:

~~~text
Base URL: http://backend:8000/v1
API Key:  value of BACKEND_API_KEY
~~~

If Open WebUI already has a persistent configuration, manually add an OpenAI-compatible connection in Admin Settings -> Connections:

~~~text
URL:     http://backend:8000/v1
API Key: your BACKEND_API_KEY
Model:   bonsai-2-27b
~~~

The compose file enables Open WebUI identity forwarding so the gateway receives user ID, name, role, and chat ID.

For best behavior, add this custom header to that OpenAI-compatible connection:

~~~json
{
  "X-OpenWebUI-Task": "{{TASK}}"
}
~~~

Background tasks such as title generation then bypass personal-memory injection and persistence.

## 4. Direct API test

~~~powershell
curl.exe http://127.0.0.1:8000/v1/models -H "Authorization: Bearer YOUR_BACKEND_API_KEY"
~~~

For normal usage, Open WebUI will call the same API at http://backend:8000/v1.

## 5. Per-user instructions

Example:

~~~powershell
curl.exe -X PUT http://127.0.0.1:8000/admin/users/dad -H "X-Admin-Key: YOUR_ADMIN_API_KEY" -H "Content-Type: application/json" -d "{\"display_name\":\"Dad\",\"instructions\":\"Prefer simple explanations. Reply in Vietnamese when I write in Vietnamese.\"}"
~~~

When requests come from Open WebUI, the actual user ID is normally Open WebUI's generated ID rather than the literal value dad.

See forwarded IDs in the structured logs:

~~~powershell
docker compose logs -f backend
~~~

Use that ID for per-user instructions and curated memory.

## 6. Add a durable memory

~~~powershell
curl.exe -X POST http://127.0.0.1:8000/admin/memories -H "X-Admin-Key: YOUR_ADMIN_API_KEY" -H "Content-Type: application/json" -d "{\"user_id\":\"dad\",\"content\":\"Dad prefers concise answers in Vietnamese.\",\"importance\":0.9,\"source\":\"manual\"}"
~~~

Normal user messages are also stored and embedded so semantically relevant facts can be retrieved from older chats.

## 7. Add RAG knowledge

~~~powershell
curl.exe -X POST http://127.0.0.1:8000/admin/documents -H "X-Admin-Key: YOUR_ADMIN_API_KEY" -H "Content-Type: application/json" -d "{\"title\":\"Home notes\",\"content\":\"Put your document text here.\",\"source\":\"manual\"}"
~~~

Set owner_user_id in the JSON body to make a document visible only to one Open WebUI user. Leave it null for a global document.

The backend chunks and embeds document text automatically.

## 8. Tailscale

After Open WebUI works locally:

~~~powershell
tailscale serve 3000
tailscale serve status
~~~

Dad opens the generated private HTTPS ts.net URL while Tailscale is running on his PC.

Do not expose ports 8000, 8080, or 5432.

## Memory design

### Recent context

The newest RECENT_MESSAGE_LIMIT non-system messages stay verbatim.

### Rolling summary

After SUMMARY_TRIGGER_MESSAGES, older messages are compacted into a factual summary while SUMMARY_KEEP_RECENT newest messages remain outside the summary.

### Long-term memory

User messages are embedded and semantically retrieved from older conversations. Curated memories can also be created through the admin API.

## RAG and embeddings

Default embedding model:

~~~text
sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
~~~

It runs on CPU by default, leaving the RTX 3060 for Bonsai/llama.cpp.

The first semantic-memory or RAG request downloads the embedding model. For fully offline use, pre-cache it first.

Disable semantic embeddings and use PostgreSQL lexical retrieval instead:

~~~dotenv
EMBEDDINGS_ENABLED=false
~~~

## Tool permissions

Empty means forward all OpenAI tool definitions:

~~~dotenv
ALLOWED_TOOL_NAMES=
~~~

Restrict tools with a comma-separated allowlist:

~~~dotenv
ALLOWED_TOOL_NAMES=weather,search_documents
~~~

The backend does not execute arbitrary functions itself.

## Ports

| Port | Service | Exposure |
| --- | --- | --- |
| 3000 | Open WebUI | localhost + Tailscale Serve |
| 8000 | FastAPI gateway | localhost only |
| 8080 | Prism llama.cpp | Windows host / Docker host bridge |
| 5432 | PostgreSQL | Docker network only |

## API overview

OpenAI-compatible:

- GET /v1/models
- POST /v1/chat/completions

These require Authorization: Bearer BACKEND_API_KEY.

Admin:

- GET /admin/users/{user_id}
- PUT /admin/users/{user_id}
- GET /admin/memories?user_id=...
- POST /admin/memories
- DELETE /admin/memories/{id}
- GET /admin/documents
- POST /admin/documents
- DELETE /admin/documents/{id}

Admin endpoints require X-Admin-Key.

Health:

- GET /health/live
- GET /health/ready

## Useful commands

~~~powershell
docker compose logs -f backend
docker compose restart backend
docker compose up --build -d
docker compose down
~~~

Delete all persistent Open WebUI and database data:

~~~powershell
docker compose down -v
~~~

Be careful: that deletes memory, RAG data, database state, and Open WebUI data.

## Security notes

- FastAPI and Open WebUI bind to 127.0.0.1 on the Windows host.
- PostgreSQL is not published to the host.
- llama.cpp should remain private.
- Only Open WebUI port 3000 should be Tailscale-served.
- Prompt and response bodies are not logged.
- Retrieved memory and RAG content is marked as untrusted context.
- Tool definitions can be allowlisted and are not executed by the gateway.

## Development

~~~powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
ruff check app tests
pytest
~~~
