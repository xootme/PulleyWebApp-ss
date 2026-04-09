#!/bin/bash
# provision_remote.sh - Remote provisioning for PulleyWebApp on GreenGeeks

APP_DIR="$HOME/public_html/cheapcadtools/tst_pulleys"
PYTHON_BIN="/opt/alt/python311/bin/python3"

echo "--- Provisioning PulleyWebApp in $APP_DIR ---"
cd "$APP_DIR" || exit 1

# 1. Create Virtual Environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating Virtual Environment..."
    $PYTHON_BIN -m venv venv
fi

# 2. Install/Update Dependencies
echo "Installing/Updating dependencies..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 3. Set Executable Permissions
echo "Setting executable permissions..."
chmod 755 index.cgi
chmod 755 provision_remote.sh

echo "--- Provisioning Complete ---"
