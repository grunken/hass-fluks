import assert from "node:assert/strict";
import test from "node:test";

const elements = new Map();
globalThis.HTMLElement = class {
  attachShadow() {
    this.shadowRoot = {
      innerHTML: "",
      querySelectorAll: () => [],
      querySelector: () => null,
    };
    return this.shadowRoot;
  }
  dispatchEvent(event) { this.lastEvent = event; return true; }
};
globalThis.CustomEvent = class {
  constructor(type, init = {}) { this.type = type; this.detail = init.detail; }
};
globalThis.customElements = {
  define: (name, element) => { if (elements.has(name)) throw new Error(`duplicate ${name}`); elements.set(name, element); },
  get: (name) => elements.get(name),
};

const { FluksControlActionEditor: Editor } = await import("../../custom_components/fluks/frontend/control-action-editor.js");
assert.equal(customElements.get("fluks-control-action-editor"), undefined);

const mode = {
  type: "serviceCall",
  service: "select.select_option",
  target: { entityId: "select.goodwe_operation_mode" },
  data: { option: { kind: "literal", value: "eco_charge" } },
};
const target = {
  type: "serviceCall",
  service: "number.set_value",
  target: { entityId: "number.goodwe_charge_target" },
  data: {
    value: {
      kind: "requestedValue",
      transforms: [
        { type: "powerToCurrent", phases: 3, voltage: 230 },
        { type: "nearest", values: [5, 10, 15] },
        { type: "valueMap", values: [{ from: 5, to: "low" }, { from: 10, to: "high" }] },
      ],
    },
  },
};
const capabilities = [{
  service: "select.select_option", name: "Select option", description: "Selects an option.",
  entities: [{ entity_id: "select.goodwe_operation_mode", name: "GoodWe mode", fields: [{ id: "option", name: "Option", required: true, selector: { type: "select" }, constraints: { options: ["eco", "eco_charge"] } }] }],
}, {
  service: "number.set_value", name: "Set value", description: "Sets a value.",
  entities: [{ entity_id: "number.goodwe_charge_target", name: "GoodWe target", fields: [{ id: "value", name: "Value", required: true, selector: { type: "number" }, constraints: { min: 0, max: 100, step: 1 } }] }],
}];

test("production editor preserves ordered explicit Save and lossless Cancel", () => {
  const editor = new Editor();
  editor.valueType = { datatype: "number", unit: "W" };
  editor.actions = [mode, target];
  editor.render = () => {};

  editor._command("up", 1);
  assert.deepEqual(editor.actions, [target, mode]);
  editor._command("cancel", 0);
  assert.deepEqual(editor.actions, [mode, target]);
  assert.equal(editor.lastEvent.type, "control-cancelled");

  editor._command("up", 1);
  editor._command("save", 0);
  assert.equal(editor.lastEvent.type, "control-saved");
  assert.deepEqual(editor.lastEvent.detail.configuration, {
    version: 1,
    actions: [target, mode],
  });
});

test("saving an empty existing sequence requests Mapping removal", () => {
  const editor = new Editor();
  editor.actions = [mode];
  editor.render = () => {};
  editor._command("remove", 0);
  editor._command("save", 0);
  assert.equal(editor.lastEvent.type, "control-saved");
  assert.equal(editor.lastEvent.detail.configuration, null);
});

test("editor exposes every documented requested-value transform as structured UI", () => {
  const editor = new Editor();
  editor.valueType = { datatype: "number", unit: "W" };
  const html = editor._transformEditor("value", []);
  for (const transform of ["powerToCurrent", "difference", "round", "clamp", "nearest", "valueMap", "invert", "scale", "offset"]) {
    assert.match(html, new RegExp(`value="${transform}"`));
  }
  assert.doesNotMatch(html, /textarea|JSON|YAML/);
});

