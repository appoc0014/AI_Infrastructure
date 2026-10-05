#!/usr/bin/env bash
# Run ON THE PRECISION 3640 (the GPU node) as a sudo-capable user.
# Installs the NVIDIA driver + container toolkit on the host so k3s can hand GPUs to pods.
# The GPU Operator is then deployed with driver/toolkit DISABLED (the host owns them).
set -euo pipefail

echo "== 1. NVIDIA driver =="
if ! command -v nvidia-smi >/dev/null; then
  sudo apt-get update
  sudo apt-get install -y ubuntu-drivers-common
  ubuntu-drivers list --gpgpu || true
  sudo ubuntu-drivers install --gpgpu
  echo ">> Driver installed. REBOOT, then re-run this script."
  exit 0
fi
nvidia-smi

echo "== 2. NVIDIA container toolkit =="
if ! dpkg -s nvidia-container-toolkit >/dev/null 2>&1; then
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
  sudo apt-get update
  sudo apt-get install -y nvidia-container-toolkit
fi

echo "== 3. Restart k3s so it auto-detects the nvidia runtime =="
if systemctl is-active --quiet k3s; then sudo systemctl restart k3s; else sudo systemctl restart k3s-agent; fi
sleep 10
sudo grep -A2 -i nvidia /var/lib/rancher/k3s/agent/etc/containerd/config*.toml | head -20 \
  || echo "!! nvidia runtime not found in containerd config - check: journalctl -u k3s"

echo "== 4. Persistent power cap (300W PSU until the MSI A750GL is installed) =="
sudo tee /etc/systemd/system/nvidia-power-cap.service >/dev/null <<'UNIT'
[Unit]
Description=Set NVIDIA persistence mode and power limits
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/usr/bin/nvidia-smi -pm 1
# GPU 0 = RTX 2070 Super. Raise or remove after the new PSU is in.
ExecStart=/usr/bin/nvidia-smi -i 0 -pl 150
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now nvidia-power-cap.service
nvidia-smi --query-gpu=index,name,power.limit --format=csv

echo "Done. From your workstation: kubectl get runtimeclass   (expect 'nvidia')"
