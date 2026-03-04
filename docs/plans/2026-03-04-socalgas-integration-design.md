# SoCal Gas Home Assistant Integration — Design

## Overview

A HACS-installable Home Assistant custom integration for SoCal Gas usage data. Phase 1 imports historical data from downloaded Green Button ZIP files. Phase 2 automates data fetching via SoCal Gas account login.

## Data Source

SoCal Gas provides Green Button Data (ESPI standard XML) via their website. The export contains:
- Hourly interval readings with Unix timestamps, gas consumption values, and cost
- Units: therms (raw values use `powerOfTenMultiplier=2`, so divide by 100)
- Billing period summaries with total cost and consumption
- Date range: configurable, up to 13 months of hourly data (~18,900 readings)

## Architecture

```
custom_components/socalgas/
├── __init__.py              # Integration setup, platform forwarding
├── manifest.json            # HA integration manifest (HACS-compatible)
├── config_flow.py           # UI config: name + file upload (Phase 1), credentials (Phase 2)
├── const.py                 # Domain, unit constants
├── sensor.py                # Sensor entities (gas consumption, cost)
├── coordinator.py           # DataUpdateCoordinator (Phase 2: periodic fetch)
├── green_button_parser.py   # ESPI XML parser → structured reading data
├── statistics.py            # Historical data injection via async_import_statistics
├── strings.json             # UI strings
└── translations/en.json     # English translations
```

Dev environment: Docker Compose with HA Core, custom_components mounted in.

## Phase 1: Historical Data Import

### Config Flow
1. User adds integration, names the account/location
2. User uploads Green Button ZIP file via HA file upload
3. Integration parses XML, injects historical statistics, creates sensors

### Green Button Parser
- Parses ESPI XML (namespace `http://naesb.org/espi`)
- Extracts `IntervalBlock` → `IntervalReading` entries
- Each reading: start (Unix epoch), duration (3600s), value (therms/100), cost (cents)
- Extracts `UsageSummary` for billing totals

### Statistics Injection
- Uses `recorder.async_import_statistics()` with `StatisticData` objects
- Creates cumulative `sum` statistics for energy dashboard compatibility
- HA's statistics engine handles hourly/daily/monthly rollups automatically

### Sensors
- `sensor.socalgas_gas_consumption` — state_class: `total_increasing`, device_class: `gas`, unit: therms
- `sensor.socalgas_gas_cost` — state_class: `total_increasing`, device_class: `monetary`, unit: USD
- Both integrate with HA's energy dashboard natively

## Phase 2: Automated Data Fetching (future)

- Add SoCal Gas credentials to config flow (username/password)
- Authenticate and download Green Button data programmatically
- DataUpdateCoordinator polls periodically for new readings
- Append new data to existing statistics

## Dev Environment

`docker-compose.yml` runs HA Core with `custom_components/` volume-mounted. No additional services needed (no MQTT/Mosquitto — using direct statistics injection).

## Key Decisions

1. **Direct statistics injection over MQTT** — HA's `import_statistics` API properly backdates data; MQTT would record at receive-time
2. **Single total_increasing sensor** — HA energy dashboard handles all rollup views automatically
3. **Green Button XML over XLSX** — XML is the structured standard; XLSX is just a rendered view
4. **Config flow file upload** — standard HA pattern for user-provided data files