test("reference transform exposes entity state and attribute selection", () => {
  const editor = new Editor();
  editor.valueType = { datatype: "number", unit: "°C" };
  editor.referenceEntities = [{ entity_id: "climate.buffer", name: "Buffer", attributes: ["temperature", "current_temperature"] }];
  editor._transformEditing = { field: "temperature", index: 0 };
  const html = editor._transformEditor("temperature", [{ type: "difference", reference: { entityId: "climate.buffer", attribute: "current_temperature" } }]);
  assert.match(html, /reference entity/);
  assert.match(html, /climate\.buffer/);
  assert.match(html, /reference value/);
  assert.match(html, /current_temperature/);
  assert.match(html, /entity state/);
});

test("transform availability follows canonical datatype, unit, and ordered pipeline", () => {
  const power = new Editor(); power.valueType = { datatype: "number", unit: "W" };
  assert.deepEqual(power._availableTransforms([]), ["powerToCurrent", "difference", "round", "clamp", "invert", "scale", "offset", "nearest", "valueMap"]);
  const converted = [{ type: "powerToCurrent", phases: 3, voltage: 230 }];
  assert.deepEqual(power._pipeline(converted), { valid: true, datatype: "number", unit: "A" });
  assert.ok(!power._availableTransforms(converted).includes("powerToCurrent"));
  assert.ok(power._availableTransforms(converted).includes("nearest"));
  assert.deepEqual(power._pipeline([
    { type: "difference", reference: { entityId: "climate.buffer" } },
    { type: "round", decimals: 0 },
    { type: "clamp", min: -5, max: 5 },
  ]), { valid: true, datatype: "number", unit: "W" });

  for (const concept of ["heatPump.temperature", "heatPump.tankTemperature", "waterHeater.temperature"]) {
    const temperature = new Editor(); temperature.controlName = concept; temperature.valueType = { datatype: "number", unit: "°C" };
    assert.ok(!temperature._availableTransforms([]).includes("powerToCurrent"));
    assert.deepEqual(temperature._pipeline([{ type: "offset", amount: 1 }]), { valid: true, datatype: "number", unit: "°C" });
  }
  const logical = new Editor(); logical.valueType = { datatype: "boolean", unit: null };
  assert.deepEqual(logical._availableTransforms([]), ["valueMap"]);
});

test("valueMap output and reordering recalculate and block invalid pipelines", () => {
  const editor = new Editor(); editor.valueType = { datatype: "number", unit: "W" };
  editor.strings = { invalid_pipeline: "Invalid adjustment sequence at step" };
  const map = { type: "valueMap", values: [{ from: 0, to: "off" }, { from: 1, to: "on" }] };
  assert.deepEqual(editor._pipeline([map]), { valid: true, datatype: "string", unit: null });
  assert.deepEqual(editor._availableTransforms([map]), ["valueMap"]);
  const invalid = [map, { type: "nearest", values: [0, 1] }];
  assert.equal(editor._pipeline(invalid).valid, false);
  assert.equal(editor._pipeline(invalid).index, 1);
  assert.equal(editor._pipeline([{ type: "nearest", values: [0, 1] }, map]).valid, true);
  assert.equal(editor._pipeline([{ type: "nearest", values: [0, 1] }]).unit, "W");
  assert.ok(editor._availableTransforms([]).includes("nearest"));

  editor._working = [{ type: "serviceCall", service: "number.set_value", target: { entityId: "number.target" }, data: { value: { kind: "requestedValue", transforms: invalid } } }];
  editor.render = () => {};
  editor._command("save", 0);
  assert.match(editor._saveError, /step 2/i);
  assert.notEqual(editor.lastEvent?.type, "control-saved");
  editor._working[0].data.value.transforms.shift();
  assert.equal(editor._configurationPipelineError(), "");
});

test("editor is driven by HA actions, entities, and dynamic fields", () => {
  const editor = new Editor();
  editor.capabilities = capabilities;
  editor._dialogDraft = structuredClone(mode);
  const html = editor._dialog();
  assert.match(html, /select\.select_option/);
  assert.match(html, /select\.goodwe_operation_mode/);
  assert.match(html, /eco_charge/);
  assert.doesNotMatch(html, /action-type|selectOption|setNumber|water_heater_temperature/);
});

