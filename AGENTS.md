# AGENTS.md — AI Maintainer Guide

## Project Overview

Home Assistant custom integration for SoCal Gas (Southern California Gas Company). Imports natural gas usage and cost data into HA's long-term statistics via the Energy Dashboard.

**Domain:** `socalgas`
**Data source:** Green Button (ESPI) XML format, delivered as ZIP files
**Statistics unit:** ft³ (cubic feet) — 1 therm ≈ 100 ft³

## Architecture

### Two Setup Paths

1. **Credentials (automated):** User provides socalgas.com login → Playwright sidecar handles browser login → captures AccessToken → downloads Green Button data daily via SmartCMobile API
2. **File Upload (manual):** User uploads a Green Button ZIP downloaded from socalgas.com → parsed and imported immediately

### Authentication Flow (Credentials Path)

```
SoCalGasAPI.authenticate()
  → Try plain HTTP login (myaccount.socalgas.com JSON API)
  → If bot-blocked → Fall back to Playwright sidecar (HTTP call)
  → Get account number (SmartCMobile API)
  → Get GNN mapping (meter number, GNN ID)
  → Download Green Button ZIP (SmartCMobile API)
```

The socalgas.com login is a JavaScript SPA (Stencil.js web components, shadow DOM). The AccessToken is generated client-side, which is why Playwright is needed when plain HTTP fails.

### Playwright Sidecar

A separate Docker container (`playwright-sidecar/`) running headless Chromium via aiohttp. Required because Playwright can't install on Alpine Linux (HA's Docker base image uses musl libc).

- `GET /health` → health check
- `POST /authenticate` → `{"username": "...", "password": "..."}` → `{"access_token": "...", "account_number": "..."}`
- Communicates with HA over Docker network via `SOCALGAS_BROWSER_URL` env var

### Data Pipeline

```
Green Button ZIP → parse XML → GreenButtonReading[] → readings_to_hourly_statistics() → StatisticEntry[] → async_import_to_ha() → HA Recorder
```

- `async_add_external_statistics` is idempotent — duplicate timestamps are overwritten
- Statistics are external (not entities), visible in Energy Dashboard and Developer Tools → Statistics

## Key Files

| File | Purpose |
|------|---------|
| `config_flow.py` | Setup wizard — menu (credentials/upload), account naming, lookback config |
| `api.py` | SoCal Gas API client — login, SSO, GNN mapping, Green Button download |
| `browser.py` | HTTP client to Playwright sidecar |
| `coordinator.py` | DataUpdateCoordinator — daily fetches, chunked historical import |
| `statistics.py` | Converts readings to HA statistics format (ft³ + USD) |
| `green_button_parser.py` | Parses Green Button ESPI XML |
| `const.py` | Constants and config keys |
| `__init__.py` | Entry setup — creates coordinator if credentials exist |
| `playwright-sidecar/server.py` | Standalone Playwright server |

## API Endpoints Used

| Endpoint | Base | Auth | Purpose |
|----------|------|------|---------|
| `/public/api/v1/web-user/authentication/login` | myaccount.socalgas.com | None | Login |
| `/api/v1/web-user/authentication/validate-and-refresh-session` | myaccount.socalgas.com | Cookies | Session refresh |
| `/api/v1/web-user/authentication/initialize-ways-to-save` | myaccount.socalgas.com | Cookies | SSO bridge |
| `/connectorsso/api/account/list` | socal.smartcmobile.com | AccessToken | Account list |
| `/connectorsso/api/usage/gnnmapping` | socal.smartcmobile.com | AccessToken | Meter mapping |
| `/greenbuttonservices/api/greenbutton/zipfile` | socal.smartcmobile.com | AccessToken | Usage data |

## Common Pitfalls

1. **SoCal Gas rate limits aggressively.** Repeated logins within minutes cause redirect to `/ui/error`. The sidecar detects this and returns 500. Wait a few minutes.

2. **SmartCMobile API responses have inconsistent shapes.** Account list returns a bare JSON array `[{...}]`, not `{"accounts": [...]}`. GNN mapping wraps in `{"GnnMeterMap": [...]}`. Always handle both.

3. **DNS ad-blockers break the login page.** The SPA depends on `sdk.split.io` for feature flags. If blocked (resolves to 127.0.0.1), the page shows "Loading..." forever. The docker-compose uses `dns: 8.8.8.8` for the sidecar.

4. **HA Energy Dashboard only accepts specific gas units.** Must use `ft³` (not therms). Conversion: therms × 100 = ft³.

5. **`config_entry` is read-only on OptionsFlow.** Use `self._entry` instead of `self.config_entry`.

6. **Reauth should only trigger for actual credential errors**, not connection failures or rate limiting. Check for "invalid" + "password" in the error message.

7. **Green Button downloads max ~31 days per request.** The coordinator chunks requests into 30-day windows.

## Testing

```bash
python -m pytest tests/ -v
```

Tests mock `homeassistant` modules and test:
- Green Button XML parsing (real ZIP fixture included)
- Statistics conversion (therms → ft³, running sums)
- Browser HTTP client (mock aiohttp responses)
- Config flow structure (menu, lookback step, source ordering)
- API client basics

## Development Setup

```bash
# Run HA + sidecar
docker compose up -d --build

# Rebuild just the sidecar
docker compose build playwright && docker compose up -d playwright

# Test sidecar health
docker exec ha-socalgas-playwright curl -s http://localhost:3000/health

# Check HA logs
docker logs ha -f --tail=50

# Nuke and restart clean
docker compose down -v && docker compose up -d --build
```
