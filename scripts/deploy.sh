#!/bin/bash
set -eu

cluster_name="$1"
http_port="$2"
provider_api_base="$3"
enable_ollama="${4:-false}"
default_model="$5"
project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
kubeconfig="$project_root/.kubeconfig"

: "${GATEWAY_API_KEY:?GATEWAY_API_KEY must be supplied by Terraform}"
: "${GATEWAY_ADMIN_USERNAME:=admin}"
: "${PROVIDER_API_KEY:=}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be supplied by Terraform}"

if [ "$enable_ollama" != "true" ] && [ -z "${PROVIDER_API_KEY//[[:space:]]/}" ]; then
  echo "Set provider_api_key for hosted inference, or set enable_ollama=true to use local Ollama." >&2
  exit 1
fi

# Terraform may retain the local resource in state after Docker Desktop has
# removed its k3d containers. Recreate that local cluster before deploying.
server_container="k3d-$cluster_name-server-0"
if ! docker inspect "$server_container" >/dev/null 2>&1; then
  echo "K3s server container is missing; recreating local cluster $cluster_name."
  bash "$project_root/scripts/create-cluster.sh" "$cluster_name" "$http_port"
fi
# K3s has a separate containerd image store. Importing is harmless when the
# image already exists there and is required after any local cluster rebuild.
k3d image import local/ai-gateway:dev --cluster "$cluster_name"

# Every `docker compose run` starts a fresh runner container. Attach this runner
# to the existing cluster network and refresh its internal-only kubeconfig.
runner_id=$(cat /etc/hostname)
cluster_network_id=$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.NetworkID}}{{end}}' "$server_container")
if [ -z "$cluster_network_id" ]; then
  echo "Could not find the Docker network for $server_container" >&2
  exit 1
fi
if ! docker network inspect "$cluster_network_id" --format '{{json .Containers}}' | grep -q "$runner_id"; then
  docker network connect "$cluster_network_id" "$runner_id"
fi
k3d kubeconfig get "$cluster_name" > "$kubeconfig"
sed -i "s#server: https://[^[:space:]]*#server: https://k3d-$cluster_name-server-0:6443#" "$kubeconfig"

kubectl --kubeconfig "$kubeconfig" config use-context "k3d-$cluster_name"
kubectl --kubeconfig "$kubeconfig" wait --for=condition=Ready nodes --all --timeout=120s
kubectl --kubeconfig "$kubeconfig" apply -f "$project_root/k8s/base/namespace.yaml"

kubectl --kubeconfig "$kubeconfig" create secret generic gateway-secrets \
  --namespace ai-lan \
  --from-literal="gateway-api-key=$GATEWAY_API_KEY" \
  --from-literal="gateway-admin-username=$GATEWAY_ADMIN_USERNAME" \
  --from-literal="provider-api-key=$PROVIDER_API_KEY" \
  --from-literal="postgres-password=$POSTGRES_PASSWORD" \
  --from-literal="provider-api-base=$provider_api_base" \
  --from-literal="default-model=$default_model" \
  --dry-run=client -o yaml | kubectl --kubeconfig "$kubeconfig" apply -f -

kubectl --kubeconfig "$kubeconfig" apply --namespace ai-lan -f "$project_root/k8s/base/postgres.yaml"
kubectl --kubeconfig "$kubeconfig" rollout status deployment/postgres --namespace ai-lan --timeout=180s

# PVCs belong to a cluster. After teardown, restore the newest portable dump
# into the new cluster before the gateway starts and initializes its tables.
postgres_pod=$(kubectl --kubeconfig "$kubeconfig" get pod --namespace ai-lan -l app=postgres -o jsonpath='{.items[0].metadata.name}')
table_count=$(kubectl --kubeconfig "$kubeconfig" exec --namespace ai-lan "$postgres_pod" -- psql -U ai_gateway -d ai_gateway -tAc "SELECT COUNT(*) FROM pg_tables WHERE schemaname='public' AND tablename NOT LIKE 'pg_%'")
if [ "$table_count" = "0" ]; then
  backup_dir="$project_root/backups"
  latest_backup=$(ls -1t "$backup_dir"/postgres-*.dump 2>/dev/null | head -n 1 || true)
  if [ -n "$latest_backup" ]; then
    echo "Restoring PostgreSQL from $(basename "$latest_backup")."
    kubectl --kubeconfig "$kubeconfig" exec --namespace ai-lan -i "$postgres_pod" -- pg_restore -U ai_gateway -d ai_gateway --no-owner --exit-on-error < "$latest_backup"
  else
    echo "No PostgreSQL backup found; starting with a fresh database."
  fi
fi

kubectl --kubeconfig "$kubeconfig" apply -k "$project_root/k8s/base"
if [ "$enable_ollama" = "true" ]; then
  kubectl --kubeconfig "$kubeconfig" apply --namespace ai-lan -f "$project_root/k8s/base/ollama.yaml"
else
  kubectl --kubeconfig "$kubeconfig" delete --namespace ai-lan -f "$project_root/k8s/base/ollama.yaml" --ignore-not-found
fi
if [ "$enable_ollama" = "true" ]; then
  # The first deployment downloads the local model (about 292 MB).
  kubectl --kubeconfig "$kubeconfig" rollout status deployment/ollama --namespace ai-lan --timeout=600s
fi
kubectl --kubeconfig "$kubeconfig" rollout restart deployment/gateway deployment/frontend --namespace ai-lan
kubectl --kubeconfig "$kubeconfig" rollout status deployment/gateway --namespace ai-lan --timeout=180s
echo "Application is ready."
