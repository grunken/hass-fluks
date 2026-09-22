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
  editor._transformTypeDraft = { field: "value", type: "powerToCurrent" };
  const html = editor._transformEditor("value", []);
  for (const transform of ["powerToCurrent", "difference", "round", "clamp", "nearest", "valueMap", "invert", "scale", "offset"]) {
    assert.match(html, new RegExp(`value="${transform}"`));
  }
  assert.doesNotMatch(html, /textarea|JSON|YAML/);
});

test("entity search includes actions and fields that the entity can provide", () => {
  const editor = new Editor();
  const capability = {
    service: "climate.set_temperature", name: "Set temperature", description: "Set a target temperature",
    entities: [{ entity_id: "climate.zone_1", name: "Zone 1", fields: [{ id: "temperature", name: "Temperature", description: "Target value", selector: { type: "number" } }] }],
  };
  editor.capabilities = [capability];
  const html = editor._dialog();
  assert.match(html, /data-search="[^"]*climate\.set_temperature[^"]*Set temperature[^"]*temperature/i);
  const node = { dataset: { search: editor._entitySearchText(capability.entities[0]) }, textContent: "Zone 1", hidden: false };
  editor.shadowRoot.querySelectorAll = () => [node];
  editor._filterChoices("entity", "temperature");
  assert.equal(node.hidden, false);
  editor._filterChoices("entity", "missing capability");
  assert.equal(node.hidden, true);
});

test("selected Home Assistant device entities rank before global fallback entities", () => {
  const editor = new Editor();
  editor.selectedDeviceId = "device-naervarme";
  editor.entityDeviceIds = { "climate.zone_1": "device-naervarme", "climate.goodwe": "device-goodwe" };
  editor.capabilities = [{
    service: "climate.set_temperature", name: "Set temperature", entities: [
      { entity_id: "climate.goodwe", name: "GoodWe temperature", fields: [] },
      { entity_id: "climate.zone_1", name: "Nærvarme Zone 1", fields: [] },
    ],
  }];
  assert.deepEqual(editor._allEntities().map((entity) => entity.entity_id), ["climate.zone_1", "climate.goodwe"]);
});

test("entity list keeps executable actions without value fields and ignores non-action metadata", () => {
  const editor = new Editor();
  editor.capabilities = [
    { service: "", name: "Observation only", entities: [{ entity_id: "sensor.read_only", name: "Read only", fields: [] }] },
    { service: "switch.turn_on", name: "Turn on", entities: [{ entity_id: "switch.enable", name: "Enable", fields: [] }] },
  ];
  assert.deepEqual(editor._allEntities().map((entity) => entity.entity_id), ["switch.enable"]);
  assert.deepEqual(editor._capsForEntity("switch.enable").map((capability) => capability.service), ["switch.turn_on"]);
});

test("action editor selects retain full width with chevron spacing", () => {
  const editor = new Editor();
  assert.match(editor._styles(), /select\{[^}]*width:100%[^}]*padding:10px 1\.5rem 10px 10px/);
});

test("new numeric action inputs default to Control value and commit that binding", () => {
  const editor = new Editor();
  editor.capabilities = [{
    service: "climate.set_temperature", name: "Set temperature", entities: [{
      entity_id: "climate.zone_1", name: "Zone 1", fields: [{ id: "temperature", name: "Temperature", required: false, selector: { type: "number" } }],
    }],
  }];
  editor._editing = null;
  editor._dialogDraft = { type: "serviceCall", service: "climate.set_temperature", target: { entityId: "climate.zone_1" }, data: {} };
  const html = editor._dialog();
  assert.match(html, /<option value="requestedValue" selected>control value<\/option>/);
  editor.shadowRoot.querySelector = (selector) => selector === "#action-error" ? { textContent: "" } : null;
  editor._commitAction();
  assert.equal(editor.actions[0].data.temperature.kind, "requestedValue");
});

