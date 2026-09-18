# Evo-Decepticon security-fix snapshot — 2026-09-17

Passive recon + source patches for Desired Solutions / Clow Studio live assets.
Method: Decepticon RoE → Recon → Detector → Verifier → Patcher. No exploitation.

## Contents

| Path | What |
|---|---|
| `evo-decepticon-scan-20260917.md` | Findings (2 CRITICAL, 3 HIGH, 3 MEDIUM) and applied LHFs |
| `meta-prompt-framework.md` | Panoptic system-dynamics meta-prompt (GeoClaw stale-link note updated) |
| `deploy-flyio/internal/web/server.go` | Auth + security headers + per-IP rate limit middleware |
| `deploy-flyio/deploy.sh` | Fly.io deploy script that sets `DASHBOARD_TOKEN`, `ALLOWED_ORIGIN`, `RATE_LIMIT_PER_MIN` |

## Production still open until deploy

These files are **source only**. `picoclaw-server.fly.dev` is not protected until:

1. Build + deploy the patched `server.go` from a Go machine.
2. `flyctl secrets set DASHBOARD_TOKEN=$(openssl rand -hex 32)`
3. `flyctl secrets set ALLOWED_ORIGIN=https://clow.studio`
4. Re-scan `/dashboard/data` (must return `401` without the token).

Do not treat this folder as the Decepticon runtime. It is a DS-asset vaccine snapshot living inside the evo-decepticon repo.
