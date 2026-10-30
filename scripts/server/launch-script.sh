#!/bin/bash
# FlowForge cloud server, first boot (paste into Lightsail "Launch script", Ubuntu 24.04 LTS).
# Installs Docker from Ubuntu's own packages, sets New York time (the daily routine runs on
# market hours), adds 4 GB of swap and downloads the code. No secrets here: .env is made by
# hand on the server (docs/runbooks/aws-server.md).
set -euxo pipefail

timedatectl set-timezone America/New_York
apt-get update
apt-get install -y docker.io docker-compose-v2 git
systemctl enable --now docker
usermod -aG docker ubuntu

# Swap: headroom for image builds next to the gateway and the node.
if [ ! -f /swapfile ]; then
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

sudo -u ubuntu git clone https://github.com/beatsbymaj/orderflowbot.git /home/ubuntu/orderflowbot
echo "flowforge launch script done" > /home/ubuntu/LAUNCH-DONE.txt
