# SoCal Gas Integration for Home Assistant

A custom Home Assistant integration that imports gas usage data from Southern California Gas Company (SoCal Gas) using their Green Button data export.

## Features

- Import historical gas usage data from SoCal Gas Green Button ZIP exports
- Automatic integration with Home Assistant's Energy Dashboard
- Hourly, daily, and monthly usage views via HA's built-in statistics
- Cost tracking from billing data
- Re-import additional data files via integration options

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

### Downloading Your Data from SoCal Gas

1. Log in to your account at [socalgas.com](https://www.socalgas.com)
2. Navigate to "My Usage" or "Green Button" data export
3. Select the date range you want (up to 13 months)
4. Choose "60 Minute" interval
5. Download the ZIP file

### Adding the Integration

1. Go to **Settings** > **Devices & Services** > **Add Integration**
2. Search for "SoCal Gas"
3. Enter a name for your account/location
4. Upload the Green Button ZIP file you downloaded
5. The integration will parse and import all historical data

### Energy Dashboard Setup

1. Go to **Settings** > **Dashboards** > **Energy**
2. Under "Gas consumption", click "Add gas source"
3. Select the "SoCal Gas Usage" statistic
4. Optionally configure the cost tracking entity

### Importing Additional Data

To import additional data files (e.g., updated exports):

1. Go to **Settings** > **Devices & Services**
2. Find the SoCal Gas integration and click **Configure**
3. Upload a new Green Button ZIP file

## Development

### Prerequisites

- Docker and Docker Compose

### Running the Dev Environment

```bash
docker compose up -d
```

Then open http://localhost:8123 to access Home Assistant.

The `custom_components/socalgas` directory is mounted into the container, so changes are reflected on restart.

### Running Tests

```bash
python -m pytest tests/ -v
```

## Roadmap

- **Phase 1** (current): Manual import of Green Button data files
- **Phase 2**: Automated data fetching via SoCal Gas account login

## License

MIT
