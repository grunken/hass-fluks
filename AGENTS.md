# AGENTS.md — hass-fluks

## Purpose

`hass-fluks` is the Home Assistant integration for **fluks**.

Its job is to bridge Home Assistant and the fluks backend.

It is an adapter, not a second implementation of fluks.

The integration should remain deliberately small, predictable, and Home Assistant-native.

---

## Brand

The product name is always written:

`fluks`

Never:

- `Fluks`
- `FLUKS`

The tagline is:

`Your energy. Decides together.`

Use the approved logo assets exactly as supplied.

Do not redraw, reinterpret, recreate, or approximate the logo in code, HTML, CSS, SVG, Canvas, generated images, or other assets.

---

## Authoritative onboarding UX reference

The authoritative onboarding UX reference is:

`docs/onboarding-reference.png`

This image is the approved visual and flow reference for onboarding.

Before implementing or modifying onboarding UX, inspect this image.

Do not:

- modify the reference image
- regenerate it
- replace it
- reinterpret the brand/logo shown in it
- create a competing onboarding design

Implement the intent of the reference using native Home Assistant Config Flow capabilities.

The reference expresses:

- visual hierarchy
- wording direction
- step order
- simplicity
- brand tone
- use of dark surfaces
- restrained green accent
- clear primary actions
- minimal technical language

Native Home Assistant UX conventions take precedence where the exact visual layout shown in the reference cannot be reproduced by Config Flow.

Do not create a custom frontend, iframe, panel, HTML application, or frontend framework merely to reproduce the mockup.

The goal is for onboarding to feel like **fluks inside Home Assistant**, not like a separate web application embedded in Home Assistant.

---

## Development API

The current development backend is:

`https://energy-api.grunken.dk`

The current OpenAPI contract is available at:

`https://energy-api.grunken.dk/openapi.yaml`

Human-readable Swagger documentation is available at:

`https://energy-api.grunken.dk/docs/`

The OpenAPI document is the source of truth for the backend HTTP contract.

The API base URL must be defined centrally so it can be changed later without touching unrelated code.

Do not scatter backend URLs throughout the integration.

Do not expose the API base URL as a normal onboarding field unless explicitly requested later.

Do not guess backend endpoints, payloads, authentication semantics, or error formats when the OpenAPI contract can answer the question.

If required functionality is missing from the documented backend contract, report the gap rather than inventing a client-side workaround.

---

## Core responsibility

The integration is responsible for:

- Home Assistant onboarding.
- Authentication against the fluks API.
- Creating or selecting a fluks Site.
- Registering the Home Assistant installation as a fluks Integration.
- Discovering Home Assistant devices/entities.
- Translating Home Assistant state into canonical fluks facts through mappings.
- Translating canonical fluks controls into Home Assistant actions through mappings.
- Understanding Home Assistant-specific metadata needed for translation.
- Sending canonical realtime data to fluks.
- Receiving canonical proposals/controls from fluks and applying the configured mapping.

The integration is NOT responsible for:

- Optimizer logic.
- Decision calculations.
- Electricity-price calculations.
- Solar-forecast calculations.
- Weather calculations.
- Canonical 15-minute energy normalization.
- Historical time-series aggregation.
- Battery arbitrage decisions.
- Site-level energy optimization.
- Reimplementing the canonical fact/control registry.
- Vendor-specific business logic in the fluks core.

Integration-specific weirdness stops at the Integration boundary.

---

## Home Assistant is optional to fluks

Home Assistant is one supported Integration type.

fluks itself must not depend on Home Assistant.

Do not design backend-facing contracts under the assumption that every fluks Site, Device, fact, or control originates from Home Assistant.

The Home Assistant Integration type is:

`homeAssistant`

---

## Canonical vocabulary

The fluks backend owns the canonical vocabulary.

`hass-fluks` must consume the canonical concepts exposed by the backend contract and must not invent competing canonical names.

Examples include:

- `site.power`
- `battery.soc`
- `battery.power`
- `battery.chargeEnergy`
- `battery.dischargeEnergy`
- `electricVehicle.power`
- `electricVehicle.distanceToSite`
- `heatPump.targetTemperature`
- `heatPump.waterTargetTemperature`
- `waterHeater.targetTemperature`

Do not introduce aliases such as:

- `ev.*`
- `heatpump.*`
- `grid.power`

Do not create Home Assistant-specific canonical concepts.

---

## Mapping boundary

Mappings translate between Home Assistant and canonical fluks concepts.

Input mapping:

`Home Assistant -> fluks canonical fact`

Output mapping:

`fluks canonical control -> Home Assistant action(s)`

The same canonical concept may be valid in both directions when the backend registry allows it.

Example:

`battery.power`

may be both an input fact and an output control.

Do not invent separate names such as:

- `battery.targetPower`
- `battery.requestedPower`
- `battery.commandPower`

