resource "terraform_data" "cluster" {
  input = {
    cluster_name = var.cluster_name
    http_port    = var.http_port
  }

  provisioner "local-exec" {
    command     = "bash '${path.module}/scripts/create-cluster.sh' '${var.cluster_name}' '${var.http_port}'"
    interpreter = ["/bin/bash", "-c"]
    environment = {
      K3S_IMAGE = var.k3s_image
    }
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

resource "terraform_data" "argocd" {
  triggers_replace = [
    terraform_data.cluster.id,
    var.argocd_version,
    filesha256("${path.module}/scripts/bootstrap-argocd.sh"),
    filesha256("${path.module}/scripts/connect-cluster.sh")
  ]

  provisioner "local-exec" {
    command     = "bash '${path.module}/scripts/bootstrap-argocd.sh'"
    interpreter = ["/bin/bash", "-c"]
    environment = {
      CLUSTER_NAME   = var.cluster_name
      ARGOCD_VERSION = var.argocd_version
    }
  }
}
