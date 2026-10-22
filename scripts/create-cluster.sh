#!/usr/bin/env sh
set -eu

cluster_name="$1"
http_port="$2"
project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
kubeconfig="$project_root/.kubeconfig"

for tool in docker k3d kubectl; do
  command -v "$tool" >/dev/null 2>&1 || { echo "$tool is required" >&2; exit 1; }
done

if ! k3d cluster get "$cluster_name" >/dev/null 2>&1; then
  k3d cluster create "$cluster_name" \
    --servers 1 --agents 1 \
    --port "0.0.0.0:${http_port}:80@loadbalancer" \
    --wait
fi

# The runner joins the k3d network so it can use K3s's internal API address.
runner_id=$(cat /etc/hostname)
cluster_network_id=$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.NetworkID}}{{end}}' "k3d-$cluster_name-server-0")
if [ -z "$cluster_network_id" ]; then
  echo "Could not find the Docker network for k3d-$cluster_name-server-0" >&2
  exit 1
fi
if ! docker network inspect "$cluster_network_id" --format '{{json .Containers}}' | grep -q "$runner_id"; then
  docker network connect "$cluster_network_id" "$runner_id"
fi
# k3d's exported kubeconfig targets a host-published API port. The runner is
# on the cluster's Docker network, so use the server container's internal URL.
k3d kubeconfig get "$cluster_name" > "$kubeconfig"
sed -i "s#server: https://[^[:space:]]*#server: https://k3d-$cluster_name-server-0:6443#" "$kubeconfig"
echo "Cluster $cluster_name is ready."