Mapping direction determines the meaning.

---

## Home Assistant input mappings

A normal input mapping identifies a Home Assistant entity and may apply deterministic transforms.

Examples of acceptable transforms:

- invert
- scale
- offset

Do not implement:

- arbitrary Python execution
- arbitrary JavaScript
- eval
- unrestricted templates
- general-purpose expression languages

Transforms must remain deterministic, serializable, validated, and testable.

---

## Energy mappings

Home Assistant may expose energy through different sensor semantics.

`hass-fluks` may understand Home Assistant-specific metadata such as:

- `device_class`
- `state_class`
- `total_increasing`
- native unit
- reset metadata
- entity availability

The integration should translate these semantics into the generic energy-ingestion contract supported by the fluks backend.

Preferred energy source is a monotonically increasing cumulative total when available.

The integration must NOT require users to create custom 15-minute utility meters or template sensors merely for fluks.

Users should be able to use the useful energy sensors their devices already expose whenever possible.

The backend performs canonical 15-minute normalization.

---

## Output mappings

An output mapping may contain an ordered sequence of Home Assistant actions.

A canonical control is not assumed to map one-to-one to a single entity.

For example, conceptually:

`battery.power`

may require:

1. Set inverter mode.
2. Set requested power.
3. Enable a control switch.

Action order must be preserved.

The integration translates the canonical requested value into the configured Home Assistant action sequence.

---

## Proposal principle

fluks proposes; it never demands.

The integration should send/apply the proposal represented by the mapping.

Do not build speculative logic explaining why a physical device did or did not follow a proposal.

Do not create unnecessary failure taxonomies around third-party device behaviour.

If fluks sends a valid proposal and the mapped Home Assistant action is sent, later observations simply describe the new real-world state.

The backend may make another proposal from that state.

Whether a device ignores a proposal is not a reason for hass-fluks to invent additional domain state.

---

## Identity

Integration and Device identities must remain stable across Home Assistant restarts.

Do not generate a new external identity every time the integration loads.

A Home Assistant installation uses a stable externally assigned `integrationId`.

A fluks Device uses a stable externally assigned `deviceId`.

Internal fluks database UUIDs are separate identities.

Do not conflate:

- external `integrationId` with internal Integration UUID
- external `deviceId` with internal Device UUID

Home Assistant `entity_id` must not be treated as the permanent identity of a physical device.

Entity IDs may change.

---

## Native Home Assistant patterns

Prefer standard Home Assistant integration patterns.

Use:

- Config Flow
- ConfigEntry lifecycle
- asynchronous I/O
- Home Assistant-provided HTTP/session facilities where appropriate
- entity/device registries
- translations/strings
- unload/reload support

Avoid blocking I/O in the event loop.

Do not create a custom frontend framework when native Home Assistant UI is sufficient.

Do not fork Home Assistant Core merely to develop this custom integration.

Do not make users configure YAML when a normal Home Assistant Config Flow is appropriate.

---

## Onboarding UX

The intended first milestone is:

1. Open `fluks`.
2. Log in OR create account.
3. Verify email for a newly created user.
4. Select an existing Site OR create a Site.
5. Register this Home Assistant installation as a `homeAssistant` Integration.
6. Create the Home Assistant ConfigEntry.
7. Finish.

The authoritative visual reference for this flow is:

`docs/onboarding-reference.png`

Do not add future onboarding steps opportunistically.

Milestone 1 does NOT include:

- Device discovery.
- Device creation.
- Mapping setup.
- WebSocket runtime.
- Realtime facts.
- Energy streaming.
- Controls.
- Decisions.
- Mapping recommendations.

Those are later milestones.

---

## User creation

Milestone 1 must support both:

- Existing user login.
- New user registration.

New-user registration follows the backend's existing email-verification flow.

Do not invent a second authentication model in the Home Assistant integration.

Before implementing authentication, inspect:

`https://energy-api.grunken.dk/openapi.yaml`

Use the documented backend contracts.

Do not guess endpoint names or payloads.

If the contract cannot support the intended onboarding flow, report the missing capability rather than compensating for it in hass-fluks.

---

## Site onboarding

Use Home Assistant-known information when appropriate.

For example, if Home Assistant already provides the Site timezone, prefer using that rather than asking the user to manually type an IANA timezone.

Do not ask users questions Home Assistant can already answer reliably.

Keep the onboarding flow minimal.

Only request Site information actually required by the backend contract.

---

## Integration registration

After Site selection/creation, register the Home Assistant installation with the backend.

The Integration type is:

`homeAssistant`

The external `integrationId` must be generated once and persisted locally.

Restarting or reloading Home Assistant must not create a new backend Integration.

If the corresponding backend Integration already exists, reuse it according to the backend API contract rather than creating duplicates.

Do not invent undocumented idempotency behavior.

