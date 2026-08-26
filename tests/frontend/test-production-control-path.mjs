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

await import("../../custom_components/fluks/frontend/control-action-editor.js?rev=existing-ha-session");
await import("../../custom_components/fluks/frontend/control-editor-panel.js?rev=production-path-test");
const PANEL_TAG = "fluks-control-editor-panel-production-path-test";
const Panel = customElements.get(PANEL_TAG);
const CONTROL_EDITOR_TAG = "fluks-control-action-editor-production-path-test";
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
    id: "site-device", type: "site", type_name: "Site", name: "Home", label: "Home", properties: {}, suggestions: {},
    concepts: [
      { concept: "site.power", label: "Power", cadence: "realtime" },
      { concept: "site.energy", label: "Energy", cadence: "interval" },
      { concept: "site.importEnergy", label: "Import energy", cadence: "interval" },
      { concept: "site.exportEnergy", label: "Export energy", cadence: "interval" },
    ],
    controls: [{ concept: "site.power", label: "Power", datatype: "number", unit: "W" }],
    mappings: { "site.power": { concept: "site.power", configuration: { entityId: "sensor.grid_power" } } },
    output_mappings: { "site.power": { concept: "site.power", configuration: { version: 1, actions: [] } } },
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
  for (const concept of ["site.power", "site.energy", "site.importEnergy", "site.exportEnergy"]) assert.match(mappingHtml, new RegExp(concept.replace('.', '\\.')));
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
