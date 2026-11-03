#!/bin/bash
set -euo pipefail

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
: "${CLUSTER_NAME:=ai-lan}"
: "${ARGOCD_VERSION:=v3.5.6}"
[[ "$ARGOCD_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Use a pinned Argo CD release tag." >&2; exit 1; }
bash "$project_root/scripts/connect-cluster.sh" "$CLUSTER_NAME"
export KUBECONFIG="$project_root/.kubeconfig"

# Do not silently replace an older cluster: it may hold application data.
server_version=$(kubectl get --raw /version)
if ! printf '%s' "$server_version" | grep -Eq '"gitVersion"[[:space:]]*:[[:space:]]*"v1\.(33|34|35|36)\.'; then
  echo "This bootstrap expects Kubernetes 1.33-1.36. See README.md migration instructions before upgrading the cluster." >&2
  exit 1
fi
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply --namespace argocd --server-side --force-conflicts \
  -f "https://raw.githubusercontent.com/argoproj/argo-cd/$ARGOCD_VERSION/manifests/install.yaml"
kubectl wait --for=condition=Established --timeout=120s \
  crd/applications.argoproj.io crd/appprojects.argoproj.io crd/applicationsets.argoproj.io
kubectl rollout status deployment/argocd-redis --namespace argocd --timeout=300s
kubectl rollout status deployment/argocd-repo-server --namespace argocd --timeout=300s
kubectl rollout status deployment/argocd-server --namespace argocd --timeout=300s
kubectl rollout status statefulset/argocd-application-controller --namespace argocd --timeout=300s
echo "Argo CD is ready. Prepare AI secrets/data, then apply cluster-config/bootstrap (see README.md)."
