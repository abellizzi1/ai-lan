#!/bin/bash
set -euo pipefail

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
kubeconfig="$project_root/.kubeconfig"
namespace="ai-lan"

bash "$project_root/scripts/connect-cluster.sh" "${CLUSTER_NAME:-ai-lan}"
export KUBECONFIG="$kubeconfig"

# Stop reconciliation before scaling the gateway, including any in-flight
# sync. This affects every application because teardown destroys the cluster.
controller=$(kubectl get statefulset argocd-application-controller --namespace argocd --ignore-not-found -o name)
if [ -n "$controller" ]; then
  kubectl scale --namespace argocd "$controller" --replicas=0
  controller_pods=$(kubectl get pods --namespace argocd -l app.kubernetes.io/name=argocd-application-controller -o name)
  if [ -n "$controller_pods" ]; then
    kubectl wait --namespace argocd --for=delete pod -l app.kubernetes.io/name=argocd-application-controller --timeout=180s
  fi
fi
gateway=$(kubectl get deployment gateway --namespace "$namespace" --ignore-not-found -o name)
if [ -n "$gateway" ]; then
  kubectl scale --namespace "$namespace" "$gateway" --replicas=0
  gateway_pods=$(kubectl get pods --namespace "$namespace" -l app=gateway -o name)
  if [ -n "$gateway_pods" ]; then
    kubectl wait --namespace "$namespace" --for=delete pod -l app=gateway --timeout=180s
  fi
fi
# --ignore-not-found tolerates an absent workload, but API/auth errors still
# abort teardown rather than silently skipping a required backup.
postgres=$(kubectl get deployment postgres --namespace "$namespace" --ignore-not-found -o name)
if [ -n "$postgres" ]; then
  bash "$project_root/scripts/backup-postgres.sh"
else
  echo "No PostgreSQL deployment found; no database backup was created."
fi

terraform -chdir="$project_root" destroy
