#!/bin/bash
set -euo pipefail

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
bash "$project_root/scripts/connect-cluster.sh" "${CLUSTER_NAME:-ai-lan}"
export KUBECONFIG="$project_root/.kubeconfig"
kubectl rollout status deployment/postgres --namespace ai-lan --timeout=180s
backup_dir="$project_root/backups"
umask 077
mkdir -p "$backup_dir"
timestamp=$(date -u +%Y%m%dT%H%M%S%NZ)
backup_file="$backup_dir/postgres-$timestamp.dump"
temp_file="$backup_file.tmp"
trap 'rm -f -- "$temp_file"' EXIT
kubectl exec --namespace ai-lan deployment/postgres -- pg_dump -U ai_gateway -d ai_gateway -Fc > "$temp_file"
if [ ! -s "$temp_file" ]; then
  echo "PostgreSQL backup is empty." >&2
  exit 1
fi
mv -- "$temp_file" "$backup_file"
echo "Backup completed: $backup_file"
