#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# deploy_to_vm.sh — Push latest code to the VM and restart the daemon.
#
# Run from your LOCAL machine (Git Bash / WSL / PowerShell with ssh):
#   bash scripts/deploy_to_vm.sh
#
# Set VM_HOST below (get it from GCP Console → VM instances → External IP).
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

VM_HOST="YOUR_VM_EXTERNAL_IP"   # e.g. 34.123.45.67
VM_USER="YOUR_VM_USER"          # e.g. buyen  (same user you SSH in as)
VM_PATH="/home/$VM_USER/soccer_vemon_plus"
LOCAL_PATH="$(cd "$(dirname "$0")/.." && pwd)"

echo "Deploying to $VM_USER@$VM_HOST:$VM_PATH ..."

# Sync project (exclude venv, output data, and local secrets)
rsync -az --progress \
    --exclude='.venv/' \
    --exclude='_headless_output/' \
    --exclude='storage/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='.git/' \
    --exclude='src/assets/.env' \
    "$LOCAL_PATH/" \
    "$VM_USER@$VM_HOST:$VM_PATH/"

echo "Code synced. Restarting daemon..."
ssh "$VM_USER@$VM_HOST" "
    cd $VM_PATH
    $VM_PATH/.venv/bin/pip install -r requirements.txt -q
    sudo systemctl restart soccer-daemon
    sudo systemctl status soccer-daemon --no-pager
"
echo "Done."
