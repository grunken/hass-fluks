/* Metadata-driven editor for persisted ordered fluks control actions.
 * It never calls services or backends; the parent owns explicit persistence.
 */
const clone = (value) => JSON.parse(JSON.stringify(value));
const esc = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const searchText = (value) => String(value ?? "").toLocaleLowerCase().replace(/[._-]+/g, " ").replace(/\s+/g, " ").trim();

export class FluksControlActionEditor extends HTMLElement {
  constructor() {
    super(); this.attachShadow({ mode: "open" });
    this._baseline = []; this._working = []; this.capabilities = [];
    this._behaviorMode = false; this._baselineBehaviors = []; this._workingBehaviors = [];
    this.allowedModes = []; this._activeMode = null; this._activeBehaviorIndex = null; this._addingBehavior = false;
    this.behaviorChoices = [];
    this._outputSuggestions = {};
    this._outputSuggestionsLoading = false;
    this.referenceEntities = [];
    this.selectedDeviceId = null; this.entityDeviceIds = {};
    this.valueType = { datatype: "number", unit: null };
    this.controlName = "Control"; this.strings = {}; this._editing = null;
    this._dialogDraft = null; this._transformEditing = null; this._transformTypeDraft = null; this._transformOpen = new Set(); this._omittedFields = new Set();
  }
  set actions(value) { this._baseline = clone(value ?? []); this._working = clone(value ?? []); this.render(); }
  get actions() { return clone(this._working); }
  set behaviors(value) {
    this._behaviorMode = true;
    const supplied = clone(value ?? []);
    if (!supplied.some((item) => item.mode === null)) supplied.unshift({ mode: null, actions: [] });
    this._baselineBehaviors = supplied; this._workingBehaviors = clone(supplied); this.render();
  }
  get behaviors() { return clone(this._workingBehaviors); }
  set outputSuggestions(value) { this._outputSuggestions = clone(value ?? {}); this.render(); }
  set outputSuggestionsLoading(value) { this._outputSuggestionsLoading = Boolean(value); this.render(); }
  connectedCallback() { this.render(); }
  _t(key) { return this.strings[key] ?? key.replaceAll("_", " "); }
  _cap(service) { return this.capabilities.find((item) => item.service === service); }
  _entity(capability, entityId) { return capability?.entities?.find((item) => item.entity_id === entityId); }
  _allEntities() {
    const entities = new Map();
    for (const capability of this.capabilities) {
      if (!this._isExecutableCapability(capability)) continue;
      for (const entity of capability.entities ?? []) if (!entities.has(entity.entity_id)) entities.set(entity.entity_id, entity);
    }
    const selectedDevice = this.selectedDeviceId;
    return [...entities.values()].sort((left, right) => {
      if (selectedDevice) {
        const related = Number(this._entityDeviceId(right) === selectedDevice) - Number(this._entityDeviceId(left) === selectedDevice);
        if (related) return related;
      }
      return left.name.localeCompare(right.name, undefined, { sensitivity: "base" });
    });
  }
  _entityDeviceId(entity) { return entity.device_id ?? this.entityDeviceIds?.[entity.entity_id]; }
  _isExecutableCapability(capability) { return typeof capability?.service === "string" && capability.service.includes("."); }
  _entitySearchText(entity) {
    const values = [entity.entity_id, entity.name, entity.metadata];
    if (Array.isArray(entity.attributes)) values.push(...entity.attributes.flatMap((attribute) => typeof attribute === "string" ? [attribute] : [attribute?.name, attribute?.id, attribute?.unit]));
    else if (entity.attributes && typeof entity.attributes === "object") values.push(...Object.keys(entity.attributes));
    for (const capability of this._capsForEntity(entity.entity_id)) {
      values.push(capability.service, capability.name, capability.description);
      const capabilityEntity = this._entity(capability, entity.entity_id);
      for (const field of capabilityEntity?.fields ?? []) {
        values.push(field.id, field.name, field.description, field.selector?.type, field.selector?.attribute, ...Object.keys(field.constraints ?? {}));
        const options = field.constraints?.options ?? field.selector?.options;
        if (Array.isArray(options)) values.push(...options);
      }
    }
    return values.filter((value) => value !== undefined && value !== null && value !== "").join(" ");
  }
  _capsForEntity(entityId) { return this.capabilities.filter((capability) => this._isExecutableCapability(capability) && this._entity(capability, entityId)); }
  _selectEntity(entityId) {
    const service = this._entity(this._cap(this._dialogDraft.service), entityId) ? this._dialogDraft.service : "";
    this._dialogDraft = { type: "serviceCall", service, target: { entityId }, data: {} };
    this._omittedFields.clear();
  }
  _summary(action) {
    const capability = this._cap(action.service); const entity = this._entity(capability, action.target?.entityId);
    const bindings = Object.values(action.data ?? {});
    const details = bindings.map((value) => value?.kind === "requestedValue"
      ? this._t("uses_control_value")
      : value?.kind === "literal" ? this._t("uses_fixed_value") : null).filter(Boolean);
    return {
      title: `${capability?.name ?? action.service} · ${entity?.name ?? action.target?.entityId ?? this._t("choose_entity")}`,
      detail: details.length ? details.join(" · ") : this._t("target_only"),
    };
  }
  render() {
    const content = this._behaviorMode ? this._renderBehaviors() : `${this._working.length ? `<ol>${this._working.map((action, index) => this._row(action, index, this._working.length)).join("")}</ol>` : `<div class="empty">${esc(this._t("no_actions"))}</div>`}<div class="toolbar"><button class="primary" data-command="add">+ ${esc(this._t("add_action"))}</button></div>`;
    this.shadowRoot.innerHTML = `<style>${this._styles()}</style><div class="editor"><h2>${esc(this.controlName)}</h2><p class="intro">${esc(this._t("action_intro"))}</p>${content}
      ${this._saveError ? `<p class="error" role="alert">${esc(this._saveError)}</p>` : ""}<div class="footer"><button data-command="cancel">${esc(this._t("cancel"))}</button><button class="primary" data-command="save">${esc(this._t("save_control"))}</button></div></div>${this._dialog()}${this._behaviorDialog()}`;
    this._wire();
  }
  _styles() { return `:host{color:var(--primary-text-color,#e8e8e8);font:14px system-ui;display:block}.editor{max-width:760px;margin:auto}h2{margin:0 0 4px;font-size:24px}.intro,.detail,.help,.behavior-description,.empty-behavior,.action-sequence-help,.manual-behavior-help,.suggestion-intro,.output-guidance p{color:var(--secondary-text-color,#aaa)}.behavior{padding:18px 0;border-top:1px solid var(--divider-color,#444)}.behavior:first-of-type{border-top:0}.behavior-heading h3{margin:0 0 4px;font-size:18px}.behavior-description{margin:0 0 12px}.action-sequence-help,.manual-behavior-help{margin:8px 0;font-size:13px}.empty-behavior{margin:8px 0 0}ol{list-style:none;margin:0;padding:0;display:grid;gap:10px}li{display:grid;grid-template-columns:36px minmax(0,1fr) auto;gap:10px;align-items:center;border:1px solid var(--divider-color,#444);border-radius:12px;padding:12px}.order,.sequence-number{width:30px;height:30px;display:grid;place-items:center;border-radius:50%;background:var(--primary-color,#1b5e45);color:var(--text-primary-color,#fff);font-weight:700}.title,.identity strong{font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.row-actions,.toolbar,.footer{display:flex;gap:6px}.row-actions{justify-content:end;flex-wrap:wrap}.toolbar{margin-top:12px;align-items:center}.toolbar .remove-behavior{margin-left:auto}.footer{display:flex;justify-content:end;border-top:1px solid var(--divider-color,#444);padding-top:16px;margin-top:16px}button{border:0;border-radius:9px;padding:9px 12px;cursor:pointer;background:var(--secondary-background-color,#333);color:inherit}button.primary{background:#238b65;color:#fff}button.danger,.error,.required{color:#ff8a80}button:disabled{opacity:.35}dialog{width:min(600px,calc(100vw - 32px));max-height:calc(100vh - 32px);overflow:auto;box-sizing:border-box;color:inherit;background:var(--card-background-color,#242424);border:1px solid var(--divider-color,#444);border-radius:14px;padding:20px}dialog::backdrop{background:rgb(0 0 0/.55)}label{display:grid;gap:6px;margin:12px 0;font-weight:600}input,select{box-sizing:border-box;width:100%;min-width:0;max-width:100%;padding:10px 1.5rem 10px 10px;border-radius:8px;border:1px solid var(--divider-color,#555);background:var(--primary-background-color,#111);color:inherit}.choices{max-height:222px;overflow:auto;border:1px solid var(--divider-color,#555);border-radius:9px}.choice{width:100%;height:62px;border-radius:0;display:grid;grid-template-columns:minmax(0,1fr);gap:2px;text-align:left;border-bottom:1px solid var(--divider-color,#444)}.choice.entity-choice{height:74px}.behavior-choice{height:auto;min-height:72px;padding:12px}.choice[hidden]{display:none}.choice:last-child{border-bottom:0}.choice span,.choice small{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.behavior-choice small{white-space:normal}.choice small{color:var(--secondary-text-color,#aaa)}.choice .entity-context{font-size:12px}.field{padding-top:8px;border-top:1px solid var(--divider-color,#444)}.field-heading{display:flex;gap:8px;align-items:baseline}.error{min-height:20px}.empty{border:1px dashed var(--divider-color,#555);border-radius:12px;padding:24px;text-align:center;color:var(--secondary-text-color,#aaa)}.output-suggestions,.output-guidance{padding:16px 0;border-top:1px solid var(--divider-color,#444);min-height:92px}.output-suggestions-loading{display:flex;align-items:center;gap:14px}.output-suggestions-loading strong{display:block;margin-bottom:3px}.output-suggestions-loading p{margin:0;color:var(--secondary-text-color,#aaa)}.loading-spinner{width:24px;height:24px;flex:none;border:3px solid color-mix(in srgb,var(--primary-color,#03a678) 25%,transparent);border-top-color:var(--primary-color,#03a678);border-radius:50%;animation:fluks-spin .8s linear infinite}@keyframes fluks-spin{to{transform:rotate(360deg)}}@keyframes fluks-suggestion-in{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}.output-suggestions-ready{animation:fluks-suggestion-in .2s ease-out}.output-suggestions-ready h3,.output-guidance h3{margin:0 0 6px}.suggestion-intro{margin:0 0 10px}.output-suggestion{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;padding:12px;border:1px solid var(--divider-color,#444);border-radius:9px;margin-top:8px;background:var(--card-background-color,#242424)}.suggestion-copy{display:grid;gap:6px;min-width:0}.suggestion-copy strong{font-size:15px}.suggestion-copy p{margin:0;color:var(--secondary-text-color,#aaa)}.action-sequence,.suggestion-action-sequence{list-style:none;margin:8px 0 0;padding:0;display:grid;gap:6px}.action-sequence li,.suggestion-action-sequence li{display:flex;grid-template-columns:none;align-items:center;gap:8px;border:0;border-radius:0;padding:0}.suggestion-action-sequence li{font-size:13px}.sequence-number{width:22px;height:22px;flex:none;font-size:12px}.transforms{margin-top:12px;padding:12px;border-radius:10px;background:var(--secondary-background-color,#333)}.transforms summary{cursor:pointer;display:flex;justify-content:space-between;gap:10px}.adjustment-status{color:var(--secondary-text-color,#aaa);font-weight:400}.transform-content{margin-top:10px}.transforms li{grid-template-columns:30px minmax(0,1fr) auto;padding:8px}.transform-copy{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}@media(prefers-reduced-motion:reduce){.loading-spinner{animation:none}.output-suggestions-ready{animation:none}}@media(max-width:600px){li,.transforms li{grid-template-columns:30px minmax(0,1fr)}.row-actions{grid-column:2;justify-content:start}.toolbar{flex-wrap:wrap}.toolbar .remove-behavior{margin-left:0}.output-suggestion{flex-direction:column}.output-suggestion button{align-self:flex-start}}`; }
  _modeKey(mode) { return mode ?? ""; }
  _behaviorChoice(mode, valueCondition = null) {
    return this.behaviorChoices.find((choice) => choice.mode === mode && choice.valueCondition === valueCondition);
  }
  _behaviorLabel(mode, valueCondition = null) {
    const label = this.controlName.charAt(0).toLocaleLowerCase() + this.controlName.slice(1);
    const choice = this._behaviorChoice(mode, valueCondition);
    return this._t(mode === null ? "default_behavior" : choice?.labelKey ?? `behavior_${mode}`).replace("{controlName}", label);
  }
  _behaviorDescription(mode, valueCondition = null) {
    const label = this.controlName.charAt(0).toLocaleLowerCase() + this.controlName.slice(1);
    const choice = this._behaviorChoice(mode, valueCondition);
    return this._t(mode === null ? "default_behavior_description" : choice?.descriptionKey ?? `behavior_${mode}_description`).replace("{controlName}", label);
  }
  _behaviorChoiceLabel(choice) {
    const label = this.controlName.charAt(0).toLocaleLowerCase() + this.controlName.slice(1);
    const mode = typeof choice === "string" ? choice : choice.mode;
    const key = typeof choice === "string" ? `behavior_${mode}_choice` : choice.labelKey;
    return this._t(key).replace("{controlName}", label);
  }
  _behaviorChoiceDescription(choice) {
    const label = this.controlName.charAt(0).toLocaleLowerCase() + this.controlName.slice(1);
    const mode = typeof choice === "string" ? choice : choice.mode;
    const key = typeof choice === "string" ? `behavior_${mode}_description` : choice.descriptionKey;
    return this._t(key).replace("{controlName}", label);
  }
  _actionsFor(mode, behaviorIndex = null) { return this._workingBehaviors[behaviorIndex ?? this._workingBehaviors.findIndex((item) => item.mode === mode)]?.actions ?? []; }
  _behaviorChoiceIdentity(choice) { return `${choice.mode ?? ""}|${choice.valueCondition ?? ""}`; }
  _availableBehaviorChoices() {
    const choices = this.behaviorChoices.length
      ? [...this.allowedModes.filter((mode) => mode !== null && mode !== "balance").map((mode) => ({ mode, valueCondition: null, labelKey: `behavior_${mode}_choice`, descriptionKey: `behavior_${mode}_description` })), ...this.behaviorChoices.filter((choice) => this.allowedModes.includes(choice.mode))]
      : this.allowedModes.filter((mode) => mode !== null).map((mode) => ({ mode, valueCondition: null, labelKey: `behavior_${mode}_choice`, descriptionKey: `behavior_${mode}_description` }));
    return choices.filter((choice, index, all) => all.findIndex((item) => this._behaviorChoiceIdentity(item) === this._behaviorChoiceIdentity(choice)) === index
      && !this._workingBehaviors.some((item) => this._behaviorChoiceIdentity(item) === this._behaviorChoiceIdentity(choice)));
  }
  _outputSuggestionEntries() {
    const configured = new Set(
      this._workingBehaviors
        .filter((item) => item.actions.length)
        .map((item) => `${item.mode ?? ""}|${item.valueCondition ?? ""}`),
    );
    return Object.entries(this._outputSuggestions)
      .map(([behavior, suggestion]) => ({ behavior, ...suggestion, label: suggestion.label ?? behavior }))
      .filter((suggestion) => suggestion.configuration?.actions?.length)
      .filter((suggestion) => !configured.has(`${suggestion.mode ?? ""}|${suggestion.valueCondition ?? ""}`));
  }
  _actionText(action) {
    const summary = this._summary(action);
    return `${summary.title}${summary.detail ? ` — ${summary.detail}` : ""}`;
  }
  _renderActionSequence(actions, className = "action-sequence") {
    return `<ol class="${className}">${actions.map((action, index) => `<li><span class="sequence-number">${index + 1}</span><span>${esc(this._actionText(action))}</span></li>`).join("")}</ol>`;
  }
  _renderOutputSuggestions() {
    const entries = this._outputSuggestionEntries();
    if (this._outputSuggestionsLoading) return `<section class="output-suggestions output-suggestions-loading" role="status" aria-live="polite"><span class="loading-spinner" aria-hidden="true"></span><div><strong>${esc(this._t("finding_best_match"))}</strong><p>${esc(this._t("finding_best_match_description"))}</p></div></section>`;
    if (!entries.length) return `<section class="output-guidance"><h3>${esc(this._t("manual_action_title"))}</h3><p>${esc(this._t("manual_action_description"))}</p></section>`;
    return `<section class="output-suggestions output-suggestions-ready"><h3>${esc(this._t("suggested_match"))}</h3><p class="suggestion-intro">${esc(this._t("suggested_action_intro"))}</p>${entries.map((suggestion, index) => `<div class="output-suggestion"><div class="suggestion-copy"><strong>${esc(suggestion.label)}</strong>${suggestion.configuration?.actions?.length ? this._renderActionSequence(suggestion.configuration.actions, "suggestion-action-sequence") : ""}<p>${esc(suggestion.explanation || this._t("suggested_action_reason"))}</p></div><button type="button" data-output-suggestion="${index}">${esc(this._t("use_suggestion"))}</button></div>`).join("")}</section>`;
  }
  _renderBehaviors() {
    const sections = this._workingBehaviors.map(({ mode, actions, valueCondition }, behaviorIndex) => `<section class="behavior"><div class="behavior-heading"><h3>${esc(this._behaviorLabel(mode, valueCondition ?? null))}</h3></div><p class="behavior-description">${esc(this._behaviorDescription(mode, valueCondition ?? null))}</p>${actions.length ? `<p class="action-sequence-help">${esc(this._t("action_sequence_help"))}</p><ol>${actions.map((action, index) => this._row(action, index, actions.length, mode, behaviorIndex)).join("")}</ol>` : `<p class="empty-behavior">${esc(this._t("no_actions"))}</p><p class="manual-behavior-help">${esc(this._t("manual_behavior_help"))}</p>`}<div class="toolbar"><button class="primary" data-command="add" data-mode="${esc(this._modeKey(mode))}" data-behavior-index="${behaviorIndex}">+ ${esc(this._t("add_action"))}</button>${mode === null ? "" : `<button class="danger remove-behavior" data-command="remove-behavior" data-mode="${esc(mode)}" data-behavior-index="${behaviorIndex}">${esc(this._t("remove_behavior"))}</button>`}</div></section>`).join("");
    const remaining = this._availableBehaviorChoices();
    const add = !remaining.length ? "" : `<div class="toolbar"><button data-command="add-behavior">+ ${esc(this._t("add_behavior"))}</button></div>`;
    return sections + this._renderOutputSuggestions() + add;
  }
  _behaviorDialog() {
    if (!this._behaviorMode || !this._addingBehavior) return "";
    const remaining = this._availableBehaviorChoices();
    return `<dialog id="behavior-dialog"><h3>${esc(this._t("choose_behavior"))}</h3><p class="help">${esc(this._t("choose_behavior_help"))}</p><div class="choices">${remaining.map((choice) => `<button type="button" class="choice behavior-choice" data-behavior-choice="${esc(choice.mode)}" data-value-condition="${esc(choice.valueCondition ?? "")}"><span>${esc(this._behaviorChoiceLabel(choice))}</span><small>${esc(this._behaviorChoiceDescription(choice))}</small></button>`).join("")}</div><div class="footer"><button data-command="cancel-behavior">${esc(this._t("cancel"))}</button></div></dialog>`;
  }
  _row(action, index, total, mode = null, behaviorIndex = null) { const summary = this._summary(action); const modeAttr = this._behaviorMode ? ` data-mode="${esc(this._modeKey(mode))}" data-behavior-index="${behaviorIndex}"` : ""; return `<li><span class="order">${index + 1}</span><div class="identity"><div class="title" title="${esc(summary.title)}">${esc(summary.title)}</div><div class="detail">${esc(summary.detail)}</div></div><div class="row-actions"><button data-command="up" data-index="${index}"${modeAttr} ${index === 0 ? "disabled" : ""}>↑ ${esc(this._t("up"))}</button><button data-command="down" data-index="${index}"${modeAttr} ${index === total - 1 ? "disabled" : ""}>↓ ${esc(this._t("down"))}</button><button data-command="edit" data-index="${index}"${modeAttr}>${esc(this._t("edit"))}</button><button class="danger" data-command="remove" data-index="${index}"${modeAttr}>${esc(this._t("remove"))}</button></div></li>`; }
  _dialog() {
    const draft = this._dialogDraft ?? { type: "serviceCall", service: "", target: { entityId: "" }, data: {} };
    const entities = this._allEntities(); const selectedEntity = entities.find((item) => item.entity_id === draft.target?.entityId); const compatible = selectedEntity ? this._capsForEntity(selectedEntity.entity_id) : [];
    const capability = compatible.find((item) => item.service === draft.service); const entity = this._entity(capability, selectedEntity?.entity_id);
    const entityOptions = entities.map((item) => `<button type="button" class="choice entity-choice" data-entity-choice="${esc(item.entity_id)}" data-search="${esc(this._entitySearchText(item))}"><span title="${esc(item.name)}">${esc(item.name)}</span><small title="${esc(item.entity_id)}">${esc(item.entity_id)}</small>${item.metadata ? `<small class="entity-context" title="${esc(item.metadata)}">${esc(item.metadata)}</small>` : ""}</button>`).join("");
    const actionOptions = compatible.map((item) => `<button type="button" class="choice" data-action-choice="${esc(item.service)}"><span>${esc(item.name)}</span><small title="${esc(item.service)}">${esc(item.service)}</small></button>`).join("");
    const staleEntity = draft.target?.entityId && !selectedEntity; const staleAction = selectedEntity && draft.service && !capability;
    const defaultField = this._editing === null ? this._defaultRequestedField(entity?.fields ?? []) : null;
    return `<dialog id="action-dialog"><h3>${esc(this._t(this._editing === null ? "add_action" : "edit_action"))}</h3><label>${esc(this._t("entity"))}<input id="entity-search" type="search" value="${esc(selectedEntity?.name ?? draft.target?.entityId ?? "")}" placeholder="${esc(this._t("search_entities"))}"></label><div class="choices" data-choices="entity">${entityOptions}</div>${selectedEntity ? `<button type="button" data-clear-entity>${esc(this._t("clear_selection"))}</button><label>${esc(this._t("action"))}<input id="action-search" type="search" value="${esc(capability?.name ?? draft.service)}" placeholder="${esc(this._t("search_actions"))}"></label><div class="choices" data-choices="action">${actionOptions}</div>` : ""}${capability ? `<p class="help">${esc(capability.description)}</p>` : ""}${entity ? `<section><h4>${esc(this._t("action_fields"))}</h4>${entity.fields.map((field) => this._field(field, draft.data?.[field.id], field === defaultField && !this._omittedFields.has(field.id))).join("")}</section>` : ""}<div class="error" id="action-error">${staleEntity ? esc(this._t("choose_entity_error")) : staleAction ? esc(this._t("choose_action_error")) : ""}</div><div class="footer"><button data-command="close-dialog">${esc(this._t("cancel"))}</button><button class="primary" data-command="commit-action">${esc(this._t(this._editing === null ? "add_action" : "save_action"))}</button></div></dialog>`;
  }
  _defaultRequestedField(fields) { return fields.find((field) => field.required && field.selector?.type === "number") ?? fields.find((field) => field.selector?.type === "number"); }
  _field(field, binding, defaultRequested = false) { const source = binding?.kind ?? (defaultRequested ? "requestedValue" : field.required ? "literal" : "omit"); const selector = { ...field.selector, ...(field.constraints ?? {}) }; return `<div class="field" data-field="${esc(field.id)}"><div class="field-heading"><strong>${esc(field.name)}</strong>${field.required ? `<span class="required">${esc(this._t("required"))}</span>` : ""}</div>${field.description ? `<p class="help">${esc(field.description)}</p>` : ""}<label>${esc(this._t("value_source"))}<select data-source="${esc(field.id)}"><option value="literal" ${source === "literal" ? "selected" : ""}>${esc(this._t("fixed_value"))}</option><option value="requestedValue" ${source === "requestedValue" ? "selected" : ""}>${esc(this._t("control_value"))}</option>${field.required ? "" : `<option value="omit" ${source === "omit" ? "selected" : ""}>${esc(this._t("not_configured"))}</option>`}</select></label>${source === "requestedValue" ? this._transformEditor(field.id, binding?.transforms ?? []) : source === "literal" ? this._literal(field, selector, binding?.value ?? field.default ?? "") : ""}</div>`; }
  _literal(field, selector, value) {
    if (selector.type === "select") return `<label>${esc(this._t("fixed_value"))}<select data-literal="${esc(field.id)}"><option value=""></option>${(selector.options ?? []).map((item) => `<option value="${esc(JSON.stringify(item))}" ${Object.is(item, value) ? "selected" : ""}>${esc(selector.option_labels?.[String(item)] ?? item)}</option>`).join("")}</select></label>`;
    if (selector.type === "boolean") return `<label>${esc(this._t("fixed_value"))}<select data-literal="${esc(field.id)}"><option value="true" ${value === true ? "selected" : ""}>${esc(this._t("yes"))}</option><option value="false" ${value === false ? "selected" : ""}>${esc(this._t("no"))}</option></select></label>`;
    if (selector.type === "number") return `<label>${esc(this._t("fixed_value"))}<input data-literal="${esc(field.id)}" type="number" value="${esc(value)}" ${selector.min !== undefined ? `min="${esc(selector.min)}"` : ""} ${selector.max !== undefined ? `max="${esc(selector.max)}"` : ""} ${selector.step !== undefined ? `step="${esc(selector.step)}"` : `step="any"`}></label>`;
    return `<label>${esc(this._t("fixed_value"))}<input data-literal="${esc(field.id)}" type="text" value="${esc(value)}"></label>`;
  }
  _valueDatatype(value) { return value === null ? "null" : typeof value; }
  _pipeline(transforms, count = transforms.length) {
    let datatype = this.valueType?.datatype; let unit = this.valueType?.unit ?? null;
    for (let index = 0; index < count; index += 1) {
      const transform = transforms[index]; const numeric = ["difference", "round", "clamp", "invert", "scale", "offset", "nearest"];
      if (![...numeric, "powerToCurrent", "valueMap"].includes(transform.type)) return { valid: false, index, transform, datatype, unit };
      if (numeric.includes(transform.type) && datatype !== "number") return { valid: false, index, transform, datatype, unit };
      if (transform.type === "difference" && (!transform.reference?.entityId || typeof transform.reference.entityId !== "string")) return { valid: false, index, transform, datatype, unit };
      if (transform.type === "powerToCurrent") {
        if (datatype !== "number" || unit !== "W") return { valid: false, index, transform, datatype, unit };
        unit = "A";
      } else if (transform.type === "valueMap") {
        const values = transform.values ?? []; const inputTypes = new Set(values.map((item) => this._valueDatatype(item.from))); const outputTypes = new Set(values.map((item) => this._valueDatatype(item.to)));
        if (!values.length || inputTypes.size !== 1 || !inputTypes.has(datatype) || outputTypes.size !== 1) return { valid: false, index, transform, datatype, unit };
        const nextDatatype = [...outputTypes][0]; if (nextDatatype !== datatype) unit = null; datatype = nextDatatype;
      }
    }
    return { valid: true, datatype, unit };
  }
  _availableTransforms(transforms, editingIndex = null) {
    const state = this._pipeline(transforms, editingIndex ?? transforms.length); if (!state.valid) return [];
    const available = ["number", "string", "boolean"].includes(state.datatype) ? ["valueMap"] : [];
    if (state.datatype === "number") available.unshift("difference", "round", "clamp", "invert", "scale", "offset", "nearest");
    if (state.datatype === "number" && state.unit === "W") available.unshift("powerToCurrent");
    return available;
  }
  _pipelineMessage(result) { return `${this._t("invalid_pipeline")} ${result.index + 1}: ${this._t(result.transform?.type === "powerToCurrent" ? "power_to_current" : result.transform?.type ?? "invalid_adjustment")} (${result.datatype}${result.unit ? ` / ${result.unit}` : ""})`; }
  _configurationPipelineError(actions = this._behaviorMode ? this._workingBehaviors.flatMap((item) => item.actions) : this._working) {
    for (const action of actions) for (const binding of Object.values(action.data ?? {})) if (binding?.kind === "requestedValue") { const result = this._pipeline(binding.transforms ?? []); if (!result.valid) return this._pipelineMessage(result); }
    return "";
  }
  _transformSummary(transform) { if (transform.type === "powerToCurrent") return `${this._t("power_to_current")} · ${transform.phases} × ${transform.voltage} V`; if (transform.type === "difference") return `${this._t("difference")} · ${transform.reference?.entityId ?? ""}`; if (transform.type === "round") return `${this._t("round")} · ${transform.decimals}`; if (transform.type === "clamp") return `${this._t("clamp")} · ${transform.min}…${transform.max}`; if (transform.type === "nearest") return `${this._t("nearest")} · ${transform.values.join(", ")}`; if (transform.type === "valueMap") return `${this._t("value_map")} · ${transform.values.length}`; if (transform.type === "scale") return `${this._t("scale")} · × ${transform.factor}`; if (transform.type === "offset") return `${this._t("offset")} · ${transform.amount}`; return this._t("invert"); }
  _transformEditor(fieldId, transforms) {
    const editIndex = this._transformEditing?.field === fieldId ? this._transformEditing.index : null;
    const editing = editIndex === null ? null : transforms[editIndex];
    const available = this._availableTransforms(transforms, editIndex);
    const selected = this._transformTypeDraft?.field === fieldId ? this._transformTypeDraft.type : editing?.type ?? available[0];
    const firstMap = editing?.values?.[0]; const fromType = firstMap === undefined ? "number" : typeof firstMap.from; const toType = firstMap?.to === null ? "null" : firstMap === undefined ? "number" : typeof firstMap.to;
    const labels = { powerToCurrent: "power_to_current", difference: "difference", round: "round", clamp: "clamp", nearest: "nearest", valueMap: "value_map", invert: "invert", scale: "scale", offset: "offset" };
    const options = [...new Set([...(selected ? [selected] : []), ...available])].map((type) => `<option value="${type}" ${selected === type ? "selected" : ""}>${esc(this._t(labels[type]))}</option>`).join("");
    const typeOptions = (selectedType, prefix) => ["number", "string", "boolean", ...(prefix === "to" ? ["null"] : [])].map((type) => `<option value="${type}" ${selectedType === type ? "selected" : ""}>${esc(this._t(`${type === "string" ? "text" : type}_type`))}</option>`).join("");
    const rows = transforms.map((transform, index) => `<li><span class="order">${index + 1}</span><span class="transform-copy">${esc(this._transformSummary(transform))}</span><span class="row-actions"><button type="button" data-transform-command="up" data-field-id="${esc(fieldId)}" data-index="${index}" ${index === 0 ? "disabled" : ""}>↑</button><button type="button" data-transform-command="down" data-field-id="${esc(fieldId)}" data-index="${index}" ${index === transforms.length - 1 ? "disabled" : ""}>↓</button><button type="button" data-transform-command="edit" data-field-id="${esc(fieldId)}" data-index="${index}">${esc(this._t("edit"))}</button><button type="button" class="danger" data-transform-command="remove" data-field-id="${esc(fieldId)}" data-index="${index}">${esc(this._t("remove"))}</button></span></li>`).join("");
    const pipeline = this._pipeline(transforms); const pipelineError = pipeline.valid ? "" : this._pipelineMessage(pipeline);
    const references = [...this.referenceEntities];
    if (editing?.reference?.entityId && !references.some((item) => item.entity_id === editing.reference.entityId)) references.push({ entity_id: editing.reference.entityId, name: editing.reference.entityId, attributes: [] });
    const selectedReferenceId = editing?.reference?.entityId ?? references[0]?.entity_id ?? "";
    const referenceEntity = references.find((item) => item.entity_id === selectedReferenceId);
    const referenceAttributes = referenceEntity?.attributes ?? [];
    const referenceFields = selected === "difference"
      ? `<label>${esc(this._t("reference_entity"))}<select data-transform-reference-entity>${references.map((item) => `<option value="${esc(item.entity_id)}" ${item.entity_id === selectedReferenceId ? "selected" : ""}>${esc(item.name)} · ${esc(item.entity_id)}</option>`).join("")}</select></label><label>${esc(this._t("reference_value"))}<select data-transform-reference-attribute><option value="" ${!editing?.reference?.attribute ? "selected" : ""}>${esc(this._t("entity_state"))}</option>${referenceAttributes.map((attribute) => `<option value="${esc(attribute)}" ${attribute === editing?.reference?.attribute ? "selected" : ""}>${esc(attribute)}</option>`).join("")}</select></label>`
      : `<div class="transform-parameters"><select data-transform-from-type>${typeOptions(fromType, "from")}</select><input data-transform-input="first" value="${esc(editing?.decimals ?? editing?.min ?? editing?.phases ?? editing?.factor ?? editing?.amount ?? editing?.values?.map((v) => v.from ?? v).join(", ") ?? "")}" placeholder="${esc(this._t(selected === "round" ? "decimals" : selected === "clamp" ? "minimum" : "from_values"))}"><select data-transform-to-type>${typeOptions(toType, "to")}</select><input data-transform-input="second" value="${esc(editing?.max ?? editing?.voltage ?? editing?.values?.map((v) => v.to).join(", ") ?? "")}" placeholder="${esc(this._t(selected === "clamp" ? "maximum" : "to_values"))}"></div>`;
    const editingOrAdding = Boolean(editing || this._transformTypeDraft?.field === fieldId);
    const body = `${rows ? `<ol>${rows}</ol>` : `<p>${esc(this._t("no_transforms"))}</p>`}${pipelineError ? `<p class="error" role="alert">${esc(pipelineError)}</p>` : ""}${editingOrAdding ? `<label>${esc(this._t("adjustment_type"))}<select data-transform-type="${esc(fieldId)}">${options}</select></label>${referenceFields}<button type="button" data-transform-command="commit" data-field-id="${esc(fieldId)}">${esc(this._t(editing ? "save_adjustment" : "add_adjustment"))}</button>` : available.length ? `<button type="button" class="add-adjustment" data-transform-command="start" data-field-id="${esc(fieldId)}">+ ${esc(this._t("add_adjustment"))}</button>` : ""}`;
    const open = this._transformOpen.has(fieldId) ? " open" : "";
    return `<details class="transforms" data-transform-section="${esc(fieldId)}"${open}><summary><strong>${esc(this._t("value_adjustments"))}</strong><span class="adjustment-status">${esc(transforms.length ? `${transforms.length}` : this._t("no_transforms"))}</span></summary><div class="transform-content">${body}</div></details>`;
  }
  _wire() {
    this.shadowRoot.querySelectorAll("button[data-command]").forEach((button) => button.onclick = () => this._command(button.dataset.command, Number(button.dataset.index), button.dataset.mode, Number(button.dataset.behaviorIndex)));
    this.shadowRoot.querySelectorAll("[data-action-choice]").forEach((node) => node.onclick = () => { this._dialogDraft.service = node.dataset.actionChoice; this._dialogDraft.data = {}; this._omittedFields.clear(); this._refreshDialog(); });
    this.shadowRoot.querySelectorAll("[data-entity-choice]").forEach((node) => node.onclick = () => { this._selectEntity(node.dataset.entityChoice); this._refreshDialog(); });
    this.shadowRoot.querySelectorAll("[data-behavior-choice]").forEach((node) => node.onclick = () => this._command("select-behavior", 0, node.dataset.behaviorChoice, null, node.dataset.valueCondition || null));
    this.shadowRoot.querySelectorAll("[data-output-suggestion]").forEach((node) => node.onclick = () => this._command("use-output-suggestion", Number(node.dataset.outputSuggestion)));
    const clearEntity = this.shadowRoot.querySelector("[data-clear-entity]"); if (clearEntity) clearEntity.onclick = () => { this._dialogDraft = { type: "serviceCall", service: "", target: { entityId: "" }, data: {} }; this._refreshDialog(); };
    [["#action-search", "action"], ["#entity-search", "entity"]].forEach(([selector, kind]) => { const input = this.shadowRoot.querySelector(selector); if (input) input.oninput = () => this._filterChoices(kind, input.value); });
    this.shadowRoot.querySelectorAll("[data-source]").forEach((node) => node.onchange = () => { this._captureFields(); const id = node.dataset.source; if (node.value === "omit") { delete this._dialogDraft.data[id]; this._omittedFields.add(id); } else { this._omittedFields.delete(id); this._dialogDraft.data[id] = { kind: node.value }; } this._refreshDialog(); });
    this.shadowRoot.querySelectorAll("[data-transform-type]").forEach((node) => node.onchange = () => { this._transformTypeDraft = { field: node.dataset.transformType, type: node.value }; this._refreshDialog(); });
    this.shadowRoot.querySelectorAll("[data-transform-reference-entity]").forEach((node) => node.onchange = () => {
      const entity = this.referenceEntities.find((item) => item.entity_id === node.value);
      const attributes = entity?.attributes ?? [];
      const select = node.closest(".transforms")?.querySelector("[data-transform-reference-attribute]");
      if (select) select.innerHTML = `<option value="">${esc(this._t("entity_state"))}</option>${attributes.map((attribute) => `<option value="${esc(attribute)}">${esc(attribute)}</option>`).join("")}`;
    });
    this.shadowRoot.querySelectorAll("[data-transform-section]").forEach((node) => node.ontoggle = () => node.open ? this._transformOpen.add(node.dataset.transformSection) : this._transformOpen.delete(node.dataset.transformSection));
    this.shadowRoot.querySelectorAll("[data-transform-command]").forEach((node) => node.onclick = () => this._transformCommand(node));
  }
  _refreshDialog() { this.render(); this.shadowRoot.querySelector("#action-dialog").showModal(); }
  _filterChoices(kind, query) { const normalized = searchText(query); this.shadowRoot.querySelectorAll(`[data-choices="${kind}"] .choice`).forEach((node) => { const searchable = node.dataset?.search ?? node.textContent; node.hidden = normalized !== "" && !searchText(searchable).includes(normalized); }); }
  _captureFields() { if (!this._dialogDraft) return; this.shadowRoot.querySelectorAll("[data-literal]").forEach((node) => { const field = this._selectedFields().find((item) => item.id === node.dataset.literal); if (!field) return; let value = node.value; if (field.selector.type === "number") value = Number(value); else if (field.selector.type === "boolean") value = value === "true"; else if (field.selector.type === "select" && value) value = JSON.parse(value); this._dialogDraft.data[field.id] = { kind: "literal", value }; }); }
  _selectedFields() { return this._entity(this._cap(this._dialogDraft?.service), this._dialogDraft?.target?.entityId)?.fields ?? []; }
  _transformCommand(node) { this._captureFields(); const field = node.dataset.fieldId; const binding = this._dialogDraft.data[field] ?? { kind: "requestedValue" }; this._dialogDraft.data[field] = binding; const transforms = binding.transforms ?? []; const index = Number(node.dataset.index); const command = node.dataset.transformCommand; if (command === "start") { this._transformOpen.add(field); this._transformEditing = null; this._transformTypeDraft = { field, type: this._availableTransforms(transforms)[0] }; this._refreshDialog(); return; } if (command === "remove") transforms.splice(index, 1); if (["up", "down"].includes(command)) { const target = command === "up" ? index - 1 : index + 1; const [item] = transforms.splice(index, 1); transforms.splice(target, 0, item); } if (command === "edit") { this._transformOpen.add(field); this._transformEditing = { field, index }; } if (command === "commit") { try { const transform = this._readTransform(field); if (this._transformEditing?.field === field) transforms[this._transformEditing.index] = transform; else transforms.push(transform); this._transformEditing = null; this._transformTypeDraft = null; } catch (_) { this.shadowRoot.querySelector("#action-error").textContent = this._t("invalid_adjustment"); return; } } binding.transforms = transforms; this._refreshDialog(); }
  _readTransform(field) {
    const root = this.shadowRoot.querySelector(`[data-field="${CSS.escape(field)}"]`);
    const type = root.querySelector("[data-transform-type]").value;
    const inputs = root.querySelectorAll("[data-transform-input]");
    const first = inputs[0]?.value.trim() ?? "";
    const second = inputs[1]?.value.trim() ?? "";
    const parse = (raw, datatype) => raw.split(",").map((value) => value.trim()).filter(Boolean).map((value) => datatype === "number" ? Number(value) : datatype === "boolean" ? value.toLowerCase() === "true" : datatype === "null" ? null : value);
    if (type === "difference") {
      const entityId = root.querySelector("[data-transform-reference-entity]")?.value;
      const attribute = root.querySelector("[data-transform-reference-attribute]")?.value;
      if (!entityId) throw new Error();
      return { type, reference: attribute ? { entityId, attribute } : { entityId } };
    }
    if (type === "round") {
      const decimals = Number(first);
      if (!Number.isInteger(decimals) || decimals < 0 || decimals > 12) throw new Error();
      return { type, decimals };
    }
    if (type === "clamp") {
      const minimum = Number(first); const maximum = Number(second);
      if (!Number.isFinite(minimum) || !Number.isFinite(maximum) || minimum > maximum) throw new Error();
      return { type, min: minimum, max: maximum };
    }
    if (type === "powerToCurrent") {
      const phases = Number(first); const voltage = Number(second);
      if (!Number.isInteger(phases) || phases < 1 || !Number.isFinite(voltage) || voltage <= 0) throw new Error();
      return { type, phases, voltage };
    }
    if (type === "nearest") {
      const values = parse(first, "number");
      if (!values.length || values.some((value) => !Number.isFinite(value))) throw new Error();
      return { type, values };
    }
    if (type === "valueMap") {
      const fromType = root.querySelector("[data-transform-from-type]").value;
      const toType = root.querySelector("[data-transform-to-type]").value;
      const from = parse(first, fromType);
      const to = toType === "null" ? from.map(() => null) : parse(second, toType);
      if (!from.length || from.length !== to.length || (fromType === "boolean" && first.split(",").some((value) => !["true", "false"].includes(value.trim().toLowerCase())))) throw new Error();
      return { type, values: from.map((value, index) => ({ from: value, to: to[index] })) };
    }
    if (type === "scale") {
      const factor = Number(first);
      if (!Number.isFinite(factor)) throw new Error();
      return { type, factor };
    }
    if (type === "offset") {
      const amount = Number(first);
      if (!Number.isFinite(amount)) throw new Error();
      return { type, amount };
    }
    return { type: "invert" };
  }
  _command(command, index, modeKey, behaviorIndex = null, valueCondition = null) {
    const mode = this._behaviorMode ? (modeKey || null) : null;
    const actions = this._behaviorMode ? this._actionsFor(mode, Number.isNaN(behaviorIndex) ? null : behaviorIndex) : this._working;
    if (["up", "down"].includes(command)) { const target = command === "up" ? index - 1 : index + 1; const [action] = actions.splice(index, 1); actions.splice(target, 0, action); this.render(); return; }
    if (command === "remove") { actions.splice(index, 1); this.render(); return; }
    if (["add", "edit"].includes(command)) { this._activeMode = mode; this._activeBehaviorIndex = behaviorIndex; this._editing = command === "edit" ? index : null; this._transformEditing = null; this._transformTypeDraft = null; this._transformOpen.clear(); this._omittedFields.clear(); this._dialogDraft = clone(this._editing === null ? { type: "serviceCall", service: "", target: { entityId: "" }, data: {} } : actions[this._editing]); this.render(); this.shadowRoot.querySelector("#action-dialog").showModal(); return; }
    if (command === "add-behavior") { this._addingBehavior = true; this.render(); this.shadowRoot.querySelector("#behavior-dialog").showModal(); return; }
    if (command === "cancel-behavior") { this._addingBehavior = false; this.render(); return; }
    if (command === "select-behavior") { if (mode) this._workingBehaviors.push({ mode, ...(this.behaviorChoices.length ? { valueCondition } : {}), actions: [] }); this._addingBehavior = false; this.render(); return; }
    if (command === "use-output-suggestion") {
      const suggestion = this._outputSuggestionEntries()[index];
      if (!suggestion?.configuration?.actions?.length) return;
      const existing = this._workingBehaviors.find((item) => item.mode === suggestion.mode && (item.valueCondition ?? null) === (suggestion.valueCondition ?? null));
      if (existing) existing.actions = clone(suggestion.configuration.actions);
      else this._workingBehaviors.push({ mode: suggestion.mode ?? null, ...(suggestion.valueCondition != null ? { valueCondition: suggestion.valueCondition } : {}), actions: clone(suggestion.configuration.actions) });
      delete this._outputSuggestions[suggestion.behavior];
      this.render();
      return;
    }
    if (command === "remove-behavior") { if (this.behaviorChoices.length && mode === "balance" && behaviorIndex !== null && !Number.isNaN(behaviorIndex)) this._workingBehaviors.splice(behaviorIndex, 1); else this._workingBehaviors = this._workingBehaviors.filter((item) => item.mode !== mode); this.render(); return; }
    if (command === "close-dialog") { this._dialogDraft = null; this._transformEditing = null; this._transformTypeDraft = null; this._transformOpen.clear(); this._omittedFields.clear(); this.shadowRoot.querySelector("#action-dialog").close(); return; }
    if (command === "commit-action") return this._commitAction();
    if (command === "cancel") { if (this._behaviorMode) this._workingBehaviors = clone(this._baselineBehaviors); else this._working = clone(this._baseline); this._saveError = ""; this.render(); this.dispatchEvent(new CustomEvent("control-cancelled")); return; }
    if (command === "save") { this._saveError = this._configurationPipelineError(); if (this._saveError) { this.render(); return; } const detail = this._behaviorMode ? { behaviors: this._workingBehaviors.filter((item) => item.actions.length).map((item) => ({ mode: item.mode, ...(this.behaviorChoices.length && item.mode === "balance" ? { valueCondition: item.valueCondition ?? null } : {}), configuration: { version: 1, actions: clone(item.actions) } })) } : { configuration: this._working.length ? { version: 1, actions: clone(this._working) } : null }; this.dispatchEvent(new CustomEvent("control-saved", { detail })); }
  }
  _commitAction() { this._captureFields(); const error = this.shadowRoot.querySelector("#action-error"); const capability = this._cap(this._dialogDraft.service); const entity = this._entity(capability, this._dialogDraft.target.entityId); if (!capability) { error.textContent = this._t("choose_action_error"); return; } if (!entity) { error.textContent = this._t("choose_entity_error"); return; } const defaultField = this._editing === null ? this._defaultRequestedField(entity.fields) : null; if (defaultField && !this._omittedFields.has(defaultField.id) && this._dialogDraft.data[defaultField.id] === undefined) this._dialogDraft.data[defaultField.id] = { kind: "requestedValue" }; for (const field of entity.fields) { const binding = this._dialogDraft.data[field.id]; if (field.required && !binding) { error.textContent = this._t("required_field_error"); return; } if (binding?.kind === "literal" && (binding.value === "" || binding.value === undefined)) { error.textContent = this._t("fixed_value_error"); return; } if (binding?.kind === "requestedValue") { const result = this._pipeline(binding.transforms ?? []); if (!result.valid) { error.textContent = this._pipelineMessage(result); return; } } } const action = clone(this._dialogDraft); const actions = this._behaviorMode ? this._actionsFor(this._activeMode, this._activeBehaviorIndex) : this._working; if (this._editing === null) actions.push(action); else actions[this._editing] = action; this._editing = null; this._activeBehaviorIndex = null; this._dialogDraft = null; this._transformOpen.clear(); this._omittedFields.clear(); this.render(); }
}
