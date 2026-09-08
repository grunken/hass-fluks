/* Embedded fluks administration panel. Backend credentials remain in Python. */
const MODULE_REVISION = new URL(import.meta.url).searchParams.get("rev");
const ELEMENT_REVISION = (MODULE_REVISION || "unversioned").toLowerCase().replace(/[^a-z0-9-]/g, "-");
const PANEL_TAG = `fluks-control-editor-panel-${ELEMENT_REVISION}`;
const CONTROL_ACTION_EDITOR_TAG = `fluks-control-action-editor-${ELEMENT_REVISION}`;
let controlActionEditorModule;
const loadControlActionEditor = () => controlActionEditorModule ??= import(`./control-action-editor.js${MODULE_REVISION ? `?rev=${encodeURIComponent(MODULE_REVISION)}` : ""}`);

const TAGLINE = "Your Energy. Decides together.";
const DEVICE_ICON_BASE = "/fluks-device-icons";
const clone = (value) => JSON.parse(JSON.stringify(value));
const esc = (value) => String(value ?? "").replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");

class FluksControlEditorPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._view = { name: "home" };
    this._pendingControl = undefined;
    this._spaceHeaterValidationError = false;
    this._clearedInputConcepts = new Set();
    this._popstate = () => {
      this._view = history.state?.fluksView ?? { name: "home" };
      this._detail = this._draft = undefined;
      this._loadView();
    };
  }

  set hass(value) {
    this._hass = value;
    if (this.isConnected && this._entryId && !this._context) this._loadContext();
  }
  set route(value) {
    this._route = value;
    const entryId = new URLSearchParams(location.search).get("config_entry") ?? undefined;
    if (entryId !== this._entryId) {
      this._entryId = entryId;
      this._context = this._detail = this._draft = undefined;
      this._pendingControl = undefined;
      this._spaceHeaterValidationError = false;
      this._clearedInputConcepts.clear();
      this._view = { name: "home" };
    }
    if (this.isConnected) this._loadContext();
  }
  set panel(value) { this._panel = value; }
  set narrow(value) { this._narrow = value; }
  connectedCallback() { addEventListener("popstate", this._popstate); this._loadContext(); }
  disconnectedCallback() { removeEventListener("popstate", this._popstate); }

  _t(key, replacements = {}) {
    let translated = this._context?.translations?.[key];
    if (!translated) {
      const args = Object.entries(replacements).flat();
      translated = this._hass?.localize(`component.fluks.panel.${key}`, ...args);
    }
    if (!translated) return key.replaceAll("_", " ");
    for (const [name, value] of Object.entries(replacements)) {
      translated = translated.replaceAll(`{${name}}`, String(value));
    }
    return translated;
  }
  async _call(type, data = {}) {
    try {
      this._error = undefined;
      return await this._hass.callWS({ type, entry_id: this._entryId, ...data });
    } catch (err) {
      this._error = this._t(`error_${err?.code || "unknown"}`);
      throw err;
    }
  }
  async _loadContext() {
    if (!this.shadowRoot || !this._hass) return;
    if (!this._entryId) return this._message(this._t("context_missing"));
    this._message(this._t("loading"));
    try {
      this._context = await this._call("fluks/config/context");
      await this._loadView();
    } catch (_) { this._message(this._error); }
  }
  async _ensureControlActionEditor() {
    const { FluksControlActionEditor } = await loadControlActionEditor();
    if (!customElements.get(CONTROL_ACTION_EDITOR_TAG)) customElements.define(CONTROL_ACTION_EDITOR_TAG, FluksControlActionEditor);
  }
  _go(view, replace = false) {
    this._view = view; this._detail = this._draft = undefined;
    const state = { ...(history.state || {}), fluksView: view };
    (replace ? history.replaceState : history.pushState).call(history, state, "");
    this._loadView();
  }
  async _loadView() {
    if (!this._context) return;
    if (["device", "edit", "controls", "control", "site-information", "delete-device"].includes(this._view.name)) {
      try { this._detail = await this._call("fluks/config/device", { device_id: this._view.deviceId }); }
      catch (_) { return this._message(this._error); }
    }
    if (this._view.name === "control") {
      try {
        await this._ensureControlActionEditor();
        this._controlCapabilities = (await this._call("fluks/config/control_capabilities")).actions;
      }
      catch (_) { return this._message(this._error); }
    }
    this._render();
  }
  _render() {
    return ({
      device: () => this._renderDevice(), edit: () => this._renderEdit(),
      add: () => this._renderAdd(), controls: () => this._renderControls(),
      control: () => this._renderControl(), "delete-device": () => this._renderDeleteDevice(),
      "site-information": () => this._renderSiteInformation(),
      "delete-site": () => this._renderDeleteSite(),
    }[this._view.name] || (() => this._renderHome()))();
  }
  _frame(title, body, back = false) {
    this.shadowRoot.innerHTML = `<style>${this._styles()}</style><main><header>
      ${back ? `<button class="icon" id="back" aria-label="${esc(this._t("back"))}">←</button>` : ""}
      <div><h1>${esc(title)}</h1>${back ? "" : `<p>${TAGLINE}</p>`}</div></header>
      ${this._error ? `<div class="error" role="alert">${esc(this._error)}</div>` : ""}${body}</main>`;
    this.shadowRoot.querySelector("#back")?.addEventListener("click", () => history.back());
  }
  _message(message) { this._frame("fluks", `<section class="card"><p>${esc(message)}</p></section>`); }

  _iconPath(type) {
    const filename = type.replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase();
    return `${DEVICE_ICON_BASE}/${filename}.png`;
  }
  _typeIcon(type, size = "list") {
    return `<img class="device-icon ${size}" src="${esc(this._iconPath(type))}" alt="">`;
  }
  _metadata(properties = {}) {
    return [properties.vendor, properties.model].filter(Boolean).join(" · ");
  }

  _renderHome() {
    const devices = this._context.devices.map((d) => `<button class="row device-row" data-device="${esc(d.id)}">
      ${this._typeIcon(d.type)}<span class="row-copy"><strong>${esc(d.label)}</strong><span>${esc(d.metadata || d.name || d.type_name)}</span></span><span class="chevron">›</span></button>`).join("");
    this._frame("fluks", `<section><div class="section-title"><h2>${esc(this._t("devices"))}</h2>
      <button class="primary" id="add">＋ ${esc(this._t("add_device"))}</button></div>
      <div class="card list">${devices || `<p>${esc(this._t("no_devices"))}</p>`}</div></section>
      <section><h2>${esc(this._t("site"))}</h2><div class="card site-row"><button class="site-link" id="site-detail">${this._typeIcon("site")}
      <span class="row-copy"><strong>${esc(this._context.site.name)}</strong></span><span class="chevron">›</span></button><button class="icon overflow" id="site-menu" aria-label="${esc(this._t("site_actions"))}" aria-haspopup="menu">⋮</button>
      <div class="context-menu" id="site-actions" role="menu" hidden><button class="menu-danger" id="delete-site" role="menuitem">${esc(this._t("delete_site"))}</button></div></div></section>`);
    this.shadowRoot.querySelector("#add").onclick = () => this._go({ name: "add" });
    this._wireMenu("site-menu", "site-actions");
    this.shadowRoot.querySelector("#delete-site").onclick = () => this._go({ name: "delete-site", stage: "confirm" });
    this.shadowRoot.querySelector("#site-detail").onclick = () => this._go({ name: "device", deviceId: this._context.site.id });
    this.shadowRoot.querySelectorAll("[data-device]").forEach((n) => n.onclick = () => this._go({ name: "device", deviceId: n.dataset.device }));
  }
  _renderDevice() {
    const site = this._detail.type === "site";
    const configured = Object.values(this._detail.mappings);
    const measurementCount = configured.filter((m) => this._detail.concepts.find((c) => c.concept === m.concept)?.cadence !== "interval").length;
    const energyCount = configured.filter((m) => this._detail.concepts.find((c) => c.concept === m.concept)?.cadence === "interval").length;
    const metadata = this._metadata(this._detail.properties);
    this._frame("", `<div class="device-heading">${this._typeIcon(this._detail.type, "hero")}<div>
      <h1>${esc(this._detail.type_name)}</h1><p class="device-name">${esc(this._detail.name || this._detail.type_name)}</p>${metadata ? `<p>${esc(metadata)}</p>` : ""}</div>
      <button class="icon overflow" id="device-menu" aria-label="${esc(this._t("device_actions"))}" aria-haspopup="menu">⋮</button>
      <div class="context-menu device-menu" id="device-actions-menu" role="menu" hidden><button class="menu-danger" id="delete" role="menuitem">${esc(this._t(site ? "delete_site" : "delete_device"))}</button></div></div>
      <div class="card list overview-list">
      <button class="row" id="edit"><ha-icon icon="mdi:chart-line"></ha-icon><span class="row-copy"><strong>${esc(this._t("measurements_energy"))}</strong><span>${measurementCount} ${esc(this._t("measurements_count"))} · ${energyCount} ${esc(this._t("energy_count"))}</span></span><span class="chevron">›</span></button>
      <button class="row" id="controls"><ha-icon icon="mdi:tune-variant"></ha-icon><span class="row-copy"><strong>${esc(this._t("controls"))}</strong><span>${this._detail.controls.length} ${esc(this._t("available"))}</span></span><span class="chevron">›</span></button>
      <button class="row" id="information"><ha-icon icon="mdi:information-outline"></ha-icon><span class="row-copy"><strong>${esc(this._t(site ? "site_information" : "device_information"))}</strong><span>${esc(site ? this._detail.name : metadata || this._t("optional"))}</span></span><span class="chevron">›</span></button></div>`, true);
    this._wireMenu("device-menu", "device-actions-menu");
    this.shadowRoot.querySelector("#edit").onclick = () => this._go({ name: "edit", deviceId: this._detail.id });
    this.shadowRoot.querySelector("#information").onclick = () => this._go({ name: site ? "site-information" : "edit", deviceId: this._detail.id });
    this.shadowRoot.querySelector("#controls").onclick = () => this._go({ name: "controls", deviceId: this._detail.id });
    this.shadowRoot.querySelector("#delete").onclick = () => this._go(site ? { name: "delete-site", stage: "confirm" } : { name: "delete-device", deviceId: this._detail.id, stage: "confirm" });
  }
  _renderSiteInformation() {
    this._frame(this._t("site_information"), `<section class="card"><h2>${esc(this._t("name"))}</h2><p>${esc(this._detail.name)}</p></section>`, true);
  }
  _wireMenu(buttonId, menuId) {
    const button = this.shadowRoot.querySelector(`#${buttonId}`);
    const menu = this.shadowRoot.querySelector(`#${menuId}`);
    button.onclick = (event) => { event.stopPropagation(); menu.hidden = !menu.hidden; if (!menu.hidden) menu.querySelector("button")?.focus(); };
    menu.onkeydown = (event) => { if (event.key === "Escape") { menu.hidden = true; button.focus(); } };
  }
  _conceptLabel(concept) {
    if (concept.label) return concept.label;
    const text = concept.concept.split(".").at(-1).replace(/([A-Z])/g, " $1");
    return text.charAt(0).toUpperCase() + text.slice(1);
  }
  _entityRecord(entityId) {
    const registry = this._context.entities.find((item) => item.entity_id === entityId);
    const state = this._hass.states[entityId];
    return registry || state ? {
      id: entityId,
      name: registry?.name || state?.attributes.friendly_name || entityId,
      secondary: entityId,
      trailing: this._formatState(state),
      deviceId: registry?.device_id,
      icon: state?.attributes.icon,
      deviceClass: registry?.device_class || state?.attributes.device_class,
    } : undefined;
  }
  _formatState(state) {
    const raw = state?.state;
    if (raw === undefined || raw === null || raw === "") return "";
    const numeric = Number(raw);
    const value = Number.isFinite(numeric)
      ? new Intl.NumberFormat(this._hass.language, { maximumFractionDigits: 3 }).format(numeric)
      : String(raw);
    return [value, state?.attributes.unit_of_measurement].filter(Boolean).join(" ");
  }
  _allEntities() {
    const registryIds = new Set(this._context.entities.map((item) => item.entity_id));
    return [
      ...this._context.entities.map((item) => this._entityRecord(item.entity_id)),
      ...Object.keys(this._hass.states).filter((entityId) => !registryIds.has(entityId)).map((entityId) => this._entityRecord(entityId)),
    ].filter(Boolean);
  }
  _temperatureUnit(state, attribute) {
    const units = new Set(["°C", "°F", "K"]);
    for (const key of [`${attribute}_unit`, "temperature_unit", "unit_of_measurement"]) {
      const unit = state?.attributes?.[key];
      if (units.has(unit)) return unit;
    }
    const domain = state?.entity_id?.split(".", 1)[0];
    if (["climate", "water_heater"].includes(domain) || state?.attributes?.device_class === "temperature") {
      const unit = this._hass.config?.unit_system?.temperature;
      return units.has(unit) ? unit : "";
    }
    return "";
  }
  _isTemperatureAttribute(state, attribute, value) {
    if (!Number.isFinite(Number(value))) return false;
    const explicitUnit = state?.attributes?.[`${attribute}_unit`];
    if (["°C", "°F", "K"].includes(explicitUnit)) return true;
    return /temperature|(^|_)temp($|_)/i.test(attribute);
  }
  _isTemperatureState(item, state) {
    return ["°C", "°F", "K"].includes(state?.attributes?.unit_of_measurement)
      || item.deviceClass === "temperature";
  }
  _entitySources(item, canonicalTemperature = false) {
    const state = this._hass.states[item.id];
    const sources = canonicalTemperature && !this._isTemperatureState(item, state)
      ? []
      : [{ ...item, attribute: "", secondary: `${this._t("entity_state")} · ${item.id}`, trailing: this._formatState(state) }];
    for (const [attribute, value] of Object.entries(state?.attributes ?? {})) {
      if (canonicalTemperature && !this._isTemperatureAttribute(state, attribute, value)) continue;
      const formatted = this._formatSourceValue(value);
      const unit = canonicalTemperature ? this._temperatureUnit({ ...state, entity_id: item.id }, attribute) : "";
      const trailing = canonicalTemperature ? this._formatTemperatureSource(value, unit) : formatted;
      if (trailing) sources.push({ ...item, attribute, unit, secondary: `${attribute} · ${item.id}`, trailing });
    }
    return sources;
  }
  _pickerValue(kind, id, key, attribute = "") {
    const item = kind === "device"
      ? this._context.ha_devices.find((device) => device.id === id)
      : this._entityRecord(id);
    const name = item?.name || this._t(kind === "device" ? "choose_ha_device_button" : "choose_entity");
    const secondary = kind === "device"
      ? [item?.manufacturer, item?.model].filter(Boolean).join(" · ")
      : item ? `${attribute || this._t("entity_state")} · ${item.secondary}` : undefined;
    const sourceValue = item && attribute
      ? this._formatTemperatureSource(this._hass.states[id]?.attributes?.[attribute], this._temperatureUnit({ ...this._hass.states[id], entity_id: id }, attribute))
      : item?.trailing;
    return `<button type="button" class="picker-value" data-picker-kind="${kind}" data-picker-key="${esc(key)}" data-picker-value="${esc(id || "")}" data-picker-attribute="${esc(attribute)}">
      <span class="source-icon"><ha-icon icon="${esc(item?.icon || (kind === "device" ? "mdi:devices" : "mdi:chart-bell-curve-cumulative"))}"></ha-icon></span>
      <span class="row-copy"><strong title="${esc(name)}">${esc(name)}</strong><span title="${esc(secondary || this._t("optional"))}">${esc(secondary || this._t("optional"))}</span></span>${sourceValue ? `<span class="trailing" title="${esc(sourceValue)}">${esc(sourceValue)}</span>` : ""}<span class="chevron">⌄</span></button>`;
  }
  _formatSourceValue(value) {
    if (value === undefined || value === null) return "";
    const numeric = Number(value);
    return Number.isFinite(numeric)
      ? new Intl.NumberFormat(this._hass.language, { maximumFractionDigits: 3 }).format(numeric)
      : typeof value === "string" || typeof value === "boolean" ? String(value) : "";
  }
  _formatTemperatureSource(value, unit) {
    const formatted = this._formatSourceValue(value);
    return formatted && unit && Number.isFinite(Number(value))
      ? `${formatted} ${unit}`
      : formatted;
  }
  _inputConversions(concept, configuration) {
    const transforms = configuration.transforms ?? [];
    const rows = transforms.map((transform, index) => {
      const detail = transform.type === "scale" ? ` × ${transform.factor}` : transform.type === "offset" ? ` ${transform.amount}` : transform.type === "valueMap" ? ` · ${Object.keys(transform.values ?? {}).length}` : "";
      return `<li><span class="order">${index + 1}</span><span class="conversion-copy"><strong>${esc(this._t(transform.type === "valueMap" ? "value_map" : transform.type))}</strong><small>${esc(detail.trim())}</small></span><span class="row-actions"><button type="button" data-input-transform="up" data-input-concept="${esc(concept)}" data-index="${index}" ${index === 0 ? "disabled" : ""}>↑ ${esc(this._t("up"))}</button><button type="button" data-input-transform="down" data-input-concept="${esc(concept)}" data-index="${index}" ${index === transforms.length - 1 ? "disabled" : ""}>↓ ${esc(this._t("down"))}</button>${transform.type === "valueMap" ? "" : `<button type="button" data-input-transform="edit" data-input-concept="${esc(concept)}" data-index="${index}">${esc(this._t("edit"))}</button>`}<button type="button" data-input-transform="remove" data-input-concept="${esc(concept)}" data-index="${index}">${esc(this._t("remove"))}</button></span></li>`;
    }).join("");
    return `<div class="input-conversions"><strong>${esc(this._t("conversions"))}</strong>${rows ? `<ol>${rows}</ol>` : `<small>${esc(this._t("no_transforms"))}</small>`}<button type="button" class="add-conversion" data-input-transform="add" data-input-concept="${esc(concept)}">＋ ${esc(this._t("add_conversion"))}</button></div>`;
  }
  _mappingFields(detail, editableConversions = false) {
    const groups = { measurements: [], energy: [] };
    for (const concept of detail.concepts) {
      const configuration = editableConversions
        ? this._inputDraft[concept.concept]
        : detail.mappings[concept.concept]?.configuration ?? {};
      const selected = editableConversions
        ? configuration?.entityId ?? ""
        : detail.mappings[concept.concept]?.configuration?.entityId || detail.suggestions[concept.concept] || "";
      groups[concept.cadence === "interval" ? "energy" : "measurements"].push(`<div class="mapping-field"><label>${esc(this._conceptLabel(concept))}${this._pickerValue("entity", selected, concept.concept, configuration.attribute)}<input type="hidden" data-concept="${esc(concept.concept)}" value="${esc(selected)}"></label>${editableConversions && selected ? this._inputConversions(concept.concept, configuration) : ""}</div>`);
    }
    return Object.entries(groups).filter(([, f]) => f.length).map(([name, fields]) => `<section class="card"><h2>${esc(this._t(name))}</h2><div class="fields">${fields.join("")}</div></section>`).join("");
  }
  _editableDeviceProperties(properties, deviceType) {
    const keys = ["displayName", "vendor", "model", ...(deviceType === "solar" ? ["installedKWp", "azimuthDegrees", "tiltDegrees"] : []), ...(deviceType === "spaceHeater" ? ["ratedPowerW"] : []), ...(deviceType === "battery" ? ["capacityKwh", "battery.socMinimum", "battery.socMaximum"] : [])];
    return Object.fromEntries(keys.filter((key) => Object.hasOwn(properties, key)).map((key) => [key, properties[key]]));
  }
  _spaceHeaterNeedsRatedPower(deviceType, mappings, properties) {
    if (deviceType !== "spaceHeater") return false;
    const hasMapping = (concept) => Boolean(mappings?.[concept]?.entityId);
    const ratedPower = properties?.ratedPowerW;
    return !hasMapping("spaceHeater.power") && !hasMapping("spaceHeater.energy")
      && (ratedPower === null || ratedPower === undefined || ratedPower === "" || !Number.isFinite(Number(ratedPower)));
  }
  _propertiesForm(properties, deviceType, learned = {}, validationError = false) {
    const physicalField = (key, suggestionKey, label, hint, unit, constraints) => {
      const configured = properties[key]; const suggestion = learned[suggestionKey];
      const displaySuggestion = suggestion == null ? "" : String(Math.trunc(Number(suggestion) * 100) / 100);
      const placeholder = displaySuggestion ? `~${displaySuggestion}` : "";
      return `<label class="physical-property">${esc(this._t(label))}<span class="property-input"><input type="number" step="any" ${constraints} data-property="${esc(key)}" value="${esc(configured ?? "")}" ${placeholder ? `placeholder="${esc(placeholder)}"` : ""}><span>${esc(unit)}</span></span>${displaySuggestion ? `<small>${esc(this._t(hint))}: ${esc(displaySuggestion)} ${esc(unit)}</small>` : ""}</label>`;
    };
    return `<section class="card"><h2>${esc(this._t("device_information"))}</h2><div class="fields three">
      ${[["displayName", "name"], ["vendor", "vendor"], ["model", "model"]].map(([key, label]) => `<label>${esc(this._t(label))}<input data-property="${key}" value="${esc(properties[key] || "")}"></label>`).join("")}</div></section>
      ${deviceType === "solar" ? `<section class="card"><h2>${esc(this._t("installation"))}</h2><div class="fields three">
      ${physicalField("installedKWp", "solar.installedKwpEstimated", "installed_solar_capacity", "estimated_by_fluks", "kWp", 'min="0"')}
      <label>${esc(this._t("direction"))}<input type="number" min="0" max="359.999" data-property="azimuthDegrees" value="${esc(properties.azimuthDegrees ?? "")}"></label>
      <label>${esc(this._t("panel_angle"))}<input type="number" min="0" max="90" data-property="tiltDegrees" value="${esc(properties.tiltDegrees ?? "")}"></label></div></section>` : ""}
      ${deviceType === "spaceHeater" ? `<section class="card"><h2>${esc(this._t("installation"))}</h2><div class="fields three"><label>${esc(this._t("rated_power"))}<span class="property-input"><input type="number" step="any" min="0" data-property="ratedPowerW" value="${esc(properties.ratedPowerW ?? "")}"><span>W</span></span>${validationError ? `<small class="validation-error" role="alert">${esc(this._t("rated_power_required"))}</small>` : ""}</label></div></section>` : ""}
      ${deviceType === "battery" ? `<section class="card"><h2>${esc(this._t("battery_configuration"))}</h2><div class="fields three">
      ${physicalField("capacityKwh", "battery.capacityKwhEstimated", "battery_capacity", "estimated_by_fluks", "kWh", 'min="0"')}
      ${physicalField("battery.socMinimum", "battery.socMinimumObserved", "minimum_soc", "lowest_observed_by_fluks", "%", 'min="0" max="100"')}
      ${physicalField("battery.socMaximum", "battery.socMaximumObserved", "maximum_soc", "highest_observed_by_fluks", "%", 'min="0" max="100"')}
      </div></section>` : ""}`;
  }
  _collectForm(useInputDraft = false) {
    const mappings = useInputDraft
      ? Object.fromEntries(Object.entries(this._inputDraft).filter(([concept, value]) => value.entityId || this._clearedInputConcepts.has(concept)).map(([concept, value]) => [concept, clone(value)]))
      : Object.fromEntries([...this.shadowRoot.querySelectorAll("[data-concept]")].map((n) => [n.dataset.concept, n.value]).filter(([, v]) => v));
    const properties = Object.fromEntries([...this.shadowRoot.querySelectorAll("[data-property]")].map((n) => [n.dataset.property, n.value === "" ? null : n.type === "number" ? Number(n.value) : n.value.trim()]));
    return { mappings, properties };
  }
  _wirePickers() {
    this.shadowRoot.querySelectorAll("[data-picker-kind]").forEach((button) => {
      button.onclick = () => this._openPicker(button.dataset.pickerKind, button.dataset.pickerValue, (value, attribute) => {
        if (button.dataset.pickerKind === "device") {
          this._selectAddHaDevice(value);
          return;
        }
        const hidden = this.shadowRoot.querySelector(`[data-concept="${CSS.escape(button.dataset.pickerKey)}"]`);
        hidden.value = value;
        if (this._inputDraft?.[button.dataset.pickerKey]) {
          this._selectInputEntity(button.dataset.pickerKey, value, attribute);
          return;
        }
        const replacement = document.createRange().createContextualFragment(this._pickerValue("entity", value, button.dataset.pickerKey));
        button.replaceWith(replacement);
        this._wirePickers();
      }, button.dataset.pickerAttribute, button.dataset.pickerKey);
    });
  }
  _selectInputEntity(concept, entityId, attribute = "") {
    this._captureInputProperties();
    this._inputDraft[concept].entityId = entityId;
    if (attribute) this._inputDraft[concept].attribute = attribute;
    else delete this._inputDraft[concept].attribute;
    if (entityId) this._clearedInputConcepts.delete(concept); else this._clearedInputConcepts.add(concept);
    this._renderInputDraft();
  }
  _cancelEdit() {
    this._inputDraft = undefined;
    this._inputDraftDevice = undefined;
    this._editProperties = undefined;
    this._clearedInputConcepts.clear();
    this._spaceHeaterValidationError = false;
    history.back();
  }
  _wireInputConversions() {
    this.shadowRoot.querySelectorAll("[data-input-transform]").forEach((button) => button.onclick = () => {
      const concept = button.dataset.inputConcept; const transforms = this._inputDraft[concept].transforms ?? [];
      const index = Number(button.dataset.index); const command = button.dataset.inputTransform;
      if (command === "add" || command === "edit") { this._openInputConversionEditor(concept, command === "edit" ? index : undefined); return; }
      if (command === "remove") transforms.splice(index, 1);
      if (command === "up" || command === "down") { const destination = command === "up" ? index - 1 : index + 1; const [item] = transforms.splice(index, 1); transforms.splice(destination, 0, item); }
      if (transforms.length) this._inputDraft[concept].transforms = transforms; else delete this._inputDraft[concept].transforms;
      this._captureInputProperties();
      this._renderInputDraft();
    });
  }
  _captureInputProperties() {
    const current = this._view.name === "add" ? this._draft?.properties : this._editProperties;
    const properties = { ...(current ?? {}), ...Object.fromEntries([...this.shadowRoot.querySelectorAll("[data-property]")].map((node) => [node.dataset.property, node.value === "" ? null : node.type === "number" ? Number(node.value) : node.value.trim()])) };
    if (this._view.name === "add" && this._draft) this._draft.properties = properties;
    else this._editProperties = properties;
  }
  _renderInputDraft() { this._view.name === "add" ? this._renderAdd() : this._renderEdit(); }
  _commitInputConversion(concept, index, type, raw) {
    let transform;
    if (type === "invert") transform = { type };
    else { const value = Number(raw); if (!Number.isFinite(value)) return false; transform = { type, [type === "scale" ? "factor" : "amount"]: value }; }
    const transforms = this._inputDraft[concept].transforms ?? [];
    if (index === undefined) transforms.push(transform); else transforms[index] = transform;
    this._inputDraft[concept].transforms = transforms;
    return true;
  }
  _openInputConversionEditor(concept, index) {
    const existing = index === undefined ? undefined : this._inputDraft[concept].transforms?.[index];
    const dialog = document.createElement("dialog"); dialog.className = "input-conversion-dialog";
    const selected = existing?.type ?? "invert"; const value = existing?.factor ?? existing?.amount ?? "";
    dialog.innerHTML = `<div class="dialog-heading"><h2>${esc(this._t(index === undefined ? "add_conversion" : "edit"))}</h2><button class="icon close" aria-label="${esc(this._t("cancel"))}">×</button></div><div class="conversion-editor"><label>${esc(this._t("adjustment_type"))}<select id="conversion-type"><option value="invert" ${selected === "invert" ? "selected" : ""}>${esc(this._t("invert"))}</option><option value="scale" ${selected === "scale" ? "selected" : ""}>${esc(this._t("scale"))}</option><option value="offset" ${selected === "offset" ? "selected" : ""}>${esc(this._t("offset"))}</option></select></label><label id="conversion-parameter">${esc(this._t(selected === "scale" ? "factor" : "amount"))}<input id="conversion-value" type="number" step="any" value="${esc(value)}"></label><div class="actions"><button id="conversion-cancel">${esc(this._t("cancel"))}</button><button class="primary" id="conversion-save">${esc(this._t(index === undefined ? "add_conversion" : "save_adjustment"))}</button></div></div>`;
    const type = dialog.querySelector("#conversion-type"); const parameter = dialog.querySelector("#conversion-parameter");
    const update = () => { parameter.hidden = type.value === "invert"; parameter.firstChild.textContent = this._t(type.value === "scale" ? "factor" : "amount"); }; type.onchange = update; update();
    const close = () => { dialog.close(); dialog.remove(); }; dialog.querySelector(".close").onclick = close; dialog.querySelector("#conversion-cancel").onclick = close;
    dialog.querySelector("#conversion-save").onclick = () => { if (!this._commitInputConversion(concept, index, type.value, dialog.querySelector("#conversion-value").value)) return; this._captureInputProperties(); close(); this._renderInputDraft(); };
    this.shadowRoot.append(dialog); dialog.showModal(); type.focus();
  }
  _openPicker(kind, selected, onSelect, selectedAttribute = "", concept = "") {
    this.shadowRoot.querySelector("dialog.picker-dialog")?.remove();
    const dialog = document.createElement("dialog");
    dialog.className = "picker-dialog";
    dialog.setAttribute("aria-label", this._t(kind === "device" ? "choose_ha_device_button" : "choose_entity"));
    const renderRows = (query = "") => {
      const normalized = query.trim().toLocaleLowerCase(this._hass.language);
      let items = kind === "device"
        ? this._context.ha_devices.map((item) => ({ id: item.id, name: item.name, secondary: [item.manufacturer, item.model].filter(Boolean).join(" · "), icon: "mdi:devices" }))
        : this._allEntities();
      const ordered = items.sort((a, b) => {
        const relatedDevice = this._detail?.ha_device_id || this._view.haDeviceId;
        if (kind === "entity" && relatedDevice) {
          const related = Number(b.deviceId === relatedDevice) - Number(a.deviceId === relatedDevice);
          if (related) return related;
        }
        return a.name.localeCompare(b.name, this._hass.language, { sensitivity: "base" });
      });
      const definitions = this._detail?.concepts ?? this._draft?.concepts ?? [];
      const definition = definitions.find((item) => item.concept === concept);
      const canonicalTemperature = definition?.datatype === "number" && ["°C", "°F", "K"].includes(definition?.unit);
      if (kind === "entity") items = ordered.flatMap((item) => this._entitySources(item, canonicalTemperature));
      else items = ordered;
      const filtered = items.filter((item) => `${item.name} ${item.secondary} ${item.id} ${item.trailing ?? ""}`.toLocaleLowerCase(this._hass.language).includes(normalized));
      dialog.querySelector(".picker-results").innerHTML = `${kind === "entity" ? `<button class="picker-row clear" data-value="" data-attribute=""><span class="source-icon"><ha-icon icon="mdi:close-circle-outline"></ha-icon></span><span class="row-copy"><strong>${esc(this._t("clear_selection"))}</strong><span>${esc(this._t("optional"))}</span></span></button>` : ""}${filtered.map((item) => `<button class="picker-row ${item.id === selected && (item.attribute ?? "") === selectedAttribute ? "selected" : ""}" data-value="${esc(item.id)}" data-attribute="${esc(item.attribute ?? "")}"><span class="source-icon"><ha-icon icon="${esc(item.icon || "mdi:devices")}"></ha-icon></span><span class="row-copy"><strong title="${esc(item.name)}">${esc(item.name)}</strong><span title="${esc(item.secondary || item.id)}">${esc(item.secondary || item.id)}</span></span>${item.trailing ? `<span class="trailing" title="${esc(item.trailing)}">${esc(item.trailing)}</span>` : ""}</button>`).join("") || `<p class="empty-results">${esc(this._t("no_results"))}</p>`}`;
      dialog.querySelectorAll("[data-value]").forEach((row) => row.onclick = () => { const value = row.dataset.value; const attribute = row.dataset.attribute; dialog.close(); dialog.remove(); onSelect(value, attribute); });
    };
    dialog.innerHTML = `<div class="dialog-heading"><h2>${esc(this._t(kind === "device" ? "choose_ha_device_button" : "choose_entity"))}</h2><button class="icon close" aria-label="${esc(this._t("cancel"))}">×</button></div><label class="search"><span class="visually-hidden">${esc(this._t("search"))}</span><input type="search" placeholder="${esc(this._t(kind === "device" ? "search_devices" : "search_entities"))}"></label><div class="picker-results"></div>`;
    this.shadowRoot.append(dialog);
    dialog.querySelector(".close").onclick = () => { dialog.close(); dialog.remove(); };
    dialog.addEventListener("cancel", () => dialog.remove());
    dialog.querySelector("input").oninput = (event) => renderRows(event.target.value);
    renderRows(); dialog.showModal(); dialog.querySelector("input").focus();
  }
  _renderEdit() {
    const site = this._detail.type === "site";
    if (this._inputDraftDevice !== this._detail.id) {
      this._inputDraftDevice = this._detail.id;
      this._spaceHeaterValidationError = false;
      this._clearedInputConcepts.clear();
      this._inputDraft = Object.fromEntries(this._detail.concepts.map((concept) => {
        const existing = this._detail.mappings[concept.concept]?.configuration;
        return [concept.concept, clone(existing ?? { version: 1, entityId: this._detail.suggestions[concept.concept] || "" })];
      }));
      this._editProperties = clone(this._editableDeviceProperties(this._detail.properties, this._detail.type));
    }
    const propertyError = this._spaceHeaterValidationError && this._spaceHeaterNeedsRatedPower(this._detail.type, this._inputDraft, this._editProperties);
    this._frame("", `<div class="device-heading compact">${this._typeIcon(this._detail.type, "header")}<div><h1>${esc(this._t("edit_mappings"))}</h1><p>${esc(this._detail.label)}</p></div></div>${this._mappingFields(this._detail, true)}${site ? "" : this._propertiesForm(this._editProperties, this._detail.type, this._detail.properties, propertyError)}${this._actions("save_mapping")}`, true);
    this._wirePickers();
    this._wireInputConversions();
    this.shadowRoot.querySelector("#cancel").onclick = () => this._cancelEdit();
    this.shadowRoot.querySelector("#save").onclick = async () => {
      try {
        const form = this._collectForm(true);
        if (this._spaceHeaterNeedsRatedPower(this._detail.type, form.mappings, form.properties)) {
          this._spaceHeaterValidationError = true;
          this._renderEdit();
          return;
        }
        this._spaceHeaterValidationError = false;
        const payload = { device_id: this._detail.id, mappings: form.mappings, properties: site ? {} : form.properties };
        const result = await this._call("fluks/config/device_save", payload);
        if (result?.properties) this._detail = { ...this._detail, properties: clone(result.properties) };
        this._inputDraft = undefined;
        this._inputDraftDevice = undefined;
        this._editProperties = undefined;
        this._clearedInputConcepts.clear();
        this._spaceHeaterValidationError = false;
        history.back();
      }
      catch (_) { this._renderEdit(); }
    };
  }
  _actions(saveKey = "save") { return `<div class="actions"><button id="cancel">${esc(this._t("cancel"))}</button><button class="primary" id="save">${esc(this._t(saveKey))}</button></div>`; }
  _renderAdd() {
    const type = this._context.device_types.find((t) => t.type === this._view.deviceType);
    const typePicker = `<section><h2>${esc(this._t("choose_type"))}</h2><div class="type-grid">${this._context.device_types.map((item) => `<button type="button" class="type-option ${item.type === this._view.deviceType ? "selected" : ""}" data-type="${esc(item.type)}">${this._typeIcon(item.type, "picker")}<strong>${esc(item.name)}</strong></button>`).join("")}</div></section>`;
    let body = typePicker;
    if (type) {
      body += `<section class="add-source"><div class="device-heading compact">${this._typeIcon(type.type, "header")}<div><h2>${esc(type.name)}</h2><p>${esc(this._t("choose_ha_device"))}</p></div></div>
        <label>${esc(this._t("home_assistant_device"))}${this._pickerValue("device", this._view.haDeviceId || "", "ha_device_id")}</label></section>`;
    }
    if (type && this._draft && this._view.haDeviceId) {
      const detail = { concepts: this._draft.concepts, mappings: {}, suggestions: this._draft.suggestions };
      const propertyError = this._spaceHeaterValidationError && this._spaceHeaterNeedsRatedPower(type.type, this._inputDraft, this._draft.properties);
      body += `<p class="suggestion-copy">${esc(this._t("review_suggestions"))}</p>${this._mappingFields(detail, true)}${this._propertiesForm(this._draft.properties, type.type, {}, propertyError)}`;
    }
    body += `<div class="actions"><button id="cancel">${esc(this._t("cancel"))}</button><button class="primary" id="save" ${!this._draft ? "disabled" : ""}>${esc(this._t("save_device"))}</button></div>`;
    this._frame(type ? `${this._t("add_device")} · ${type.name}` : this._t("add_device"), body, true);
    this.shadowRoot.querySelectorAll("[data-type]").forEach((node) => node.onclick = () => {
      this._view = { name: "add", deviceType: node.dataset.type };
      this._spaceHeaterValidationError = false;
      this._draft = undefined;
      this._inputDraft = undefined;
      this._inputDraftDevice = undefined;
      this._clearedInputConcepts.clear();
      history.replaceState({ ...(history.state || {}), fluksView: this._view }, "");
      this._renderAdd();
    });
    this._wirePickers();
    this._wireInputConversions();
    this.shadowRoot.querySelector("#cancel").onclick = () => history.back();
    this.shadowRoot.querySelector("#save").onclick = async () => {
      if (!this._draft || !this._view.haDeviceId) return;
      try {
        const form = this._collectForm(true);
        if (this._spaceHeaterNeedsRatedPower(this._view.deviceType, form.mappings, form.properties)) {
          this._spaceHeaterValidationError = true;
          this._renderAdd();
          return;
        }
        this._spaceHeaterValidationError = false;
        await this._call("fluks/config/add_save", { device_type: this._view.deviceType, ha_device_id: this._view.haDeviceId, ...form });
        this._context = await this._call("fluks/config/context"); this._go({ name: "home" });
      } catch (_) { this._renderAdd(); }
    };
  }
  async _selectAddHaDevice(haDeviceId) {
    if (!haDeviceId || !this._view.deviceType) return;
    try {
      const draft = await this._call("fluks/config/add_review", { device_type: this._view.deviceType, ha_device_id: haDeviceId });
      this._draft = draft;
      this._view = { ...this._view, haDeviceId };
      this._spaceHeaterValidationError = false;
      this._inputDraftDevice = `add:${this._view.deviceType}:${haDeviceId}`;
      this._clearedInputConcepts.clear();
      this._inputDraft = Object.fromEntries(draft.concepts.map((concept) => [concept.concept, {
        version: 1,
        entityId: draft.suggestions[concept.concept] || "",
      }]));
      history.replaceState({ ...(history.state || {}), fluksView: this._view }, "");
      this._renderAdd();
    } catch (_) { this._renderAdd(); }
  }
  _renderControls() {
    const rows = this._detail.controls.map((c) => `<button class="row" data-control="${esc(c.concept)}"><span class="row-copy"><strong>${esc(this._conceptLabel(c))}</strong><small>${esc(this._detail.output_mappings[c.concept] ? this._t("configured") : this._t("not_configured"))}</small></span><span>›</span></button>`).join("");
    this._frame(`${this._detail.type_name} · ${this._t("controls")}`, `<div class="card list">${rows || `<p>${esc(this._t("not_configured"))}</p>`}</div>`, true);
    this.shadowRoot.querySelectorAll("[data-control]").forEach((n) => n.onclick = () => this._go({ name: "control", deviceId: this._detail.id, concept: n.dataset.control }));
  }
  _renderControl() {
    const control = this._detail.controls.find((c) => c.concept === this._view.concept);
    if (!control) return this._message(this._t("context_missing"));
    this._frame(`${this._detail.type_name} · ${this._conceptLabel(control)}`, `<p>${esc(this._t("control_persistence_intro"))}</p><${CONTROL_ACTION_EDITOR_TAG}></${CONTROL_ACTION_EDITOR_TAG}>`, true);
    const editor = this.shadowRoot.querySelector(CONTROL_ACTION_EDITOR_TAG);
    const key = `${this._detail.id}:${control.concept}`;
    editor.controlName = this._conceptLabel(control);
    editor.strings = Object.fromEntries([
      "action_intro", "no_actions", "add_action", "save_control", "up", "down", "edit", "remove",
      "edit_action", "action", "search_actions", "action_fields", "target_only", "required",
      "save_action", "choose_entity", "clear_selection", "entity", "value_source", "yes", "no",
      "control_value", "fixed_value", "fixed", "choose_entity_error", "fixed_value_error", "cancel",
      "choose_action_error", "required_field_error", "value_adjustments", "transform_order_help", "no_transforms",
      "adjustment_type", "power_to_current", "nearest", "value_map", "invert", "scale", "offset",
      "phases", "voltage", "allowed_values", "input_type", "output_type", "from_values", "to_values",
      "number_type", "text_type", "boolean_type", "null_type", "factor", "amount",
      "add_adjustment", "save_adjustment", "invalid_adjustment",
      "invalid_pipeline",
      "default_behavior", "default_behavior_description", "add_behavior", "remove_behavior", "choose_behavior", "choose_behavior_help",
      "behavior_target", "behavior_target_choice", "behavior_target_description",
      "behavior_limit", "behavior_limit_choice", "behavior_limit_description",
      "behavior_balance", "behavior_balance_choice", "behavior_balance_description",
      "behavior_release", "behavior_release_choice", "behavior_release_description",
      "behavior_charge", "behavior_charge_choice", "behavior_charge_description",
      "behavior_discharge", "behavior_discharge_choice", "behavior_discharge_description",
    ].map((key) => [key, this._t(key)]));
    editor.capabilities = this._controlCapabilities ?? [];
    editor.valueType = { datatype: control.datatype, unit: control.unit ?? null };
    editor.allowedModes = control.mappingModes ?? [];
    const persisted = (this._detail.output_mappings[control.concept] ?? []).map((mapping) => ({ mode: mapping.mode ?? null, actions: mapping.configuration?.actions ?? [] }));
    editor.behaviors = this._pendingControl?.key === key ? this._pendingControl.behaviors : persisted;
    editor.addEventListener("control-saved", async (e) => {
      this._pendingControl = { key, behaviors: e.detail.behaviors };
      try {
        await this._call("fluks/config/control_save", {
          device_id: this._detail.id,
          concept: control.concept,
          behaviors: e.detail.behaviors,
        });
        this._pendingControl = undefined;
        history.back();
      } catch (_) { this._renderControl(); }
    }, { once: true });
    editor.addEventListener("control-cancelled", () => {
      if (this._pendingControl?.key === key) this._pendingControl = undefined;
      history.back();
    }, { once: true });
  }
  _credentials(titleKey, bodyKey, label) {
    return `<section class="card"><h2>${esc(this._t(titleKey))}</h2><p>${esc(this._t(bodyKey))}</p><label>${esc(this._t("email"))}<input id="email" type="email" autocomplete="username"></label><label>${esc(this._t("password"))}<input id="password" type="password" autocomplete="current-password"></label></section><div class="actions"><button id="cancel">${esc(this._t("cancel"))}</button><button class="danger" id="confirm">${esc(label)}</button></div>`;
  }
  _renderDeleteDevice() {
    const confirm = this._view.stage === "confirm";
    const body = confirm ? `<section class="card danger-zone"><h2>${esc(this._t("delete_device_title", { deviceName: this._detail.label }))}</h2><p>${esc(this._t("delete_device_body"))}</p></section><div class="actions"><button id="cancel">${esc(this._t("cancel"))}</button><button class="danger" id="continue">${esc(this._t("delete_device"))}</button></div>` : this._credentials("delete_device_auth_title", "delete_device_auth_body", this._t("delete_device"));
    this._frame(this._t("delete_device"), body, true);
    this.shadowRoot.querySelector("#cancel").onclick = () => history.back();
    this.shadowRoot.querySelector("#continue")?.addEventListener("click", () => { this._view = { ...this._view, stage: "login" }; this._renderDeleteDevice(); });
    this.shadowRoot.querySelector("#confirm")?.addEventListener("click", async () => {
      try { await this._call("fluks/config/device_delete", { device_id: this._detail.id, email: this.shadowRoot.querySelector("#email").value, password: this.shadowRoot.querySelector("#password").value }); this._context = await this._call("fluks/config/context"); this._go({ name: "home" }); }
      catch (_) { this._renderDeleteDevice(); }
    });
  }
  _renderDeleteSite() {
    const confirm = this._view.stage === "confirm";
    const body = confirm ? `<section class="card danger-zone"><h2>${esc(this._t("delete_site_title", { siteName: this._context.site.name }))}</h2><p>${esc(this._t("delete_site_body"))}</p></section><div class="actions"><button id="cancel">${esc(this._t("cancel"))}</button><button class="danger" id="continue">${esc(this._t("delete_site"))}</button></div>` : this._credentials("delete_site_auth_title", "delete_site_auth_body", this._t("delete_site"));
    this._frame(this._t("delete_site"), body, true);
    this.shadowRoot.querySelector("#cancel").onclick = () => history.back();
    this.shadowRoot.querySelector("#continue")?.addEventListener("click", () => { this._view = { ...this._view, stage: "login" }; this._renderDeleteSite(); });
    this.shadowRoot.querySelector("#confirm")?.addEventListener("click", async () => {
      try { await this._call("fluks/config/site_delete", { email: this.shadowRoot.querySelector("#email").value, password: this.shadowRoot.querySelector("#password").value }); history.replaceState({}, "", "/config/integrations"); location.reload(); }
      catch (_) { this._renderDeleteSite(); }
    });
  }
  _styles() { return `
    :host{display:block;min-height:100%;box-sizing:border-box;background:var(--primary-background-color);color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family,system-ui,sans-serif)}
    *{box-sizing:border-box}main{max-width:1100px;margin:auto;padding:28px 24px 48px}header{display:flex;gap:12px;align-items:flex-start;margin-bottom:24px;min-height:44px}header h1:empty{display:none}
    h1,h2,h3,p{margin-top:0}h1{font-size:28px;line-height:1.2;margin-bottom:5px}h2{font-size:18px;margin-bottom:12px}h3{font-size:17px;margin-bottom:5px}p{color:var(--secondary-text-color);line-height:1.45;margin-bottom:14px}
    section{margin:0 0 24px}.card{position:relative;background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:12px;padding:18px;margin-bottom:14px;box-shadow:var(--ha-card-box-shadow,none)}.list{padding:0;overflow:hidden}
    button{font:inherit;border:1px solid var(--divider-color);background:var(--secondary-background-color);color:var(--primary-text-color);border-radius:9px;padding:9px 14px;min-height:42px;cursor:pointer}button:hover{filter:brightness(1.06)}button:focus-visible,input:focus-visible{outline:2px solid var(--primary-color);outline-offset:2px}button:disabled{opacity:.5;cursor:not-allowed}
    .primary{background:var(--primary-color);color:var(--text-primary-color,#fff);border-color:var(--primary-color)}.danger{background:var(--error-color);border-color:var(--error-color);color:#fff}.danger-zone{border-color:var(--error-color)}.menu-danger{color:var(--error-color);background:transparent;border:0;width:100%;text-align:left}
    .icon{border:0;background:transparent;font-size:25px;padding:4px;width:42px;min-width:42px}.overflow{margin-left:auto;font-weight:700}.section-title,.actions{display:flex;justify-content:space-between;align-items:center;gap:12px}.actions{justify-content:flex-end;margin:22px 0 0}
    .row{width:100%;display:flex;align-items:center;gap:14px;text-align:left;border:0;border-bottom:1px solid var(--divider-color);border-radius:0;padding:13px 16px;background:transparent;color:var(--primary-text-color)}.row:last-child{border-bottom:0}.row-copy{display:flex;flex-direction:column;gap:3px;min-width:0;flex:1;overflow:hidden}.row-copy strong,.row-copy span{display:block;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.row-copy strong{font-weight:600}.row-copy span,.eyebrow,small{color:var(--secondary-text-color);font-size:13px}.chevron{font-size:24px;color:var(--secondary-text-color);flex:none}
    .device-icon{display:block;object-fit:contain;flex:none}.device-icon.list{width:38px;height:38px}.device-icon.hero{width:62px;height:62px}.device-icon.header{width:50px;height:50px}.device-icon.picker{width:70px;height:70px}
    .device-heading{position:relative;display:flex;align-items:center;gap:18px;margin:2px 0 24px;padding-right:48px}.device-heading h1,.device-heading h2{margin:0 0 4px}.device-heading p{margin:0}.device-heading .device-name{font-size:16px;color:var(--primary-text-color)}.device-heading.compact{margin-bottom:20px}
    .site-row{display:grid;grid-template-columns:minmax(0,1fr) 44px;align-items:center;gap:8px;padding:6px 8px 6px 10px}.site-link{display:flex;align-items:center;gap:12px;min-width:0;width:100%;padding:7px 4px;border:0;background:transparent;text-align:left}.site-row ha-icon{color:var(--secondary-text-color)}.site-hero{width:62px;height:62px;color:var(--primary-color)}.site-header{width:50px;height:50px;color:var(--primary-color)}
    .context-menu{position:absolute;z-index:5;right:10px;top:52px;min-width:180px;padding:6px;background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:9px;box-shadow:var(--ha-card-box-shadow,0 4px 14px rgba(0,0,0,.24))}.context-menu[hidden]{display:none}.device-menu{right:0;top:44px}
    .overview-list .row{min-height:72px}.overview-list ha-icon{color:var(--primary-color);width:28px}.fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));align-items:start;gap:14px}.fields.two{grid-template-columns:repeat(2,minmax(0,1fr))}.fields.three{grid-template-columns:repeat(3,minmax(0,1fr))}
    label{display:grid;gap:7px;font-weight:600;margin-bottom:8px;min-width:0}input,select{box-sizing:border-box;width:100%;padding:11px;border-radius:8px;border:1px solid var(--divider-color);background:var(--input-fill-color,var(--secondary-background-color));color:var(--primary-text-color);font:inherit}.property-input{position:relative;display:block;min-width:0}.property-input input{min-width:0;padding-right:54px}.property-input>span{position:absolute;right:12px;top:50%;transform:translateY(-50%);color:var(--secondary-text-color);font-weight:500;pointer-events:none}.physical-property small{font-weight:400}.validation-error{color:var(--error-color);font-weight:500}
    .mapping-field{min-width:0}.input-conversions{display:grid;gap:10px;margin-top:10px}.input-conversions ol{list-style:none;margin:0;padding:0;display:grid;gap:8px}.input-conversions li{display:grid;grid-template-columns:30px minmax(0,1fr) auto;align-items:center;gap:9px;padding:9px;border:1px solid var(--divider-color);border-radius:9px}.input-conversions .order{width:28px;height:28px;display:grid;place-items:center;border-radius:50%;background:var(--primary-color);color:#fff;font-weight:700}.conversion-copy{display:grid;min-width:0}.conversion-copy strong,.conversion-copy small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.input-conversions .row-actions{display:flex;gap:4px;flex-wrap:wrap}.input-conversions .row-actions button{min-height:34px;padding:5px 8px}.add-conversion{justify-self:start}.conversion-editor{padding:8px 18px 18px}.conversion-editor [hidden]{display:none}
    .picker-value{width:100%;height:62px;display:flex;align-items:center;gap:11px;text-align:left;padding:10px 12px;background:var(--secondary-background-color);overflow:hidden}.source-icon{display:grid;place-items:center;width:34px;height:34px;flex:none;color:var(--primary-color)}
    .type-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:12px;margin-bottom:24px}.type-option{display:flex;min-height:128px;align-items:center;justify-content:center;flex-direction:column;gap:8px;background:var(--card-background-color)}.type-option.selected{border:2px solid var(--primary-color);background:color-mix(in srgb,var(--primary-color) 8%,var(--card-background-color))}.add-source{border-top:1px solid var(--divider-color);padding-top:22px}.suggestion-copy{margin:4px 0 18px}
    dialog{width:min(620px,calc(100vw - 32px));max-height:min(720px,calc(100vh - 32px));padding:0;border:1px solid var(--divider-color);border-radius:14px;background:var(--card-background-color);color:var(--primary-text-color);box-shadow:0 14px 45px rgba(0,0,0,.38)}dialog::backdrop{background:rgba(0,0,0,.58)}.dialog-heading{display:flex;justify-content:space-between;align-items:center;padding:18px 18px 8px}.dialog-heading h2{margin:0}.search{padding:8px 16px;margin:0}.picker-results{max-height:min(530px,65vh);overflow:auto;border-top:1px solid var(--divider-color)}.picker-row{width:100%;height:62px;display:flex;align-items:center;gap:11px;text-align:left;border:0;border-bottom:1px solid var(--divider-color);border-radius:0;background:transparent;padding:8px 15px;overflow:hidden}.picker-row.selected{outline:2px solid var(--primary-color);outline-offset:-2px}.picker-row .trailing{flex:0 1 150px;min-width:0;max-width:28%;margin-left:auto;color:var(--secondary-text-color);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:right}.empty-results{padding:22px}.visually-hidden{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
    .error{background:var(--error-color);color:#fff;padding:12px;margin-bottom:16px;border-radius:7px}
    @media(max-width:700px){main{padding:16px 12px 36px}.fields,.fields.two,.fields.three{grid-template-columns:1fr}.type-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.device-icon.hero{width:52px;height:52px}.section-title{align-items:flex-start}.actions{position:sticky;z-index:4;bottom:0;background:var(--primary-background-color);padding:10px 0}.row{padding:12px}.card{padding:14px}.list{padding:0}.picker-results{max-height:60vh}.picker-row .trailing{flex-basis:96px;max-width:24%}.input-conversions li{grid-template-columns:30px minmax(0,1fr)}.input-conversions .row-actions{grid-column:2}}
    @media(max-width:390px){.type-grid{grid-template-columns:1fr 1fr}.type-option{min-height:108px}.device-icon.picker{width:58px;height:58px}.section-title{flex-wrap:wrap}}
  `; }
}
customElements.define(PANEL_TAG, FluksControlEditorPanel);
