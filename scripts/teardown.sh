#!/usr/bin/env sh

kubectl --kubeconfig /workspace/.kubeconfig -n ai-lan scale deployment/gateway --replicas=0
kubectl --kubeconfig /workspace/.kubeconfig -n ai-lan scale deployment/postgres --replicas=0
kubectl --kubeconfig /workspace/.kubeconfig -n ai-lan delete pvc postgres-data --wait=true

terraform destroy
