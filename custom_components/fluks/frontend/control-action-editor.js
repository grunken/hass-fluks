/* Focused browser-only prototype for ordered fluks control actions.
 *
 * Embedded by the production configuration panel, it performs no network,
 * storage, service, or backend operation. Control persistence and execution
 * remain deliberately outside the current milestone.
 */

const clone = (value) => JSON.parse(JSON.stringify(value));

const actionBinding = (action) => action?.option ?? action?.value;

class FluksControlActionEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._baseline = [];
    this._working = [];
    this.entities = [];
    this.controlName = "Control";
    this.strings = {};
    this._editing = null;
  }

  set actions(value) {
    this._baseline = clone(value ?? []);
    this._working = clone(value ?? []);
    this.render();
  }

  get actions() {
    return clone(this._working);
  }

  connectedCallback() {
    this.render();
  }

  _t(key) { return this.strings[key] ?? key.replaceAll("_", " "); }

  _summary(action) {
    const entityId = action.entityId ?? action.target?.entityId;
    const entity = this.entities.find((item) => item.entityId === entityId);
    const target = entity?.name ?? entityId ?? this._t("choose_entity");
    const operation = action.type === "callService" ? action.service : this._t(action.type);
    const binding = actionBinding(action);
    const detail = binding?.source === "control"
      ? this._t("control_value") : `${this._t("fixed")}: ${binding?.value ?? ""}`;
    return { title: `${operation} · ${target}`, detail };
  }

  render() {
    if (!this.shadowRoot) return;
    this.shadowRoot.innerHTML = `
      <style>
        :host { color: var(--primary-text-color, #e8e8e8); font: 14px system-ui; display: block; }
        .editor { max-width: 720px; margin: 0 auto; }
        h2 { margin: 0 0 4px; font-size: 24px; }
        .intro { color: var(--secondary-text-color, #aaa); margin: 0 0 20px; }
        ol { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
        li { display: grid; grid-template-columns: 36px 1fr auto; gap: 10px; align-items: center;
             border: 1px solid var(--divider-color, #444); border-radius: 12px; padding: 12px; }
        .order { width: 30px; height: 30px; display: grid; place-items: center; border-radius: 50%;
                 background: #1b5e45; color: white; font-weight: 700; }
        .title { font-weight: 650; } .detail { color: var(--secondary-text-color, #aaa); margin-top: 3px; }
        .row-actions { display: flex; gap: 4px; flex-wrap: wrap; justify-content: end; }
        button { border: 0; border-radius: 9px; padding: 9px 12px; cursor: pointer;
                 background: var(--secondary-background-color, #333); color: inherit; }
        button.primary { background: #238b65; color: white; }
        button.danger { color: #ff8a80; }
        button:disabled { opacity: .35; cursor: default; }
        .toolbar, .footer { display: flex; gap: 10px; margin-top: 16px; }
        .footer { justify-content: end; border-top: 1px solid var(--divider-color, #444); padding-top: 16px; }
        dialog { width: min(520px, calc(100vw - 40px)); color: inherit; background: var(--card-background-color, #242424);
                 border: 1px solid var(--divider-color, #444); border-radius: 14px; padding: 20px; }
        dialog::backdrop { background: rgb(0 0 0 / .55); }
        label { display: grid; gap: 6px; margin: 12px 0; font-weight: 600; }
        select, input { box-sizing: border-box; width: 100%; padding: 10px; border-radius: 8px;
                        border: 1px solid var(--divider-color, #555); background: var(--primary-background-color, #111);
                        color: inherit; }
        .error { min-height: 20px; color: #ff8a80; }
        .empty { border: 1px dashed var(--divider-color, #555); border-radius: 12px; padding: 24px;
                 color: var(--secondary-text-color, #aaa); text-align: center; }
      </style>
      <div class="editor">
        <h2>${this.controlName}</h2>
        <p class="intro">${this._t("action_intro")}</p>
        ${this._working.length ? `<ol>${this._working.map((action, index) => this._row(action, index)).join("")}</ol>` : `<div class="empty">${this._t("no_actions")}</div>`}
        <div class="toolbar"><button class="primary" data-command="add">+ ${this._t("add_action")}</button></div>
        <div class="footer"><button data-command="cancel">${this._t("cancel")}</button><button class="primary" data-command="save">${this._t("save_control")}</button></div>
      </div>
      ${this._dialog()}
    `;
    this.shadowRoot.querySelectorAll("button[data-command]").forEach((button) =>
      button.addEventListener("click", () => this._command(button.dataset.command, Number(button.dataset.index)))
    );
    this.shadowRoot.querySelector("#action-type")?.addEventListener("change", () => this._updateDialogFields());
    this.shadowRoot.querySelector("#value-source")?.addEventListener("change", () => this._updateDialogFields());
  }

  _row(action, index) {
    const summary = this._summary(action);
    return `<li><span class="order">${index + 1}</span><div><div class="title">${summary.title}</div><div class="detail">${summary.detail}</div></div>
      <div class="row-actions"><button data-command="up" data-index="${index}" ${index === 0 ? "disabled" : ""}>↑ ${this._t("up")}</button>
      <button data-command="down" data-index="${index}" ${index === this._working.length - 1 ? "disabled" : ""}>↓ ${this._t("down")}</button>
      <button data-command="edit" data-index="${index}">${this._t("edit")}</button><button class="danger" data-command="remove" data-index="${index}">${this._t("remove")}</button></div></li>`;
  }

  _dialog() {
    const action = this._editing === null ? null : this._working[this._editing];
    const type = action?.type ?? "selectOption";
    const binding = actionBinding(action) ?? { source: "fixed", value: "" };
    return `<dialog id="action-dialog"><h3>${this._t(action ? "edit_action" : "add_action")}</h3>
      <label>${this._t("action_type")}<select id="action-type"><option value="selectOption" ${type === "selectOption" ? "selected" : ""}>${this._t("selectOption")}</option>
      <option value="setNumber" ${type === "setNumber" ? "selected" : ""}>${this._t("setNumber")}</option>
      <option value="callService" ${type === "callService" ? "selected" : ""}>${this._t("water_heater_temperature")}</option></select></label>
      <div id="conditional-fields">${this._conditionalFields(type, action, binding)}</div>
      <div class="error" id="action-error"></div>
      <div class="footer"><button data-command="close-dialog">${this._t("cancel")}</button><button class="primary" data-command="commit-action">${this._t(action ? "save_action" : "add_action")}</button></div></dialog>`;
  }

  _conditionalFields(type, action, binding) {
    const domains = type === "selectOption" ? ["select"] : type === "setNumber" ? ["number"] : ["water_heater"];
    const currentEntity = action?.entityId ?? action?.target?.entityId ?? "";
    const entities = this.entities.filter((entity) => domains.includes(entity.domain));
    const entityOptions = [`<option value="">${this._t("choose_entity")}</option>`, ...entities.map((entity) => `<option value="${entity.entityId}" ${entity.entityId === currentEntity ? "selected" : ""}>${entity.name}</option>`)].join("");
    const source = binding.source ?? "fixed";
    const service = type === "callService" ? `<label>${this._t("ha_action")}<input value="water_heater.set_temperature" disabled></label><label>${this._t("parameter")}<input value="temperature" disabled></label>` : "";
    return `${service}<label>${this._t("entity")}<select id="action-entity">${entityOptions}</select></label>
      <label>${this._t("value_source")}<select id="value-source"><option value="control" ${source === "control" ? "selected" : ""}>${this._t("control_value")}</option><option value="fixed" ${source === "fixed" ? "selected" : ""}>${this._t("fixed_value")}</option></select></label>
      ${source === "fixed" ? `<label>${this._t("fixed_value")}<input id="fixed-value" type="${type === "selectOption" ? "text" : "number"}" value="${binding.value ?? ""}" placeholder="${type === "selectOption" ? "eco_charge" : "50"}"></label>` : ""}`;
  }

  _updateDialogFields() {
    const type = this.shadowRoot.querySelector("#action-type").value;
    const source = this.shadowRoot.querySelector("#value-source")?.value ?? "fixed";
    this.shadowRoot.querySelector("#conditional-fields").innerHTML = this._conditionalFields(type, null, { source });
    this.shadowRoot.querySelector("#value-source").addEventListener("change", () => this._updateDialogFields());
  }

  _command(command, index) {
    if (command === "up" || command === "down") {
      const target = command === "up" ? index - 1 : index + 1;
      const [action] = this._working.splice(index, 1); this._working.splice(target, 0, action); this.render(); return;
    }
    if (command === "remove") { this._working.splice(index, 1); this.render(); return; }
    if (command === "add" || command === "edit") {
      this._editing = command === "edit" ? index : null; this.render(); this.shadowRoot.querySelector("#action-dialog").showModal(); return;
    }
    if (command === "close-dialog") { this.shadowRoot.querySelector("#action-dialog").close(); return; }
    if (command === "commit-action") { this._commitAction(); return; }
    if (command === "cancel") {
      this._working = clone(this._baseline); this.render(); this.dispatchEvent(new CustomEvent("control-cancelled")); return;
    }
    if (command === "save") {
      if (!this._working.length) return;
      this._baseline = clone(this._working);
      this.dispatchEvent(new CustomEvent("control-saved", { detail: { actions: clone(this._baseline) } }));
    }
  }

  _commitAction() {
    const type = this.shadowRoot.querySelector("#action-type").value;
    const entityId = this.shadowRoot.querySelector("#action-entity").value;
    const source = this.shadowRoot.querySelector("#value-source").value;
    const fixedInput = this.shadowRoot.querySelector("#fixed-value");
    const error = this.shadowRoot.querySelector("#action-error");
    if (!entityId) { error.textContent = this._t("choose_entity_error"); return; }
    if (source === "fixed" && !fixedInput?.value) { error.textContent = this._t("fixed_value_error"); return; }
    const numeric = type !== "selectOption";
    const binding = source === "control" ? { source: "control" } : { source: "fixed", value: numeric ? Number(fixedInput.value) : fixedInput.value };
    const action = type === "selectOption"
      ? { type, entityId, option: binding }
      : type === "setNumber"
        ? { type, entityId, value: binding }
        : { type, service: "water_heater.set_temperature", target: { entityId }, parameter: "temperature", value: binding };
    if (this._editing === null) this._working.push(action); else this._working[this._editing] = action;
    this._editing = null; this.render();
  }
}

customElements.define("fluks-control-action-editor", FluksControlActionEditor);
