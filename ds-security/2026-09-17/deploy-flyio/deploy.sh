#!/bin/bash
# ============================================================
# Clow Studio v2.5 — Productization Blueprint (Path A+B)
# v2.5: + Obscura headless browser (Rust, no Chromium) — fetch/render/scrape live pages, CDP, MCP, stealth
# v2.4: + Agent Governance Toolkit (Microsoft) & Graft (NanoNets)
# v2.3: + Quantum Garden & Inner Citadel (happiness/career resilience engines)
# v2.2: + Freemium LLM metering (DeepSeek request limits per tier)
# v2.1: + BioMimic Intelligence Suite (resilience, corruption-as-cancer, tipping, dormancy)
# ============================================================
# One-command deploy — Path A (Managed SaaS) + Path B (Self-Host)
# Freemium: Free tier gets 5 DeepSeek LLM calls/day, then guided to paid.
# ============================================================loy for ALL platforms:
#   Telegram (10 bots) + Discord (9 bots) + WhatsApp + Web
#
# Usage:
#   bash deploy.sh                    # Deploy all platforms
#   bash deploy.sh --telegram         # Telegram only
#   bash deploy.sh --discord          # Discord only
#   bash deploy.sh --whatsapp         # WhatsApp only
#   bash deploy.sh --web              # Web PWA only
#   bash deploy.sh --status           # Check deployment status
#   bash deploy.sh --init             # Interactive setup (new users)
#   bash deploy.sh --all              # Deploy everything
# ============================================================
set -e

VERSION="2.3"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "  🦞 Clow Studio v$VERSION — Productization Blueprint"
echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  10 specialized AI bots. One binary. Deploy anywhere."
echo ""

# ─── Parse args ───────────────────────────────────────────────

DEPLOY_TELEGRAM=false
DEPLOY_DISCORD=false
DEPLOY_WHATSAPP=false
DEPLOY_WEB=false
CHECK_STATUS=false
INIT_MODE=false

for arg in "$@"; do
  case "$arg" in
    --telegram) DEPLOY_TELEGRAM=true ;;
    --discord) DEPLOY_DISCORD=true ;;
    --whatsapp) DEPLOY_WHATSAPP=true ;;
    --web) DEPLOY_WEB=true ;;
    --status) CHECK_STATUS=true ;;
    --init) INIT_MODE=true ;;
    --all|"") DEPLOY_TELEGRAM=true; DEPLOY_DISCORD=true; DEPLOY_WHATSAPP=true; DEPLOY_WEB=true ;;
  esac
done

# ─── Interactive Setup ────────────────────────────────────────

if [ "$INIT_MODE" = true ]; then
  echo "  🛠️  Interactive Setup"
  echo "  ─────────────────────"
  echo ""
  
  # Check prerequisites
  echo "  Checking prerequisites..."
  for cmd in go curl; do
    command -v $cmd &> /dev/null && echo "  ✅ $cmd installed" || echo "  ❌ $cmd not installed — install it first"
  done
  
  # Bot selection
  echo ""
  echo "  Which bots do you want to deploy?"
  echo "    1) All 10 bots (recommended)"
  echo "    2) Crypto Pack (ChainShield + Invest + deeptechx)"
  echo "    3) Travel Pack (WanderSync + WorldMonitor + Legal)"
  echo "    4) Founder Pack (Productize + Legal + JobPulse + Invest)"
  echo "    5) Custom selection"
  echo ""
  read -p "  Choose [1-5]: " BOT_CHOICE
  echo ""
  
  case "$BOT_CHOICE" in
    2) echo "  Selected: Crypto Pack (3 bots)" ;;
    3) echo "  Selected: Travel Pack (3 bots)" ;;
    4) echo "  Selected: Founder Pack (4 bots)" ;;
    5) echo "  Custom selection — edit .env after setup" ;;
    *) echo "  Selected: All 10 bots" ;;
  esac
  
  # LLM backend
  echo ""
  echo "  LLM Backend:"
  echo "    1) DeepSeek (recommended — ~$0.50/mo)"
  echo "    2) OpenAI"
  echo "    3) Ollama (local, free)"
  echo "    4) Skip (offline mode)"
  echo ""
  read -p "  Choose [1-4]: " LLM_CHOICE
  echo ""
  
  # Generate .env
  echo "  Generating configuration..."
  cp .env.template .env 2>/dev/null || touch .env
  
  case "$LLM_CHOICE" in
    2) echo "LLM_BASE_URL=https://api.openai.com/v1" >> .env
       echo "LLM_MODEL=gpt-4o" >> .env
       echo "  ⏳ Set LLM_API_KEY in .env" ;;
    3) echo "LLM_BASE_URL=http://localhost:11434/v1" >> .env
       echo "LLM_MODEL=llama3.2:3b" >> .env
       echo "  ✅ Local Ollama configured" ;;
    4) echo "LLM_API_KEY=offline" >> .env
       echo "LLM_BASE_URL=http://localhost:11434/v1" >> .env
       echo "  ✅ Offline mode" ;;
    *) echo "LLM_BASE_URL=https://api.deepseek.com/v1" >> .env
       echo "LLM_MODEL=deepseek-chat" >> .env
       echo "  ✅ DeepSeek configured" ;;
  esac
  
  echo ""
  echo "  ✅ .env created. Edit it with your bot tokens and API keys."
  echo "  Then run: bash deploy.sh"
  exit 0
