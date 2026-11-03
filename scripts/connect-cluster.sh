#!/bin/bash
set -euo pipefail

cluster_name="${1:-${CLUSTER_NAME:-ai-lan}}"
project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
kubeconfig="$project_root/.kubeconfig"

# Each fresh infra runner needs its own connection to the cluster network.
runner_id=$(cat /etc/hostname)
cluster_network_id=$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.NetworkID}}{{end}}' "k3d-$cluster_name-server-0")
if [ -z "$cluster_network_id" ]; then
  echo "Could not find the Docker network for k3d-$cluster_name-server-0" >&2
  exit 1
fi
if ! docker network inspect "$cluster_network_id" --format '{{json .Containers}}' | grep -q "$runner_id"; then
  docker network connect "$cluster_network_id" "$runner_id"
fi
umask 077
k3d kubeconfig get "$cluster_name" > "$kubeconfig"
chmod 600 "$kubeconfig"
sed -i "s#server: https://[^[:space:]]*#server: https://k3d-$cluster_name-server-0:6443#" "$kubeconfig"
kubectl --kubeconfig "$kubeconfig" wait --for=condition=Ready nodes --all --timeout=120s
