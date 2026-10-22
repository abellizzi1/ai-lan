output "website_url" {
  description = "Open this from a device connected to the allowed Wi-Fi network."
  value       = "http://<your-laptop-LAN-IP>:${var.http_port}"
}

output "api_url" {
  value = "http://<your-laptop-LAN-IP>:${var.http_port}/v1/chat/completions"
}
