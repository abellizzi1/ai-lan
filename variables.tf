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
  description = "Bootstrap administrator password for website login. Supply through TF_VAR_gateway_api_key."
  type        = string
  sensitive   = true
  default     = "admin"
}

variable "gateway_admin_username" {
  description = "Username for the initial administrator account."
  type        = string
  default     = "admin"
}

variable "provider_api_base" {
  description = "OpenAI-compatible upstream API base URL. Defaults to OpenAI, or the in-cluster Ollama service when enable_ollama is true."
  type        = string
  default     = null
}

variable "default_model" {
  description = "Model selected by the website and used when an API request omits model. Defaults to gemma3:270m with Ollama and gpt-4o-mini with hosted inference."
  type        = string
  default     = null
}

variable "provider_api_key" {
  description = "API key used by the gateway to call an OpenAI-compatible provider. Local Ollama ignores this value."
  type        = string
  default     = null
  sensitive   = true

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
  default     = "admin"
}
