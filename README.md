# ECHONET Lite Integration for Home Assistant

ECHONET Lite protocol integration for Home Assistant. Enables communication with ECHONET Lite compatible devices on the home network.

## Features

- **Automatic Device Discovery**: Discovers ECHONET Lite devices via multicast
- **Multi-Platform Support**: Supports 8 entity platforms:
  - Climate (Air Conditioners - 0x0130)
  - Fan (Air Cleaners - 0x0135)
  - Binary Sensor (Detection & Status)
  - Button (Commands & Resets)
  - Number (Numeric Parameters)
  - Select (Enum Parameters)
  - Sensor (Read-only Values)
  - Switch (Toggle States)
- **50+ Device Classes**: Extensive support for ECHONET Lite device types
- **Network Configuration**: Manual network interface selection
- **Polling Configuration**: Adjustable polling intervals
- **Experimental Mode**: Support for experimental device classes

## Installation

Install through HACS (recommended):

1. Add this repository to HACS as a custom repository
2. Search for "ECHONET Lite"
3. Install the integration
4. Restart Home Assistant

### Manual Installation

1. Copy `custom_components/echonet_lite` to your `custom_components` directory
2. Restart Home Assistant

## Configuration

1. Go to Settings → Devices & Services
2. Click "Create Automation" (or find the ECHONET Lite integration)
3. Select your network interface:
   - **Auto**: Automatic detection (recommended)
   - **Custom IP**: Bind to specific interface (e.g., 192.168.1.10)
4. The integration will begin listening on the ECHONET Lite multicast channel (224.0.23.0:3610)

## Options

After installation, configure:

- **Polling Interval**: How often to refresh device properties (default: 60 seconds)
- **Enable Experimental**: Include unverified device classes (use with caution)

## Supported Devices

### Primary Device Classes

- **Air Conditioner (0x0130)**: Full climate control support
- **Air Cleaner (0x0135)**: Fan speed and operation modes
- **Water Heater (0x026B, 0x0272)**: Temperature and operation
- **Solar System (0x027C)**: Power generation monitoring
- **Battery Storage (0x027D, 0x027E)**: Charging/discharging control
- **Smart Meter (0x0280-0x0290)**: Power consumption tracking
- **Door Lock (0x026F)**: Lock status and control
- **Lighting (0x0290)**: Brightness and color control
- **Refrigerator (0x03B7)**: Temperature monitoring
- **Washer/Dryer (0x03D3)**: Operation modes and cycles

### Supported Features

- Read-only properties (Sensors)
- Writable properties (Numbers, Selects, Switches)
- Commands (Buttons)
- Binary states (Binary Sensors)

## Network Requirements

This integration requires:

- UDP multicast support on your network
- Port 3610 accessible on your network interface
- ECHONET Lite devices on the same network segment
- Multicast address 224.0.23.0:3610 must be allowed

## Troubleshooting

### No Devices Discovered

1. Verify ECHONET Lite devices are powered on and connected
2. Check network multicast is enabled
3. Ensure your network interface selection is correct
4. Increase polling interval if network is congested

### Runtime Error or Devices Not Responding

The integration will create repair issues if:
- No frames received for extended period (check device power/network)
- Runtime errors occur (check network configuration)

Use the integration repair flow to restart the ECHONET Lite service.

## Supported Platforms

- Home Assistant 2023.5 or later
- Python 3.10 or later

## Requirements

- `pyhems==0.3.0` - ECHONET Lite protocol implementation

## License

Apache License 2.0 - See LICENSE file for details

## Contributing

Contributions are welcome! Please submit issues and pull requests on the GitHub repository.

## Acknowledgments

- ECHONET Lite specification by ECHONET Consortium
- Home Assistant for the excellent integration framework
