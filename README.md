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
- **12 entity platforms**: Climate, Fan, Water Heater, Lock, Cover, Light, Binary Sensor, Button, Number, Select, Sensor, Switch
- **Experimental mode** to enable 50+ additional unverified device classes

## Supported Devices

Device classes fall into two categories:

- **Stable** — enabled by default, no extra configuration needed
- **Experimental** — must be enabled via the integration options; not verified with real hardware

### Stable Device Classes

| Class Code | Device | HA Platform |
|------------|--------|-------------|
| 0x0130 | Home Air Conditioner | Climate + generic entities |
| 0x0133 | Ventilation Fan | Fan + generic entities |
| 0x0134 | Air Conditioner Ventilation Fan | Fan + generic entities |
| 0x0135 | Air Cleaner | Fan + generic entities |
| 0x026B | Electoric Water Heater | Water Heater + generic entities |
| 0x026F | Electric Lock | Lock + generic entities |
| 0x0279 | Residential Solar Power Generation | Generic entities |
| 0x027D | Storage Battery | Generic entities |
| 0x05FD | Switch (JEM-A/HA terminals) | Generic entities |
| 0x05FF | Controller | Generic entities |

### Experimental Device Classes with Dedicated Platforms

The following experimental classes are exposed as fully-featured HA platform entities:

| Class Code | Device | HA Platform |
|------------|--------|-------------|
| 0x0260 | Electrically Operated Blind | Cover + generic entities |
| 0x0263 | Electrically Operated Shutter | Cover + generic entities |
| 0x0290 | General Lighting | Light + generic entities |
| 0x0291 | Mono-Functional Lighting | Light + generic entities |
| 0x02A3 | Lighting System | Light + generic entities |
| 0x02A4 | Extended Lighting System | Light + generic entities |

All other experimental classes are supported via generic entities only.

## Platform Details

### Climate (0x0130)

- **HVAC modes**: Off, Auto, Cool, Heat, Dry, Fan Only
- **Fan modes**: Auto, Low (Level 1) – High (Level 8), 9 speeds
- **Swing modes**: Off, Vertical, Horizontal, Both
- **Temperature**: 0–50°C, 1°C step

### Fan (0x0133, 0x0134, 0x0135)

- **Speed**: 8 levels mapped to percentage
- **Preset modes**: Auto, Manual

### Water Heater (0x026B)

Aggregates operation status (EPC 0x80), operation mode (EPC 0xB0) and target temperature (EPC 0xB3) into a single entity.

- **Operations**: `auto` (automatic water heating), `manual` (manual water heating), `manual_off` (manual heating stopped / away), `off`
- **Target temperature**: setpoint via EPC 0xB3 (range derived from the device's MRA definition, 1°C step)
- **Current temperature**: measured water temperature (EPC 0xC1) — also exposed as a standalone sensor

### Lock (0x026F)

- **Locked state**: requires both main lock (EPC 0xE0) and sub-lock (EPC 0xE1, if advertised) to be locked
- **Jammed state**: indicated when alarm status (EPC 0xE5) reports an abnormality
- **Lock / Unlock**: writes to main lock (EPC 0xE0) only

### Cover (0x0260 Blind, 0x0263 Shutter)

- **Commands**: Open, Close, Stop (always available)
- **Position control** (EPC 0xE1): available when the device supports set operations, 0–100%
- **Tilt control** (EPC 0xE2): available when the device supports set operations, 0–100% mapped to 0–180°
- **State** (EPC 0xEA): Open, Closed, Opening, Closing, Stopped; falls back to position percentage when not advertised

### Light (0x0290, 0x0291, 0x02A3, 0x02A4)

| Feature | 0x0290 General | 0x0291 Mono | 0x02A3 System | 0x02A4 Extended |
|---------|:-:|:-:|:-:|:-:|
| On / Off | ✓ | ✓ | ✓ | ✓ |
| Brightness (EPC 0xB0, 0–100%) | ✓ | ✓ | ✓ | ✓ |
| Color temperature (EPC 0xB1) | ✓ | — | — | — |
| Lighting mode effect (EPC 0xB6) | ✓ | — | — | — |

**Color temperature presets** (0x0290 only): Incandescent (2700 K), White (4000 K), Daylight White (5000 K), Daylight Color (6500 K). Arbitrary kelvin values are snapped to the nearest preset.

**Lighting mode effects** (0x0290 only): `auto`, `normal` (main lighting), `night` (night lighting), `color` (color lighting).

### Generic Entity Platforms

All remaining properties are automatically mapped based on the ECHONET Lite property definition:

| Condition | Writable | Read-only |
|-----------|----------|-----------|
| 2-value enum | Switch | Binary Sensor |
| 3+ value enum | Select | Sensor (enum) |
| 1-value enum | Button | — |
| Numeric | Number | Sensor |

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

> Enabling experimental mode is required for Water Heater, Lock, Cover, Light, and all other non-stable device classes.

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
