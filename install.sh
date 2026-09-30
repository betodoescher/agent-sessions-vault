#!/usr/bin/env bash
# Installs sessions-vault: CLI in ~/.local/bin, config, systemd user timer and shell alias.
# Usage: ./install.sh s3://my-bucket [systemd interval, default 15min]
set -euo pipefail

BUCKET=${1:?usage: ./install.sh s3://bucket [interval]}
INTERVAL=${2:-15min}
DIR=$(cd "$(dirname "$0")" && pwd)
UNIT_DIR=~/.config/systemd/user

mkdir -p ~/.local/bin ~/.config/sessions-vault "$UNIT_DIR"
chmod +x "$DIR/sessions_vault.py"
ln -sf "$DIR/sessions_vault.py" ~/.local/bin/sessions-vault

cat > ~/.config/sessions-vault/env <<EOF
SESSIONS_VAULT_BUCKET=$BUCKET
AWS_PROFILE=${AWS_PROFILE:-default}
EOF
chmod 600 ~/.config/sessions-vault/env

cat > "$UNIT_DIR/sessions-vault.service" <<EOF
[Unit]
Description=Back up Cursor/Kiro sessions to S3

[Service]
Type=oneshot
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=%h/.local/bin/sessions-vault backup
Nice=10
IOSchedulingClass=idle
EOF

cat > "$UNIT_DIR/sessions-vault.timer" <<EOF
[Unit]
Description=Periodic backup of Cursor/Kiro sessions

[Timer]
OnBootSec=5min
OnUnitActiveSec=$INTERVAL
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now sessions-vault.timer

for rc in ~/.zshrc ~/.bashrc; do
  if [ -f "$rc" ] && ! grep -q "sessions-vault pick" "$rc"; then
    echo "alias sessions='sessions-vault pick'" >> "$rc"
  fi
done

command -v fzf >/dev/null || echo "WARNING: install fzf to use the 'sessions' alias (e.g. sudo apt install fzf)"
echo "installed. logs: journalctl --user -u sessions-vault -n 20"
