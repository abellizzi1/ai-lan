# Private AI gateway

TODOs:
- auth
- remove unneeded things (Traefik?)
- hookup model provider endpoint
- add tracking of tokens used per user
- cleanup UI
- firewalls to prevent external connections? research if this is actually needed

This project runs all tooling inside an Ubuntu container. Terraform uses k3d to create K3s containers in Docker, then deploys a web UI, FastAPI gateway, PostgreSQL, and Traefik ingress. The gateway checks a client API key, applies a daily request quota, writes request metadata to PostgreSQL, and forwards requests to an OpenAI-compatible hosted inference API.

The only required host application is Docker Desktop running Linux containers. Terraform, k3d, kubectl, Docker CLI, and every deployment script run inside the provided Ubuntu image; there are no PowerShell scripts or host-language dependencies.

This is a starter stack for personal use, not a production security boundary. It has one shared client key, no user identity provider, and no TLS termination.

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

```sh
docker compose build infra
docker compose run --rm --entrypoint bash infra
```

The second command opens a shell *inside Ubuntu*. Run every remaining command in that shell.

## Values you need

Set these three environment variables inside the Ubuntu shell. Terraform automatically maps a `TF_VAR_name` environment variable to its matching Terraform input variable.

```sh
# A key you create. Browser/API clients will use it in the X-API-Key header.
export TF_VAR_gateway_api_key="$(openssl rand -hex 32)"

# A password you create. It is used only between the gateway and internal PostgreSQL.
# Hex avoids URL-escaping issues in the database connection string.
export TF_VAR_postgres_password="$(openssl rand -hex 24)"

# Copy this from the API-key page of your hosted inference provider.
# With the default configuration, create/copy an OpenAI API key from platform.openai.com.
export TF_VAR_provider_api_key='paste-provider-key-here'
```

For the default OpenAI provider, create a normal project API key in the [OpenAI Platform API keys page](https://platform.openai.com/api-keys), then paste that value as `TF_VAR_provider_api_key`. Do not use an organization Admin key for this application. Other providers have an equivalent API-key page in their dashboard.

The first two commands generated values without displaying them. Print and save the gateway key in a password manager now; it is the value you enter in the website. The PostgreSQL password normally never needs to be used again.

```sh
printf 'Gateway key: %s\n' "$TF_VAR_gateway_api_key"
```

Do not commit any of these values to a file in this repository.

## Deploy

Still inside the Ubuntu shell, initialize Terraform and apply the configuration:

```sh
terraform init
terraform apply
```

Terraform will show its plan and ask for confirmation. On completion it prints the website and API URLs, using `YOUR_LAPTOP_LAN_IP` as a reminder to substitute the actual LAN address reported by your router.

The default provider endpoint is OpenAI's `https://api.openai.com/v1`. For another OpenAI-compatible provider, also provide its documented API base URL:

```sh
terraform apply \
  -var='provider_api_base=https://provider.example/v1'
```

The service is published by Docker Desktop on port `8080` by default. From a device on your Wi-Fi, open `http://YOUR_LAPTOP_LAN_IP:8080`. Enter the gateway key in the page, or call:

```sh
curl http://YOUR_LAPTOP_LAN_IP:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_SAVED_GATEWAY_KEY' \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Hello"}]}'
```

## Operations

All commands below run from the same Ubuntu shell, where the `TF_VAR_*` values are still set:

```sh
kubectl --kubeconfig /workspace/.kubeconfig get pods -n ai-lan
kubectl --kubeconfig /workspace/.kubeconfig logs deployment/gateway -n ai-lan
```

Changing gateway source files then re-running `terraform apply` rebuilds and redeploys the image. To rotate a secret, replace its environment variable, then run `terraform apply -replace=terraform_data.application`. To destroy the cluster, run `terraform destroy` before leaving the Ubuntu shell.

## How the container works

`compose.yaml` mounts the Docker daemon socket into the Ubuntu `infra` container. This lets its Docker CLI create the K3s node containers and build the gateway image without installing Terraform or Kubernetes tools on the host. The runner joins k3d's private Docker network to reach the K3s API; only the web/API ingress is published on host port `8080`.
