# LAN AI Gateway

A self-hosted AI chat site and OpenAI-compatible API for a local network. Terraform creates the shared k3d cluster and installs Argo CD. Argo CD deploys the frontend, gateway, PostgreSQL, and optional Ollama from Git. Gateway images are built and pushed manually to Docker Hub.

This is a personal-use stack. It has local accounts and request limits, but serves over HTTP without TLS. Use it only on a trusted network until HTTPS is configured.

## Repository layout

| Folder | Purpose |
| --- | --- |
| Root Terraform files, `scripts/`, `compose.yaml`, `Dockerfile.infra` | Shared cluster infrastructure and Argo CD installation; keep the existing Terraform state here. |
| `services/gateway/` | Python application source and Dockerfile; manually build and publish the service image from here. |
| [`ai-lan-config/`](ai-lan-config/README.md) | Standalone application configuration repo: Kubernetes manifests, browser files, provider/model settings, and the deployed image tag. |
| [`cluster-config/`](cluster-config/README.md) | Standalone cluster configuration repo: Argo CD projects and application registrations, including the root application. Future monitoring apps register here. |

The two configuration folders are currently ordinary directories tracked by this repository. No nested Git repositories or remote repositories have been created. Each is self-contained and can later be copied, including its dotfiles, to the root of a separate Git repository. The Argo CD paths below assume each folder becomes a repository root.

## Setup and migration

Existing users: read **Migrating an existing deployment** below before applying Terraform. No cluster creation, deployment, or teardown is performed merely by editing these files.

A running Docker daemon and the `docker compose` CLI are required on the host. Terraform, k3d, kubectl, and the deployment scripts run inside the `infra` container. From the repository root:

```bash
docker compose build infra
docker compose run --rm --service-ports infra bash
```

Inside the runner, initialize the infrastructure. This creates a cluster and installs Argo CD; application deployment starts only after its Git repositories and runtime secrets are ready.

```bash
terraform init
terraform plan
terraform apply
export KUBECONFIG=/workspace/.kubeconfig
```

