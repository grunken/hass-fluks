import assert from "node:assert/strict";
import test from "node:test";

const elements = new Map();
globalThis.HTMLElement = class {
  attachShadow() {
    this.shadowRoot = { innerHTML: "", querySelectorAll: () => [], querySelector: () => null };
    return this.shadowRoot;
  }
  addEventListener() {}
  dispatchEvent() { return true; }
};
globalThis.customElements = {
  define: (name, value) => { if (elements.has(name)) throw new Error(`duplicate ${name}`); elements.set(name, value); },
  get: (name) => elements.get(name),
};
globalThis.CustomEvent = class {};
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
globalThis.history = { back: () => {} };
globalThis.CSS = { escape: (value) => value };

await import("../../custom_components/fluks/frontend/control-action-editor.js?rev=existing-ha-session");
await import("../../custom_components/fluks/frontend/control-editor-panel.js?rev=production-path-test");
const PANEL_TAG = "fluks-control-editor-panel-production-path-test";
const Panel = customElements.get(PANEL_TAG);
const CONTROL_EDITOR_TAG = "fluks-control-action-editor-production-path-test";
assert.ok(Panel, "the parent panel registers without waiting for its Controls child");
await new Panel()._ensureControlActionEditor();
const Editor = customElements.get(CONTROL_EDITOR_TAG);

test("long-lived HA session opens the production panel repeatedly without duplicate registration", () => {
  assert.equal(customElements.get("fluks-control-action-editor"), undefined);
  assert.ok(Editor);
  assert.doesNotThrow(() => { new Panel(); new Panel(); });
});

test("two frontend revisions use their current revision-specific panel classes", async () => {
  await import("../../custom_components/fluks/frontend/control-editor-panel.js?rev=next-production-path-test");
  const NextPanel = customElements.get("fluks-control-editor-panel-next-production-path-test");
  assert.ok(NextPanel);
  assert.notEqual(NextPanel, Panel);
  assert.equal(customElements.get(PANEL_TAG), Panel);
  assert.equal(customElements.get("fluks-control-editor-panel"), undefined);
});

test("production panel passes discovered actions and compatible entities to actual editor", () => {
  const capability = {
    service: "number.set_value", name: "Set value", description: "Sets a value.",
    entities: [{ entity_id: "number.goodwe_target", name: "Charge limit", metadata: "GoodWe Inverter · goodwe · GoodWe · GW10K-ET", fields: [{ id: "value", name: "Value", required: true, selector: { type: "number" }, constraints: { min: 0, max: 100, step: 1 } }] }],
  };
  const panel = new Panel();
  panel._context = { translations: {} };
  panel._view = { name: "control", deviceId: "device-1", concept: "battery.power" };
  panel._detail = { id: "device-1", type_name: "Battery", controls: [{ concept: "battery.power", label: "Power", datatype: "number", unit: "W" }], output_mappings: {} };
  panel._controlCapabilities = [capability];
  let editor;
  panel._frame = () => {
    editor = new Editor();
    panel.shadowRoot.querySelector = (selector) => selector === CONTROL_EDITOR_TAG ? editor : null;
  };
  panel._renderControl();

  assert.deepEqual(editor.capabilities, [capability]);
  assert.deepEqual(editor.valueType, { datatype: "number", unit: "W" });
  editor._dialogDraft = { type: "serviceCall", service: "number.set_value", target: { entityId: "number.goodwe_target" }, data: {} };
  const rendered = editor._dialog();
  assert.match(rendered, /Set value/);
  assert.match(rendered, /number\.set_value/);
  assert.match(rendered, /Charge limit/);
  assert.match(rendered, /number\.goodwe_target/);
  assert.match(rendered, /GoodWe Inverter · goodwe · GoodWe · GW10K-ET/);
  assert.doesNotMatch(rendered, /selectOption|setNumber|water heater temperature/i);
});

test("production Controls path passes global reference entities and attributes", () => {
  const panel = new Panel();
  panel._context = {
    translations: {},
    entities: [{ entity_id: "climate.buffer", name: "Buffer" }],
  };
  panel._hass = { localize: () => undefined, states: { "climate.buffer": { attributes: { temperature: 35.2, current_temperature: 34.8 } } } };
  panel._view = { name: "control", deviceId: "device-1", concept: "heatPump.temperature" };
  panel._detail = { id: "device-1", type_name: "Heat pump", controls: [{ concept: "heatPump.temperature", label: "Temperature", datatype: "number", unit: "°C" }], output_mappings: {} };
  let editor;
  panel._frame = () => { editor = new Editor(); panel.shadowRoot.querySelector = (selector) => selector === CONTROL_EDITOR_TAG ? editor : null; };
  panel._renderControl();
  assert.deepEqual(editor.referenceEntities, [{ entity_id: "climate.buffer", name: "Buffer", attributes: ["current_temperature", "temperature"] }]);
});

test("production control path groups backend mode mappings into behavior sections", () => {
  const panel = new Panel(); panel._context = { translations: {} };
  panel._view = { name: "control", deviceId: "device-1", concept: "battery.power" };
  panel._detail = {
    id: "device-1", type_name: "Battery",
    controls: [{ concept: "battery.power", label: "Power", datatype: "number", unit: "W", mappingModes: [null, "target", "limit", "balance", "release"] }],
    output_mappings: {
      "battery.power": [
        { mode: null, configuration: { version: 1, actions: [] } },
        { mode: "release", configuration: { version: 1, actions: [{ type: "serviceCall", service: "switch.turn_off", target: { entityId: "switch.control" }, data: {} }] } },
      ],
    },
  };
  let editor;
  panel._frame = () => { editor = new Editor(); panel.shadowRoot.querySelector = (selector) => selector === CONTROL_EDITOR_TAG ? editor : null; };
  panel._renderControl();
  assert.deepEqual(editor.allowedModes, [null, "target", "limit", "balance", "release"]);
  assert.deepEqual(editor.behaviors.map(({ mode }) => mode), [null, "release"]);
  assert.match(editor.shadowRoot.innerHTML, /default behavior/i);
  assert.match(editor.shadowRoot.innerHTML, /behavior release/i);
});

test("production Site power path forwards persisted value conditions", () => {
  const panel = new Panel(); panel._context = { translations: {} };
  panel._view = { name: "control", deviceId: "site-1", concept: "site.power" };
  panel._detail = {
    id: "site-1", type_name: "Site",
    controls: [{ concept: "site.power", label: "Power", datatype: "number", unit: "W", mappingModes: [null, "balance", "release"] }],
    output_mappings: { "site.power": [{ mode: "balance", valueCondition: "ltZero", configuration: { version: 1, actions: [] } }] },
  };
  let editor;
  panel._frame = () => { editor = new Editor(); panel.shadowRoot.querySelector = (selector) => selector === CONTROL_EDITOR_TAG ? editor : null; };
  panel._renderControl();
  assert.deepEqual(editor.behaviorChoices.map(({ mode, valueCondition }) => ({ mode, valueCondition })), [
    { mode: "balance", valueCondition: "gtZero" },
    { mode: "balance", valueCondition: "ltZero" },
    { mode: "balance", valueCondition: "eqZero" },
  ]);
  assert.deepEqual(editor.behaviors.find((item) => item.mode === "balance").valueCondition, "ltZero");
});

