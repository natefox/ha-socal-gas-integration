# SoCal Gas Integration for Home Assistant

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

### HACS (Recommended)

1. Open HACS in your Home Assistant instance
2. Click the three dots in the top right corner and select "Custom repositories"
3. Add this repository URL and select "Integration" as the category
4. Click "Download" on the SoCal Gas integration
5. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/socalgas` directory to your Home Assistant's `custom_components` directory
2. Restart Home Assistant

## Setup

When adding the integration, you'll see two options:

### Option 1: Log in with Credentials (Automated, Docker only)

> **Note:** This method requires the Playwright sidecar container, which only works with Docker-based HA installs. If you're running HA OS, use the file upload method below.

1. Go to **Settings** > **Devices & Services** > **Add Integration**
2. Search for "SoCal Gas"
3. Choose **Log in with socalgas.com credentials**
4. Enter your socalgas.com email and password
5. Name your account (defaults to last 4 digits of account number)
6. Choose how many days of historical data to import (default: 365)
7. The integration will automatically fetch data daily

**Docker setup required:** You must run the Playwright sidecar container alongside HA. See [Development](#development) for docker-compose setup.

### Option 2: Upload Green Button Data (Manual, works everywhere)

This works on all HA installations including HA OS, HA Supervised, and Docker.

1. Log in to your account at [socalgas.com](https://www.socalgas.com)
2. Navigate to **My Account** → **Analyze Usage** → **Download My Data (Green Button)**
3. Select the date range you want (up to 13 months)
4. Choose "60 Minute" interval and download the ZIP file
5. Go to **Settings** > **Devices & Services** > **Add Integration**
6. Search for "SoCal Gas"
7. Choose **Upload a Green Button data file**
8. Upload the ZIP file and name your account

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
| HA Container (Docker) | Yes (with sidecar) | Yes |
| HA OS | No | Yes |
| HA Supervised | Experimental | Yes |
| HA Core | No | Yes |

The credentials method requires the Playwright sidecar Docker container because the socalgas.com login page uses client-side JavaScript that cannot be handled by plain HTTP requests.

## Development

### Prerequisites

- Docker and Docker Compose
- Python 3.12+ (for running tests locally)

### Running the Dev Environment

```bash
docker compose up -d --build
```

This starts both Home Assistant and the Playwright sidecar container. Open http://localhost:8123 to access HA.

The `custom_components/socalgas` directory is mounted into the container, so code changes are reflected on restart.

### Playwright Sidecar

The sidecar container runs headless Chromium to handle the socalgas.com login flow. It's configured via `docker-compose.yml` and communicates with HA over the Docker network.

```bash
# Rebuild sidecar after changes
docker compose build playwright && docker compose up -d playwright

# Check sidecar health
docker exec ha-socalgas-playwright curl -s http://localhost:3000/health

# View sidecar logs
docker logs ha-socalgas-playwright -f --tail=50
```

### Running Tests

```bash
python -m pytest tests/ -v
```

## License

MIT
