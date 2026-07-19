#!/usr/bin/env bash
# Skrypt wstepnej konfiguracji serwera Mikrus.
# Uruchom jako root lub z sudo: bash setup_mikrus.sh
set -euo pipefail

echo "== Aktualizacja systemu =="
apt-get update && apt-get upgrade -y

echo "== Tworzenie uzytkownika 'agent' (jesli nie istnieje) =="
id -u agent &>/dev/null || useradd -m -s /bin/bash agent

echo "== Instalacja Python 3.11 + venv =="
apt-get install -y python3 python3-venv python3-pip git

echo "== Instalacja Node.js 20.x =="
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs

echo "== Instalacja JDK 17 (do budowania Androida) =="
apt-get install -y openjdk-17-jdk-headless

echo "== Instalacja Android SDK cmdline-tools =="
SDK_DIR=/home/agent/android-sdk
mkdir -p "$SDK_DIR/cmdline-tools"
cd /tmp
curl -o cmdline-tools.zip https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
unzip -q cmdline-tools.zip -d "$SDK_DIR/cmdline-tools"
mv "$SDK_DIR/cmdline-tools/cmdline-tools" "$SDK_DIR/cmdline-tools/latest"
export ANDROID_HOME="$SDK_DIR"
yes | "$SDK_DIR/cmdline-tools/latest/bin/sdkmanager" --licenses
"$SDK_DIR/cmdline-tools/latest/bin/sdkmanager" "platform-tools" "platforms;android-34" "build-tools;34.0.0"
chown -R agent:agent "$SDK_DIR"

echo "== Klonowanie repo agent-system (podmien URL na swoje forkowane repo) =="
su - agent -c "git clone <URL_TWOJEGO_REPO_AGENT_SYSTEM> /home/agent/android-agent-system"

echo "== Python venv + zaleznosci orchestratora =="
su - agent -c "cd /home/agent/android-agent-system/orchestrator && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"

echo "== Zaleznosci mostu WhatsApp =="
su - agent -c "cd /home/agent/android-agent-system/whatsapp-bridge && npm install"

echo "== Klonowanie/tworzenie repo aplikacji Android =="
echo "   (recznie: git clone <URL_REPO_ANDROID> /home/agent/android-app"
echo "    albo skopiuj android-app-template/* jako punkt startowy nowego repo)"

echo "=========================================================="
echo "Kolejne kroki RECZNE:"
echo "1. Uzupelnij /home/agent/android-agent-system/.env (skopiuj z .env.example)"
echo "2. Ustaw miesieczny limit klucza na openrouter.ai/settings/keys"
echo "3. cp deploy/*.service /etc/systemd/system/"
echo "4. systemctl daemon-reload"
echo "5. systemctl enable --now orchestrator whatsapp-bridge"
echo "6. journalctl -u whatsapp-bridge -f   # zeskanuj QR przy pierwszym starcie"
echo "=========================================================="