test("production action and entity searches filter immediately and restore globally", () => {
  const editor = new Editor();
  assert.match(editor._styles(), /\.choice\[hidden\]\{display:none\}/);
  const actionNodes = [
    { textContent: "Turn on switch.turn_on", hidden: false },
    { textContent: "Set value number.set_value", hidden: false },
    { textContent: "Set temperature climate.set_temperature", hidden: false },
    { textContent: "Set temperature water_heater.set_temperature", hidden: false },
  ];
  const entityNodes = [
    { textContent: "GoodWe battery target number.goodwe_battery_target goodwe", hidden: false },
    { textContent: "Monta current limit number.monta_current_limit monta", hidden: false },
    { textContent: "Other number.unrelated", hidden: false },
  ];
  editor.shadowRoot.querySelectorAll = (selector) => selector.includes('action') ? actionNodes : entityNodes;

  editor._filterChoices("action", "turn on");
  assert.deepEqual(actionNodes.map((node) => node.hidden), [false, true, true, true]);
  editor._filterChoices("action", "number");
  assert.deepEqual(actionNodes.map((node) => node.hidden), [true, false, true, true]);
  editor._filterChoices("action", "set_temperature");
  assert.deepEqual(actionNodes.map((node) => node.hidden), [true, true, false, false]);
  editor._filterChoices("action", "water heater");
  assert.deepEqual(actionNodes.map((node) => node.hidden), [true, true, true, false]);
  editor._filterChoices("action", "does not exist");
  assert.ok(actionNodes.every((node) => node.hidden));
  editor._filterChoices("action", "");
  assert.ok(actionNodes.every((node) => !node.hidden));

  editor._dialogDraft = { type: "serviceCall", service: "number.set_value", target: { entityId: "number.monta_current_limit" }, data: {} };
  editor._filterChoices("entity", "GoodWe");
  assert.deepEqual(entityNodes.map((node) => node.hidden), [false, true, true]);
  editor._filterChoices("entity", "number.monta");
  assert.deepEqual(entityNodes.map((node) => node.hidden), [true, false, true]);
  assert.equal(editor._dialogDraft.target.entityId, "number.monta_current_limit");
  editor._filterChoices("entity", "");
  assert.ok(entityNodes.every((node) => !node.hidden));
});

test("production Controls handoff is independent of fluks Device and input mappings", () => {
  const capability = { service: "switch.turn_on", name: "Turn on", entities: [{ entity_id: "switch.unrelated", name: "Unrelated switch", fields: [] }] };
  const renderFor = (typeName, mappings) => {
    const panel = new Panel(); panel._context = { translations: {} };
    panel._view = { name: "control", deviceId: "device", concept: "battery.power" };
    panel._detail = { id: "device", type_name: typeName, controls: [{ concept: "battery.power", label: "Power", datatype: "number", unit: "W" }], mappings, output_mappings: {} };
    panel._controlCapabilities = [capability]; let editor;
    panel._frame = () => { editor = new Editor(); panel.shadowRoot.querySelector = (selector) => selector === CONTROL_EDITOR_TAG ? editor : null; };
    panel._renderControl(); return editor.capabilities;
  };
  assert.deepEqual(renderFor("Battery", { "battery.power": { configuration: { entityId: "sensor.goodwe" } } }), [capability]);
  assert.deepEqual(renderFor("Electric vehicle", { "electricVehicle.power": { configuration: { entityId: "sensor.monta" } } }), [capability]);
});

test("production Add Device edits and persists suggested Input Mapping conversions", async () => {
  const panel = new Panel();
  panel._hass = { states: { "sensor.battery_power": { state: "1200", attributes: { friendly_name: "Battery power", unit_of_measurement: "W" } } }, language: "en", localize: () => undefined };
  panel._context = {
    translations: {
      choose_type: "Choose device type", add_device: "Add device", choose_ha_device: "Choose a Home Assistant device",
      home_assistant_device: "Home Assistant device", review_suggestions: "Review suggestions", measurements: "Measurements",
      energy: "Energy", conversions: "Conversions", no_transforms: "No conversions", add_conversion: "Add conversion",
      save_device: "Save device", cancel: "Cancel", optional: "Optional", up: "Up", down: "Down", edit: "Edit", remove: "Remove",
      invert: "Invert sign", scale: "Scale", offset: "Offset", device_information: "Device information", name: "Name", vendor: "Vendor", model: "Model",
      suggested_match: "Suggested match", possible_match: "Possible match", use_suggestion: "Use",
    },
    device_types: [{ type: "battery", name: "Battery" }],
    ha_devices: [{ id: "ha-battery", name: "GoodWe battery", manufacturer: "GoodWe", model: "GW10K" }],
    entities: [{ entity_id: "sensor.battery_power", name: "Battery power", device_id: "ha-battery" }],
  };
  panel._view = { name: "add", deviceType: "battery" };
  const review = {
    concepts: [{ concept: "battery.power", label: "Power", cadence: "realtime" }],
    proposals: { "battery.power": {
      concept: "battery.power",
      source: { entityId: "sensor.battery_power" },
      configuration: { version: 1, entityId: "sensor.battery_power", unit: "W" },
      score: 20,
      evidence: [{ family: "unit", code: "exact_unit", weight: 4 }],
      runner_up_gap: 8,
      classification: "auto",
      alternatives: [],
    } },
    properties: { displayName: "GoodWe battery", vendor: "GoodWe", model: "GW10K" },
  };
  const calls = [];
  panel._go = () => {};
  panel._call = async (type, data) => {
    calls.push({ type, data });
    if (type === "fluks/config/add_review") return review;
    if (type === "fluks/config/context") return panel._context;
    return { device_id: "device-created" };
  };
  const originalRenderAdd = panel._renderAdd.bind(panel);
  panel._renderAdd = () => {};
  await panel._selectAddHaDevice("ha-battery");
  panel._renderAdd = originalRenderAdd;

  let rendered = "";
  const save = {}; const cancel = {};
  panel._frame = (_title, body) => { rendered = body; };
  panel.shadowRoot.querySelector = (selector) => selector === "#save" ? save : selector === "#cancel" ? cancel : null;
  panel.shadowRoot.querySelectorAll = () => [];
  panel._renderAdd();
  assert.match(rendered, /sensor\.battery_power/);
  assert.match(rendered, /Add conversion/);
  assert.deepEqual(panel._inputDraft["battery.power"], { version: 1, entityId: "sensor.battery_power", unit: "W" });

  assert.equal(panel._commitInputConversion("battery.power", undefined, "invert", ""), true);
  assert.equal(panel._commitInputConversion("battery.power", undefined, "scale", "0.5"), true);
  panel._renderAdd();
  assert.ok(rendered.indexOf("Invert sign") < rendered.indexOf("Scale"));

  await save.onclick();
  const persisted = calls.find((call) => call.type === "fluks/config/add_save").data.mappings["battery.power"];
  assert.deepEqual(persisted, {
    version: 1,
    entityId: "sensor.battery_power",
    unit: "W",
    transforms: [{ type: "invert" }, { type: "scale", factor: 0.5 }],
  });

  const reopened = new Panel();
  reopened._context = panel._context; reopened._hass = panel._hass;
  reopened._view = { name: "edit", deviceId: "device-created" };
  reopened._detail = {
    id: "device-created", type: "battery", type_name: "Battery", label: "Battery · GoodWe battery",
    concepts: review.concepts, proposals: {}, properties: review.properties,
    mappings: { "battery.power": { configuration: persisted } },
  };
  reopened._frame = (_title, body) => { rendered = body; };
  reopened.shadowRoot.querySelector = (selector) => selector === "#save" || selector === "#cancel" ? {} : null;
  reopened.shadowRoot.querySelectorAll = () => [];
  reopened._renderEdit();
  assert.deepEqual(reopened._inputDraft["battery.power"], persisted);
  assert.match(rendered, /Invert sign/);
  assert.match(rendered, /Scale/);
});

