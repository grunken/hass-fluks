# fluks configuration-panel architecture

Initial onboarding remains a native Home Assistant Config Flow. Administration
of an existing ConfigEntry uses a direct, embedded integration configuration
panel registered with `config_panel_domain="fluks"`.

Home Assistant therefore owns the Configure route and supplies the non-secret
ConfigEntry identifier:

```text
/fluks-control-editor?config_entry=<entry id>
```

The panel has no sidebar title, is admin-only, uses a direct web component (not
an iframe), receives `hass`, and follows Home Assistant theme variables. It is
registered once at integration setup. Existing ConfigEntries require no data
migration.

## Trusted boundary

```text
embedded panel
→ authenticated, admin-only Home Assistant WebSocket command
→ hass-fluks Python operation
→ ConfigEntry integrationKey
→ fluks backend
```

The browser never receives `integrationKey`, a human JWT, or a backend password
from ConfigEntry data. It never calls the fluks backend directly. Destructive
credentials entered by an administrator are submitted only to the local
operation command, used for one human login and DELETE, and discarded. A
successful login is never interpreted as successful deletion.

The command surface is finite rather than a transport proxy:

- `fluks/config/context`: Site, sorted Device presentation, live physical
  catalog types, and safe Home Assistant Device presentation.
- `fluks/config/device`: one Device, its current Integration-owned input
  Mappings, matcher suggestions for missing concepts, optional properties, and
  catalog-declared controls.
- `fluks/config/add_review`: duplicate validation, deterministic external
  deviceId, and Python matcher results.
- `fluks/config/add_save`: deterministic Device create/recovery and confirmed
  Mapping creation.
- `fluks/config/device_save`: incremental Device PATCH and Mapping
  POST/PATCH/DELETE.
- `fluks/config/control_save`: create, update, delete, or no-op one
  catalog-declared output Mapping using configuration version 1.
- `fluks/config/control_capabilities`: normalized, representable Home Assistant
  actions, compatible entities, action fields, and safe live value constraints.
- `fluks/config/device_delete`: temporary human login and lifecycle DELETE.
- `fluks/config/site_delete`: temporary human login, lifecycle DELETE, and
  ConfigEntry removal after confirmed success.

All commands require an authenticated Home Assistant administrator and an
explicit fluks ConfigEntry ID. Unknown/non-fluks entries are rejected. Errors
are reduced to stable product codes; raw backend responses are not returned.

## Production application layers

Initial installation and reauthentication use the native Config Flow. Existing
ConfigEntries are administered only through the embedded configuration panel;
there is no Options Flow configuration UI.

The authenticated panel commands reuse the API client, backend-owned canonical
catalog, deterministic matcher, input-configuration builder, shared Device
identity/presentation helpers, ConfigEntry identities, and local HA discovery
contexts. Python owns Device, Mapping, authentication, and lifecycle
orchestration. The browser owns presentation and drafts only.

## Panel navigation

```text
Configure → panel home
          ├─ Add device → type → HA Device → review → Save
          ├─ Device → edit measurements/properties → Save
          │          ├─ Controls → ordered action/transform editor → Save
          │          └─ Delete → confirm → temporary login
          └─ Site → Delete → confirm → temporary login
```

History state stores only view names and opaque non-secret Device/concept
identifiers. Back and Cancel discard drafts. Refresh reconstructs core state
from `config_entry` and Python; unsaved drafts may be lost. Switching entry IDs
clears all Device and control draft state. Missing context renders a
safe error instead of selecting a default entry.

The layout uses responsive CSS grids, 42-pixel minimum button heights, wrapping
lists, and a single-column layout below 700 pixels. Destructive actions use
Home Assistant theme colors and always require confirmation.

## Production panel presentation

The Milestone 5 visual pass follows the five supplied panel mocks as separate
states. Canonical Device types use the approved transparent PNG assets served
locally from `/fluks-device-icons`; camel-case catalog types are converted to
their existing snake-case filenames without a local type registry.

Home Assistant 2026.8 includes `ha-device-picker` and `ha-entity-picker`, but
those Lit components are frontend-internal modules. A standalone custom panel
has no supported public import URL for them, and depending on private bundle
paths would be upgrade-fragile. The panel therefore owns one small searchable,
keyboard-compatible picker surface using browser `<dialog>` semantics and HA
theme variables. It keeps opaque HA Device/entity IDs as values while showing
friendly names first and metadata second. Entity results rank the selected HA
Device first but do not remove unrelated or metadata-incomplete candidates.

Add Device is one reactive draft page: visual canonical type selection, HA
Device selection, server-side matcher suggestions, mappings/properties, then
explicit Save. Changing type or HA Device never invokes a mutation command.
Site and Device lifecycle actions live behind contextual overflow menus; only
their confirmation views use persistent destructive styling.

## Controls boundary

The ordered-action editor is embedded under live catalog concepts with `control`
usage. Action, entity, and field choices come from Home Assistant's registered
service descriptions and live entity metadata; incomplete and opaque schemas are
excluded by a small capability filter. It edits output configuration v1 directly:
ordered `serviceCall` actions, typed literal/requested values, and ordered
deterministic transforms. Explicit Save reconciles mode-specific output Mappings
through the existing POST/PATCH/DELETE APIs; machine-equal state is a no-op.
Cancel discards the draft. Credentials and persistence remain in Python.
Runtime Decision snapshots resolve the external Device identity and exact
Decision mode to one output Mapping, apply requested-value transforms, and call
the configured Home Assistant services sequentially.

## Rejected mechanisms

- An Options Flow cannot provide the ordered, reactive administration UX needed
  by the production configuration surface.
- A custom dialog requires private frontend internals and is too constrained for
  ordered actions on mobile.
- An iframe does not naturally receive `hass`, routing, theme, or entity context.
- Home Assistant's automation editor is an internal frontend implementation and
  exposes scripting features fluks intentionally does not support.

The production panel uses no private Home Assistant frontend imports. Its
supported boundary is `panel_custom`, `config_panel_domain`, the injected
`hass` object, and authenticated WebSocket commands.
