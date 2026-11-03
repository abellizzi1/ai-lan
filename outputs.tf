output "website_url" {
  description = "Open this from a device on the local network."
  value       = "http://<your-host-LAN-IP>:${var.http_port}"
}

output "api_url" {
  value = "http://<your-host-LAN-IP>:${var.http_port}/v1/chat/completions"
}

output "argocd_access" {
  description = "Run in the infra container; open https://localhost:8081 on the Docker host."
  value       = "kubectl --kubeconfig /workspace/.kubeconfig -n argocd port-forward --address 0.0.0.0 svc/argocd-server 8081:443"
}
