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
globalThis.customElements = { define: (name, value) => elements.set(name, value), get: (name) => elements.get(name) };
globalThis.CustomEvent = class {};
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
globalThis.history = { back: () => {} };

await import("../../custom_components/fluks/frontend/control-editor-panel.js?rev=production-path-test");
const Panel = customElements.get("fluks-control-editor-panel");
const Editor = customElements.get("fluks-control-action-editor");

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
    panel.shadowRoot.querySelector = (selector) => selector === "fluks-control-action-editor" ? editor : null;
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
    panel._frame = () => { editor = new Editor(); panel.shadowRoot.querySelector = (selector) => selector === "fluks-control-action-editor" ? editor : null; };
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
