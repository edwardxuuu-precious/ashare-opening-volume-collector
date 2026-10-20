#!/bin/bash
set -euo pipefail
install -d -m 755 /usr/local/libexec
install -o root -g root -m 755 /opt/stock-collector/deploy/collector/stop_after_run.py /usr/local/libexec/stock-collector-stop.py
cat > /etc/systemd/system/stock-collector.service <<'EOF'
[Unit]
Description=Bounded AKShare full-market collection into private S3
After=network-online.target
Wants=network-online.target stock-collector-budget-stop.timer
[Service]
Type=simple
User=stockcollector
Group=stockcollector
EnvironmentFile=/etc/stock-collector.env
WorkingDirectory=/opt/stock-collector
ExecStart=/opt/stock-collector/venv/bin/python /opt/stock-collector/server/cloud_worker.py --bucket ${STOCK_BUCKET} --root /var/lib/stock-collector --max-hours 20 --resume-attempted
ExecStopPost=+/usr/bin/python3 /usr/local/libexec/stock-collector-stop.py
Restart=no
TimeoutStopSec=150
KillMode=mixed
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/stock-collector
UMask=0077
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/stock-collector-budget-stop.service <<'EOF'
[Unit]
Description=Hard 24-hour Stock collector host budget guard
[Service]
Type=oneshot
ExecStart=/usr/sbin/shutdown -h now
EOF
cat > /etc/systemd/system/stock-collector-budget-stop.timer <<'EOF'
[Unit]
Description=Stop the collector host within 24 hours of every boot
[Timer]
OnBootSec=24h
AccuracySec=1min
Unit=stock-collector-budget-stop.service
[Install]
WantedBy=timers.target
EOF
# Collector journal is bounded independently of the API's 14-day CloudWatch logs.
install -d -m 755 /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/stock-retention.conf <<'EOF'
[Journal]
MaxRetentionSec=14day
SystemMaxUse=100M
EOF
systemctl restart systemd-journald
# Scheduler now starts the EC2 instance; do not retain a duplicate in-host daily timer.
systemctl disable --now stock-collector.timer 2>/dev/null || true
systemctl daemon-reload
systemctl enable --now stock-collector-budget-stop.timer
# Replace the old first-bootstrap one-shot shutdown with the every-boot timer.
shutdown -c 2>/dev/null || true
# Deliberately do not enable/start stock-collector.service until real source probes pass.
