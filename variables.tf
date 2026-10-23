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

variable "gateway_api_key" {
  description = "Initial client API key for the gateway. Supply through TF_VAR_gateway_api_key."
  type        = string
  sensitive   = true
}

variable "provider_api_base" {
  description = "OpenAI-compatible upstream API base URL, without a trailing slash. Defaults to the in-cluster Ollama service."
  type        = string
  default     = "http://ollama:11434/v1"
}

variable "provider_api_key" {
  description = "API key used by the gateway to call an optional external OpenAI-compatible provider. Local Ollama ignores this value."
  type        = string
  default     = "ollama"
  sensitive   = true
}

variable "postgres_password" {
  description = "Password for the gateway PostgreSQL role. Supply through TF_VAR_postgres_password."
  type        = string
  sensitive   = true
}
