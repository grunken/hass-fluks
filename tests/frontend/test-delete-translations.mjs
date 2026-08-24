import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const elements = new Map();
globalThis.HTMLElement = class {
  attachShadow() {
    this.shadowRoot = { querySelector: () => null };
    return this.shadowRoot;
  }
};
globalThis.customElements = {
  define: (name, element) => elements.set(name, element),
  get: (name) => elements.get(name),
};
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
globalThis.history = { back: () => {} };

await import("../../custom_components/fluks/frontend/control-editor-panel.js");
const Panel = customElements.get("fluks-control-editor-panel");

const translations = async (locale) => {
  const parsed = JSON.parse(await readFile(
    new URL(`../../custom_components/fluks/translations/${locale}.json`, import.meta.url),
    "utf8",
  ));
  return parsed.panel;
};

const panelFor = (strings) => {
  const panel = new Panel();
  panel._hass = { language: "en", localize: () => undefined };
  panel._context = { site: { name: "Home" }, translations: strings };
  panel.shadowRoot.querySelector = (selector) => selector === "#cancel" ? {} : null;
  panel._frame = (title, body) => { panel.rendered = { title, body }; };
  return panel;
};

test("production render path resolves English delete copy and placeholders", async () => {
  const panel = panelFor(await translations("en"));
  panel._detail = { id: "internal-id", label: "Battery · Inverter Goodwe #1" };

  panel._view = { name: "delete-device", stage: "confirm" };
  panel._renderDeleteDevice();
  assert.match(panel.rendered.body, /Delete Battery · Inverter Goodwe #1\?/);
  assert.match(panel.rendered.body, /This will permanently delete this device from fluks\. This cannot be undone\./);
  assert.doesNotMatch(panel.rendered.body, /delete device (title|body)/i);

  panel._view = { name: "delete-site", stage: "confirm" };
  panel._renderDeleteSite();
  assert.match(panel.rendered.body, /Delete Home\?/);
  assert.match(panel.rendered.body, /including all devices connected to it/);
  assert.doesNotMatch(panel.rendered.body, /delete site (title|body)/i);

  panel._view = { name: "delete-site", stage: "login" };
  panel._renderDeleteSite();
  assert.match(panel.rendered.body, /Confirm identity/);
  assert.match(panel.rendered.body, /fluks account that owns this site/);
});

test("production render path resolves Danish resource names", async () => {
  const panel = panelFor(await translations("da"));
  panel._detail = { id: "internal-id", label: "Solceller · Inverter Goodwe #1" };

  panel._view = { name: "delete-device", stage: "confirm" };
  panel._renderDeleteDevice();
  assert.match(panel.rendered.body, /Slet Solceller · Inverter Goodwe #1\?/);
  assert.match(panel.rendered.body, /Dette sletter enheden permanent fra fluks/);

  panel._view = { name: "delete-device", stage: "login" };
  panel._renderDeleteDevice();
  assert.match(panel.rendered.body, /Bekræft din identitet/);
  assert.match(panel.rendered.body, /fluks-konto, der ejer enhedens site/);
});