test("suggested and ambiguous proposals require acceptance and remain fully overridable", () => {
  for (const classification of ["suggest", "unresolved"]) {
    const panel = new Panel();
    panel._context = {
      translations: {
        measurements: "Measurements", suggested_match: "Suggested match",
        possible_match: "Possible match", use_suggestion: "Use", entity_state: "State",
      },
      entities: [{ entity_id: "sensor.battery_power", name: "Battery power", device_id: "ha-battery" }],
    };
    panel._hass = {
      states: { "sensor.battery_power": { state: "1.2", attributes: { unit_of_measurement: "kW" } } },
      language: "en", localize: () => undefined,
    };
    panel._detail = {
      id: `device-${classification}`, type: "battery", type_name: "Battery", label: "Battery", properties: {},
      concepts: [{ concept: "battery.power", label: "Power", datatype: "number", unit: "W", cadence: "realtime" }],
      mappings: {},
      proposals: { "battery.power": {
        concept: "battery.power",
        source: { entityId: "sensor.battery_power" },
        configuration: {
          version: 1, entityId: "sensor.battery_power", unit: "kW",
          transforms: [{ type: "scale", factor: 1000 }],
        },
        score: 16, evidence: [], runner_up_gap: classification === "suggest" ? 5 : 0,
        classification, alternatives: [],
      } },
    };
    let body; const use = {}; const cancel = {}; const save = {};
    panel._frame = (_title, html) => { body = html; };
    panel.shadowRoot.querySelector = (selector) => selector === "#cancel" ? cancel : selector === "#save" ? save : null;
    panel.shadowRoot.querySelectorAll = (selector) => selector === "[data-use-proposal]" ? [use] : [];

    panel._renderEdit();
    assert.equal(panel._inputDraft["battery.power"].entityId, "");
    assert.match(body, classification === "suggest" ? /Suggested match/ : /Possible match/);
    use.dataset = { useProposal: "battery.power" };
    // Rewire after the minimal DOM stub gains the button's dataset.
    panel._wirePickers();
    use.onclick();
    assert.deepEqual(panel._inputDraft["battery.power"], {
      version: 1, entityId: "sensor.battery_power", unit: "kW",
      transforms: [{ type: "scale", factor: 1000 }],
    });

    panel._selectInputEntity("battery.power", "sensor.manual_power");
    assert.deepEqual(panel._inputDraft["battery.power"], {
      version: 1, entityId: "sensor.manual_power",
    });
    assert.deepEqual(panel._collectForm(true).mappings["battery.power"], {
      version: 1, entityId: "sensor.manual_power",
    });
  }
});

test("production action dialog selects a global Entity before compatible Action", () => {
  const editor = new Editor();
  editor.capabilities = [
    { service: "number.set_value", name: "Set value", entities: [
      { entity_id: "number.goodwe_target", name: "GoodWe target", metadata: "GoodWe · GW10K", fields: [{ id: "value", name: "Value", required: true, selector: { type: "number" } }] },
      { entity_id: "number.monta_limit", name: "Monta limit", metadata: "Monta", fields: [{ id: "value", name: "Value", required: true, selector: { type: "number" } }] },
    ] },
    { service: "select.select_option", name: "Select option", entities: [
      { entity_id: "select.goodwe_mode", name: "Mode", metadata: "GoodWe", fields: [{ id: "option", name: "Option", required: true, selector: { type: "select" } }] },
    ] },
    { service: "select.select_next", name: "Select next", entities: [
      { entity_id: "select.goodwe_mode", name: "Mode", metadata: "GoodWe", fields: [] },
    ] },
    { service: "water_heater.set_temperature", name: "Set temperature", entities: [
      { entity_id: "water_heater.naervarme", name: "Nærvarme", metadata: "Panasonic", fields: [{ id: "temperature", name: "Temperature", required: true, selector: { type: "number" } }] },
    ] },
  ];
  editor._dialogDraft = { type: "serviceCall", service: "", target: { entityId: "" }, data: {} };
  const initial = editor._dialog();
  assert.ok(initial.indexOf('id="entity-search"') < initial.indexOf('id="action-error"'));
  assert.doesNotMatch(initial, /id="action-search"/);
  for (const context of ["GoodWe", "Monta", "Nærvarme"]) assert.match(initial, new RegExp(context));

  editor._selectEntity("select.goodwe_mode");
  const selectDialog = editor._dialog();
  assert.ok(selectDialog.indexOf('id="entity-search"') < selectDialog.indexOf('id="action-search"'));
  assert.match(selectDialog, /select\.select_option/);
  assert.match(selectDialog, /select\.select_next/);
  assert.doesNotMatch(selectDialog, /number\.set_value|water_heater\.set_temperature/);

  editor._selectEntity("number.monta_limit");
  const numberDialog = editor._dialog();
  assert.match(numberDialog, /number\.set_value/);
  assert.doesNotMatch(numberDialog, /select\.select_option|water_heater\.set_temperature/);

  editor._selectEntity("water_heater.naervarme");
  const heaterDialog = editor._dialog();
  assert.match(heaterDialog, /water_heater\.set_temperature/);
  assert.doesNotMatch(heaterDialog, /number\.set_value|select\.select_option/);
});

test("changing Entity retains only proven-compatible Action and clears stale fields", () => {
  const editor = new Editor();
  const fields = [{ id: "value", name: "Value", required: true, selector: { type: "number" } }];
  editor.capabilities = [{ service: "number.set_value", name: "Set value", entities: [
    { entity_id: "number.goodwe", name: "GoodWe", fields },
    { entity_id: "number.monta", name: "Monta", fields },
  ] }, { service: "select.select_option", name: "Select option", entities: [
    { entity_id: "select.goodwe", name: "GoodWe mode", fields: [] },
  ] }];
  const transforms = [{ type: "nearest", values: [6, 8, 10] }];
  editor._dialogDraft = { type: "serviceCall", service: "number.set_value", target: { entityId: "number.goodwe" }, data: { value: { kind: "requestedValue", transforms } } };
  assert.match(editor._dialog(), /value_adjustments|value adjustments/i);

  editor._selectEntity("number.monta");
  assert.equal(editor._dialogDraft.service, "number.set_value");
  assert.deepEqual(editor._dialogDraft.data, {});

  editor._dialogDraft.data = { value: { kind: "literal", value: 7 } };
  editor._selectEntity("select.goodwe");
  assert.equal(editor._dialogDraft.service, "");
  assert.deepEqual(editor._dialogDraft.data, {});
});

