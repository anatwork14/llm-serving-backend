# LLM Serving Backend

Private local AI stack for Windows + NVIDIA GPU.

This repository runs almost everything in Docker:

- Open WebUI
- FastAPI gateway
- PostgreSQL + pgvector
- Prism llama.cpp
- Ternary-Bonsai-2-27B
- persistent model storage

Tailscale stays installed natively on Windows so Dad can reach the browser UI privately.

## Architecture

~~~text
Dad's Windows PC
      |
      | Tailscale
      v
https://your-pc....ts.net
      |
      v
Windows host
      |
      v
Docker Desktop / WSL2
      |
      +-- Open WebUI :3000
      |      |
      |      v
      +-- FastAPI :8000
      |      |
      |      +-- PostgreSQL + pgvector
      |      |
      |      v
      +-- Prism llama.cpp :8080
             |
             v
      Ternary-Bonsai-2-27B
             |
             v
        RTX 3060 12 GB
~~~

Only Open WebUI is exposed through Tailscale. The backend, database, and llama.cpp service stay private.

## Recommended hardware

This repository is preconfigured for:

- Windows 10/11
- NVIDIA RTX 3060 12 GB
- Docker Desktop using the WSL2 backend
- a current NVIDIA Windows driver
- roughly 15 GB of free disk space for the model, images, and persistent data

The default model is:

~~~text
prism-ml/Ternary-Bonsai-2-27B-gguf
Ternary-Bonsai-2-27B-PQ2_0.gguf
~~~

Vision is enabled by default with:

~~~text
Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf
~~~

Default context size is 16,384 tokens.

## First-time setup

### 1. Install these once

Install:

1. Docker Desktop
2. Tailscale
3. Git
4. A current NVIDIA driver

In Docker Desktop, use the WSL2 backend.

Log into Tailscale on your Windows PC.

### 2. Clone and run one command

~~~powershell
git clone https://github.com/anatwork14/llm-serving-backend.git
cd llm-serving-backend
.\setup.ps1
~~~

That script automatically:

- verifies Docker is running
- verifies Docker can see your NVIDIA GPU
- creates .env with random local secrets
- builds the Prism llama.cpp container
- downloads Bonsai 2 into a persistent Docker volume
- downloads the vision projector
- starts PostgreSQL
- starts the FastAPI backend
- starts Open WebUI
- configures Tailscale Serve when Tailscale is installed and connected

The first run downloads roughly 8 GB of model files, so it is much slower than later starts. Interrupted model downloads resume automatically.

When setup finishes, open:

~~~text
http://127.0.0.1:3000
~~~

If Tailscale Serve was configured, setup also prints the private ts.net address.

## Open WebUI accounts

On a fresh install, open Open WebUI and create the first account. The first account becomes the administrator.

Open WebUI normally disables signup after the first account for safety.

To give Dad his own account:

1. Sign in as the administrator.
2. Open Admin Settings.
3. Temporarily enable signup.
4. Let Dad open the private Tailscale URL and create his account.
5. Disable signup again if you want the instance closed to new users.

Because Open WebUI forwards user identity headers to FastAPI, Dad's memory and your memory remain separated.

## Normal daily use

Start everything:

~~~powershell
.\start.ps1
~~~

Stop everything:

~~~powershell
.\stop.ps1
~~~

You can also use Docker Compose directly:

~~~powershell
docker compose up -d
docker compose down
~~~

The model, database, and Open WebUI data are persistent. Stopping containers does not delete them.

## What the FastAPI gateway does

Open WebUI talks only to FastAPI.

FastAPI provides:

- OpenAI-compatible GET /v1/models
- OpenAI-compatible POST /v1/chat/completions
- SSE streaming
- per-user identity
- system instructions
- per-user instructions
- conversation persistence
- rolling summaries
- cross-chat long-term memory
- PostgreSQL + pgvector semantic retrieval
- RAG document retrieval
- tool-definition allowlisting
- structured logging
- separate backend and admin API keys

The request path is:

~~~text
Open WebUI
    |
    v
