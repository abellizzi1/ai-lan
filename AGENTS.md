# Project context

LAN AI Gateway (Hearth AI) is a personal, self-hosted AI chat site with local
accounts, saved conversations, an admin dashboard, and an OpenAI-compatible API.
Terraform deploys nginx, a Python gateway, PostgreSQL, and optional Ollama to k3d.
The current deployment serves HTTP on a trusted LAN; HTTPS is not configured.

This is a public GitHub repository. Keep credentials, personal machine paths, and
private deployment details out of tracked files. Only share intentional project
settings in `.codex/`; never copy the personal `~/.codex/` directory into the repo.

Use this map to go directly to the files needed for the task. Read the relevant
implementation before changing behavior; a full repository scan is usually
unnecessary. Keep this file concise and update it when these conventions change.

## Where things live

- `README.md`: setup, required environment variables, usage, backup and restore.
- `services/gateway/app/main.py`: FastAPI app; startup schema initialization,
  authentication, admin routes, chat persistence, provider calls, and quotas.
- `services/gateway/requirements.txt`: pinned Python dependencies; runtime is
  Python 3.12 (`services/gateway/Dockerfile`).
- `k8s/base/frontend/index.html` and `app.js`: chat UI, styles, and browser logic.
- `k8s/base/frontend/admin.html` and `admin.js`: admin dashboard and its logic.
  The frontend is plain HTML/CSS/JavaScript, with no package manager or build step.
- `k8s/base/`: Kubernetes deployments, services, ingress, and storage.
  `kustomization.yaml` packages the four frontend files into a ConfigMap.
- `main.tf`, `variables.tf`, `outputs.tf`, `versions.tf`: infrastructure entry
  points. Source and manifest hashes trigger image rebuilds and redeployment.
- `scripts/`: create the cluster, build/import the gateway image, deploy, and
  back up PostgreSQL before teardown.
- `compose.yaml` and `Dockerfile.infra`: Linux infrastructure runner with Docker,
  Terraform, k3d, and kubectl; the repository is mounted at `/workspace`.

## Implementation conventions

- Keep changes consistent with the existing FastAPI/asyncpg and plain JavaScript
  structure. Explain the need before introducing a framework or new dependency.
- Database schema changes currently belong in `lifespan()` in `main.py`. Make
  startup changes idempotent and preserve existing accounts and conversations.
- Authenticate through `current_user()` and require `require_admin()` for admin
  operations. Enforce authorization in backend queries, including chat ownership.
  Conversations currently belong to one account; there is no sharing model yet.
- Passwords use Argon2. Session cookies contain random tokens; PostgreSQL stores
  their SHA-256 hashes. Preserve these conventions when changing authentication.
- Both chat messages and `/v1/chat/completions` use request blocking and quotas.
  Daily request counts and rolling 60-minute provider-reported token usage are
  separate limits. Keep accounting attributed to the authenticated requester.
- Requests are serialized per user with in-memory asyncio locks. The deployment
  has one gateway replica; those locks do not coordinate multiple processes.
- Assistant Markdown is rendered and sanitized on the backend. Preserve that
  boundary; render usernames and user text through `textContent` in the browser.
- Provider access is OpenAI-compatible and can target hosted inference or Ollama.
  Responses are currently non-streaming. Codex's model settings in `.codex/` are
  independent of the gateway's `DEFAULT_MODEL` and Terraform `default_model`.
- Maintain mobile layouts and existing login/admin flows when editing the UI.
- Document user-visible behavior and setup changes in `README.md`.

## Verification

Run checks relevant to the change and report what was actually verified:

- Gateway: validate Python syntax without importing the app or contacting the
  database/provider. Check the affected request flow when the app is available.
- Frontend: run `node --check` on each changed JavaScript file. For UI behavior,
  check the affected flow in a browser when the app is available.
- Terraform: run `terraform fmt -check -diff` in the repository root, then
  `terraform validate` when initialized.
- Shell scripts: run `bash -n` on changed scripts. Preserve LF line endings.
- Manifests: render with `kubectl kustomize k8s/base` when kubectl is available.
- Run `git diff --check` before finishing. If tooling is unavailable, report the
  limitation; do not deploy the application merely to validate a small edit.

## Running and deployment

The supported infrastructure workflow runs inside the Linux `infra` container:
`docker compose build infra`, then `docker compose run --rm infra bash`. Follow
the README for environment variables and Terraform commands from `/workspace`.
The Windows host shell is PowerShell; deployment scripts require Bash.

`scripts/deploy.sh` refreshes `.kubeconfig`, creates Kubernetes secrets, restores
the newest `backups/postgres-*.dump` into an empty database, and restarts services.
The namespace is `ai-lan`; the default published HTTP port is `8080`.

For a requested teardown, use `scripts/teardown.sh`, which backs up PostgreSQL
before `terraform destroy`. Cluster volumes disappear on teardown; retain dumps.
Treat `terraform.tfstate*`, `.kubeconfig`, `.env`, `*.tfvars`, and database backups
as sensitive runtime data. Avoid reading or printing them unless needed for the
task, and never copy credentials or database contents into tracked documentation.
