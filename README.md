# SoCal Gas Integration for Home Assistant

<img src="https://github.com/nfox/ha-socal-gas-integration/raw/main/custom_components/socalgas/logo.png" width="128" alt="SoCal Gas logo">

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-blue?style=for-the-badge&logo=homeassistantcommunitystore&logoColor=ccc)](https://hacs.xyz)
[![HA Version](https://img.shields.io/badge/HA-2024.1.0+-green?style=for-the-badge&logo=home-assistant&logoColor=ccc)](https://www.home-assistant.io)

A custom Home Assistant integration that imports gas usage data from Southern California Gas Company (SoCal Gas) using their Green Button data export.

## Features

- **Two setup methods:** Automatic login with socalgas.com credentials, or manual Green Button ZIP upload
- Automatic data fetching with configurable refresh interval (credentials mode)
- Configurable historical data import (up to 2 years)
- On-demand re-download of specific date ranges
- Automatic integration with Home Assistant's Energy Dashboard
- Hourly usage and cost tracking via HA's long-term statistics
- Re-import additional data files via integration options
- Idempotent imports — duplicate data is overwritten, not duplicated

## Installation

### Step I: Install the integration

#### Option 1: via HACS

[![Open your Home Assistant instance and add this repository to HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=nfox&repository=ha-socal-gas-integration&category=integration)

1. Click the button above, or manually add this repository as a custom repository in HACS
2. Search for "SoCal Gas" and download the integration
3. **Restart Home Assistant**
4. Continue to _Step II: Adding the integration_

#### Option 2: Manual installation

1. Using the tool of choice, open the directory for your HA configuration (where you find `configuration.yaml`)
2. If you do not have a `custom_components` directory there, you need to create it
3. Copy the `custom_components/socalgas` directory from this repository into your `custom_components` directory
4. **Restart Home Assistant**
5. Continue to _Step II: Adding the integration_

### Step II: Adding the integration

**You must have installed the integration (Step I) before proceeding!**

#### Option 1: My Home Assistant (2021.3+)

Just click the following button to start the configuration automatically:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=socalgas)

#### Option 2: Manually

1. Go to **Settings** > **Devices & Services** > **Add Integration**
2. Search for "SoCal Gas" and select it

#### Setup options

When adding the integration, you'll see two setup methods:

**Log in with socalgas.com credentials (Automated)**

> Requires [Browserless Chrome](https://www.browserless.io/) — a headless browser service that handles the socalgas.com login flow. See [Browserless Setup](#browserless-setup) below for your install type.

1. Choose **Log in with socalgas.com credentials**
2. Enter your socalgas.com email, password, and Browserless URL
3. Name your account (defaults to last 4 digits of account number)
4. Choose how many days of historical data to import (default: 365)
5. The integration will automatically fetch data daily

**Upload Green Button Data (Manual, works everywhere)**

This works on all HA installations without any additional setup.

1. Log in to your account at [socalgas.com](https://www.socalgas.com)
2. Navigate to **My Account** → **Analyze Usage** → **Download My Data (Green Button)**
3. Select the date range you want (up to 13 months)
4. Choose "60 Minute" interval and download the ZIP file
5. Choose **Upload a Green Button data file**
6. Upload the ZIP file and name your account

### Browserless Setup

The credentials method requires a Browserless Chrome instance because the socalgas.com login page uses client-side JavaScript that cannot be handled by plain HTTP requests.

#### HA OS / HA Supervised

1. Install the **Browserless Chrome** add-on from the [alexbelgium add-on repository](https://github.com/alexbelgium/hassio-addons)
2. Start the add-on
3. In the integration config, set the Browserless URL to `http://addon-browserless-chrome:3000` (append `?token=YOUR_TOKEN` if you configured a token in the add-on)

#### Docker

Add the Browserless service to your `docker-compose.yml`:

```yaml
browserless:
  image: ghcr.io/browserless/chromium
  container_name: ha-socalgas-browserless
  restart: unless-stopped
  environment:
    - TOKEN=my-secret-token
  dns:
    - 8.8.8.8
    - 8.8.4.4
```

In the integration config, set the Browserless URL to `http://browserless:3000?token=my-secret-token`.

#### HA Core (venv)

Run Browserless Chrome as a standalone Docker container and set the Browserless URL to `http://localhost:3000?token=my-secret-token` (or wherever Browserless is reachable).

### Energy Dashboard Setup

1. Go to **Settings** > **Dashboards** > **Energy**
2. Under "Gas consumption", click "Add gas source"
3. Select **SoCal Gas Usage** (unit: ft³)
4. For cost tracking, select **SoCal Gas Cost** (unit: USD)

## Configuration

After initial setup, you can configure the integration by going to **Settings** > **Devices & Services**, finding SoCal Gas, and clicking **Configure**. You'll see a menu with the following options:

### Re-download a Date Range

*(Credentials mode only)* Re-download data for a specific date range from socalgas.com. Useful if data was missing or you want to refresh a particular period. The start date can go back up to 730 days (2 years).

1. Click **Configure** on the integration
2. Choose **Re-download a date range**
3. Pick a start and end date (defaults to the last 30 days)
4. The download runs in the background — check the **Notifications** panel for progress

### Upload a Green Button File

Import additional or updated data from a manually downloaded Green Button ZIP file. Duplicate data is handled automatically.

1. Click **Configure** on the integration
2. Choose **Upload a Green Button file**
3. Select your ZIP file

### Change Settings

Configure the automatic refresh interval (credentials mode). The default is 24 hours. You can set it anywhere from 1 to 168 hours (1 week).

1. Click **Configure** on the integration
2. Choose **Change settings**
3. Set the refresh interval in hours

### How Automatic Updates Work

When set up with credentials, the integration automatically downloads new data on a recurring schedule (default: every 24 hours).

- **Initial setup**: downloads your full historical data based on the lookback period you chose
- **Subsequent refreshes**: queries the HA recorder for the latest imported data point and fetches from there (with a 1-day overlap buffer), up to a maximum of 30 days. If HA was offline for a week, the next refresh automatically catches up.
- **Data is downloaded in 30-day chunks** due to SoCal Gas API limits, with a 5-second pause between chunks to avoid rate limiting. All chunks are downloaded first, then deduplicated by hour and imported as a single batch to ensure consistent cumulative sums.
- **Duplicate data is overwritten**, not duplicated — re-importing the same date range is safe

All downloads happen in the background. You can monitor progress via the **Notifications** panel (bell icon in the sidebar). Import and re-download operations use separate notifications so they don't overwrite each other. All progress is also logged to the Home Assistant log at INFO level.

Only one download operation runs at a time — if you trigger a re-download while an automatic refresh is in progress, it will wait for the refresh to complete first.

## Compatibility

| Install Method | Credentials (auto) | File Upload (manual) |
|---|---|---|
| HA Container (Docker) | Yes (with Browserless service) | Yes |
| HA OS | Yes (with Browserless add-on) | Yes |
| HA Supervised | Yes (with Browserless add-on) | Yes |
| HA Core | Yes (with external Browserless) | Yes |

## Development

### Prerequisites

- Docker and Docker Compose
- Python 3.12+ (for running tests locally)

### Running the Dev Environment

```bash
docker compose up -d
```

This starts both Home Assistant and the Browserless Chrome container. Open http://localhost:8123 to access HA.

The `custom_components/socalgas` directory is mounted into the container, so code changes are reflected on restart.

The default Browserless URL for the dev environment is `http://browserless:3000?token=my-secret-token`.

### Running Tests

```bash
python -m pytest tests/ -v
```

## License

MIT
