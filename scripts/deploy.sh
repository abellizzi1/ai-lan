#!/bin/bash
set -eu

cluster_name="$1"
http_port="$2"
provider_api_base="$3"
project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
kubeconfig="$project_root/.kubeconfig"

: "${GATEWAY_API_KEY:?GATEWAY_API_KEY must be supplied by Terraform}"
: "${PROVIDER_API_KEY:?PROVIDER_API_KEY must be supplied by Terraform}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be supplied by Terraform}"

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
  --from-literal="provider-api-key=$PROVIDER_API_KEY" \
  --from-literal="postgres-password=$POSTGRES_PASSWORD" \
  --from-literal="provider-api-base=$provider_api_base" \
  --dry-run=client -o yaml | kubectl --kubeconfig "$kubeconfig" apply -f -

kubectl --kubeconfig "$kubeconfig" apply -k "$project_root/k8s/base"
kubectl --kubeconfig "$kubeconfig" rollout status deployment/postgres --namespace ai-lan --timeout=180s
# The first deployment downloads the local model (about 292 MB). Do not report
# the application ready until the model can actually receive requests.
kubectl --kubeconfig "$kubeconfig" rollout status deployment/ollama --namespace ai-lan --timeout=600s
kubectl --kubeconfig "$kubeconfig" rollout restart deployment/gateway deployment/frontend --namespace ai-lan
kubectl --kubeconfig "$kubeconfig" rollout status deployment/gateway --namespace ai-lan --timeout=180s
echo "Application is ready."