fi

# ─── Status Check ───────────────────────────────────────────

if [ "$CHECK_STATUS" = true ]; then
  echo "  📊 Deployment Status"
  echo ""
  
  # Check Go
  command -v go &> /dev/null && echo "  ✅ Go $(go version | grep -oP 'go\S+')" || echo "  ❌ Go not installed"
  
  # Check Fly.io
  if command -v flyctl &> /dev/null; then
    echo "  ✅ flyctl installed"
    flyctl apps list 2>/dev/null | grep -q clow-studio && echo "  ✅ Fly.io app deployed" || echo "  ⚠️  No Fly.io app found"
  else
    echo "  ⚠️  flyctl not installed (optional for cloud deploy)"
  fi
  
  # Check tokens
  echo ""
  echo "  Bots:"
  for bot in DEEPTECHX_BOT_TOKEN WANDERSYNC_BOT_TOKEN CHAINSHIELD_BOT_TOKEN JOBPULSE_BOT_TOKEN PRODUCTIZE_BOT_TOKEN CLAWINVEST_BOT_TOKEN LEGAL_BOT_TOKEN ELARA_BOT_TOKEN WM_BOT_TOKEN AFRI_BOT_TOKEN; do
    [ -n "${!bot}" ] && echo "  ✅ $bot configured" || echo "  ⚠️  $bot not set"
  done
  
  echo ""
  echo "  To deploy: bash deploy.sh"
  exit 0
fi

# ─── Step 1: Build ────────────────────────────────────────

echo "  🔨 Step 1: Building Go binary..."
echo ""

BUILD_FLAGS="-ldflags=\"-s -w\""
CGO_ENABLED=1 go build $BUILD_FLAGS -o clow-studio .

BINARY_SIZE=$(du -h clow-studio | cut -f1)
echo "  ✅ Built: clow-studio ($BINARY_SIZE)"
echo ""

# ─── Step 2: Deploy to Fly.io ─────────────────────────────────

