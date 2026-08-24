/* Embedded fluks administration panel. Backend credentials remain in Python. */
import "./control-action-editor.js";

const TAGLINE = "Your Energy. Decides together.";
const DEVICE_ICON_BASE = "/fluks-device-icons";
const esc = (value) => String(value ?? "").replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");

class FluksControlEditorPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._view = { name: "home" };
    this._controlDrafts = new Map();
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
      this._controlDrafts.clear();
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
  _go(view, replace = false) {
    this._view = view; this._detail = this._draft = undefined;
    const state = { ...(history.state || {}), fluksView: view };
    (replace ? history.replaceState : history.pushState).call(history, state, "");
    this._loadView();
  }
  async _loadView() {
    if (!this._context) return;
    if (["device", "edit", "controls", "control", "delete-device"].includes(this._view.name)) {
      try { this._detail = await this._call("fluks/config/device", { device_id: this._view.deviceId }); }
      catch (_) { return this._message(this._error); }
    }
    this._render();
  }
  _render() {
    return ({
      device: () => this._renderDevice(), edit: () => this._renderEdit(),
      add: () => this._renderAdd(), controls: () => this._renderControls(),
      control: () => this._renderControl(), "delete-device": () => this._renderDeleteDevice(),
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
      <section><h2>${esc(this._t("site"))}</h2><div class="card site-row"><ha-icon icon="mdi:home-outline"></ha-icon>
      <strong>${esc(this._context.site.name)}</strong><button class="icon overflow" id="site-menu" aria-label="${esc(this._t("site_actions"))}" aria-haspopup="menu">⋮</button>
      <div class="context-menu" id="site-actions" role="menu" hidden><button class="menu-danger" id="delete-site" role="menuitem">${esc(this._t("delete_site"))}</button></div></div></section>`);
    this.shadowRoot.querySelector("#add").onclick = () => this._go({ name: "add" });
    this._wireMenu("site-menu", "site-actions");
    this.shadowRoot.querySelector("#delete-site").onclick = () => this._go({ name: "delete-site", stage: "confirm" });
    this.shadowRoot.querySelectorAll("[data-device]").forEach((n) => n.onclick = () => this._go({ name: "device", deviceId: n.dataset.device }));
  }
  _renderDevice() {
    const configured = Object.values(this._detail.mappings);
    const measurementCount = configured.filter((m) => this._detail.concepts.find((c) => c.concept === m.concept)?.cadence !== "interval").length;
    const energyCount = configured.filter((m) => this._detail.concepts.find((c) => c.concept === m.concept)?.cadence === "interval").length;
    const metadata = this._metadata(this._detail.properties);
    this._frame("", `<div class="device-heading">${this._typeIcon(this._detail.type, "hero")}<div>
      <h1>${esc(this._detail.type_name)}</h1><p class="device-name">${esc(this._detail.name || this._detail.type_name)}</p>${metadata ? `<p>${esc(metadata)}</p>` : ""}</div>
      <button class="icon overflow" id="device-menu" aria-label="${esc(this._t("device_actions"))}" aria-haspopup="menu">⋮</button>
      <div class="context-menu device-menu" id="device-actions-menu" role="menu" hidden><button class="menu-danger" id="delete" role="menuitem">${esc(this._t("delete_device"))}</button></div></div>
      <div class="card list overview-list">
      <button class="row" id="edit"><ha-icon icon="mdi:chart-line"></ha-icon><span class="row-copy"><strong>${esc(this._t("measurements_energy"))}</strong><span>${measurementCount} ${esc(this._t("measurements_count"))} · ${energyCount} ${esc(this._t("energy_count"))}</span></span><span class="chevron">›</span></button>
      <button class="row" id="controls"><ha-icon icon="mdi:tune-variant"></ha-icon><span class="row-copy"><strong>${esc(this._t("controls"))}</strong><span>${this._detail.controls.length} ${esc(this._t("available"))} · ${esc(this._t("prototype_only"))}</span></span><span class="chevron">›</span></button>
      <button class="row" id="information"><ha-icon icon="mdi:information-outline"></ha-icon><span class="row-copy"><strong>${esc(this._t("device_information"))}</strong><span>${esc(metadata || this._t("optional"))}</span></span><span class="chevron">›</span></button></div>`, true);
    this._wireMenu("device-menu", "device-actions-menu");
    this.shadowRoot.querySelector("#edit").onclick = () => this._go({ name: "edit", deviceId: this._detail.id });
    this.shadowRoot.querySelector("#information").onclick = () => this._go({ name: "edit", deviceId: this._detail.id });
    this.shadowRoot.querySelector("#controls").onclick = () => this._go({ name: "controls", deviceId: this._detail.id });
    this.shadowRoot.querySelector("#delete").onclick = () => this._go({ name: "delete-device", deviceId: this._detail.id, stage: "confirm" });
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
  _pickerValue(kind, id, key) {
    const item = kind === "device"
      ? this._context.ha_devices.find((device) => device.id === id)
      : this._entityRecord(id);
    const name = item?.name || this._t(kind === "device" ? "choose_ha_device_button" : "choose_entity");
    const secondary = kind === "device"
      ? [item?.manufacturer, item?.model].filter(Boolean).join(" · ")
      : item?.secondary;
    return `<button type="button" class="picker-value" data-picker-kind="${kind}" data-picker-key="${esc(key)}" data-picker-value="${esc(id || "")}">
      <span class="source-icon"><ha-icon icon="${esc(item?.icon || (kind === "device" ? "mdi:devices" : "mdi:chart-bell-curve-cumulative"))}"></ha-icon></span>
      <span class="row-copy"><strong title="${esc(name)}">${esc(name)}</strong><span title="${esc(secondary || this._t("optional"))}">${esc(secondary || this._t("optional"))}</span></span><span class="chevron">⌄</span></button>`;
  }
  _mappingFields(detail) {
    const groups = { measurements: [], energy: [] };
    for (const concept of detail.concepts) {
      const selected = detail.mappings[concept.concept]?.configuration?.entityId || detail.suggestions[concept.concept] || "";
      groups[concept.cadence === "interval" ? "energy" : "measurements"].push(`<label>${esc(this._conceptLabel(concept))}${this._pickerValue("entity", selected, concept.concept)}<input type="hidden" data-concept="${esc(concept.concept)}" value="${esc(selected)}"></label>`);
    }
    return Object.entries(groups).filter(([, f]) => f.length).map(([name, fields]) => `<section class="card"><h2>${esc(this._t(name))}</h2><div class="fields">${fields.join("")}</div></section>`).join("");
  }
  _propertiesForm(properties, solar) {
    return `<section class="card"><h2>${esc(this._t("device_information"))}</h2><div class="fields three">
      ${[["displayName", "name"], ["vendor", "vendor"], ["model", "model"]].map(([key, label]) => `<label>${esc(this._t(label))}<input data-property="${key}" value="${esc(properties[key] || "")}"></label>`).join("")}</div></section>
      ${solar ? `<section class="card"><h2>${esc(this._t("installation"))}</h2><div class="fields two">
      <label>${esc(this._t("direction"))}<input type="number" min="0" max="359.999" data-property="azimuthDegrees" value="${esc(properties.azimuthDegrees ?? "")}"></label>
      <label>${esc(this._t("panel_angle"))}<input type="number" min="0" max="90" data-property="tiltDegrees" value="${esc(properties.tiltDegrees ?? "")}"></label></div></section>` : ""}`;
  }
  _collectForm() {
    const mappings = Object.fromEntries([...this.shadowRoot.querySelectorAll("[data-concept]")].map((n) => [n.dataset.concept, n.value]).filter(([, v]) => v));
    const properties = Object.fromEntries([...this.shadowRoot.querySelectorAll("[data-property]")].map((n) => [n.dataset.property, n.value === "" ? null : n.type === "number" ? Number(n.value) : n.value.trim()]));
    return { mappings, properties };
  }
  _wirePickers() {
    this.shadowRoot.querySelectorAll("[data-picker-kind]").forEach((button) => {
      button.onclick = () => this._openPicker(button.dataset.pickerKind, button.dataset.pickerValue, (value) => {
        if (button.dataset.pickerKind === "device") {
          this._selectAddHaDevice(value);
          return;
        }
        const hidden = this.shadowRoot.querySelector(`[data-concept="${CSS.escape(button.dataset.pickerKey)}"]`);
        hidden.value = value;
        const replacement = document.createRange().createContextualFragment(this._pickerValue("entity", value, button.dataset.pickerKey));
        button.replaceWith(replacement);
        this._wirePickers();
      });
    });
  }
  _openPicker(kind, selected, onSelect) {
    this.shadowRoot.querySelector("dialog.picker-dialog")?.remove();
    const dialog = document.createElement("dialog");
    dialog.className = "picker-dialog";
    dialog.setAttribute("aria-label", this._t(kind === "device" ? "choose_ha_device_button" : "choose_entity"));
    const renderRows = (query = "") => {
      const normalized = query.trim().toLocaleLowerCase(this._hass.language);
      const items = kind === "device"
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
      const filtered = ordered.filter((item) => `${item.name} ${item.secondary} ${item.id}`.toLocaleLowerCase(this._hass.language).includes(normalized));
      dialog.querySelector(".picker-results").innerHTML = `${kind === "entity" ? `<button class="picker-row clear" data-value=""><span class="source-icon"><ha-icon icon="mdi:close-circle-outline"></ha-icon></span><span class="row-copy"><strong>${esc(this._t("clear_selection"))}</strong><span>${esc(this._t("optional"))}</span></span></button>` : ""}${filtered.map((item) => `<button class="picker-row ${item.id === selected ? "selected" : ""}" data-value="${esc(item.id)}"><span class="source-icon"><ha-icon icon="${esc(item.icon || "mdi:devices")}"></ha-icon></span><span class="row-copy"><strong title="${esc(item.name)}">${esc(item.name)}</strong><span title="${esc(item.secondary || item.id)}">${esc(item.secondary || item.id)}</span></span>${item.trailing ? `<span class="trailing" title="${esc(item.trailing)}">${esc(item.trailing)}</span>` : ""}</button>`).join("") || `<p class="empty-results">${esc(this._t("no_results"))}</p>`}`;
      dialog.querySelectorAll("[data-value]").forEach((row) => row.onclick = () => { const value = row.dataset.value; dialog.close(); dialog.remove(); onSelect(value); });
    };
    dialog.innerHTML = `<div class="dialog-heading"><h2>${esc(this._t(kind === "device" ? "choose_ha_device_button" : "choose_entity"))}</h2><button class="icon close" aria-label="${esc(this._t("cancel"))}">×</button></div><label class="search"><span class="visually-hidden">${esc(this._t("search"))}</span><input type="search" placeholder="${esc(this._t(kind === "device" ? "search_devices" : "search_entities"))}"></label><div class="picker-results"></div>`;
    this.shadowRoot.append(dialog);
    dialog.querySelector(".close").onclick = () => { dialog.close(); dialog.remove(); };
    dialog.addEventListener("cancel", () => dialog.remove());
    dialog.querySelector("input").oninput = (event) => renderRows(event.target.value);
    renderRows(); dialog.showModal(); dialog.querySelector("input").focus();
  }
  _renderEdit() {
    this._frame("", `<div class="device-heading compact">${this._typeIcon(this._detail.type, "header")}<div><h1>${esc(this._t("edit_mappings"))}</h1><p>${esc(this._detail.label)}</p></div></div>${this._mappingFields(this._detail)}${this._propertiesForm(this._detail.properties, this._detail.type === "solar")}${this._actions()}`, true);
    this._wirePickers();
    this.shadowRoot.querySelector("#cancel").onclick = () => history.back();
    this.shadowRoot.querySelector("#save").onclick = async () => {
      try { await this._call("fluks/config/device_save", { device_id: this._detail.id, ...this._collectForm() }); history.back(); }
      catch (_) { this._renderEdit(); }
    };
  }
  _actions() { return `<div class="actions"><button id="cancel">${esc(this._t("cancel"))}</button><button class="primary" id="save">${esc(this._t("save"))}</button></div>`; }
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
      body += `<p class="suggestion-copy">${esc(this._t("review_suggestions"))}</p>${this._mappingFields(detail)}${this._propertiesForm(this._draft.properties, type.type === "solar")}`;
    }
    body += `<div class="actions"><button id="cancel">${esc(this._t("cancel"))}</button><button class="primary" id="save" ${!this._draft ? "disabled" : ""}>${esc(this._t("save_device"))}</button></div>`;
    this._frame(type ? `${this._t("add_device")} · ${type.name}` : this._t("add_device"), body, true);
    this.shadowRoot.querySelectorAll("[data-type]").forEach((node) => node.onclick = () => {
      this._view = { name: "add", deviceType: node.dataset.type };
      this._draft = undefined;
      history.replaceState({ ...(history.state || {}), fluksView: this._view }, "");
      this._renderAdd();
    });
    this._wirePickers();
    this.shadowRoot.querySelector("#cancel").onclick = () => history.back();
    this.shadowRoot.querySelector("#save").onclick = async () => {
      if (!this._draft || !this._view.haDeviceId) return;
      try {
        await this._call("fluks/config/add_save", { device_type: this._view.deviceType, ha_device_id: this._view.haDeviceId, ...this._collectForm() });
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
      history.replaceState({ ...(history.state || {}), fluksView: this._view }, "");
      this._renderAdd();
    } catch (_) { this._renderAdd(); }
  }
  _renderControls() {
    const rows = this._detail.controls.map((c) => `<button class="row" data-control="${esc(c.concept)}"><span><strong>${esc(this._conceptLabel(c))}</strong><small>${esc(this._controlDrafts.has(`${this._detail.id}:${c.concept}`) ? this._t("prototype_only") : this._t("not_configured"))}</small></span><span>›</span></button>`).join("");
    this._frame(`${this._detail.type_name} · ${this._t("controls")}`, `<div class="card list">${rows || `<p>${esc(this._t("not_configured"))}</p>`}</div>`, true);
    this.shadowRoot.querySelectorAll("[data-control]").forEach((n) => n.onclick = () => this._go({ name: "control", deviceId: this._detail.id, concept: n.dataset.control }));
  }
  _renderControl() {
    const control = this._detail.controls.find((c) => c.concept === this._view.concept);
    if (!control) return this._message(this._t("context_missing"));
    this._frame(`${this._detail.type_name} · ${this._conceptLabel(control)}`, `<p>${esc(this._t("prototype_only"))}</p><fluks-control-action-editor></fluks-control-action-editor>`, true);
    const editor = this.shadowRoot.querySelector("fluks-control-action-editor");
    const key = `${this._detail.id}:${control.concept}`;
    editor.controlName = this._conceptLabel(control);
    editor.strings = Object.fromEntries([
      "action_intro", "no_actions", "add_action", "save_control", "up", "down", "edit", "remove",
      "edit_action", "action_type", "selectOption", "setNumber", "water_heater_temperature",
      "save_action", "choose_entity", "ha_action", "parameter", "entity", "value_source",
      "control_value", "fixed_value", "fixed", "choose_entity_error", "fixed_value_error", "cancel",
    ].map((key) => [key, this._t(key)]));
    editor.entities = Object.values(this._hass.states).filter((s) => ["select", "number", "water_heater"].includes(s.entity_id.split(".")[0])).map((s) => ({ entityId: s.entity_id, domain: s.entity_id.split(".")[0], name: s.attributes.friendly_name || s.entity_id }));
    editor.actions = this._controlDrafts.get(key) || [];
    editor.addEventListener("control-saved", (e) => { this._controlDrafts.set(key, e.detail.actions); history.back(); }, { once: true });
    editor.addEventListener("control-cancelled", () => history.back(), { once: true });
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
    .site-row{display:grid;grid-template-columns:28px 1fr 44px;align-items:center;gap:12px;padding:12px 14px}.site-row ha-icon{color:var(--secondary-text-color)}
    .context-menu{position:absolute;z-index:5;right:10px;top:52px;min-width:180px;padding:6px;background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:9px;box-shadow:var(--ha-card-box-shadow,0 4px 14px rgba(0,0,0,.24))}.context-menu[hidden]{display:none}.device-menu{right:0;top:44px}
    .overview-list .row{min-height:72px}.overview-list ha-icon{color:var(--primary-color);width:28px}.fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.fields.two{grid-template-columns:repeat(2,minmax(0,1fr))}.fields.three{grid-template-columns:repeat(3,minmax(0,1fr))}
    label{display:grid;gap:7px;font-weight:600;margin-bottom:8px;min-width:0}input{width:100%;padding:11px;border-radius:8px;border:1px solid var(--divider-color);background:var(--input-fill-color,var(--secondary-background-color));color:var(--primary-text-color);font:inherit}
    .picker-value{width:100%;height:62px;display:flex;align-items:center;gap:11px;text-align:left;padding:10px 12px;background:var(--secondary-background-color);overflow:hidden}.source-icon{display:grid;place-items:center;width:34px;height:34px;flex:none;color:var(--primary-color)}
    .type-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:12px;margin-bottom:24px}.type-option{display:flex;min-height:128px;align-items:center;justify-content:center;flex-direction:column;gap:8px;background:var(--card-background-color)}.type-option.selected{border:2px solid var(--primary-color);background:color-mix(in srgb,var(--primary-color) 8%,var(--card-background-color))}.add-source{border-top:1px solid var(--divider-color);padding-top:22px}.suggestion-copy{margin:4px 0 18px}
    dialog{width:min(620px,calc(100vw - 32px));max-height:min(720px,calc(100vh - 32px));padding:0;border:1px solid var(--divider-color);border-radius:14px;background:var(--card-background-color);color:var(--primary-text-color);box-shadow:0 14px 45px rgba(0,0,0,.38)}dialog::backdrop{background:rgba(0,0,0,.58)}.dialog-heading{display:flex;justify-content:space-between;align-items:center;padding:18px 18px 8px}.dialog-heading h2{margin:0}.search{padding:8px 16px;margin:0}.picker-results{max-height:min(530px,65vh);overflow:auto;border-top:1px solid var(--divider-color)}.picker-row{width:100%;height:62px;display:flex;align-items:center;gap:11px;text-align:left;border:0;border-bottom:1px solid var(--divider-color);border-radius:0;background:transparent;padding:8px 15px;overflow:hidden}.picker-row.selected{outline:2px solid var(--primary-color);outline-offset:-2px}.picker-row .trailing{flex:0 1 150px;min-width:0;max-width:28%;margin-left:auto;color:var(--secondary-text-color);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:right}.empty-results{padding:22px}.visually-hidden{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
    .error{background:var(--error-color);color:#fff;padding:12px;margin-bottom:16px;border-radius:7px}
    @media(max-width:700px){main{padding:16px 12px 36px}.fields,.fields.two,.fields.three{grid-template-columns:1fr}.type-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.device-icon.hero{width:52px;height:52px}.section-title{align-items:flex-start}.actions{position:sticky;z-index:4;bottom:0;background:var(--primary-background-color);padding:10px 0}.row{padding:12px}.card{padding:14px}.list{padding:0}.picker-results{max-height:60vh}.picker-row .trailing{flex-basis:96px;max-width:24%}}
    @media(max-width:390px){.type-grid{grid-template-columns:1fr 1fr}.type-option{min-height:108px}.device-icon.picker{width:58px;height:58px}.section-title{flex-wrap:wrap}}
  `; }
}
customElements.define("fluks-control-editor-panel", FluksControlEditorPanel);
