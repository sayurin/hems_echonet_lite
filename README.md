# HEMS ECHONET Lite Integration for Home Assistant

[![HACS Default](https://img.shields.io/badge/HACS-Default-orange)](https://hacs.xyz/)
[![Quality Scale: Bronze](https://img.shields.io/badge/Quality%20Scale-Bronze-orange)](https://www.home-assistant.io/docs/quality_scale/)
[![License: MIT](https://img.shields.io/github/license/sayurin/hems_echonet_lite)](LICENSE)
[![Sponsor](https://img.shields.io/badge/Sponsor-%E2%9D%A4-pink?logo=github)](https://github.com/sponsors/sayurin)

ECHONET Lite protocol integration for Home Assistant, powered by [pyhems](https://github.com/sayurin/pyhems). Communicates with ECHONET Lite compatible devices on the local network via UDP multicast.

## Features

- **Automatic device discovery** via ECHONET Lite multicast (224.0.23.0:3610)
- **Periodic re-discovery** every hour for newly joined devices
- **Event-driven updates** — entity state changes are pushed immediately upon frame receipt
- **Property polling** every 60 seconds for devices that do not send notifications
- **Runtime health monitoring** — creates repair issues when no frames are received for 5 minutes
- **8 entity platforms**: Climate, Fan, Binary Sensor, Button, Number, Select, Sensor, Switch
- **Experimental mode** to enable unverified device classes

## Supported Devices

### Stable Device Classes

These device classes are enabled by default:

| Class Code | Device | Entity Platform |
|------------|--------|-----------------|
| 0x0130 | Home Air Conditioner | Climate + generic entities |
| 0x0135 | Air Cleaner | Fan + generic entities |
| 0x0279 | Residential Solar Power Generation | Generic entities |
| 0x027D | Storage Battery | Generic entities |
| 0x05FD | Switch (supporting JEM-A/HA terminals) | Generic entities |
| 0x05FF | Controller | Generic entities |

### Climate (0x0130) Features

- **HVAC modes**: Off, Auto, Cool, Heat, Dry, Fan Only
- **Fan modes**: Auto, Low (Level 1) – High (Level 8), 9 speeds
- **Swing modes**: Off, Vertical, Horizontal, Both
- **Temperature**: 0–50°C, 1°C step

### Fan (0x0133, 0x0134, 0x0135) Features

- **Speed**: 8 levels mapped to percentage
- **Preset modes**: Auto, Manual

### Generic Entity Platforms

Properties are automatically mapped to entity platforms based on the ECHONET Lite property definition:

| Condition | Writable | Read-only |
|-----------|----------|-----------|
| 2-value enum | Switch | Binary Sensor |
| 3+ value enum | Select | Sensor (enum) |
| 1-value enum | Button | — |
| Numeric | Number | Sensor |

### Experimental Device Classes

Enable **"Enable experimental device classes"** in the integration options to access 50+ additional device classes, including:

Water heaters, electric locks, lighting, refrigerators, washing machines, smart meters, EV chargers, bathroom dryers, and more.

> **Note**: Experimental device classes have not been verified with real hardware. Some entities may behave unexpectedly.

## Installation

### HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=sayurin&repository=hems_echonet_lite&category=integration)

1. Click the button above, or search for **"HEMS echonet lite"** in HACS
2. Install the integration
3. Restart Home Assistant

### Manual

1. Copy `custom_components/echonet_lite` to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **"HEMS echonet lite"**
3. Select your network interface:
   - **Auto** (`0.0.0.0`): Listen on all interfaces (recommended)
   - **Specific IP**: Bind to a particular network interface
4. The integration starts listening on the ECHONET Lite multicast group (224.0.23.0:3610)

> Only one instance is allowed per Home Assistant installation (`single_config_entry`).

### Options

After setup, configure in **Settings → Devices & Services → HEMS → Configure**:

| Option | Description | Default |
|--------|-------------|---------|
| Enable experimental device classes | Include unverified device classes | Off |

### Reconfiguration

The network interface can be changed at any time via **Settings → Devices & Services → HEMS → Reconfigure**.

## Network Requirements

- UDP multicast support on your network
- Port 3610 accessible
- ECHONET Lite devices on the same network segment (or with multicast routing)
- Multicast group 224.0.23.0 allowed through firewalls

## Troubleshooting

### No Devices Discovered

1. Verify ECHONET Lite devices are powered on and connected to the network
2. Check that UDP multicast is enabled on your router/switch
3. Try selecting a specific network interface instead of Auto
4. Confirm port 3610 is not blocked by a firewall

### Repair Issues

The integration automatically creates repair issues when:

- **Runtime inactive**: No ECHONET Lite frames received for 5 minutes — check device power and network connectivity
- **Runtime client error**: A network error occurred — use the repair flow to restart the service

## Requirements

- Home Assistant 2024.1 or later

## Acknowledgments

- [ECHONET Consortium](https://echonet.jp/) for the ECHONET Lite specification
