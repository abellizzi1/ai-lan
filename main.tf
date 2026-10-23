locals {
  image_name  = "local/ai-gateway:dev"
  provider_api_base = coalesce(var.provider_api_base, var.enable_ollama ? "http://ollama:11434/v1" : "https://api.openai.com/v1")
  provider_api_key  = coalesce(var.provider_api_key, var.enable_ollama ? "ollama" : "")
  source_hash = sha256(join("", [for file in fileset(path.module, "services/gateway/**") : filesha256("${path.module}/${file}")]))
}

resource "terraform_data" "cluster" {
  input = {
    cluster_name = var.cluster_name
    http_port    = var.http_port
  }

  provisioner "local-exec" {
    command     = "bash '${path.module}/scripts/create-cluster.sh' '${var.cluster_name}' '${var.http_port}'"
    interpreter = ["/bin/bash", "-c"]
  }

  provisioner "local-exec" {
    when        = destroy
    command     = "k3d cluster delete \"$K3D_CLUSTER_NAME\""
    interpreter = ["/bin/bash", "-c"]
    environment = {
      K3D_CLUSTER_NAME = self.input.cluster_name
    }
  }
}

resource "terraform_data" "images" {
  triggers_replace = [local.source_hash, terraform_data.cluster.id]

  provisioner "local-exec" {
    command     = "bash '${path.module}/scripts/build-image.sh' '${var.cluster_name}' '${local.image_name}'"
    interpreter = ["/bin/bash", "-c"]
  }
}

resource "terraform_data" "application" {
  triggers_replace = [
    terraform_data.images.id,
    tostring(var.enable_ollama),
    local.provider_api_base,
    sha256(local.provider_api_key),
    filesha256("${path.module}/k8s/base/kustomization.yaml"),
    filesha256("${path.module}/k8s/base/gateway.yaml"),
    filesha256("${path.module}/k8s/base/frontend.yaml"),
    filesha256("${path.module}/k8s/base/ollama.yaml"),
    filesha256("${path.module}/k8s/base/postgres.yaml"),
    filesha256("${path.module}/k8s/base/network.yaml"),
    filesha256("${path.module}/scripts/deploy.sh")
  ]

  depends_on = [terraform_data.cluster, terraform_data.images]

  provisioner "local-exec" {
    command     = "bash '${path.module}/scripts/deploy.sh' '${var.cluster_name}' '${var.http_port}' '${local.provider_api_base}' '${var.enable_ollama}'"
    interpreter = ["/bin/bash", "-c"]
    environment = {
      GATEWAY_API_KEY   = var.gateway_api_key
      PROVIDER_API_KEY  = local.provider_api_key
      POSTGRES_PASSWORD = var.postgres_password
    }
  }
}