test("production overview keeps Site separate and navigates into shared Device detail", () => {
  const panel = new Panel();
  panel._context = {
    translations: { devices: "Devices", site: "Site", add_device: "Add device", site_actions: "Site actions", delete_site: "Delete site" },
    devices: [{ id: "battery-1", type: "battery", label: "Battery · GoodWe", metadata: "GoodWe" }],
    site: { id: "site-device", name: "Home" },
  };
  let rendered; let destination;
  const deleteSite = { focus: () => {} };
  const siteMenu = {};
  const siteActions = { hidden: true, querySelector: () => deleteSite };
  const nodes = new Map([
    ['#add', {}], ['#site-detail', {}], ['#delete-site', deleteSite],
    ['#site-menu', siteMenu], ['#site-actions', siteActions],
  ]);
  const deviceRow = { dataset: { device: "battery-1" } };
  panel._frame = (title, body) => { rendered = { title, body }; };
  panel._go = (view) => { destination = view; };
  panel.shadowRoot.querySelector = (selector) => nodes.get(selector);
  panel.shadowRoot.querySelectorAll = (selector) => selector === "[data-device]" ? [deviceRow] : [];
  panel._renderHome();

  assert.match(rendered.body, /data-device="battery-1"/);
  assert.match(rendered.body, /<button class="site-link" id="site-detail"><img[^>]+\/site\.png[^]*<strong>Home<\/strong>[^]*<span class="chevron">›<\/span><\/button>/);
  assert.doesNotMatch(rendered.body, /id="site-detail"[^>]*(disabled|aria-disabled)/);
  assert.doesNotMatch(rendered.body, /data-device="site-device"/);
  nodes.get('#site-detail').onclick();
  assert.deepEqual(destination, { name: "device", deviceId: "site-device" });

  destination = undefined;
  let propagationStopped = false;
  siteMenu.onclick({ stopPropagation: () => { propagationStopped = true; } });
  assert.equal(propagationStopped, true);
  assert.equal(destination, undefined);
  assert.equal(siteActions.hidden, false);

  deleteSite.onclick();
  assert.deepEqual(destination, { name: "delete-site", stage: "confirm" });
  deviceRow.onclick();
  assert.deepEqual(destination, { name: "device", deviceId: "battery-1" });
});

test("Site detail reuses shared Mapping and Controls views from catalog data", () => {
  const panel = new Panel();
  panel._context = { translations: {
    site_information: "Site information", measurements_energy: "Measurements & energy", controls: "Controls",
    measurements_count: "measurements", energy_count: "energy mappings", available: "available", delete_site: "Delete site",
    device_actions: "Actions", configured: "Configured", not_configured: "Not configured", measurements: "Measurements", energy: "Energy",
  }, entities: [] };
  panel._hass = { states: {}, language: "en", localize: () => undefined };
  panel._detail = {
    id: "site-device", type: "site", type_name: "Site", name: "Home", label: "Home", properties: {}, proposals: {},
    concepts: [
      { concept: "site.power", label: "Power", cadence: "realtime" },
      { concept: "site.energy", label: "Energy", cadence: "interval" },
      { concept: "site.importEnergy", label: "Import energy", cadence: "interval" },
      { concept: "site.exportEnergy", label: "Export energy", cadence: "interval" },
      { concept: "site.outdoorTemperature", label: "Outdoor temperature", cadence: "realtime", datatype: "number", unit: "°C" },
    ],
    controls: [{ concept: "site.power", label: "Power", datatype: "number", unit: "W" }],
    mappings: { "site.power": { concept: "site.power", configuration: { entityId: "sensor.grid_power" } } },
    output_mappings: { "site.power": [{ concept: "site.power", mode: null, configuration: { version: 1, actions: [] } }] },
  };
  let rendered; const destinations = []; const nodes = new Map([...['#edit', '#controls', '#information', '#delete'].map((key) => [key, {}])]);
  panel._frame = (title, body) => { rendered = { title, body }; };
  panel._wireMenu = () => {};
  panel._go = (view) => destinations.push(view);
  panel.shadowRoot.querySelector = (selector) => nodes.get(selector);
  panel._renderDevice();
  assert.match(rendered.body, /Measurements &amp; energy/);
  assert.match(rendered.body, /Site information/);
  assert.match(rendered.body, /Home/);
  nodes.get('#edit').onclick(); nodes.get('#controls').onclick(); nodes.get('#information').onclick();
  assert.deepEqual(destinations, [
    { name: "edit", deviceId: "site-device" },
    { name: "controls", deviceId: "site-device" },
    { name: "site-information", deviceId: "site-device" },
  ]);
  nodes.get('#delete').onclick();
  assert.deepEqual(destinations.at(-1), { name: "delete-site", stage: "confirm" });

  const mappingHtml = panel._mappingFields(panel._detail);
  for (const concept of ["site.power", "site.energy", "site.importEnergy", "site.exportEnergy", "site.outdoorTemperature"]) assert.match(mappingHtml, new RegExp(concept.replace('.', '\\.')));
  assert.match(mappingHtml, /sensor\.grid_power/);

  const controlNode = { dataset: { control: "site.power" } };
  panel.shadowRoot.querySelectorAll = () => [controlNode];
  panel._renderControls();
  assert.match(rendered.body, /Power/);
  assert.match(rendered.body, /Configured/);
  assert.doesNotMatch(rendered.body, /Import energy|Export energy/);
  controlNode.onclick();
  assert.deepEqual(destinations.at(-1), { name: "control", deviceId: "site-device", concept: "site.power" });
});