if [ "$DEPLOY_TELEGRAM" = true ] || [ "$DEPLOY_WEB" = true ]; then
  echo "  🚀 Step 2: Deploying to Fly.io..."
  echo ""
  
  if command -v flyctl &> /dev/null; then
    # ─── Security hardening (Evo-Decepticon scan 2026-09-17) ─────────────
    # Set DASHBOARD_TOKEN to gate /dashboard/* behind a bearer token.
    # Without it, the dashboard routes are PUBLIC (dev mode).
    if [ -n "$DASHBOARD_TOKEN" ]; then
      flyctl secrets set DASHBOARD_TOKEN="$DASHBOARD_TOKEN" 2>&1 | tail -2
      echo "  🔐 DASHBOARD_TOKEN set — dashboard routes protected"
    else
      echo "  ⚠️  DASHBOARD_TOKEN unset — dashboard routes will be PUBLIC"
      echo "      Set it: export DASHBOARD_TOKEN=\$(openssl rand -hex 32)"
    fi
    if [ -n "$ALLOWED_ORIGIN" ]; then
      flyctl secrets set ALLOWED_ORIGIN="$ALLOWED_ORIGIN" 2>&1 | tail -2
    fi
    if [ -n "$RATE_LIMIT_PER_MIN" ]; then
      flyctl secrets set RATE_LIMIT_PER_MIN="$RATE_LIMIT_PER_MIN" 2>&1 | tail -2
    fi

    flyctl deploy --remote-only 2>&1 | tail -5
    APP_URL=$(flyctl apps info --json 2>/dev/null | grep -o '"Hostname":"[^"]*"' | cut -d'"' -f4 || echo "clow-studio.fly.dev")
    echo "  ✅ Fly.io deploy complete"
    echo "  🌐 URL: https://$APP_URL"
    echo "  📊 Dashboard: https://$APP_URL/dashboard"
  else
    echo "  ⚠️  flyctl not found. Install: curl -fsSL https://fly.io/install.sh | sh"
    echo "  📦 Binary built. Deploy manually: flyctl deploy"
  fi
  echo ""
fi

# ─── Step 3: Verify ───────────────────────────────────────

echo "  ✅ Step 3: Verification"
echo ""

# Test Telegram bots
echo "  Telegram bots:"
for pair in "DEEPTECHX_BOT_TOKEN:@deeptechx_bot" "WANDERSYNC_BOT_TOKEN:@wandersync_bot" "CHAINSHIELD_BOT_TOKEN:@chaincypher_bot" "JOBPULSE_BOT_TOKEN:@gigclow_bot" "PRODUCTIZE_BOT_TOKEN:@productization_bot" "CLAWINVEST_BOT_TOKEN:@investclawd_bot" "LEGAL_BOT_TOKEN:@legalclow_bot" "ELARA_BOT_TOKEN:@elarawellness_bot" "WM_BOT_TOKEN:@wmagentic_bot" "AFRI_BOT_TOKEN:@afri_agent_bot"; do
  IFS=":" read -r var name <<< "$pair"
  TOKEN="${!var}"
  if [ -n "$TOKEN" ]; then
    RESP=$(curl -s "https://api.telegram.org/bot$TOKEN/getMe")
    echo "$RESP" | grep -q '"ok":true' && echo "  ✅ $name: Connected" || echo "  ❌ $name: Failed"
  fi
done

# Test health endpoint
if command -v flyctl &> /dev/null; then
  APP_URL=$(flyctl apps info --json 2>/dev/null | grep -o '"Hostname":"[^"]*"' | cut -d'"' -f4 || echo "clow-studio.fly.dev")
  HEALTH=$(curl -s -o /dev/null -w "%{http_code}" "https://$APP_URL/health" 2>/dev/null || echo "000")
  [ "$HEALTH" = "200" ] && echo "  ✅ Web server: Healthy (HTTP $HEALTH)" || echo "  ⚠️  Web server: HTTP $HEALTH"
fi

echo ""
echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅ Clow Studio v$VERSION Deploy Complete!"
echo ""
echo "  📱 Telegram: t.me/deeptechx_bot (and 9 others)"
echo "  💬 Discord:  Gateway connected (all 9 bots)"
echo "  📱 WhatsApp: +${WHATSAPP_PHONE_NUMBER_ID:-not configured}"
echo "  🌐 Web:      https://${APP_URL:-clow-studio.fly.dev}/app"
echo "  📊 Dashboard: https://${APP_URL:-clow-studio.fly.dev}/dashboard"
echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  🦞 Clow Studio v$VERSION — Productization Blueprint"
echo "  One binary. 10 bots. $3/mo. Deploy anywhere."
echo ""