New clusters use K3s `v1.34.3-k3s1`; Argo CD is pinned to `v3.5.6`. The bootstrap checks for Kubernetes 1.33-1.36, the tested range for Argo CD 3.5. Existing clusters are reused without an automatic version upgrade. `k3s_image` affects new clusters only. Review the [Argo CD installation/version guidance](https://argo-cd.readthedocs.io/en/stable/operator-manual/installation/) and upgrade notes before changing either version.

For each new runner shell, refresh the network connection and kubeconfig:

```bash
bash scripts/connect-cluster.sh
export KUBECONFIG=/workspace/.kubeconfig
```

Use `CLUSTER_NAME` for the scripts and the same Terraform `cluster_name` when using a nondefault cluster name.

### Prepare application secrets and data

Set these in the runner, reusing your existing passwords during migration or restore. Keep credentials out of Git. Hosted inference defaults to OpenAI; use a [project API key](https://platform.openai.com/api-keys).

```bash
export GATEWAY_ADMIN_USERNAME='admin'
export GATEWAY_API_KEY='<your-admin-password>'
export POSTGRES_PASSWORD='<your-postgres-password>'
export PROVIDER_API_KEY='<your-provider-api-key>'
bash scripts/prepare-ai-lan.sh
```

This creates `gateway-secrets` directly in Kubernetes, starts PostgreSQL, and restores the newest `backups/postgres-*.dump` by timestamp if the database is empty. It preserves an existing database. Restore completes before Argo CD is allowed to start the gateway. Move dumps out of `backups/` beforehand if you intentionally want a fresh database. A failed restore rolls back its transaction. The preparation script refuses to run after the Argo CD `ai-lan` Application is registered, to avoid racing a running gateway.

For local Ollama use `export PROVIDER_API_KEY='ollama'` and select `overlays/ollama` in the Argo CD Application. For another hosted provider, edit `provider-api-base` and `default-model` in `ai-lan-config/base/kustomization.yaml`. Ollama overrides those settings in its own overlay. Its first sync downloads `gemma3:270m`; changing that model also requires updating the model pull command and readiness probe in `overlays/ollama/ollama.yaml`.

After registration, use `bash scripts/configure-ai-secrets.sh` to update secrets, followed by `kubectl -n ai-lan rollout restart deployment/gateway`. Updating the PostgreSQL Secret does **not** change an existing database role's password; rotate that password in PostgreSQL as a separate maintenance operation. Old `TF_VAR_gateway_api_key`, `TF_VAR_gateway_admin_username`, `TF_VAR_postgres_password`, and `TF_VAR_provider_api_key` exports are accepted by the secret script as a migration convenience. Provider base/model settings now live in Git.

### Connect Argo CD to Git

Argo CD reads committed files from a reachable Git remote; it cannot watch these local folders. When you publish the configuration folders, replace these placeholders:

| Files | Value to replace |
| --- | --- |
| `cluster-config/bootstrap/project.yaml`, `bootstrap/root-application.yaml` | `https://github.com/REPLACE_ME/cluster-config.git` |
| `cluster-config/clusters/local/ai-lan-project.yaml`, `ai-lan-application.yaml` | `https://github.com/REPLACE_ME/ai-lan-config.git` |

The placeholders are deliberately not real repository settings. Adjust `targetRevision: main` if needed, and push both repositories before registration. If you keep a single repository instead, use its URL everywhere and set the root Application path to `cluster-config/clusters/local` and the AI Application path to `ai-lan-config/overlays/lan` (or `.../ollama`).

For private repositories, first add repository credentials in Argo CD **Settings → Repositories** or create repository credential Secrets directly in Kubernetes. Keep tokens and SSH keys out of these folders. The default manifests assume the gateway image is publicly pullable; see the application configuration README for private Docker Hub images.

After secrets/data are prepared and both Git sources are reachable:

```bash
kubectl apply -k cluster-config/bootstrap
kubectl -n argocd get applications
kubectl -n ai-lan rollout status deployment/gateway --timeout=300s
```

The last command may need to be run after the first Argo sync creates the gateway. The root Application creates the AI project and its child Application. The child automatically syncs and repairs drift, and prunes removed workload resources. Namespace and data PVCs are protected from Argo pruning/deletion. Removing an Application registration leaves its workloads running; explicit decommissioning is required. Root pruning is disabled, so removing a child definition from Git also requires explicitly removing that registration. Protected removed resources appear as requiring pruning until handled intentionally.

### Open Argo CD

From the runner started with `--service-ports`:

```bash
kubectl -n argocd port-forward --address 0.0.0.0 svc/argocd-server 8081:443
```

Open `https://localhost:8081` on the Docker host, accept the initial self-signed certificate, and sign in as `admin`. In another runner shell (`docker compose run --rm infra bash`), reconnect and obtain the initial password:

```bash
bash scripts/connect-cluster.sh
export KUBECONFIG=/workspace/.kubeconfig
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
```

Change the initial password in Argo CD, then delete `argocd-initial-admin-secret`. The Compose port binding exposes this UI only on the Docker host's loopback interface. Application HTTP ingress remains on LAN port `8080`.

### Migrating an existing deployment

Keep Terraform state, the cluster name, and existing credentials. Do not move or delete `terraform.tfstate*`. The resource address `terraform_data.cluster` is unchanged; removing the old `images` and `application` Terraform resources does not delete Kubernetes workloads because they had no destroy provisioners.

1. Before rebuilding the runner, use the existing `infra` image to run `bash scripts/backup-postgres.sh`. Copy the dump somewhere safe. Inspect `kubectl --kubeconfig /workspace/.kubeconfig get nodes` for the Kubernetes version.
2. For a compatible existing cluster (1.33-1.36), rebuild the runner and run `terraform init`, `terraform plan`, and `terraform apply`. The plan should retain `terraform_data.cluster`, remove the old image/application resources from Terraform state, and add the Argo CD bootstrap. Investigate any unexpected cluster destruction before applying.
3. The previous default was Kubernetes 1.31. The new bootstrap refuses that version. To use the documented rebuild route, run `bash scripts/teardown.sh` **intentionally**, using the existing runner first. This backs up AI data and destroys the entire cluster. Then rebuild the runner, run `terraform apply`, and continue with application preparation above to restore the dump. This is a disruptive cluster recreation, not an automatic migration.
4. Supply the same credentials, run `prepare-ai-lan.sh` before registering Argo applications, then configure Git and apply the bootstrap. Existing hosted provider/model settings must be copied into the new ConfigMap. For an existing Ollama deployment select its overlay before the first sync.

Remove obsolete application variables from private `.tfvars` files/CLI flags: `gateway_api_key`, `gateway_admin_username`, `postgres_password`, `provider_api_key`, `provider_api_base`, `default_model`, and `enable_ollama`. Terraform now accepts infrastructure variables only. Do not copy old secret values into configuration YAML.

## Publishing a service update

From the current source repository root, after editing `services/gateway/app/main.py` or other service code:

```bash
docker build -t angelobellizzi/ai-gateway:0.0.2 services/gateway
docker push angelobellizzi/ai-gateway:0.0.2
```

Then change `images[0].newTag` in `ai-lan-config/overlays/lan/kustomization.yaml` from `0.0.1` to `0.0.2`, commit, and push the configuration repository. Both the hosted and Ollama overlays inherit this tag. Argo CD detects the commit and deploys it automatically; allow a few minutes for polling or use **Refresh → Sync** in the UI. Use a new immutable version tag for each build. Rolling back means committing a previously published tag; database schema changes may need a separate recovery plan.

No Terraform apply, image import, or local image build runs during application sync. Browser files live in `ai-lan-config/base/frontend/`; committing them updates the generated ConfigMap and rolls the frontend pods. Provider/model ConfigMap changes also roll gateway pods through Kustomize's generated name hashes.

## Use

### Accessing the website

Open `http://<YOUR_HOST_LAN_IP>:8080` from a device on the local network and sign in with the admin username and password. The admin can create other accounts and view request history and provider-reported token usage in **Admin dashboard**. Conversations are saved in PostgreSQL per account. API requests to `/v1/chat/completions` use the login session cookie.

### Shared conversations

Open a saved conversation, choose **Participants**, and add an existing account by username. Sharing grants access to the full conversation
history and lets participants send messages. Chats remain private until shared.

Only the owner can add or remove other participants or delete the conversation for everyone. Participants can leave through **Participants**.

Only one message can be processed per conversation - sending and participant changes are disabled while the AI responds, and competing API writes receive HTTP 409 rather than being queued. Other conversations remain available. Request limits and token usage apply to the account that sends the message.

### Setting token limits

In **Admin dashboard**, the admin can optionally set an hourly token limit that applies to each account, and an optional limit for an individual account. Leave either field blank and save to clear it. If both are set, the lower limit applies. The gateway counts provider-reported tokens from the previous 60 minutes and rejects new chat and API completion requests when that total reaches the limit. Requests without provider-reported usage do not add to the total, and an in-progress request can finish above the limit. Usage falls out of the rolling window automatically.

### Blocking requests

The admin can block new chat messages and API completions for all accounts or for selected accounts in **Admin dashboard**. These switches work independently: removing the all-users block leaves individual blocks in place. Blocked users can still sign in and read saved conversations, and administrators can still use the dashboard to restore request access. Requests already in progress may finish.

### API requests

For an API request, sign in first and pass the session cookie:

```bash
curl -c /tmp/ai-gateway-cookies.txt -X POST http://<YOUR_HOST_LAN_IP>:8080/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"YOUR_ADMIN_PASSWORD"}'
curl -b /tmp/ai-gateway-cookies.txt http://<YOUR_HOST_LAN_IP>:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Hello"}]}'
```

### Managing the cluster

You can utilize `kubectl` within the `infra` container if cluster management is required.

## Backups and teardown

Run `bash scripts/backup-postgres.sh` from the `infra` container for an online database backup without teardown. Dumps are written to the host's ignored `backups/` directory. Keep a copy elsewhere if the data matters.

For a requested cluster teardown, run `bash scripts/teardown.sh`. It stops the Argo application controller, stops the AI gateway, backs up PostgreSQL, and calls `terraform destroy`. API errors or backup failures abort destruction. If teardown fails or is cancelled, resume with `kubectl -n argocd scale statefulset/argocd-application-controller --replicas=1`; Argo will restore gateway replicas. Before the initial Argo migration, also restore the gateway explicitly with `kubectl -n ai-lan scale deployment/gateway --replicas=1`.

Teardown destroys **every application and local volume in the shared cluster**, including future monitoring workloads. This script only backs up the AI PostgreSQL database. Add backup procedures for other stateful projects before using cluster teardown once those projects are installed. Argo's PVC protection does not protect against cluster deletion. Ollama models must be downloaded again after teardown. After recreation, run `prepare-ai-lan.sh` to restore data **before** applying the Argo bootstrap; Terraform itself no longer restores the database.

## How deployment works

The `infra` container mounts the host Docker socket. Terraform owns cluster creation and Argo CD installation only. Runtime application secrets and initial data restoration are operator steps; Argo CD owns steady-state Kubernetes workloads. The runner joins the cluster's Docker network to reach the Kubernetes API. The web/API ingress is published on host port `8080` by default.

To add the future networking monitoring project, create a separate configuration repository and namespace, then add its AppProject and Application to `cluster-config/clusters/local/kustomization.yaml`. Push that change and the existing root Application registers it. The current AI ingress catches all hostnames at `/`; give the monitoring app an explicit hostname (with LAN DNS/hosts entries) to share port 8080 without route collisions. Namespace boundaries and AppProject permissions should be specific to each app. The cluster configuration repository grants deployment control; restrict its write access to administrators.

## Working with Codex

- `AGENTS.md` provides persistent project context.
- `.codex/config.toml` sets the project defaults for Codex.

Start a new Codex session in this directory to load the project instructions. Project configuration loads when the directory is trusted in Codex.

## Running with Podman

Future effort
