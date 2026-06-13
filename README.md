# HEMS Echonet Lite Integration for Home Assistant

[![HACS](https://img.shields.io/badge/hacs-default-blue)](https://hacs.xyz/)
[![Quality Scale](https://img.shields.io/github/manifest-json/quality_scale/sayurin/hems_echonet_lite?filename=custom_components/echonet_lite/manifest.json&label=quality+scale&color=mediumpurple)](https://www.home-assistant.io/docs/quality_scale/)
[![License](https://img.shields.io/github/license/sayurin/hems_echonet_lite)](https://github.com/sayurin/hems_echonet_lite/blob/master/LICENSE)
[![Version](https://img.shields.io/github/v/release/sayurin/hems_echonet_lite)](https://github.com/sayurin/hems_echonet_lite/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/sayurin/hems_echonet_lite/latest/total)](https://github.com/sayurin/hems_echonet_lite/releases/latest)
[![Starts](https://img.shields.io/github/stars/sayurin/hems_echonet_lite?style=flat&color=gold)](https://github.com/sayurin/hems_echonet_lite)
[![Sponsor](https://img.shields.io/github/sponsors/sayurin?color=darksalmon)](https://github.com/sponsors/sayurin)

[日本語](README.ja.md)

ECHONET Lite protocol integration for Home Assistant, powered by [pyhems](https://github.com/sayurin/pyhems). Communicates with ECHONET Lite compatible devices on the local network via UDP multicast — no cloud, no account, no API key required.

## Use Cases

- Control your air conditioner remotely or via automations, and monitor its state.
- Monitor residential solar power generation and storage battery charge levels.
- Automate water heater schedules to heat water during off-peak electricity hours.
- Lock or unlock your electric door lock based on time or presence.
- Control blinds and shutters based on sunlight or schedules.
- Control lighting based on ambient conditions or schedules.

## Supported Devices

Device classes fall into two categories:

- **Stable** — verified with real hardware; enabled by default, no extra configuration needed.
- **Experimental** — not verified with real hardware; must be enabled via integration options.

### Stable Device Classes

| Class Code | Device | HA Platform |
|------------|--------|-------------|
| 0x0130 | Home Air Conditioner | Climate + generic entities |
| 0x0135 | Air Cleaner | Fan + generic entities |
| 0x026B | Electric Water Heater | Water Heater + generic entities |
| 0x026F | Electric Lock | Lock + generic entities |
| 0x0279 | Residential Solar Power Generation | Generic entities |
| 0x027D | Storage Battery | Generic entities |
| 0x05FD | Switch (JEM-A/HA terminals) | Generic entities |
| 0x05FF | Controller | Generic entities |

Verified hardware:
- **Home Air Conditioner**: Mitsubishi Electric Kirigamine Z series
- **Air Cleaner**: Sharp KI-SX70-W
- **電気錠**: Yamato Denki ECHONET Lite Adapter
- **Residential Solar Power Generation / Storage Battery**: Sharp SUNVISTA
- **Switch**: Panasonic HF-JA1
- **Controller**: Sharp JH-RVB1, JH-RWL8

### Experimental Device Classes

The following classes require **Enable experimental device classes** to be turned on in the integration options.

**With dedicated HA platform entities:**

| Class Code | Device | HA Platform |
|------------|--------|-------------|
| 0x0133 | Ventilation Fan | Fan + generic entities |
| 0x0134 | Air Conditioner Ventilation Fan | Fan + generic entities |
| 0x0260 | Electrically Operated Blind | Cover + generic entities |
| 0x0263 | Electrically Operated Shutter | Cover + generic entities |
| 0x0290 | General Lighting | Light + generic entities |
| 0x0291 | Mono-Functional Lighting | Light + generic entities |
| 0x02A3 | Lighting System | Light + generic entities |
| 0x02A4 | Extended Lighting System | Light + generic entities |

**Via generic entities only** (50+ additional classes including sensors, meters, EV chargers, cookware, and more — see the full list in the integration options UI).

## Supported Functionality

### Climate (0x0130)

- **HVAC modes**: Off, Auto, Cool, Heat, Dry, Fan Only
- **Fan modes**: Auto, Low (Level 1) – High (Level 8), 9 speeds
- **Swing modes**: Off, Vertical, Horizontal, Both
- **Temperature**: 0–50°C, 1°C step

### Fan (0x0133, 0x0134, 0x0135)

- **Speed**: 8 levels mapped to percentage
- **Preset modes**: Auto, Manual

### Water Heater (0x026B)

Aggregates operation status (EPC 0x80), operation mode (EPC 0xB0), and target temperature (EPC 0xB3) into a single entity.

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

## Manufacturer-Specific Extensions

Beyond the standard ECHONET Lite specification, the integration includes additional property definitions for specific manufacturers. These are applied automatically when the matching manufacturer code is detected on a device.

### Sharp — Air Cleaner (0x0135)

Sharp air cleaners expose extra environmental data via manufacturer-specific EPC 0xF1. The following sensor entities are added when a Sharp device is detected:

| Entity | Unit | Notes |
|--------|------|-------|
| Temperature | °C | Room temperature measured inside the unit |
| Humidity | %RH | Relative humidity measured inside the unit |
| PM2.5 | µg/m³ | Particulate matter concentration |

### Sharp — Residential Solar Power Generation (0x0279)

Sharp solar power systems expose per-string input data via manufacturer-specific EPCs. The following sensor entities are added when a Sharp device is detected:

| Entity | Unit | EPC |
|--------|------|-----|
| Input Voltage 1–4 | V | 0xF2 |
| Input Current 1–4 | A | 0xF3 |
| Input Power 1–4 | W | 0xF4 |

### Sharp — Controller (0x05FF)

Sharp home energy controllers expose grid buy/sell data via manufacturer-specific EPCs. The following sensor entities are added when a Sharp device is detected:

| Entity | Unit | EPC |
|--------|------|-----|
| Instantaneous Electric Power Sold | W | 0xF2 |
| Instantaneous Electric Power Bought | W | 0xF3 |
| Cumulative Electric Energy Sold | Wh | 0xF4 |
| Cumulative Electric Energy Bought | Wh | 0xF5 |

## Data Updates

The integration uses both polling and event-driven updates:

- **Event-driven**: Devices that support property change notifications (INF frames) push state changes immediately upon receipt.
- **Property polling**: Every 60 seconds for devices that do not send notifications.
- **Device re-discovery**: Every hour via multicast, so newly joined devices are detected automatically.
- **Health monitoring**: If no ECHONET Lite frames are received for 5 minutes, a repair issue is created.

## Installation

### HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=sayurin&repository=hems_echonet_lite&category=integration)

1. Select the button above, or search for **"HEMS Echonet Lite"** in HACS.
2. Install the integration.
3. Restart Home Assistant.

### Manual

1. Copy `custom_components/echonet_lite` to your Home Assistant `custom_components` directory.
2. Restart Home Assistant.

## Prerequisites

- Home Assistant 2026.3 or later
- ECHONET Lite compatible devices on the same local network as Home Assistant
- UDP multicast traffic allowed on your network (address 224.0.23.0, port 3610)
- If running Home Assistant in a container or VM, ensure multicast traffic is properly forwarded (for example, `network_mode: host` in Docker)

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **"HEMS Echonet Lite"**.
3. Select your network interface:
   - **Auto** (`0.0.0.0`): Listen on all interfaces (recommended)
   - **Specific IP**: Bind to a particular network interface
4. The integration starts listening on the ECHONET Lite multicast group (224.0.23.0:3610) and discovers devices automatically.

> Only one instance is allowed per Home Assistant installation.

### Options

After setup, configure in **Settings → Devices & Services → HEMS Echonet Lite → Configure**:

| Option | Description | Default |
|--------|-------------|---------|
| Enable experimental device classes | Include device classes that have not been verified with real hardware. These may not work correctly. | Off |

### Reconfiguration

The network interface can be changed at any time via **Settings → Devices & Services → HEMS Echonet Lite → Reconfigure**.

## Examples

### Control air conditioning based on a schedule

Turn off the air conditioner automatically when everyone leaves home, and turn it back on shortly before someone returns.

```yaml
automation:
  - alias: "Turn off AC when everyone leaves"
    triggers:
      - trigger: state
        entity_id: group.everyone
        to: not_home
    actions:
      - action: climate.turn_off
        target:
          entity_id: climate.living_room_air_conditioner

  - alias: "Pre-cool before arriving home"
    triggers:
      - trigger: state
        entity_id: group.everyone
        to: home
    actions:
      - action: climate.set_temperature
        target:
          entity_id: climate.living_room_air_conditioner
        data:
          hvac_mode: cool
          temperature: 26
```

### Heat water during off-peak electricity hours

Schedule the electric water heater to heat water at night when electricity rates are lower.

```yaml
automation:
  - alias: "Start water heating at off-peak hours"
    triggers:
      - trigger: time
        at: "23:00:00"
    actions:
      - action: water_heater.set_operation_mode
        target:
          entity_id: water_heater.electric_water_heater
        data:
          operation_mode: auto

  - alias: "Stop water heating in the morning"
    triggers:
      - trigger: time
        at: "06:00:00"
    actions:
      - action: water_heater.set_operation_mode
        target:
          entity_id: water_heater.electric_water_heater
        data:
          operation_mode: manual_off
```

### Auto-lock the door at bedtime

Lock the electric door lock every night and send a notification if it was already unlocked.

```yaml
automation:
  - alias: "Auto-lock door at bedtime"
    triggers:
      - trigger: time
        at: "23:30:00"
    conditions:
      - condition: state
        entity_id: lock.front_door
        state: unlocked
    actions:
      - action: lock.lock
        target:
          entity_id: lock.front_door
      - action: notify.mobile_app
        data:
          message: "Front door was unlocked and has been locked automatically."
```

### Close blinds at sunset

Automatically close electrically operated blinds when the sun sets.

```yaml
automation:
  - alias: "Close blinds at sunset"
    triggers:
      - trigger: sun
        event: sunset
        offset: "-00:30:00"
    actions:
      - action: cover.close_cover
        target:
          entity_id: cover.living_room_blind
```

## Known Limitations

- Only IPv4 networks are supported.
- UDP multicast must be supported and enabled on your network.
- Some device properties may not be available if the device does not advertise them in its property map.
- Experimental device classes have not been tested with real hardware and may not function correctly.
- Only one integration instance per Home Assistant installation is supported.

## Troubleshooting

### No devices discovered

After setting up the integration, no devices appear at all.

1. Verify your ECHONET Lite devices are powered on and connected to the network.
2. Check that UDP multicast traffic (224.0.23.0:3610) is allowed on your network.
3. If using Docker, ensure the container uses `network_mode: host` or has proper multicast routing configured.
4. Try selecting a specific network interface instead of **Auto** in the integration settings.
5. Check the Home Assistant logs for any error messages related to `echonet_lite` or `pyhems`.

### Some devices not discovered

Some ECHONET Lite devices appear, but others do not.

1. If the missing device is an experimental device class, enable **Enable experimental device classes** in the integration options.
2. Some devices may take longer to respond. Wait a few minutes and check again, as re-discovery runs every hour.
3. Try reloading the integration from **Settings → Devices & Services → HEMS Echonet Lite → ⋮ → Reload**.
4. Verify the device supports ECHONET Lite. Some appliances have ECHONET Lite disabled by default and require enabling via the manufacturer's app or settings.

### Devices show as unavailable

Devices were discovered but later show as unavailable.

1. Check the device's network connection and power state.
2. Verify the device has not entered a power-saving mode that disables network communication.
3. Check **Settings → System → Repairs** for any issues reported by the integration and follow the suggested resolution steps.

### Repair issues

The integration automatically creates repair issues in the following situations:

- **Runtime inactive**: No ECHONET Lite frames received for 5 minutes — check device power and network connectivity.
- **Runtime client error**: A network error occurred — use the repair flow to restart the service.

## Removing the Integration

To remove the integration, go to **Settings → Devices & Services**, select **HEMS Echonet Lite**, and select **Delete**.

### Removing a single device

Individual devices can be removed from **Settings → Devices & Services → HEMS Echonet Lite → (device) → Delete**, but only when the device is no longer reachable on the local network (not currently active in the integration).

If a device is still being discovered (powered on and responding), removal will be rejected. Power off the device or remove it from your network first, then try again.

## Acknowledgments

- [ECHONET Consortium](https://echonet.jp/) for the ECHONET Lite specification
- [pyhems](https://github.com/sayurin/pyhems) library for ECHONET Lite protocol handling
