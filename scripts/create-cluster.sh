#!/bin/bash
set -eu

cluster_name="$1"
http_port="$2"
project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

for tool in docker k3d kubectl; do
  command -v "$tool" >/dev/null 2>&1 || { echo "$tool is required" >&2; exit 1; }
done

if ! k3d cluster get "$cluster_name" >/dev/null 2>&1; then
  k3d cluster create "$cluster_name" \
    --servers 1 --agents 1 \
    --image "${K3S_IMAGE:-rancher/k3s:v1.34.3-k3s1}" \
    --port "0.0.0.0:${http_port}:80@loadbalancer" \
    --wait
fi

bash "$project_root/scripts/connect-cluster.sh" "$cluster_name"
echo "Cluster $cluster_name is ready."
