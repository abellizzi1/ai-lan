# Project context

LAN AI Gateway (Hearth AI) is a personal, self-hosted AI chat site with local
accounts, saved conversations, an admin dashboard, and an OpenAI-compatible API.
Terraform creates shared k3d infrastructure and installs Argo CD. Argo CD deploys
nginx, a Python gateway, PostgreSQL, and optional Ollama from Git configuration.
The current deployment serves HTTP on a trusted LAN; HTTPS is not configured.

This is a public GitHub repository. Keep credentials, personal machine paths, and
private deployment details out of tracked files. Only share intentional project
settings in `.codex/`; never copy the personal `~/.codex/` directory into the repo.

Use this map to go directly to the files needed for the task. Read the relevant
implementation before changing behavior; a full repository scan is usually
unnecessary. Keep this file concise and update it when these conventions change.

## Where things live

- `README.md`: service image publishing and application usage.
- `services/gateway/app/main.py`: FastAPI app, schema initialization, authentication,
  admin routes, chat persistence, provider calls, and quotas.
- `services/gateway/requirements.txt` and `Dockerfile`: Python dependencies and image.
- Sibling `../ai-lan-config/`: Kubernetes manifests, frontend assets, provider/model
  settings, and the deployed gateway image tag.
- Sibling `../cluster-config/`: starting point for infrastructure, Terraform,
  the infra Dockerfile/Compose runner, Argo registrations, and operator scripts.

## Implementation conventions

- Keep changes consistent with the existing FastAPI/asyncpg and plain JavaScript
  structure. Explain the need before introducing a framework or new dependency.
- Database schema changes currently belong in `lifespan()` in `main.py`. Make
  startup changes idempotent and preserve existing accounts and conversations.
- Authenticate through `current_user()` and require `require_admin()` for admin
  operations. Enforce authorization in backend queries, including chat ownership.
  Conversations have one owner and optional `conversation_members`. Participants
  can read/send; only owners can add/remove others or delete chats. Members can leave.
- Passwords use Argon2. Session cookies contain random tokens; PostgreSQL stores
  their SHA-256 hashes. Preserve these conventions when changing authentication.
- Both chat messages and `/v1/chat/completions` use request blocking and quotas.
  Daily request counts and rolling 60-minute provider-reported token usage are
  separate limits. Keep accounting attributed to the authenticated requester.
- Requests are serialized per user with in-memory asyncio locks. The deployment
  has one gateway replica; those locks do not coordinate multiple processes.
  Conversation writes also use a per-chat lock and reject competing writes with
  HTTP 409. SSE snapshots expose pending prompts and completed turns to authorized
  participants; reconnects reload state and each delivery rechecks session/access.
- Assistant Markdown is rendered and sanitized on the backend. Preserve that
  boundary; render usernames and user text through `textContent` in the browser.
- Provider access is OpenAI-compatible and can target hosted inference or Ollama.
  Responses are currently non-streaming. Codex's model settings in `.codex/` are
  independent of the gateway's `DEFAULT_MODEL` in the application ConfigMap.
- Build/push gateway images manually; deploy by committing an immutable image
  tag to the config repo. Keep secrets outside Git. Preserve Argo PVC protection
  and hashed ConfigMap references so configuration changes roll affected pods.
- Maintain mobile layouts and existing login/admin flows when editing the UI.
- Document user-visible behavior and setup changes in `README.md`.

## Verification

- Validate Python syntax without importing the app or contacting the database/provider.
- For frontend edits in the sibling config repo, run `node --check` on changed JavaScript.
- Run `git diff --check` in changed repositories.
- Infrastructure and manifest checks belong in the sibling `cluster-config` repo.
- Do not write tests unless requested; use relevant syntax/configuration checks.

## Running and deployment

Start from `../cluster-config/README.md`. Its Linux infra runner mounts that repo
at `/workspace`. Initial AI preparation downloads the PostgreSQL manifest from
GitHub; no application config or source checkout is needed to create the cluster.
Build/push the service image manually here and deploy by updating the config repo.
Terraform state, kubeconfig, secrets, private variables, and database backups are
sensitive runtime data kept outside Git in `cluster-config`.
