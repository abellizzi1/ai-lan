# Private AI Gateway

TODOs:
- remove unneeded things (Traefik?)
- add tracking of tokens used per user
- admin is the only person who can create an account - leave this?
- dns setup in personal router

Self-hosted LAN AI gateway for OpenAI-compatible APIs, with household accounts, request limits, and usage logging. Terraform deploys the web UI, gateway, PostgreSQL, and ingress to a local k3d cluster. Inference uses OpenAI by default or optional in-cluster Ollama.

The only required host application is Docker running Linux containers. Terraform, k3d, kubectl, Docker CLI, and every deployment script run inside the provided Ubuntu image; there are no PowerShell scripts or host-language dependencies.

This is a starter stack for personal use, not a production security boundary. It provides local username/password accounts, but currently serves over HTTP without TLS; use it only on a trusted home network until HTTPS is configured.

## Deployment flow

There are two layers of containers:

1. The `infra` Ubuntu container is the command runner. It contains Terraform, k3d, kubectl, and Docker CLI.
2. When Terraform runs, it asks the Docker daemon to create the K3s node containers, build the FastAPI image, and deploy the Kubernetes resources into that K3s cluster.

The scripts Terraform calls are all in `scripts/` and run in this order:

1. `create-cluster.sh` creates (or reuses) the `ai-lan` K3s cluster and writes `.kubeconfig`.
2. `build-image.sh` builds `services/gateway` and imports that image into K3s. K3s has its own image store, distinct from Docker's local image list.
3. `deploy.sh` creates the Kubernetes secret, applies the manifests, waits for PostgreSQL, then waits for the gateway.

If Docker Desktop removes or resets the local K3s containers, `deploy.sh` detects the missing server container, recreates the local cluster, and re-imports the gateway image before applying the application.

Nothing needs to be installed outside Docker Desktop. Start by building the runner:

```bash
docker compose build infra
docker compose run --rm infra bash
```

The second command opens a shell *inside Ubuntu*. Run every remaining command in that shell.

## Values you need

Set the gateway and database values inside the Ubuntu shell. For hosted inference, also set the provider key; it can be skipped when using Ollama. Terraform automatically maps a `TF_VAR_name` environment variable to its matching Terraform input variable.

```bash
# Initial administrator password (at least 12 characters). Keep it safe.
export TF_VAR_gateway_api_key="$(openssl rand -hex 24)"
export TF_VAR_gateway_admin_username='admin'

# A password you create. It is used only between the gateway and internal PostgreSQL.
# Hex avoids URL-escaping issues in the database connection string.
export TF_VAR_postgres_password="$(openssl rand -hex 24)"

# For hosted inference, copy this from your provider's API-key page.
# The default provider is OpenAI. Skip this when enabling local Ollama.
export TF_VAR_provider_api_key='paste-provider-key-here'
```

For the default OpenAI provider, create a normal project API key in the [OpenAI Platform API keys page](https://platform.openai.com/api-keys), then paste that value as `TF_VAR_provider_api_key`. Do not use an organization Admin key for this application. Other providers have an equivalent API-key page in their dashboard. For another OpenAI-compatible provider, also set `TF_VAR_provider_api_base` to its documented API base URL.

The generated values are not displayed. Save the administrator password in a password manager; you will use it to sign in to the website. The PostgreSQL password normally never needs to be used again.

```bash
printf 'Administrator password: %s\n' "$TF_VAR_gateway_api_key"
```

Do not commit any of these values to a file in this repository.

## Deploy

Still inside the Ubuntu shell, initialize Terraform and apply the configuration:

```bash
terraform init
terraform apply
```

Terraform will show its plan and ask for confirmation. On completion it prints the website and API URLs, using `YOUR_LAPTOP_LAN_IP` as a reminder to substitute the actual LAN address reported by your router.

The default provider endpoint is OpenAI's `https://api.openai.com/v1`. To use another OpenAI-compatible hosted provider, set its documented API base URL:

```bash
terraform apply \
  -var='provider_api_base=https://provider.example/v1'
```

To run inference locally with Ollama instead, enable it during apply. The first deployment downloads the configured model (about 292 MB); no hosted provider key is needed:

```bash
terraform apply -var='enable_ollama=true'
```

To switch back to hosted inference, apply with `-var='enable_ollama=false'` (the default) and set `TF_VAR_provider_api_key` again if it is not already set in the shell.

The website uses `gemma3:270m` when Ollama is enabled and `gpt-4o-mini` with hosted inference. Set `-var='default_model=MODEL_NAME'` to choose another model. API clients may omit `model` to use the same default.

The service is published by Docker Desktop on port `8080` by default. From a device on your Wi-Fi, open `http://YOUR_LAPTOP_LAN_IP:8080` and sign in with the administrator username and password. The administrator can create separate household accounts in the page. Chat API requests use the login session cookie.

The website keeps separate conversations in the current browser and sends earlier messages with each reply so the model has context. These chats are stored on that device; they do not sync between devices or live in PostgreSQL.

```bash
curl -c cookies.txt -X POST http://YOUR_LAPTOP_LAN_IP:8080/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"YOUR_ADMIN_PASSWORD"}'
curl -b cookies.txt http://YOUR_LAPTOP_LAN_IP:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Hello"}]}'
```

## Operations

All commands below run from the same Ubuntu shell, where the `TF_VAR_*` values are still set:

```bash
kubectl --kubeconfig /workspace/.kubeconfig get pods -n ai-lan
kubectl --kubeconfig /workspace/.kubeconfig logs deployment/gateway -n ai-lan
```

PostgreSQL uses a Kubernetes PVC while the cluster is running. A PVC is cluster-local in this k3d setup, so `./scripts/teardown.sh` first creates a timestamped PostgreSQL dump in the project `backups/` folder, then destroys the cluster. The dump is stored on the Windows host through the project mount and is ignored by Git. If backup creation fails, teardown stops before destroying the cluster. On the next `terraform apply`, the newest dump in `backups/` is restored into the new PVC before the gateway starts. Keep a copy of important dumps somewhere outside this project too. To start with a fresh database, move the dumps out of `backups/` before deploying.

## How the container works

`compose.yaml` mounts the Docker daemon socket into the Ubuntu `infra` container. This lets its Docker CLI create the K3s node containers and build the gateway image without installing Terraform or Kubernetes tools on the host. The runner joins k3d's private Docker network to reach the K3s API; only the web/API ingress is published on host port `8080`.