test("value adjustments start collapsed and are added only after choosing Add adjustment", () => {
  const editor = new Editor();
  editor.valueType = { datatype: "number", unit: "W" };
  const initial = editor._transformEditor("value", []);
  assert.match(initial, /<details class="transforms"/);
  assert.doesNotMatch(initial, /<select data-transform-type/);
  assert.match(initial, /no transforms/);
  editor._dialogDraft = { data: { value: { kind: "requestedValue" } } };
  editor._refreshDialog = () => {};
  editor._transformCommand({ dataset: { fieldId: "value", transformCommand: "start" } });
  const adding = editor._transformEditor("value", []);
  assert.match(adding, /data-transform-type/);
  assert.match(adding, /value="powerToCurrent"/);
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

test("Battery Power exposes local signed execution modes including hold", () => {
  const editor = new Editor();
  editor.controlName = "Power";
  editor.allowedModes = [null, "charge", "discharge", "hold", "release"];
  editor.behaviors = [];
  editor._addingBehavior = true;
  editor.render();
  assert.match(editor.shadowRoot.innerHTML, /data-behavior-choice="charge"/);
  assert.match(editor.shadowRoot.innerHTML, /data-behavior-choice="discharge"/);
  assert.match(editor.shadowRoot.innerHTML, /data-behavior-choice="hold"/);
});

test("output Mapping suggestions render and require explicit acceptance", () => {
  const editor = new Editor();
  editor.controlName = "Power";
  editor.allowedModes = [null, "hold"];
  editor.behaviors = [];
  editor.strings = { suggested_match: "Suggested match", use_suggestion: "Use", loading_mapping_suggestions: "Loading" };
  const suggestion = {
    mode: "hold",
    configuration: { version: 1, actions: [mode] },
    confidence: 0.91,
    explanation: "Use the idle action.",
  };
  editor.outputSuggestions = { hold: { ...suggestion, behavior: "hold", label: "Holds" } };
  assert.match(editor.shadowRoot.innerHTML, /Holds/);
  assert.match(editor.shadowRoot.innerHTML, /Use/);
  assert.deepEqual(editor.behaviors, [{ mode: null, actions: [] }]);
  editor.render = () => {};
  editor._command("use-output-suggestion", 0);
  assert.deepEqual(editor.behaviors.find((item) => item.mode === "hold").actions, [mode]);
});

test("control editor explains configured action order and manual fallback", () => {
  const editor = new Editor();
  editor.controlName = "Power";
  editor.capabilities = capabilities;
  editor.strings = {
    default_behavior: "Default behavior", default_behavior_description: "Use these actions for the requested power.",
    action_sequence_help: "These actions run from top to bottom.", uses_control_value: "Uses the requested value",
    uses_fixed_value: "Uses a fixed value", target_only: "Runs without a value", no_actions: "No actions configured.",
    manual_behavior_help: "Add the actions for this behavior.", manual_action_title: "Configure the actions",
    manual_action_description: "No suggestion is available. Add the actions manually.",
    add_action: "Add action", suggested_match: "Suggested match", use_suggestion: "Use",
  };
  editor.allowedModes = [null];
  editor.behaviors = [{ mode: null, actions: [mode, target] }];
  assert.match(editor.shadowRoot.innerHTML, /These actions run from top to bottom/);
  assert.match(editor.shadowRoot.innerHTML, /Select option · GoodWe mode/);
  assert.match(editor.shadowRoot.innerHTML, /Set value · GoodWe target/);

  editor.behaviors = [];
  assert.match(editor.shadowRoot.innerHTML, /Configure the actions/);
  assert.match(editor.shadowRoot.innerHTML, /No suggestion is available/);
});

test("control editor presents a friendly loading state for suggestions", () => {
  const editor = new Editor();
  editor.strings = {
    finding_best_match: "Finding the best match…",
    finding_best_match_description: "fluks is examining the selected device's available controls.",
  };
  editor.behaviors = [];
  editor.outputSuggestionsLoading = true;
  assert.match(editor.shadowRoot.innerHTML, /loading-spinner/);
  assert.match(editor.shadowRoot.innerHTML, /Finding the best match/);
  assert.match(editor.shadowRoot.innerHTML, /examining the selected device/);
  assert.match(editor.shadowRoot.innerHTML, /role="status"/);
});

test("control editor explains a suggested action sequence and its reason", () => {
  const editor = new Editor();
  editor.controlName = "Tank temperature";
  editor.capabilities = capabilities;
  editor.strings = {
    suggested_match: "Suggested match", suggested_action_intro: "Review this sequence before using it.",
    suggested_action_reason: "Best match", use_suggestion: "Use", uses_fixed_value: "Uses a fixed value",
    uses_control_value: "Uses the requested value", target_only: "Runs without a value",
    default_behavior: "Default behavior", default_behavior_description: "Set the requested temperature.",
    action_sequence_help: "These actions run from top to bottom.", add_action: "Add action",
  };
  editor.allowedModes = [null];
  editor.behaviors = [];
  editor.outputSuggestions = {
    default: {
      mode: null,
      label: "Targets tank temperature",
      explanation: "The selected tank action accepts the requested value.",
      configuration: { version: 1, actions: [target] },
    },
  };
  assert.match(editor.shadowRoot.innerHTML, /Targets tank temperature/);
  assert.match(editor.shadowRoot.innerHTML, /Set value · GoodWe target/);
  assert.match(editor.shadowRoot.innerHTML, /selected tank action accepts/);
  assert.match(editor.shadowRoot.innerHTML, /Use/);
});

test("numeric power behavior editor round-trips signed value conditions", () => {
  const editor = new Editor();
  editor.valueType = { datatype: "number", unit: "W" };
  editor.behaviorChoices = [
    { mode: "balance", valueCondition: "gtZero", labelKey: "behavior_balance_import", descriptionKey: "behavior_balance_import_description" },
    { mode: "balance", valueCondition: "ltZero", labelKey: "behavior_balance_export", descriptionKey: "behavior_balance_export_description" },
    { mode: "balance", valueCondition: "eqZero", labelKey: "behavior_balance_zero", descriptionKey: "behavior_balance_zero_description" },
  ];
  editor.allowedModes = [null, "balance", "release"];
  editor.strings = {
    behavior_balance_import: "Balances power when importing",
    behavior_balance_import_description: "Balance toward requested import power.",
    behavior_balance_export: "Balances power when exporting",
    behavior_balance_export_description: "Balance toward requested export power.",
    behavior_balance_zero: "Balances power at zero exchange",
    behavior_balance_zero_description: "Balance toward zero grid exchange.",
  };
  editor.behaviors = [
    { mode: "balance", valueCondition: "gtZero", actions: [target] },
    { mode: "balance", valueCondition: "ltZero", actions: [mode] },
  ];
  assert.match(editor.shadowRoot.innerHTML, /Balances power when importing/);
  assert.match(editor.shadowRoot.innerHTML, /Balances power when exporting/);
  assert.doesNotMatch(editor.shadowRoot.innerHTML, /data-value-condition-index/);
  editor.render = () => {};
  editor._command("save", 0);
  assert.deepEqual(editor.lastEvent.detail.behaviors.map((item) => item.valueCondition), ["gtZero", "ltZero"]);
});

test("Site power offers semantic balance choices without exposing valueCondition", () => {
  const editor = new Editor();
  editor.allowedModes = [null, "target", "limit", "balance", "release"];
  editor.behaviorChoices = [
    { mode: "balance", valueCondition: "gtZero", labelKey: "import", descriptionKey: "import_description" },
    { mode: "balance", valueCondition: "ltZero", labelKey: "export", descriptionKey: "export_description" },
    { mode: "balance", valueCondition: "eqZero", labelKey: "zero", descriptionKey: "zero_description" },
  ];
  editor.strings = {
    import: "Balances power when importing", import_description: "Import description",
    export: "Balances power when exporting", export_description: "Export description",
    zero: "Balances power at zero exchange", zero_description: "Zero description",
  };
  editor.behaviors = [];
  editor._addingBehavior = true;
  editor.render();
  const dialog = editor.shadowRoot.innerHTML;
  assert.match(dialog, /Balances power when importing/);
  assert.match(dialog, /Balances power when exporting/);
  assert.match(dialog, /Balances power at zero exchange/);
  assert.match(dialog, /data-value-condition="gtZero"/);
  assert.match(dialog, /data-value-condition="ltZero"/);
  assert.match(dialog, /data-value-condition="eqZero"/);
  assert.doesNotMatch(dialog, /Balances power<\/span>/);
  assert.doesNotMatch(dialog, /value condition/i);

  editor._command("select-behavior", 0, "balance", null, "gtZero");
  editor._command("select-behavior", 0, "balance", null, "ltZero");
  editor._command("select-behavior", 0, "balance", null, "eqZero");
  assert.deepEqual(editor.behaviors.filter((item) => item.mode === "balance").map((item) => item.valueCondition), ["gtZero", "ltZero", "eqZero"]);
  assert.deepEqual(editor._availableBehaviorChoices().filter((choice) => choice.mode === "balance"), []);
});

test("legacy unconditional Site balance remains editable without a new null choice", () => {
  const editor = new Editor();
  editor.allowedModes = [null, "balance"];
  editor.behaviorChoices = [
    { mode: "balance", valueCondition: "gtZero", labelKey: "import", descriptionKey: "import_description" },
    { mode: "balance", valueCondition: "ltZero", labelKey: "export", descriptionKey: "export_description" },
    { mode: "balance", valueCondition: "eqZero", labelKey: "zero", descriptionKey: "zero_description" },
  ];
  editor.strings = { behavior_balance: "Balances power", behavior_balance_description: "Legacy balance" };
  editor.behaviors = [{ mode: "balance", valueCondition: null, actions: [mode] }];
  assert.match(editor.shadowRoot.innerHTML, /Balances power/);
  assert.doesNotMatch(editor.shadowRoot.innerHTML, /value condition/i);
  assert.deepEqual(editor._availableBehaviorChoices().map((choice) => choice.valueCondition), ["gtZero", "ltZero", "eqZero"]);
});