test("heat pump mapping UI renders only finalized catalog concepts", () => {
  const panel = new Panel();
  panel._context = { translations: { measurements: "Measurements", energy: "Energy", configured: "Configured", not_configured: "Not configured" }, entities: [] };
  panel._hass = { states: {}, language: "en", localize: () => undefined };
  panel._detail = {
    id: "heat-pump", type: "heatPump", type_name: "Heat pump", properties: {}, proposals: {}, mappings: {}, output_mappings: {},
    concepts: [
      { concept: "heatPump.power", label: "Power", cadence: "realtime" },
      { concept: "heatPump.energy", label: "Energy", cadence: "interval" },
      { concept: "heatPump.bufferEnergy", label: "Buffer energy", cadence: "interval" },
      { concept: "heatPump.tankEnergy", label: "Tank energy", cadence: "interval" },
      { concept: "heatPump.temperature", label: "Temperature", cadence: "realtime" },
      { concept: "heatPump.tankTemperature", label: "Tank temperature", cadence: "realtime" },
      { concept: "heatPump.state", label: "Operating state", cadence: "realtime" },
    ],
    controls: [
      { concept: "heatPump.power", label: "Power" },
      { concept: "heatPump.temperature", label: "Temperature" },
      { concept: "heatPump.tankTemperature", label: "Tank temperature" },
    ],
  };
  let rendered = "";
  panel._frame = (_title, body) => { rendered = body; };
  panel.shadowRoot.querySelectorAll = () => [];
  const mappings = panel._mappingFields(panel._detail);
  panel._renderControls();
  const controls = rendered;
  for (const label of ["Power", "Temperature", "Tank temperature"]) {
    assert.match(mappings, new RegExp(label));
    assert.match(controls, new RegExp(label));
  }
  assert.match(mappings, /Energy/);
  assert.match(mappings, /Buffer energy/);
  assert.match(mappings, /Tank energy/);
  assert.match(mappings, /Operating state/);
  assert.doesNotMatch(controls, /Buffer energy|Tank energy/);
  assert.deepEqual(
    new Set([...`${mappings}${controls}`.matchAll(/heatPump\.[A-Za-z]+/g)].map(([concept]) => concept)),
    new Set([
      "heatPump.power",
      "heatPump.energy",
      "heatPump.bufferEnergy",
      "heatPump.tankEnergy",
      "heatPump.temperature",
      "heatPump.tankTemperature",
      "heatPump.state",
    ]),
  );
});

test("ordinary Device and canonical Site route to their distinct lifecycle deletes", () => {
  const routeDelete = (type) => {
    const panel = new Panel(); const nodes = new Map([...['#edit', '#controls', '#information', '#delete'].map((key) => [key, {}])]);
    panel._context = { translations: { measurements_energy: "Measurements & energy", controls: "Controls", measurements_count: "measurements", energy_count: "energy", available: "available", device_information: "Device information", site_information: "Site information", device_actions: "Actions", delete_device: "Delete device", delete_site: "Delete site", optional: "Optional" } };
    panel._detail = { id: `${type}-id`, type, type_name: type === "site" ? "Site" : "Battery", name: "Example", properties: {}, concepts: [], controls: [], mappings: {}, output_mappings: {} };
    let destination;
    panel._frame = () => {};
    panel._wireMenu = () => {};
    panel._go = (view) => { destination = view; };
    panel.shadowRoot.querySelector = (selector) => nodes.get(selector);
    panel._renderDevice();
    nodes.get('#delete').onclick();
    return destination;
  };

  assert.deepEqual(routeDelete("battery"), { name: "delete-device", deviceId: "battery-id", stage: "confirm" });
  assert.deepEqual(routeDelete("site"), { name: "delete-site", stage: "confirm" });
});

test("production Edit mappings path saves and restores ordered input conversions", async () => {
  const detail = {
    id: "site-device", type: "site", type_name: "Site", label: "Site · Home", properties: {},
    concepts: [{ concept: "site.power", label: "Power", datatype: "number", unit: "W", cadence: "realtime" }],
    mappings: { "site.power": { configuration: { version: 1, entityId: "sensor.grid_power" } } }, proposals: {},
  };
  const panel = new Panel(); panel._context = { translations: {}, entities: [] }; panel._detail = detail;
  panel._hass = { states: { "sensor.grid_power": { state: "4919", attributes: { friendly_name: "Grid power", unit_of_measurement: "W" } } }, language: "en", localize: () => undefined };
  let body; let saved;
  const cancel = {}; const save = {};
  panel._call = async (type, payload) => { if (type === "fluks/config/device_save") saved = payload; return {}; };
  panel._frame = (_title, html) => { body = html; };
  panel.shadowRoot.querySelectorAll = (selector) => {
    if (selector === "[data-picker-kind]") return [];
    if (selector === "[data-input-transform]") return [];
    if (selector === "[data-property]") return [];
    return [];
  };
  panel.shadowRoot.querySelector = (selector) => {
    if (selector === "#cancel") return cancel;
    if (selector === "#save") return save;
    return null;
  };

  panel._renderEdit();
  assert.match(body, /sensor\.grid_power/);
  assert.match(body, /Conversions|conversions/);
  assert.match(body, /data-input-transform="add"/);
  assert.doesNotMatch(body, /data-input-transform-type|data-input-transform-value/);
  assert.equal(panel._commitInputConversion("site.power", undefined, "invert", ""), true);
  panel._renderEdit();
  assert.match(body, /Invert sign|invert/);
  assert.match(body, /class="order">1</);
  assert.match(body, /data-input-transform="up"[^>]+disabled/);
  assert.match(body, /data-input-transform="down"[^>]+disabled/);
  assert.match(body, /data-input-transform="edit"/);
  await save.onclick();
  assert.deepEqual(saved.mappings["site.power"], {
    version: 1, entityId: "sensor.grid_power", transforms: [{ type: "invert" }],
  });

  const reopened = new Panel(); reopened._context = panel._context; reopened._hass = panel._hass;
  reopened._detail = { ...detail, mappings: { "site.power": { configuration: saved.mappings["site.power"] } } };
  reopened._frame = (_title, html) => { body = html; };
  reopened.shadowRoot.querySelectorAll = () => [];
  reopened.shadowRoot.querySelector = (selector) => selector === "#cancel" ? {} : selector === "#save" ? {} : null;
  reopened._renderEdit();
  assert.match(body, /Invert sign|invert/);
  assert.match(body, /sensor\.grid_power/);
});

test("Battery physical fields keep learned knowledge separate from editable values", () => {
  const panel = new Panel();
  panel._context = { translations: {
    edit_mappings: "Edit mappings and properties", device_information: "Device information",
    battery_configuration: "Battery configuration", battery_capacity: "Battery capacity",
    minimum_soc: "Minimum SOC", maximum_soc: "Maximum SOC", estimated_by_fluks: "Estimated by fluks",
    lowest_observed_by_fluks: "Lowest observed by fluks", highest_observed_by_fluks: "Highest observed by fluks",
    name: "Name", vendor: "Vendor", model: "Model", save_mapping: "Save mapping", cancel: "Cancel",
  }, entities: [] };
  panel._hass = { states: {}, language: "en", localize: () => undefined };
  panel._detail = {
    id: "battery-device", type: "battery", type_name: "Battery", label: "Battery · Test",
    properties: {
      "battery.capacityKwhEstimated": 15.8,
      "battery.socMinimumObserved": 15,
      "battery.socMaximumObserved": 97,
    }, concepts: [], mappings: {}, proposals: {}, controls: [], output_mappings: {},
  };
  let body;
  panel._frame = (_title, html) => { body = html; };
  panel.shadowRoot.querySelectorAll = () => [];
  panel.shadowRoot.querySelector = (selector) => selector === "#cancel" || selector === "#save" ? {} : null;
  panel._renderEdit();

  assert.match(body, /data-property="capacityKwh" value="" placeholder="~15\.8"/);
  assert.match(body, /data-property="battery\.socMinimum" value="" placeholder="~15"/);
  assert.match(body, /data-property="battery\.socMaximum" value="" placeholder="~97"/);
  assert.match(body, /Estimated by fluks: 15\.8 kWh/);
  assert.match(body, /Lowest observed by fluks: 15 %/);
  assert.match(body, /Highest observed by fluks: 97 %/);
  assert.deepEqual(panel._editProperties, {});

  panel._inputDraft = {};
  panel.shadowRoot.querySelectorAll = (selector) => selector === "[data-property]" ? [
    { dataset: { property: "capacityKwh" }, value: "", type: "number" },
    { dataset: { property: "battery.socMinimum" }, value: "", type: "number" },
    { dataset: { property: "battery.socMaximum" }, value: "", type: "number" },
  ] : [];
  assert.deepEqual(panel._collectForm(true).properties, {
    capacityKwh: null, "battery.socMinimum": null, "battery.socMaximum": null,
  });
});

