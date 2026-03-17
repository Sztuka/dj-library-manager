#!/usr/bin/env bash
# ============================================================================
# Dev environment switcher for DJ Library Manager
#
# Usage:
#   scripts/dev_env.sh on     # Switch to dev/test environment
#   scripts/dev_env.sh off    # Restore production environment
#   scripts/dev_env.sh status # Show current environment
#
# What it does:
#   - Backs up config.local.yml → config.local.yml.prod
#   - Patches INBOX path to data/ab_test/unsorted-test/
#   - Patches UNSORTED_CSV to data/unsorted-test.csv
#   - Patches LOGS_DIR to LOGS-test/
#
# This lets you run the full workflow (scan → enrich → review)
# on test tracks without affecting production data.
# ============================================================================

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="$REPO_DIR/config.local.yml"
CONFIG_PROD="$REPO_DIR/config.local.yml.prod"
CONFIG_DEV="$REPO_DIR/config.local.yml.dev"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

show_status() {
    if [[ -f "$CONFIG_PROD" ]]; then
        echo -e "${YELLOW}🧪 DEV environment active${NC}"
        echo "   INBOX:        data/ab_test/unsorted-test/"
        echo "   UNSORTED_CSV: data/unsorted-test.csv"
        echo "   LOGS:         LOGS-test/"
        echo ""
        echo "   Run: scripts/dev_env.sh off  to restore production"
    else
        echo -e "${GREEN}🏭 PRODUCTION environment active${NC}"
        echo "   INBOX:        ~/Music Unsorted"
        echo "   UNSORTED_CSV: data/unsorted.csv"
        echo "   LOGS:         LOGS/"
    fi
}

switch_on() {
    if [[ -f "$CONFIG_PROD" ]]; then
        echo -e "${YELLOW}⚠️  Dev environment already active. Use 'off' first.${NC}"
        show_status
        exit 1
    fi

    if [[ ! -f "$CONFIG" ]]; then
        echo -e "${RED}❌ config.local.yml not found${NC}"
        exit 1
    fi

    # Create unsorted-test dir if needed
    mkdir -p "$REPO_DIR/data/ab_test/unsorted-test"
    mkdir -p "$REPO_DIR/LOGS-test"

    # Backup production config
    cp "$CONFIG" "$CONFIG_PROD"
    echo -e "${GREEN}✅ Production config backed up → config.local.yml.prod${NC}"

    # Create dev config: patch inbox + unsorted_csv + logs
    # Start from production config and patch the paths
    cp "$CONFIG_PROD" "$CONFIG"

    # Use Python to safely patch YAML
    "$REPO_DIR/.venv/bin/python" - "$CONFIG" "$REPO_DIR" <<'PYEOF'
import sys, yaml
from pathlib import Path

config_path = Path(sys.argv[1])
repo_dir = Path(sys.argv[2])

with open(config_path) as f:
    data = yaml.safe_load(f) or {}

# Patch paths for dev environment
data["inbox_dir"] = str(repo_dir / "data" / "ab_test" / "unsorted-test")
data["INBOX_UNSORTED"] = str(repo_dir / "data" / "ab_test" / "unsorted-test")
data["UNSORTED_CSV"] = str(repo_dir / "data" / "unsorted-test.csv")
data["LOGS_DIR"] = str(repo_dir / "LOGS-test")

with open(config_path, "w") as f:
    yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

print(f"  inbox_dir:    {data['inbox_dir']}")
print(f"  UNSORTED_CSV: {data['UNSORTED_CSV']}")
print(f"  LOGS_DIR:     {data['LOGS_DIR']}")
PYEOF

    # Also save a copy as .dev for quick re-switching
    cp "$CONFIG" "$CONFIG_DEV"

    echo ""
    echo -e "${GREEN}🧪 DEV environment activated!${NC}"
    echo ""
    echo "Now you can:"
    echo "  1. Copy test tracks into:  data/ab_test/unsorted-test/"
    echo "  2. Run scan:               .venv/bin/python -m djlib.cli scan"
    echo "  3. Run enrich:             .venv/bin/python -m djlib.cli enrich-online"
    echo "  4. Open Review UI:         .venv/bin/python -m djlib.cli review"
    echo ""
    echo "Your production data is safe in config.local.yml.prod"
}

switch_off() {
    if [[ ! -f "$CONFIG_PROD" ]]; then
        echo -e "${YELLOW}⚠️  Production config not found — already in production mode?${NC}"
        show_status
        exit 1
    fi

    # Restore production config
    cp "$CONFIG_PROD" "$CONFIG"
    rm "$CONFIG_PROD"
    echo -e "${GREEN}🏭 Production environment restored!${NC}"
    echo ""
    echo "Dev data preserved in:"
    echo "  data/unsorted-test.csv    (staging CSV)"
    echo "  LOGS-test/                (logs)"
    echo "  data/ab_test/unsorted-test/ (audio files)"
}

case "${1:-status}" in
    on|dev|test)
        switch_on
        ;;
    off|prod|production)
        switch_off
        ;;
    status|st)
        show_status
        ;;
    *)
        echo "Usage: $0 {on|off|status}"
        echo ""
        echo "  on     Switch to dev/test environment"
        echo "  off    Restore production environment"
        echo "  status Show current environment"
        exit 1
        ;;
esac
