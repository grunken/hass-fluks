# Fluks for Home Assistant

**Your energy. Decides together.**

**One home. One energy system. Smarter decisions together.**

Fluks is an intelligent home energy optimization platform that learns how your home uses energy, forecasts what comes next, and coordinates your energy devices as one connected system.

Your solar panels, battery, electric vehicle and heating shouldn't have to make decisions independently.

**Fluks brings them together.**

[**Visit fluks.one**](https://fluks.one) · [**Get started**](https://fluks.one/en/#get-started) · [**Report an issue**](https://github.com/grunken/hass-fluks/issues)

---

## Why Fluks?

Most home energy automations react to what's happening right now.

Fluks also considers **what's likely to happen next**.

It combines:

- **Learning** — Understand how your home and its devices actually behave.
- **Forecasting** — Anticipate energy demand, solar production and upcoming conditions.
- **Electricity prices** — Consider when energy is cheaper or more expensive.
- **Whole-home optimization** — Coordinate energy decisions across devices instead of optimizing each device in isolation.
- **Continuous adaptation** — Reconsider decisions as measurements and forecasts change.

The goal is simple: **make better use of your home's energy, automatically.**

## Your entire home. Working together.

Fluks is designed to coordinate the energy capabilities already available through Home Assistant.

| Energy system | What Fluks considers |
|---|---|
| ☀️ Solar panels | Production, forecasts and available surplus |
| 🔋 Home batteries | Charging, discharging and energy storage |
| 🚗 Electric vehicles | Charging demand and available vehicle information |
| ♨️ Heat pumps | Heating demand and thermal storage |
| 🚿 Water heaters | Hot water temperature and heating flexibility |
| 🌡️ Space heating | Temperature, energy consumption and heating response |
| 🏠 Your home | Overall energy balance, demand and electricity prices |

Fluks evaluates these capabilities together, according to the measurements and controls available in your installation.

**Your home is more than a collection of smart devices. Fluks treats it as one energy system.**

## Built for Home Assistant

Home Assistant connects Fluks to your existing devices.

Fluks provides the intelligence that learns, forecasts and optimizes across them.

The integration:

1. Discovers device information and available capabilities.
2. Helps you map measurements and controls.
3. Sends observations to Fluks.
4. Receives and executes optimization decisions.
5. Lets you review and adjust device configuration.

Fluks uses the sensors and controls exposed by your existing Home Assistant integrations, regardless of manufacturer.

**No additional device-specific Fluks integration is required.**

You continue using the integrations that already connect your devices to Home Assistant. Fluks builds on the information and control capabilities they provide.

Actual functionality depends on the measurements and controls exposed by those integrations.

## Install with HACS

Fluks is currently available through HACS as a custom repository while its official HACS listing is pending.

### Step 1 — Add the repository

1. Open **HACS** in Home Assistant.
2. Open the three-dot menu and select **Custom repositories**.
3. Enter the repository URL:

   `https://github.com/grunken/hass-fluks`

4. Select **Integration** as the repository type.
5. Add the repository.

### Step 2 — Install Fluks

1. Find **Fluks** in HACS.
2. Select **Download**.
3. Restart Home Assistant when required.

### Step 3 — Connect your home

1. Go to **Settings → Devices & services**.
2. Select **Add integration**.
3. Search for **Fluks**.
4. Follow the configuration flow.

You're ready to start connecting your home's energy devices.

**[Full installation guide →](https://fluks.one/en/#get-started)**

### Manual installation

Alternatively, copy the `custom_components/fluks` directory into your Home Assistant configuration directory, preserving the directory structure.

Restart Home Assistant and add the Fluks integration through **Settings → Devices & services**.

## Smart mapping. Less configuration.

Every Home Assistant installation is different.

Manufacturers use different entity names, measurements and controls. Fluks uses a flexible mapping system to understand these capabilities.

During setup, Fluks can suggest mappings automatically based on the sensors and controls provided by your existing Home Assistant integrations.

You can review, accept or adjust the suggestions before they are used.

**The goal is to make configuration as simple as possible, without requiring manufacturer-specific rules.**

## How Fluks works

The process is continuous:

**Observe → Learn → Forecast → Optimize → Decide → Adapt**

Home Assistant supplies observations through the sensors exposed by your existing integrations and executes supported controls through their available actions.

Fluks processes energy information, forecasts and device capabilities to produce coordinated decisions.

As new observations arrive, Fluks can adapt its plans to changing conditions.

Rather than maintaining separate automations for every possible combination of prices, weather and devices, Fluks approaches the home as a single optimization problem.

## Join the Fluks beta

**Fluks is looking for early Home Assistant users.**

Whether you have solar panels, a home battery, an EV charger, a heat pump or another interesting energy setup, your experience can help shape Fluks.

We're especially interested in:

- Different manufacturers and Home Assistant integrations.
- Homes with multiple energy devices.
- Feedback on installation and automatic mapping.
- Real-world optimization behavior.
- Bugs, missing capabilities and usability improvements.

You don't need to be a developer to participate.

**[Get started with Fluks →](https://fluks.one/en/#get-started)**

Found something that doesn't work?

**[Report an issue on GitHub →](https://github.com/grunken/hass-fluks/issues)**

## Offline plans

While offline, the Site page shows the latest `fluks.plan`, including upcoming scheduled Decisions and expiry in local time. Elapsed entries disappear automatically; this does not indicate whether their actions executed. The integration saves the complete plan and the configuration received through its normal Core requests locally. The Site name remains available after a Home Assistant restart.

Core controls execution while connected. When disconnected, future plan Decisions use the same configured Home Assistant Mappings. Past targets are not replayed after restart. At expiry, release Decisions end local control through those Mappings; temperature controls retain their existing restoration and manual-override behavior. Reconnection stops local scheduling immediately.

Offline batches are durably claimed before dispatch. Online and offline Decisions use the same limit of three execution attempts, including the initial attempt, retrying failed calls or verification without repeating completed actions. The persisted claim prevents replay after a restart without preventing retries during that execution. A crash after a claim can therefore leave an action incomplete rather than replay it. Release actions still depend on working configured Mappings and Home Assistant services; failures appear in the native Logbook, and subsequent Decisions continue.

## Troubleshooting

**Fluks doesn't appear in Home Assistant**

Check that the integration was installed correctly and restart Home Assistant.

**A measurement or control wasn't detected**

Open the device configuration and review the available mappings. You can adjust mappings manually.

**Something isn't working as expected**

Open a [GitHub issue](https://github.com/grunken/hass-fluks/issues) with a description of the problem, relevant logs and the Home Assistant version.

Please remove credentials, tokens and other private information before sharing logs.

## Open source integration. Independent intelligence.

This repository contains the open-source Home Assistant integration for Fluks.

The Fluks backend, forecasting and optimization services are separate components.

The Home Assistant integration is licensed under the **MIT License**. See [LICENSE](LICENSE).

## Links

- **Website:** https://fluks.one
- **Installation:** https://fluks.one/en/#get-started
- **Releases:** https://github.com/grunken/hass-fluks/releases
- **Issues and feedback:** https://github.com/grunken/hass-fluks/issues

---

**Fluks**

*Your energy. Decides together.*

https://fluks.one