test("configured Battery values take precedence, clear to hints, and remain Battery-only", () => {
  const panel = new Panel(); panel._context = { translations: {} };
  const learned = {
    "battery.capacityKwhEstimated": 14.038032,
    "battery.socMinimumObserved": 15.129,
    "battery.socMaximumObserved": 97.987,
  };
  const configured = { capacityKwh: 20, "battery.socMinimum": 10, "battery.socMaximum": 90 };
  const body = panel._propertiesForm(configured, "battery", learned);
  assert.match(body, /data-property="capacityKwh" value="20"/);
  assert.match(body, /data-property="battery\.socMinimum" value="10"/);
  assert.match(body, /data-property="battery\.socMaximum" value="90"/);
  assert.match(body, /data-property="capacityKwh" value="20" placeholder="~14\.03"/);

  const cleared = panel._propertiesForm({}, "battery", learned);
  assert.match(cleared, /placeholder="~14\.03"/);
  assert.match(cleared, /placeholder="~15\.12"/);
  assert.match(cleared, /placeholder="~97\.98"/);
  assert.match(cleared, /estimated by fluks: 14\.03 kWh/i);
  assert.doesNotMatch(cleared, /14\.038032|15\.129|97\.987/);
  const unknown = panel._propertiesForm({}, "battery", {});
  assert.doesNotMatch(unknown, /placeholder="~/);
  assert.doesNotMatch(panel._propertiesForm({}, "heatPump"), /Battery configuration|capacityKwh|socMinimum|socMaximum/);
});

test("Solar installed capacity keeps learned suggestion separate and round-trips backend state", async () => {
  const learned = { "solar.installedKwpEstimated": 14.038032 };
  const panel = new Panel();
  panel._context = { translations: {}, entities: [] };
  panel._hass = { states: {}, language: "en", localize: () => undefined };
  panel._view = { name: "edit" };
  panel._detail = {
    id: "solar-device", type: "solar", type_name: "Solar", label: "Solar · Test",
    properties: learned, concepts: [], mappings: {}, proposals: {},
  };
  const cancel = {}; const save = {}; let body; let saved;
  const installedInput = { dataset: { property: "installedKWp" }, value: "", type: "number" };
  panel._frame = (_title, html) => { body = html; };
  panel.shadowRoot.querySelectorAll = (selector) => selector === "[data-property]" ? [installedInput] : [];
  panel.shadowRoot.querySelector = (selector) => selector === "#cancel" ? cancel : selector === "#save" ? save : null;
  panel._call = async (_type, payload) => { saved = payload; return { properties: learned }; };

  panel._renderEdit();
  assert.match(body, /<div class="fields three">/);
  assert.match(body, /data-property="installedKWp" value="" placeholder="~14\.03"/);
  assert.match(body, /estimated by fluks: 14\.03 kWp/i);
  const styles = panel._styles();
  assert.match(styles, /\.fields\{display:grid;grid-template-columns:repeat\(2,minmax\(0,1fr\)\);align-items:start;gap:14px\}/);
  assert.match(styles, /input,select\{box-sizing:border-box;width:100%/);
  assert.match(styles, /\.property-input\{position:relative;display:block;min-width:0\}/);
  assert.match(styles, /\.property-input input\{min-width:0;padding-right:54px\}/);
  assert.match(styles, /\.property-input>span\{position:absolute;right:12px;top:50%/);
  assert.deepEqual(panel._editProperties, {});
  await save.onclick();
  assert.deepEqual(saved.properties, { installedKWp: null });
  assert.equal(Object.hasOwn(saved.properties, "solar.installedKwpEstimated"), false);

  panel._detail = { ...panel._detail, properties: learned };
  panel._inputDraftDevice = undefined;
  installedInput.value = "16.75";
  panel._call = async (_type, payload) => {
    saved = payload;
    return { properties: { ...learned, installedKWp: 16.75 } };
  };
  panel._renderEdit();
  await save.onclick();
  assert.equal(saved.properties.installedKWp, 16.75);
  panel.shadowRoot.querySelectorAll = () => [];
  panel._renderEdit();
  assert.match(body, /data-property="installedKWp" value="16\.75" placeholder="~14\.03"/);

  panel.shadowRoot.querySelectorAll = (selector) => selector === "[data-property]" ? [{ ...installedInput, value: "" }] : [];
  panel._call = async (_type, payload) => {
    saved = payload;
    return { properties: learned };
  };
  await save.onclick();
  assert.equal(saved.properties.installedKWp, null);
  panel.shadowRoot.querySelectorAll = () => [];
  panel._renderEdit();
  assert.match(body, /data-property="installedKWp" value="" placeholder="~14\.03"/);
});

test("Solar edit merges only missing Forecast.Solar property suggestions", () => {
  const panel = new Panel();
  panel._context = { translations: {}, entities: [] };
  panel._hass = { states: {}, language: "en", localize: () => undefined };
  panel._view = { name: "edit" };
  panel._detail = {
    id: "solar-device", type: "solar", type_name: "Solar", label: "Solar · Test",
    properties: { installedKWp: 12 },
    property_suggestions: { installedKWp: 8.45, azimuthDegrees: 182, tiltDegrees: 37 },
    concepts: [], mappings: {}, proposals: {},
  };
  let body;
  const cancel = {};
  const save = {};
  panel._frame = (_title, html) => { body = html; };
  panel.shadowRoot.querySelectorAll = () => [];
  panel.shadowRoot.querySelector = (selector) => selector === "#cancel" ? cancel : selector === "#save" ? save : null;

  panel._renderEdit();

  assert.deepEqual(panel._editProperties, {
    installedKWp: 12,
    azimuthDegrees: 182,
    tiltDegrees: 37,
  });
  assert.match(body, /data-property="installedKWp" value="12"/);
  assert.match(body, /data-property="azimuthDegrees" value="182"/);
  assert.match(body, /data-property="tiltDegrees" value="37"/);
});

test("Space Heater exposes the existing ratedPowerW Installation property", () => {
  const panel = new Panel();
  panel._context = { translations: { installation: "Installation", rated_power: "Rated power" }, entities: [] };
  panel._hass = { states: {}, language: "en", localize: () => undefined };
  const empty = panel._propertiesForm({}, "spaceHeater");
  assert.match(empty, /Installation/);
  assert.match(empty, /<div class="fields three"><label>Rated power/);
  assert.match(empty, /Rated power/);
  assert.match(empty, /data-property="ratedPowerW" value=""/);
  assert.match(empty, />W<\/span>/);

  const configured = panel._propertiesForm({ ratedPowerW: 2400 }, "spaceHeater");
  assert.match(configured, /data-property="ratedPowerW" value="2400"/);
  assert.doesNotMatch(configured, /installedKWp|capacityKwh/);
});

test("Space Heater ratedPowerW uses the existing Device Save property path", async () => {
  const panel = new Panel();
  panel._context = { translations: { installation: "Installation", rated_power: "Rated power" }, entities: [] };
  panel._hass = { states: {}, language: "en", localize: () => undefined };
  panel._view = { name: "edit" };
  panel._detail = { id: "space-heater-device", type: "spaceHeater", type_name: "Space heater", label: "Space heater", properties: { ratedPowerW: 1800 }, concepts: [], mappings: {}, proposals: {} };
  const cancel = {};
  const save = {};
  let body;
  let saved;
  const input = { dataset: { property: "ratedPowerW" }, value: "2400", type: "number" };
  panel._frame = (_title, html) => { body = html; };
  panel.shadowRoot.querySelectorAll = (selector) => selector === "[data-property]" ? [input] : [];
  panel.shadowRoot.querySelector = (selector) => selector === "#cancel" ? cancel : selector === "#save" ? save : null;
  panel._call = async (_type, payload) => {
    saved = payload;
    return { properties: { ratedPowerW: 2400 } };
  };

  panel._renderEdit();
  assert.match(body, /data-property="ratedPowerW" value="1800"/);
  await save.onclick();
  assert.equal(saved.device_id, "space-heater-device");
  assert.deepEqual(saved.properties, { ratedPowerW: 2400 });
});

test("successful Battery property Save discards the stale draft before reopening", async () => {
  const panel = new Panel();
  panel._context = { translations: {}, entities: [] };
  panel._hass = { states: {}, language: "en", localize: () => undefined };
  panel._view = { name: "edit" };
  panel._detail = {
    id: "battery-device", type: "battery", type_name: "Battery", label: "Battery · Test",
    properties: { capacityKwh: 16.058507, "battery.capacityKwhEstimated": 14.038032 },
    concepts: [], mappings: {}, proposals: {},
  };
  const cancel = {}; const save = {}; let body; let saved;
  const propertyInputs = [
    { dataset: { property: "capacityKwh" }, value: "18.5", type: "number" },
    { dataset: { property: "battery.socMinimum" }, value: "12.5", type: "number" },
    { dataset: { property: "battery.socMaximum" }, value: "94.5", type: "number" },
  ];
  panel._frame = (_title, html) => { body = html; };
  panel.shadowRoot.querySelectorAll = (selector) => selector === "[data-property]" ? propertyInputs : [];
  panel.shadowRoot.querySelector = (selector) => selector === "#cancel" ? cancel : selector === "#save" ? save : null;
  panel._call = async (_type, payload) => {
    saved = payload;
    return { properties: {
      capacityKwh: 18.5, "battery.socMinimum": 12.5, "battery.socMaximum": 94.5,
      "battery.capacityKwhEstimated": 14.038032,
    } };
  };

  panel._renderEdit();
  assert.match(body, /data-property="capacityKwh" value="16\.058507"/);
  await save.onclick();
  assert.deepEqual(saved.properties, {
    capacityKwh: 18.5, "battery.socMinimum": 12.5, "battery.socMaximum": 94.5,
  });
  assert.equal(panel._detail.properties.capacityKwh, 18.5);
  assert.equal(panel._detail.properties["battery.socMinimum"], 12.5);
  assert.equal(panel._detail.properties["battery.socMaximum"], 94.5);
  assert.equal(panel._inputDraftDevice, undefined);
  assert.equal(panel._editProperties, undefined);

  panel.shadowRoot.querySelectorAll = () => [];
  panel._renderEdit();
  assert.match(body, /data-property="capacityKwh" value="18\.5"/);
  assert.match(body, /data-property="battery\.socMinimum" value="12\.5"/);
  assert.match(body, /data-property="battery\.socMaximum" value="94\.5"/);
  assert.doesNotMatch(body, /data-property="capacityKwh" value="16\.058507"/);
});

test("Space Heater rated power is required only without real Power or Energy mappings", async () => {
  const run = async (concept, ratedPower) => {
    const panel = new Panel();
    panel._context = { translations: { edit_mappings: "Edit mappings", device_information: "Device information", installation: "Installation", rated_power: "Rated power", rated_power_required: "Required to calculate Power and Energy when no real measurements are mapped.", name: "Name", vendor: "Vendor", model: "Model", save_mapping: "Save mapping", cancel: "Cancel" }, entities: [] };
    panel._hass = { states: {}, language: "en", localize: () => undefined };
    panel._view = { name: "edit" };
    const concepts = concept ? [{ concept, label: concept, datatype: "number", unit: "W", cadence: "realtime" }] : [];
    panel._detail = { id: "space-heater-device", type: "spaceHeater", label: "Space heater", properties: ratedPower === undefined ? {} : { ratedPowerW: ratedPower }, concepts, mappings: concept ? { [concept]: { configuration: { entityId: "sensor.real" } } } : {}, proposals: {} };
    const cancel = {}; const save = {}; let calls = 0; let body;
    panel._frame = (_title, html) => { body = html; };
    panel.shadowRoot.querySelector = (selector) => selector === "#cancel" ? cancel : selector === "#save" ? save : null;
    panel.shadowRoot.querySelectorAll = (selector) => selector === "[data-property]" ? [{ dataset: { property: "ratedPowerW" }, value: ratedPower === undefined ? "" : String(ratedPower), type: "number" }] : [];
    panel._call = async () => { calls += 1; return { properties: {} }; };
    panel._renderEdit();
    await save.onclick();
    return { calls, body };
  };

  const missing = await run(undefined, undefined);
  assert.equal(missing.calls, 0);
  assert.match(missing.body, /rated_power_required|Required to calculate Power and Energy/);
  assert.equal((await run("spaceHeater.power", undefined)).calls, 1);
  assert.equal((await run("spaceHeater.energy", undefined)).calls, 1);
  assert.equal((await run(undefined, 2000)).calls, 1);
});

test("Input Mapping conversions follow the unsaved Entity draft immediately", () => {
  const panel = new Panel();
  panel._context = { translations: {}, entities: [] };
  panel._hass = { states: {}, language: "en", localize: () => undefined };
  panel._detail = {
    id: "device", type: "battery", type_name: "Battery", label: "Battery · Test", properties: {},
    concepts: [{ concept: "battery.power", label: "Power", datatype: "number", unit: "W", cadence: "realtime" }],
    mappings: {}, proposals: {},
  };
  let body;
  panel._frame = (_title, html) => { body = html; };
  panel.shadowRoot.querySelectorAll = () => [];
  panel.shadowRoot.querySelector = (selector) => selector === "#cancel" ? {} : selector === "#save" ? {} : null;

  panel._renderEdit();
  assert.doesNotMatch(body, /data-input-transform="add"/);

  panel._selectInputEntity("battery.power", "sensor.goodwe_power");
  assert.match(body, /data-input-transform="add"/);
  assert.equal(panel._inputDraft["battery.power"].entityId, "sensor.goodwe_power");

  panel._selectInputEntity("battery.power", "");
  assert.doesNotMatch(body, /data-input-transform="add"/);
  assert.equal(panel._inputDraft["battery.power"].entityId, "");
});

test("production Input Mapping picker offers entity state and attributes directly", () => {
  const panel = new Panel();
  panel._context = { translations: { entity_state: "State" }, entities: [] };
  panel._hass = {
    states: { "climate.buffer": { state: "heat", attributes: {
      temperature: 50,
      current_temperature: 56,
      target_temp_high: 58,
      target_temp_low: 47,
      hvac_action: "heating",
      ambient: 51,
      ambient_unit: "°C",
      hvac_modes: ["off", "heat"],
      fan_mode: "auto",
      friendly_name: "Buffer",
      supported_features: 385,
    } } },
    config: { unit_system: { temperature: "°C" } }, language: "en", localize: () => undefined,
  };
  panel._inputDraft = {
    "heatPump.temperature": { version: 1, entityId: "climate.buffer" },
  };
  panel._renderInputDraft = () => {};
  const detail = {
    concepts: [{ concept: "heatPump.temperature", label: "Temperature", cadence: "realtime" }],
    mappings: {}, proposals: {},
  };

  const sources = panel._entitySources({ id: "climate.buffer", name: "Buffer", secondary: "climate.buffer" }, true);
  assert.deepEqual(sources.map(({ attribute, trailing }) => ({ attribute, trailing })), [
    { attribute: "temperature", trailing: "50 °C" },
    { attribute: "current_temperature", trailing: "56 °C" },
    { attribute: "target_temp_high", trailing: "58 °C" },
    { attribute: "target_temp_low", trailing: "47 °C" },
    { attribute: "ambient", trailing: "51 °C" },
  ]);
  assert.match(panel._pickerValue("entity", "climate.buffer", "heatPump.temperature", "hvac_action"), /heating/);
  assert.doesNotMatch(panel._pickerValue("entity", "climate.buffer", "heatPump.temperature", "hvac_action"), /heating °C/);
  const nonTemperatureSources = panel._entitySources({ id: "climate.buffer", name: "Buffer", secondary: "climate.buffer" });
  assert.ok(nonTemperatureSources.some(({ attribute }) => attribute === "fan_mode"));
  assert.ok(nonTemperatureSources.some(({ attribute }) => attribute === "friendly_name"));
  assert.ok(nonTemperatureSources.some(({ attribute }) => attribute === "supported_features"));

  panel._hass.states["sensor.room_temperature"] = { state: "21.5", attributes: { unit_of_measurement: "°C" } };
  assert.deepEqual(
    panel._entitySources({ id: "sensor.room_temperature", name: "Room", secondary: "sensor.room_temperature", deviceClass: "temperature" }, true)
      .map(({ attribute, trailing }) => ({ attribute, trailing })),
    [{ attribute: "", trailing: "21.5 °C" }],
  );
  panel._hass.states["sensor.accumulated_energy"] = { state: "1234", attributes: { unit_of_measurement: "kWh", device_class: "energy" } };
  panel._hass.states["binary_sensor.heating"] = { state: "on", attributes: { device_class: "heat" } };
  panel._hass.states["sensor.cycle_count"] = { state: "42", attributes: { friendly_name: "Cycle count" } };
  for (const item of [
    { id: "sensor.accumulated_energy", deviceClass: "energy" },
    { id: "binary_sensor.heating", deviceClass: "heat" },
    { id: "sensor.cycle_count" },
  ]) assert.deepEqual(panel._entitySources({ name: item.id, secondary: item.id, ...item }, true), []);

  panel._selectInputEntity("heatPump.temperature", "climate.buffer", "current_temperature");
  assert.equal(panel._inputDraft["heatPump.temperature"].attribute, "current_temperature");
  assert.equal(panel._collectForm(true).mappings["heatPump.temperature"].attribute, "current_temperature");
  const rendered = panel._mappingFields(detail, true);
  assert.match(rendered, /current_temperature · climate\.buffer/);
  assert.match(rendered, /data-picker-attribute="current_temperature"/);

  panel._selectInputEntity("heatPump.temperature", "climate.buffer");
  assert.equal(Object.hasOwn(panel._inputDraft["heatPump.temperature"], "attribute"), false);
  assert.deepEqual(panel._collectForm(true).mappings["heatPump.temperature"], {
    version: 1, entityId: "climate.buffer",
  });
});

test("clearing a persisted Input Mapping remains cleared until Save or Cancel", async () => {
  const persisted = { version: 1, entityId: "sensor.saved_power", transforms: [{ type: "invert" }] };
  const panel = new Panel();
  panel._context = { translations: {}, entities: [] };
  panel._hass = { states: {}, language: "en", localize: () => undefined };
  panel._detail = {
    id: "device", type: "battery", type_name: "Battery", label: "Battery · Test", properties: {},
    concepts: [{ concept: "battery.power", label: "Power", datatype: "number", unit: "W", cadence: "realtime" }],
    mappings: { "battery.power": { configuration: persisted } }, proposals: {},
  };
  let body; let saved; let backCount = 0;
  const originalBack = history.back; history.back = () => { backCount += 1; };
  const cancel = {}; const save = {};
  panel._frame = (_title, html) => { body = html; };
  panel._call = async (_type, payload) => { saved = payload; return {}; };
  panel.shadowRoot.querySelectorAll = () => [];
  panel.shadowRoot.querySelector = (selector) => selector === "#cancel" ? cancel : selector === "#save" ? save : null;

  panel._renderEdit();
  assert.match(body, /sensor\.saved_power/);
  assert.match(body, /data-input-transform="add"/);
  panel._selectInputEntity("battery.power", "");
  assert.doesNotMatch(body, /sensor\.saved_power/);
  assert.doesNotMatch(body, /data-input-transform="add"/);
  assert.equal(panel._inputDraft["battery.power"].entityId, "");

  await save.onclick();
  assert.deepEqual(saved.mappings, {
    "battery.power": { version: 1, entityId: "", transforms: [{ type: "invert" }] },
  });

  panel._inputDraftDevice = undefined;
  panel._renderEdit();
  panel._selectInputEntity("battery.power", "");
  cancel.onclick();
  assert.equal(panel._inputDraft, undefined);
  panel._renderEdit();
  assert.match(body, /sensor\.saved_power/);
  assert.match(body, /data-input-transform="add"/);
  assert.ok(backCount >= 2);
  history.back = originalBack;
});
