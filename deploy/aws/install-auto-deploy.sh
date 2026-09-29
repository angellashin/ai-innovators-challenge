#!/usr/bin/env bash
set -euo pipefail

repo=/home/ubuntu/ai-innovators-challenge
if [[ $(id -u) -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi
if [[ ! -f "$repo/deploy/aws/auto_deploy.py" ]]; then
  echo "Pull the latest main in $repo before installing." >&2
  exit 1
fi

cat > /etc/systemd/system/replan-auto-deploy.service <<'EOF'
[Unit]
Description=Deploy REPLAN after main CI passes
Wants=network-online.target docker.service
After=network-online.target docker.service

[Service]
Type=oneshot
User=ubuntu
Group=ubuntu
SupplementaryGroups=docker
WorkingDirectory=/home/ubuntu/ai-innovators-challenge
Environment=HOME=/home/ubuntu
Environment=PATH=/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/python3 /home/ubuntu/ai-innovators-challenge/deploy/aws/auto_deploy.py
TimeoutStartSec=45min
EOF

cat > /etc/systemd/system/replan-auto-deploy.timer <<'EOF'
[Unit]
Description=Check REPLAN main CI every five minutes

[Timer]
OnBootSec=2min
OnCalendar=*:0/5
Persistent=true
Unit=replan-auto-deploy.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now replan-auto-deploy.timer
systemctl start --no-block replan-auto-deploy.service
echo "Automatic deployment timer installed; first CI-approved deployment is starting."
