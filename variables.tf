variable "cluster_name" {
  description = "Name for the local k3d/K3s cluster."
  type        = string
  default     = "ai-lan"
}

variable "http_port" {
  description = "Host TCP port exposed to your local network."
  type        = number
  default     = 8080
}

variable "wifi_cidr" {
  description = "Your Wi-Fi IPv4 subnet allowed through Traefik (for example 192.168.1.0/24)."
  type        = string

  validation {
    condition     = var.wifi_cidr == trimspace(var.wifi_cidr) && can(cidrnetmask(var.wifi_cidr))
    error_message = "wifi_cidr must be a whitespace-free IPv4 CIDR, for example 192.168.0.0/24."
  }
}

variable "gateway_api_key" {
  description = "Initial client API key for the gateway. Supply through TF_VAR_gateway_api_key."
  type        = string
  sensitive   = false
}

variable "provider_api_base" {
  description = "OpenAI-compatible upstream API base URL, without a trailing slash."
  type        = string
  default     = "https://api.openai.com/v1"
}

variable "provider_api_key" {
  description = "API key used by the gateway to call the hosted inference provider. Supply through TF_VAR_provider_api_key."
  type        = string
  sensitive   = false
}

variable "postgres_password" {
  description = "Password for the gateway PostgreSQL role. Supply through TF_VAR_postgres_password."
  type        = string
  sensitive   = false
}
