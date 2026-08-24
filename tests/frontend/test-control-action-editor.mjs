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
  define: (name, element) => elements.set(name, element),
  get: (name) => elements.get(name),
};

await import("../../custom_components/fluks/frontend/control-action-editor.js");
const Editor = customElements.get("fluks-control-action-editor");

const mode = {
  type: "selectOption",
  entityId: "select.goodwe_operation_mode",
  option: { source: "fixed", value: "eco_charge" },
};
const target = {
  type: "setNumber",
  entityId: "number.goodwe_charge_target",
  value: { source: "control" },
};

test("production editor preserves ordered explicit Save and lossless Cancel", () => {
  const editor = new Editor();
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
  assert.deepEqual(editor.lastEvent.detail.actions, [target, mode]);
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