---

## ConfigEntry

Persist only what the integration actually requires.

Do not use the Home Assistant ConfigEntry as a second database for backend domain data.

Secrets/tokens must be handled according to Home Assistant conventions.

Avoid unnecessary personal metadata.

A Home Assistant restart or ConfigEntry reload must not change stable fluks identities.

---

## Privacy

Do not send unnecessary Home Assistant personal labels to fluks.

Avoid sending things such as:

- person names
- room labels
- friendly names containing personal information

unless they are specifically required for an explicitly implemented feature.

Canonical data should be based on:

- Site identity
- Integration identity
- Device identity
- canonical concept
- value
- timestamp
- required generic mapping/ingestion semantics

Only send information required for the implemented feature.

---

## API client

Keep backend HTTP communication behind a small asynchronous API client.

Do not scatter raw HTTP calls through Config Flow or future runtime code.

Use Home Assistant's shared HTTP session facilities where appropriate.

Do not use blocking network I/O.

Do not build a generic fluks SDK unless there is a concrete need for one.

The API client should implement only the backend operations currently required by hass-fluks.

The OpenAPI contract remains authoritative.

---

## Error handling

Backend/network failures must not result in raw tracebacks being shown as onboarding UX.

Translate expected failures into appropriate Home Assistant Config Flow errors.

Examples include:

- invalid authentication
- invalid verification code
- backend validation failure
- unavailable backend
- timeout

Do not hide unexpected programmer errors behind broad exception handling.

Do not invent large error taxonomies without a concrete requirement.

---

## Testing

Automated tests are part of the implementation.

Do not treat hass-fluks as too small to test.

Use standard Home Assistant custom-integration testing patterns where practical.

Tests must not depend on the live development backend.

Mock backend HTTP communication.

The live development API:

`https://energy-api.grunken.dk`

is for development/manual integration testing, not automated unit/config-flow tests.

Tests should focus on observable behavior and contracts rather than implementation details.

Important areas include:

- Config Flow transitions
- login success/failure
- registration
- email verification
- Site selection
- Site creation
- stable integration identity
- Integration registration/reuse
- ConfigEntry creation
- setup/unload/reload
- backend/network failures

Future milestones should add tests for their own behavior rather than prebuilding speculative test infrastructure.

---

## Simplicity

Prefer the smallest implementation that solves the current requirement.

Do not build speculative infrastructure merely because it may be useful later.

Do not remove obvious extensibility required by the already-agreed architecture.

The distinction is:

> Model complexity fluks genuinely needs. Ignore complexity fluks does not need to understand.

Prefer boring code over clever code.

---

## Scope discipline

Do not implement future milestones opportunistically.

If the current task is onboarding, do not add:

- discovery
- mapping recommendations
- WebSocket consumers
- control execution
- telemetry
- energy ingestion
- retry engines
- background synchronization
- optimizer behaviour

unless explicitly required by the current task.

If an adjacent concern is discovered, report it instead of silently expanding scope.

Do not add placeholder abstractions for future features simply because they may eventually exist.

---

## Release and versioning

During unreleased development, the project version remains:

`1.0.0`

Do not increment the package/integration version because a breaking or additive change is made during development.

The first actual release occurs when the project owner creates:

`git tag 1.0.0`

After the first real release, normal SemVer can follow release tags.

Do not create Git tags.

Do not create commits unless explicitly requested.

Do not add gratuitous version numbers to development artifact filenames or labels.

---

## Git

Do not create a commit unless explicitly requested.

Do not create a tag unless explicitly requested.

Do not rewrite Git history.

Do not discard unrelated working-tree changes.

Before finishing a task, inspect the diff and ensure the task did not accidentally expand beyond its requested scope.

Leave completed changes available for review.

---

## Validation

For each task, run the smallest relevant validation first and the full project validation when appropriate.

At minimum consider:

- Python syntax/import validation
- Home Assistant Config Flow tests
- API-client tests
- async behaviour
- setup/unload/reload behaviour
- manifest validation
- HACS validation where applicable
- existing repository lint/style/test commands

Do not introduce heavyweight tooling before there is a concrete need.

Report validation performed and any failures clearly.

---

## Working with the backend contract

When a task requires backend interaction:

1. Read the current OpenAPI contract from:

   `https://energy-api.grunken.dk/openapi.yaml`

2. Identify the exact existing endpoint and schema.

3. Implement against that contract.

4. Do not guess missing behavior.

5. If the required capability does not exist, report the backend gap.

6. Do not modify the backend from the hass-fluks repository.

Swagger UI is available for human inspection at:

`https://energy-api.grunken.dk/docs/`

The raw OpenAPI document remains the machine-readable source of truth.

---

## Working rule

Keep `hass-fluks` boring.

It should be a reliable adapter between Home Assistant and fluks.

The intelligence belongs in fluks.