FastAPI
    |
    +--> user instructions
    +--> conversation summary
    +--> relevant old memories
    +--> relevant RAG chunks
    |
    v
llama.cpp
    |
    v
GPU
~~~

llama.cpp remains an inference server only.

## Docker services

~~~text
llm
  Prism llama.cpp + CUDA
  private port 8080
  GPU: RTX 3060

db
  PostgreSQL + pgvector

backend
  FastAPI
  localhost:8000

open-webui
  browser UI
  localhost:3000
~~~

Inside Docker:

~~~text
Open WebUI -> http://backend:8000/v1
FastAPI    -> http://llm:8080/v1
FastAPI    -> db:5432
~~~

There is no host.docker.internal dependency for model inference.

## Model container

The LLM image uses Prism ML's pinned Linux CUDA llama.cpp release:

~~~text
PRISM_LLAMA_TAG=prism-b10709-9a9394a
PRISM_CUDA_VERSION=12.4
~~~

The model container automatically downloads missing model files into the named Docker volume model-data.

The default server settings are:

~~~text
GPU layers:       99
context:          16384
flash attention:  on
vision:           on
image max tokens: 1024
Jinja tools:      on
~~~

Change them in .env if needed.

For example:

~~~dotenv
LLM_CONTEXT_SIZE=32768
LLM_ENABLE_VISION=false
~~~

Restart the LLM after changing them:

~~~powershell
docker compose up -d --force-recreate llm
~~~

## Persistent data

Docker volumes:

~~~text
model-data
  Bonsai GGUF files

postgres-data
  conversations
  memory
  RAG data
  user instructions

open-webui-data
  Open WebUI users
  chats
  UI settings
~~~

A normal stop keeps all data:

~~~powershell
docker compose down
~~~

Do not run this unless you intentionally want a full reset:

~~~powershell
docker compose down -v
~~~

That deletes model files, database memory, and Open WebUI state.

## Serving and concurrency

The RTX 3060 configuration is intentionally bounded for interactive family use.

Prism llama.cpp runs with:

~~~text
parallel slots:       2
continuous batching:  enabled
prompt cache:          enabled
context pool:          16384 tokens
~~~

FastAPI adds admission control in front of llama.cpp:

~~~text
foreground + background active: 2 max
background active:              1 max
queued requests:                16 max
queue timeout:                  120 seconds
~~~

Foreground chats have priority over Open WebUI utility tasks such as titles,
tags, suggestions, and other requests carrying X-OpenWebUI-Task. Background
requests also run with reasoning disabled. Rolling conversation summaries run
after the user response as low-priority maintenance rather than blocking the
user's request.

These values can be changed in .env:

~~~dotenv
LLM_PARALLEL_SLOTS=2
LLM_MAX_CONCURRENT_REQUESTS=2
LLM_MAX_BACKGROUND_REQUESTS=1
LLM_MAX_QUEUE_SIZE=16
LLM_QUEUE_TIMEOUT_SECONDS=120
~~~

With unified KV cache, the configured context is a shared pool across active
sequences rather than an independent full context allocation for every slot.
Avoid increasing parallel slots just because VRAM appears available; measure
latency and context pressure under realistic concurrent chats first.

The readiness endpoint includes current admission state:

~~~powershell
curl.exe http://127.0.0.1:8000/health/ready
~~~

## Logs

Everything:

~~~powershell
docker compose logs -f
~~~

Model download and inference:

~~~powershell
docker compose logs -f llm
~~~

FastAPI:

~~~powershell
docker compose logs -f backend
~~~

Open WebUI:

~~~powershell
docker compose logs -f open-webui
~~~

## Health checks

FastAPI live check:

~~~powershell
curl.exe http://127.0.0.1:8000/health/live
~~~

Full readiness check:

~~~powershell
curl.exe http://127.0.0.1:8000/health/ready
~~~

Container status:

~~~powershell
docker compose ps
~~~

Run the full no-restart diagnostic:

~~~powershell
.\doctor.ps1
~~~

