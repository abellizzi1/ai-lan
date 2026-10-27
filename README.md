# LAN AI Gateway

A self-hosted AI chat site and OpenAI-compatible API for a local network. Terraform deploys the frontend, gateway, PostgreSQL, and ingress to a k3d cluster. Inference uses OpenAI by default or an optional in-cluster Ollama server.

This is a personal-use stack. It has local accounts and request limits, but serves over HTTP without TLS. Use it only on a trusted network until HTTPS is configured.

## Setup

A running Docker daemon and the `docker compose` CLI are required on the host. Terraform, k3d, kubectl, and the deployment scripts run inside the `infra` container. From the repository root:

```bash
docker compose build infra
docker compose run --rm infra bash
```

In that shell, choose and save an admin password and a PostgreSQL password, each at least 12 characters. Re-enter them if you open a new `infra` shell.

```bash
read -rsp 'Admin password: ' TF_VAR_gateway_api_key; echo
export TF_VAR_gateway_api_key
export TF_VAR_gateway_admin_username='admin'
read -rsp 'PostgreSQL password: ' TF_VAR_postgres_password; echo
export TF_VAR_postgres_password
```

For hosted inference, also set a provider API key. The default endpoint is OpenAI's `https://api.openai.com/v1`; use a [project API key](https://platform.openai.com/api-keys), not an organization Admin key.

```bash
read -rsp 'Provider API key: ' TF_VAR_provider_api_key; echo
export TF_VAR_provider_api_key
```

For another OpenAI-compatible provider, set `TF_VAR_provider_api_base` to its API base URL. For local Ollama, skip the provider key and use the Ollama apply command below. Do not commit passwords or API keys to this repository.

Initialize and deploy from the same `infra` shell:

```bash
terraform init
terraform apply
```

For local inference, run `terraform apply -var='enable_ollama=true'` instead. The default Ollama model is `gemma3:270m`; hosted inference defaults to `gpt-4o-mini`. Set `TF_VAR_default_model` to choose another model. To switch back to a hosted provider, set its API key and run `terraform apply -var='enable_ollama=false'`.

## Use

Open `http://YOUR_HOST_LAN_IP:8080` from a device on the local network and sign in with the admin username and password. The admin can create other accounts and view request history and provider-reported token usage in **Admin dashboard**. Conversations are saved in PostgreSQL per account. API requests to `/v1/chat/completions` use the login session cookie.

For an API request, sign in first and pass the session cookie:

```bash
curl -c /tmp/ai-gateway-cookies.txt -X POST http://YOUR_HOST_LAN_IP:8080/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"YOUR_ADMIN_PASSWORD"}'
curl -b /tmp/ai-gateway-cookies.txt http://YOUR_HOST_LAN_IP:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Hello"}]}'
```

To check the deployment from the `infra` shell:

```bash
kubectl --kubeconfig /workspace/.kubeconfig get pods -n ai-lan
kubectl --kubeconfig /workspace/.kubeconfig logs deployment/gateway -n ai-lan
```

## Backups and teardown

Run `bash scripts/teardown.sh` from the `infra` shell to back up PostgreSQL and destroy the cluster. The script stops if the backup fails. Dumps are written to the host's `backups/` directory, which Git ignores. Keep a copy elsewhere if the data matters.

On the next `terraform apply`, the newest dump is restored into a fresh database. Move dumps out of `backups/` before deploying if you want an empty database. The Ollama container image is cached by Docker, but its model is stored in a cluster volume and must be downloaded again after teardown.

## How deployment works

The `infra` container mounts the host Docker socket. Terraform calls the scripts in `scripts/` to create the k3d cluster, build and import the gateway image, and apply the Kubernetes manifests. The runner joins the cluster's Docker network to reach the Kubernetes API. The web/API ingress is published on host port `8080` by default.
