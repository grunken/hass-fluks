# Fluks

**Your energy. Decides together.**

Fluks is an intelligent energy management system that coordinates the energy in your home.

Instead of controlling solar production, batteries, electric vehicles, heating and other energy devices independently, Fluks looks at the home as a whole.

It combines what is happening now with what is expected to happen next and continuously decides how the available energy should be used.

## Home Assistant

This integration connects Home Assistant to Fluks.

Home Assistant provides observations from your home and executes control decisions made by Fluks.

Fluks does not require integrations for specific brands or device models. It uses the capabilities already exposed by Home Assistant.

**If it works with Home Assistant, it can work with Fluks.**

## Mapping

Every Home Assistant installation is different.

Entity names, measurements and available controls vary between manufacturers and integrations. Fluks uses mappings to connect those capabilities to the things it understands.

During configuration, Fluks can automatically suggest mappings based on what Home Assistant exposes.

You review and accept the mappings before they are used.

This means Fluks can work with existing Home Assistant integrations without needing device-specific support for every manufacturer.

## How it works

The Home Assistant integration has two primary responsibilities:

1. Send observations from your home to Fluks.
2. Execute control decisions received from Fluks.

Fluks combines these observations with information such as forecasts, electricity prices and the capabilities available in your home.

Home Assistant remains the connection to your physical devices. Fluks provides the intelligence that coordinates them.

## Installation

Clone this repository into your Home Assistant `custom_components` directory so the integration is available as:

```text
custom_components/fluks
```

Restart Home Assistant after installation.

Then go to:

**Settings → Devices & services → Add integration → Fluks**

## Configuration

The Fluks integration guides you through connecting your home and configuring the devices Fluks can use.

For each device, select the corresponding device from Home Assistant.

Fluks inspects the available capabilities and can suggest mappings automatically. Review the suggestions, adjust them if necessary, and save the configuration.

Once configured, observations and control decisions are exchanged automatically between Home Assistant and Fluks.

Configuration can be changed later from the Fluks integration.

## What Fluks can coordinate

Depending on what is available in your home, Fluks can coordinate energy across things such as:

- Solar production
- Battery storage
- Electric vehicle charging
- Heat pumps
- Water heating
- Space heating
- Other energy consumption and controllable loads

These are not optimized as isolated devices. Fluks considers how they affect the energy balance of the home together.

## Troubleshooting

If Fluks does not appear after installation, verify that the integration is located in:

```text
custom_components/fluks
```

and restart Home Assistant.

After adding Fluks, configuration and connection status are available from the integration under **Settings → Devices & services**.

If a measurement or control is not mapped automatically, open the device configuration and review its mappings. Suggestions can be adjusted manually.

## Development

This repository contains the Home Assistant integration for Fluks.

The Fluks energy-management backend is a separate part of the Fluks platform.

## License

See [LICENSE](LICENSE).# hass-fluks