The doctor checks Docker, container status, backend readiness, GPU visibility,
a small end-to-end gateway -> Prism -> Bonsai completion, and Tailscale status.
Use `.\doctor.ps1 -SkipModelTest` if you only want infrastructure checks.

## System prompt

Edit:

~~~text
config/system_prompt.txt
~~~

Then rebuild the backend:

~~~powershell
docker compose up -d --build backend
~~~

## Per-user instructions

Find the user's Open WebUI ID in backend logs:

~~~powershell
docker compose logs backend
~~~

Then set instructions with the localhost admin API:

~~~powershell
curl.exe -X PUT http://127.0.0.1:8000/admin/users/USER_ID -H "X-Admin-Key: YOUR_ADMIN_API_KEY" -H "Content-Type: application/json" -d "{\"display_name\":\"Dad\",\"instructions\":\"Use simple explanations and reply in Vietnamese when I write in Vietnamese.\"}"
~~~

ADMIN_API_KEY is stored in your local .env file.

## Add a curated memory

~~~powershell
curl.exe -X POST http://127.0.0.1:8000/admin/memories -H "X-Admin-Key: YOUR_ADMIN_API_KEY" -H "Content-Type: application/json" -d "{\"user_id\":\"USER_ID\",\"content\":\"Prefers short Vietnamese explanations.\",\"importance\":0.9,\"source\":\"manual\"}"
~~~

Normal user messages are also persisted and embedded for semantic cross-chat recall.

## Add RAG knowledge

~~~powershell
curl.exe -X POST http://127.0.0.1:8000/admin/documents -H "X-Admin-Key: YOUR_ADMIN_API_KEY" -H "Content-Type: application/json" -d "{\"title\":\"Family notes\",\"content\":\"Put document text here.\",\"source\":\"manual\"}"
~~~

Leave owner_user_id empty for global knowledge, or set it to an Open WebUI user ID for private per-user knowledge.

## Embeddings

Memory and RAG embeddings default to:

~~~text
sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
~~~

They run on CPU so the RTX 3060 remains dedicated to Bonsai.

The embedding model downloads automatically on first use.

Disable semantic embeddings if needed:

~~~dotenv
EMBEDDINGS_ENABLED=false
~~~

The backend then falls back to PostgreSQL lexical search.

## Tailscale

Tailscale remains native on Windows rather than running in Docker.

setup.ps1 and start.ps1 attempt to configure:

~~~powershell
tailscale serve --bg 3000
~~~

Check the private URL:

~~~powershell
tailscale serve status
~~~

Dad only needs:

1. Tailscale running.
2. A browser.
3. Your private ts.net URL.

Do not expose ports 8000, 8080, or 5432 through Tailscale.

## Troubleshooting

### Docker cannot see the RTX 3060

Run:

~~~powershell
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
~~~

If this fails:

- update the NVIDIA Windows driver
- update WSL
- make sure Docker Desktop uses WSL2
- restart Docker Desktop

### First startup looks stuck

Watch:

~~~powershell
docker compose logs -f llm
~~~

The first run downloads several gigabytes before llama.cpp becomes healthy.

### Open WebUI has no model

Check:

~~~powershell
docker compose ps
curl.exe http://127.0.0.1:8000/health/ready
docker compose logs backend
docker compose logs llm
~~~

The model exposed to Open WebUI is:

~~~text
bonsai-2-27b
~~~

## Security

- Open WebUI is bound to Windows localhost.
- Tailscale provides private remote access.
- FastAPI is bound to Windows localhost.
- llama.cpp is not published to Windows.
- PostgreSQL is not published to Windows.
- Open WebUI uses a backend API key.
- FastAPI uses a separate admin API key.
- llama.cpp uses a separate internal API key.
- secrets are generated locally into .env and are gitignored.
- prompts and model responses are not written to application logs.
- retrieved RAG and memory content is marked as untrusted context.
- arbitrary model-generated tools are not executed by the gateway itself.

## Development

~~~powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
ruff check app tests
pytest
~~~