test("typed literal and requestedValue bindings retain the existing Mapping shape", () => {
  const editor = new Editor();
  editor.capabilities = capabilities;
  editor.actions = [mode, target];
  assert.equal(typeof editor.actions[0].data.option.value, "string");
  assert.equal(editor.actions[1].data.value.kind, "requestedValue");
  assert.deepEqual(editor.actions[1].data.value.transforms, target.data.value.transforms);
});

test("production editor removal remains a draft until Save", () => {
  const editor = new Editor();
  editor.actions = [mode, target];
  editor.render = () => {};

  editor._command("remove", 0);
  assert.deepEqual(editor.actions, [target]);
  editor._command("cancel", 0);
  assert.deepEqual(editor.actions, [mode, target]);
});

test("mode mappings render as behavior sections and save as separate records", () => {
  const editor = new Editor();
  editor.valueType = { datatype: "number", unit: "W" };
  editor.strings = {
    default_behavior: "Default behavior", behavior_target: "When targeting power",
    behavior_release: "When fluks releases control", no_actions: "No actions configured",
    add_action: "Add action", add_behavior: "Add behavior", behavior: "Behavior",
  };
  editor.allowedModes = [null, "target", "release"];
  editor.behaviors = [
    { mode: null, actions: [target] },
    { mode: "release", actions: [mode] },
  ];
  const html = editor.shadowRoot.innerHTML;
  assert.match(html, /Default behavior/);
  assert.match(html, /When fluks releases control/);
  assert.match(html, /Add behavior/);
  assert.doesNotMatch(html, /mode: null/);

  editor.render = () => {};
  editor._workingBehaviors.push({ mode: "target", actions: [] });
  editor._command("save", 0);
  assert.deepEqual(editor.lastEvent.detail.behaviors, [
    { mode: null, configuration: { version: 1, actions: [target] } },
    { mode: "release", configuration: { version: 1, actions: [mode] } },
  ]);
});

test("each behavior keeps its own deterministic action order", () => {
  const editor = new Editor();
  editor.behaviors = [
    { mode: null, actions: [mode, target] },
    { mode: "target", actions: [target, mode] },
  ];
  editor.render = () => {};
  editor._command("up", 1, "target");
  assert.deepEqual(editor.behaviors, [
    { mode: null, actions: [mode, target] },
    { mode: "target", actions: [mode, target] },
  ]);
});

test("empty Power behaviors stay compact and catalog modes open as intent choices", () => {
  const editor = new Editor();
  editor.controlName = "Power";
  editor.allowedModes = [null, "target", "limit", "release"];
  editor.behaviors = [];
  assert.match(editor.shadowRoot.innerHTML, /empty-behavior/);
  assert.doesNotMatch(editor.shadowRoot.innerHTML, /<div class="empty">/);
  assert.doesNotMatch(editor.shadowRoot.innerHTML, /<select id="behavior-mode"/);

  editor._addingBehavior = true;
  editor.render();
  const dialog = editor.shadowRoot.innerHTML;
  assert.match(dialog, /data-behavior-choice="target"/);
  assert.match(dialog, /data-behavior-choice="limit"/);
  assert.match(dialog, /data-behavior-choice="release"/);
  assert.doesNotMatch(dialog, /data-behavior-choice="balance"/);
});

test("backend-provided Mapping execution modes include charge and discharge", () => {
  const editor = new Editor();
  editor.controlName = "Power";
  editor.allowedModes = [null, "target", "charge", "discharge"];
  editor.behaviors = [];
  editor._addingBehavior = true;
  editor.render();
  assert.match(editor.shadowRoot.innerHTML, /data-behavior-choice="charge"/);
  assert.match(editor.shadowRoot.innerHTML, /data-behavior-choice="discharge"/);
});
