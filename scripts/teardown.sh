#!/bin/bash
set -euo pipefail

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
kubeconfig="$project_root/.kubeconfig"
namespace="ai-lan"

if kubectl --kubeconfig "$kubeconfig" get deployment/postgres --namespace "$namespace" >/dev/null 2>&1; then
  backup_dir="$project_root/backups"
  mkdir -p "$backup_dir"
  timestamp=$(date -u +%Y%m%dT%H%M%SZ)
  backup_file="$backup_dir/postgres-$timestamp.dump"
  temp_file="$backup_file.tmp"
  postgres_pod=$(kubectl --kubeconfig "$kubeconfig" get pod --namespace "$namespace" -l app=postgres -o jsonpath='{.items[0].metadata.name}')

  echo "Backing up PostgreSQL to $backup_file"
  kubectl --kubeconfig "$kubeconfig" exec --namespace "$namespace" "$postgres_pod" -- pg_dump -U ai_gateway -d ai_gateway -Fc > "$temp_file"
  if [ ! -s "$temp_file" ]; then
    echo "PostgreSQL backup is empty; refusing to destroy the cluster." >&2
    rm -f "$temp_file"
    exit 1
  fi
  mv "$temp_file" "$backup_file"
  echo "Backup completed."
  kubectl --kubeconfig "$kubeconfig" -n "$namespace" scale deployment/gateway --replicas=0
else
  echo "No PostgreSQL deployment found; no database backup was created."
fi

terraform destroy
