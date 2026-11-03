#!/bin/bash
set -euo pipefail

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
bash "$project_root/scripts/connect-cluster.sh" "${CLUSTER_NAME:-ai-lan}"
export KUBECONFIG="$project_root/.kubeconfig"

# Bootstrap data before registering the app so the gateway cannot initialize
# a new schema while a backup is being restored.
managed_app=$(kubectl get application ai-lan --namespace argocd --ignore-not-found -o name)
if [ -n "$managed_app" ]; then
  echo "ai-lan is already managed by Argo CD. Use configure-ai-secrets.sh for secret updates; restore only during explicit maintenance." >&2
  exit 1
fi
bash "$project_root/scripts/configure-ai-secrets.sh"
kubectl apply --namespace ai-lan -f "$project_root/ai-lan-config/base/postgres.yaml"
kubectl rollout status deployment/postgres --namespace ai-lan --timeout=180s
table_count=$(kubectl exec --namespace ai-lan deployment/postgres -- psql -U ai_gateway -d ai_gateway -tAc "SELECT COUNT(*) FROM pg_tables WHERE schemaname='public' AND tablename NOT LIKE 'pg_%'")
if [ "$table_count" = "0" ]; then
  shopt -s nullglob
  backups=("$project_root"/backups/postgres-*.dump)
  if [ "${#backups[@]}" -gt 0 ]; then
    gateway=$(kubectl get deployment gateway --namespace ai-lan --ignore-not-found -o name)
    if [ -n "$gateway" ]; then
      echo "Refusing restore while an existing gateway deployment could write to the database. Stop it during maintenance first." >&2
      exit 1
    fi
    latest_backup="${backups[${#backups[@]}-1]}"
    echo "Restoring PostgreSQL from $(basename "$latest_backup")."
    kubectl exec --namespace ai-lan -i deployment/postgres -- \
      pg_restore -U ai_gateway -d ai_gateway --no-owner --exit-on-error --single-transaction < "$latest_backup"
  else
    echo "No PostgreSQL backup found; starting with a fresh database."
  fi
else
  echo "Existing PostgreSQL tables found; preserving the database."
fi
echo "AI data is ready. Register cluster-config/bootstrap after configuring and pushing the Git repositories."
