#!/bin/bash
set -eu

cluster_name="$1"
image_name="$2"
project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

docker build --tag "$image_name" "$project_root/services/gateway"
k3d image import "$image_name" --cluster "$cluster_name"
