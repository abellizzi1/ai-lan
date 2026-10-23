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
  default     = "blah"
}

variable "provider_api_base" {
  description = "OpenAI-compatible upstream API base URL. Defaults to OpenAI, or the in-cluster Ollama service when enable_ollama is true."
  type        = string
  default     = null
}

variable "provider_api_key" {
  description = "API key used by the gateway to call an OpenAI-compatible provider. Local Ollama ignores this value."
  type        = string
  default     = null
  sensitive   = true

  validation {
    condition     = var.enable_ollama || (var.provider_api_key != null && length(trimspace(var.provider_api_key)) > 0)
    error_message = "Set provider_api_key for hosted inference, or set enable_ollama=true to use the local Ollama provider."
  }
}

variable "enable_ollama" {
  description = "Deploy the in-cluster Ollama server and use it as the inference provider."
  type        = bool
  default     = false
}

variable "postgres_password" {
  description = "Password for the gateway PostgreSQL role. Supply through TF_VAR_postgres_password."
  type        = string
  sensitive   = true
  default     = "blah"
}
