# ITC FDE Team 4

## Run the Vastra services in local Docker containers (Windows PowerShell)

Install and start Docker Desktop, then from the repository root run:

```powershell
docker --context default compose up --build
```

This starts the NGINX gateway, legacy monolith, partner stubs, and modern API. The Compose setup
creates the legacy SQLite database on first startup and persists both databases in Docker volumes.
The local `.env` controls partner latency and payment failure rate; if it does not exist, copy the
template first:

```powershell
Copy-Item .env.example .env
```

Access the application through the NGINX gateway:

- Modern API workbench: http://127.0.0.1:8080/ui/
- Modern API docs: http://127.0.0.1:8080/docs
- Modern API health: http://127.0.0.1:8080/v1/health
- Legacy test UI: http://127.0.0.1:8080/legacy/test-ui/
- Gateway health: http://127.0.0.1:8080/health

Stop the containers with `Ctrl+C`, or run `docker --context default compose down` in another
terminal. Database volumes are retained when containers stop. The full architecture and direct
non-container setup are described in the [modern API README](FDE_Project_Codebase/vastra-modern-api/README.md).

This development machine's `desktop-linux` Docker context returned an engine error, so the commands
above explicitly use the working `default` context. On a machine where the current Docker context
is healthy, `docker compose up --build` and `docker compose down` are equivalent.
