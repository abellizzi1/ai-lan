locals {
  image_name  = "local/ai-gateway:dev"
  source_hash = sha256(join("", [for file in fileset(path.module, "services/gateway/**") : filesha256("${path.module}/${file}")]))
}

resource "terraform_data" "cluster" {
  input = {
    cluster_name = var.cluster_name
    http_port    = var.http_port
  }

  provisioner "local-exec" {
    command     = "sh '${path.module}/scripts/create-cluster.sh' '${var.cluster_name}' '${var.http_port}'"
    interpreter = ["/bin/sh", "-c"]
  }

  provisioner "local-exec" {
    when        = destroy
    command     = "k3d cluster delete \"$K3D_CLUSTER_NAME\""
    interpreter = ["/bin/sh", "-c"]
    environment = {
      K3D_CLUSTER_NAME = self.input.cluster_name
    }
  }
}

resource "terraform_data" "images" {
  triggers_replace = [local.source_hash, terraform_data.cluster.id]

  provisioner "local-exec" {
    command     = "sh '${path.module}/scripts/build-image.sh' '${var.cluster_name}' '${local.image_name}'"
    interpreter = ["/bin/sh", "-c"]
  }
}

resource "terraform_data" "application" {
  triggers_replace = [
    terraform_data.images.id,
    filesha256("${path.module}/k8s/base/kustomization.yaml"),
    filesha256("${path.module}/k8s/base/gateway.yaml"),
    filesha256("${path.module}/k8s/base/frontend.yaml"),
    filesha256("${path.module}/k8s/base/postgres.yaml"),
    filesha256("${path.module}/k8s/base/network.yaml")
  ]

  depends_on = [terraform_data.cluster, terraform_data.images]

  provisioner "local-exec" {
    command     = "sh '${path.module}/scripts/deploy.sh' '${var.cluster_name}' '${var.http_port}' '${var.wifi_cidr}' '${var.provider_api_base}'"
    interpreter = ["/bin/sh", "-c"]
    environment = {
      GATEWAY_API_KEY   = var.gateway_api_key
      PROVIDER_API_KEY  = var.provider_api_key
      POSTGRES_PASSWORD = var.postgres_password
    }
  }
}
