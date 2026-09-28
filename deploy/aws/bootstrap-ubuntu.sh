#!/usr/bin/env bash
set -euo pipefail

# Run once on a fresh Ubuntu EC2 instance as the ubuntu user.
# This script does not clone the repository or write application secrets.
sudo apt-get update
# Ubuntu 24.04 ships Compose v2 and buildx as docker-compose-v2 and docker-buildx;
# docker-compose-plugin exists only in Docker's own apt repository.
sudo apt-get install -y docker.io docker-compose-v2 docker-buildx nginx git
sudo systemctl enable --now docker nginx
sudo usermod -aG docker "$USER"

echo "Docker and Nginx are installed. Log out and back in, then run docker compose from the repository root."
