#!/usr/bin/env bash
# ==============================================================================
# Qashbocks Standalone Installer
# Installs Qashbocks CLI, sets up configuration, and ensures sample database exists.
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_TARGET="${HOME}/.local/bin"
CONFIG_DIR="${HOME}/.config/qashbocks"

echo "================================================================="
echo "  Installing Qashbocks - Standalone Personal Finance Ledger"
echo "================================================================="

# 1. Check Python version
if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 is not installed or not in PATH." >&2
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Found Python ${PY_VER}."

# 2. Install dependencies
echo "Installing dependencies (Jinja2)..."
python3 -m pip install --user --upgrade jinja2 setuptools >/dev/null 2>&1 || {
    echo "Notice: Pip install failed or restricted; attempting local installation..."
    pip3 install --user jinja2 || true
}

# 3. Ensure ~/.local/bin exists
mkdir -p "${BIN_TARGET}"

# 4. Make bin/qashbocks executable
chmod +x "${SCRIPT_DIR}/bin/qashbocks"
chmod +x "${SCRIPT_DIR}/tools/"*.py

# 5. Create symlink in ~/.local/bin/qashbocks
echo "Creating symlink in ${BIN_TARGET}/qashbocks..."
ln -sf "${SCRIPT_DIR}/bin/qashbocks" "${BIN_TARGET}/qashbocks"

# 6. Initialize default config in ~/.config/qashbocks/config.toml if not present
mkdir -p "${CONFIG_DIR}"
if [ ! -f "${CONFIG_DIR}/config.toml" ]; then
    echo "Writing initial configuration to ${CONFIG_DIR}/config.toml..."
    sed "s|data/sample.db|${SCRIPT_DIR}/data/sample.db|g" "${SCRIPT_DIR}/config.example.toml" > "${CONFIG_DIR}/config.toml"
else
    echo "Existing configuration found at ${CONFIG_DIR}/config.toml (preserving)."
fi

# 7. Ensure sample database exists
if [ ! -f "${SCRIPT_DIR}/data/sample.db" ]; then
    echo "Generating demonstration database at data/sample.db..."
    PYTHONPATH="${SCRIPT_DIR}" python3 -m qashbocks.schema "${SCRIPT_DIR}/data/sample.db"
fi

echo ""
echo "================================================================="
echo "  Qashbocks installation complete!"
echo "================================================================="
echo ""
echo "Ensure ${BIN_TARGET} is in your PATH:"
echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
echo ""
echo "Try running:"
echo "  qashbocks accounts"
echo "  qashbocks bills"
echo "  qashbocks register checking"
echo "  qashbocks find \"groceries\""
echo "  qashbocks forecast"
echo ""
