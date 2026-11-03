#!/bin/bash
set -euo pipefail

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
bash "$project_root/scripts/connect-cluster.sh" "${CLUSTER_NAME:-ai-lan}"
export KUBECONFIG="$project_root/.kubeconfig"

# Accept the previous environment names during migration; secrets no longer
# pass through Terraform or live in either configuration repository.
: "${GATEWAY_API_KEY:=${TF_VAR_gateway_api_key:-}}"
: "${GATEWAY_ADMIN_USERNAME:=${TF_VAR_gateway_admin_username:-admin}}"
: "${POSTGRES_PASSWORD:=${TF_VAR_postgres_password:-}}"
: "${PROVIDER_API_KEY:=${TF_VAR_provider_api_key:-}}"
: "${GATEWAY_API_KEY:?Set GATEWAY_API_KEY to the bootstrap administrator password}"
: "${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD to the existing or new database password}"

kubectl apply -f "$project_root/ai-lan-config/base/namespace.yaml"
kubectl create secret generic gateway-secrets --namespace ai-lan \
  --from-literal="gateway-api-key=$GATEWAY_API_KEY" \
  --from-literal="gateway-admin-username=$GATEWAY_ADMIN_USERNAME" \
  --from-literal="provider-api-key=$PROVIDER_API_KEY" \
  --from-literal="postgres-password=$POSTGRES_PASSWORD" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "AI secrets configured. Existing pods require a restart to read changed secrets."
