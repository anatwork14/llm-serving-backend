# LLM Serving — Quick Start

A Windows + Docker Desktop local LLM stack with:

- **Prism llama.cpp** + Ternary-Bonsai-2-27B on NVIDIA GPU
- **FastAPI** OpenAI-compatible gateway on port `8000`
- **PostgreSQL + pgvector** for memory/RAG
- **Open WebUI** on port `3000`
- optional **Tailscale Serve/Funnel** for remote access

## Architecture

~~~text
Client
  |
  | OpenAI-compatible API
  v
FastAPI :8000
  |
  +--> PostgreSQL / pgvector
  |
  v
llama.cpp :8080   (Docker-internal only)
  |
  v
Ternary-Bonsai-2-27B
~~~

Do not expose raw llama.cpp `:8080` or PostgreSQL `:5432` directly.

## 1. First-time setup

Requirements:

- Windows 10/11
- Docker Desktop using Linux/WSL2 containers
- NVIDIA driver + supported NVIDIA GPU
- Git
- Tailscale if you want remote access

Clone and setup:

~~~powershell
git clone https://github.com/anatwork14/llm-serving-backend.git
cd llm-serving-backend
.\setup.ps1
~~~

The first run downloads the model and can take significantly longer than later starts.

## 2. Daily start / stop

Start:

~~~powershell
.\start.ps1
~~~

Stop:

~~~powershell
.\stop.ps1
~~~

Check everything:

~~~powershell
.\doctor.ps1
~~~

Useful Docker checks:

~~~powershell
docker compose ps
docker compose logs -f llm
docker compose logs -f backend
~~~

## 3. Local access

Open WebUI:

~~~text
http://127.0.0.1:3000
~~~

FastAPI health:

~~~powershell
curl.exe http://127.0.0.1:8000/health/ready
~~~

Authenticated model list:

~~~powershell
$key = ((Get-Content .env | Where-Object { $_ -like "BACKEND_API_KEY=*" }) -replace "^BACKEND_API_KEY=", "")
curl.exe http://127.0.0.1:8000/v1/models -H "Authorization: Bearer $key"
~~~

The OpenAI-compatible base URL is:

~~~text
http://127.0.0.1:8000/v1
~~~

Model alias:

~~~text
bonsai-2-27b
~~~

## 4. Recommended public access: Tailscale Funnel

For public Internet access, the simplest setup is to keep FastAPI bound to localhost and let **Tailscale Funnel** provide the public HTTPS endpoint.

First ensure direct Windows/LAN exposure is disabled:

~~~powershell
.\public-api.ps1 -Disable
~~~

Start Funnel:

~~~powershell
tailscale funnel --bg 8000
~~~

Check the public URL:

~~~powershell
tailscale funnel status
~~~

You should get a URL similar to:

~~~text
https://your-pc.your-tailnet.ts.net
~~~

Your public OpenAI-compatible base URL becomes:

~~~text
https://your-pc.your-tailnet.ts.net/v1
~~~

Clients still authenticate with `BACKEND_API_KEY` from `.env`.

Example:

~~~powershell
curl.exe https://your-pc.your-tailnet.ts.net/v1/models -H "Authorization: Bearer YOUR_BACKEND_API_KEY"
~~~

Stop all Funnel exposure:

~~~powershell
tailscale funnel reset
~~~

Tailscale Funnel gives you a public HTTPS URL without router port forwarding or a public/static IPv4. Funnel requires Tailscale Funnel to be enabled for the tailnet; the CLI will guide you through enabling it when needed.

Official docs:

- https://tailscale.com/docs/features/tailscale-funnel
- https://tailscale.com/docs/reference/tailscale-cli/funnel

## 5. Private remote access: Tailscale Serve

If only devices inside your Tailscale network should connect, use **Tailscale Serve** instead of Funnel.

The existing setup/start scripts already support Tailscale Serve for Open WebUI.

Check status:

~~~powershell
tailscale serve status
~~~

Serve is private to your tailnet; Funnel is public to the Internet.

## 6. Alternative: expose port 8000 directly

If you intentionally want direct LAN/router access:

Open **PowerShell as Administrator**:

~~~powershell
.\public-api.ps1 -Port 8000
~~~

This:

- sets `API_BIND_ADDRESS=0.0.0.0`
- publishes FastAPI on Windows port `8000`
- adds a Windows Firewall rule on Private networks
- recreates the backend
- smoke-tests `/v1/models`

Check:

~~~powershell
docker compose ps
~~~

Expected backend mapping:

~~~text
0.0.0.0:8000->8000/tcp
~~~

Find the Windows LAN IP:

~~~powershell
Get-NetIPAddress -AddressFamily IPv4
~~~

Then another machine on the LAN can use:

~~~text
http://WINDOWS_LAN_IP:8000/v1
~~~

with:

~~~text
Authorization: Bearer <BACKEND_API_KEY>
~~~

To make this reachable from the wider Internet, your router must:

1. reserve the Windows PC's LAN IP with DHCP reservation;
2. forward TCP port `8000` to that LAN IP;
3. have a usable public IPv4 rather than blocking inbound traffic behind CGNAT.

Direct `http://PUBLIC_IP:8000` is **not encrypted**. Prefer Tailscale Funnel or another HTTPS reverse proxy for long-lived Internet exposure.

Disable direct exposure:

~~~powershell
.\public-api.ps1 -Disable
~~~

## 7. OpenAI Python example

~~~python
from openai import OpenAI

client = OpenAI(
    base_url="https://your-pc.your-tailnet.ts.net/v1",
    api_key="YOUR_BACKEND_API_KEY",
)

response = client.chat.completions.create(
    model="bonsai-2-27b",
    messages=[
        {"role": "user", "content": "Hello from another computer"}
    ],
)

print(response.choices[0].message.content)
~~~

For local/LAN use, replace `base_url` with the appropriate `http://...:8000/v1` URL.

## 8. Important secrets

Local secrets are stored in:

~~~text
.env
~~~

Important keys:

~~~text
BACKEND_API_KEY  -> external FastAPI / OpenAI-compatible clients
ADMIN_API_KEY    -> admin endpoints
LLAMA_API_KEY    -> internal FastAPI -> llama.cpp authentication
~~~

Use **BACKEND_API_KEY** for external API clients.

Never commit `.env`.

## 9. Common troubleshooting

### Docker Linux engine unavailable

~~~powershell
docker info --format "{{.OSType}}"
~~~

Expected:

~~~text
linux
~~~

If Docker Desktop is stuck:

~~~powershell
wsl --shutdown
~~~

Then restart Docker Desktop.

### Backend is not ready

~~~powershell
docker compose ps
docker compose logs --tail=100 backend llm
.\doctor.ps1
~~~

### Check the model directly through the gateway

~~~powershell
$key = ((Get-Content .env | Where-Object { $_ -like "BACKEND_API_KEY=*" }) -replace "^BACKEND_API_KEY=", "")
curl.exe http://127.0.0.1:8000/v1/models -H "Authorization: Bearer $key"
~~~

## Recommended deployment

For this project, the preferred public path is:

~~~text
Internet
   |
   | HTTPS
   v
Tailscale Funnel (*.ts.net)
   |
   v
127.0.0.1:8000
FastAPI + BACKEND_API_KEY
   |
   v
Docker network
   |
   +--> PostgreSQL
   |
   v
llama.cpp :8080
~~~

This keeps the raw model server and database private while exposing only the authenticated OpenAI-compatible gateway.
