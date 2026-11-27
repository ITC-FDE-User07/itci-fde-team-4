# ITC FDE Team 4

## Run the Week 2 automated tests (Windows PowerShell)

From the repository root, run the script that follows Step 4 of the Week 2 solution guide:

```powershell
.\FDE_Project_Codebase\tools\run_week2_tests.ps1
```

It runs `python -m pytest -q` from each of `vastra-modern-api`, `vastra-data-platform`, and
`vastra-ml-p2`. The script prefers each package's `.venv`, then the shared
`FDE_Project_Codebase\.venv`, the repository `.venv`, and finally Python on `PATH`. Install the
dependencies from the guide before running it. Each run stores one log per package and a
`summary.txt` under a timestamped subfolder in `Test results`. The script runs the remaining
suites if one fails and returns a non-zero exit code if any suite fails.

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

## Hands-on Kubernetes deployment

The local Kubernetes deployment runs the same four services in the `vastra` namespace. The legacy
and modern APIs are intentionally fixed at **one replica each** because they use SQLite. A local
PVC stores the legacy SQLite file shared read-only with the modern API, and a separate PVC stores
the modern API database.

From the repository root, build and import the application images into the local Docker Desktop
Kubernetes cluster:

```powershell
.\FDE_Project_Codebase\kubernetes\load-images.ps1
```

The helper imports the locally built images into the cluster's containerd cache on its control-plane
node and removes its temporary debug pod when finished. The application pods are pinned to that
node so the local images and SQLite storage stay available. Apply the manifests and wait for each
deployment:

```powershell
kubectl apply -f FDE_Project_Codebase\kubernetes\local-stack.yaml
kubectl rollout status deployment/partners -n vastra
kubectl rollout status deployment/legacy -n vastra
kubectl rollout status deployment/modern-api -n vastra
kubectl rollout status deployment/gateway -n vastra
```

In another terminal, forward the gateway and open the same UI and API routes:

```powershell
kubectl port-forward -n vastra service/gateway 8081:8080
```

Use `http://127.0.0.1:8081/ui/` for the modern API workbench and
`http://127.0.0.1:8081/legacy/test-ui/` for the legacy test page. Port 8081 avoids a conflict with
the Docker Compose gateway on 8080. Check status with
`kubectl get pods,services,pvc -n vastra`. Keep both API deployments at one replica for this
SQLite demo. To remove the deployment, run `kubectl delete -f FDE_Project_Codebase\kubernetes\local-stack.yaml`;
deleting the PVCs also removes this local demo's database data.
