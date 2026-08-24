# Control action editor architecture evaluation

Inspected against Home Assistant Core 2026.8 and frontend package
`20260729.7`.

## A — Native Options Flow

Native forms can serialize an `ActionSelector`, and `ObjectSelector` with
`multiple: true` provides an ordered list with Add, Edit, Remove, and drag
sorting. The latter is implemented by `ha-selector-object` and `ha-sortable`.

It is not sufficient for this product editor. An object selector has one static
schema for every row. It cannot conditionally show select-option fields, numeric
fields, or service parameter fields based on action type. It cannot populate a
fixed option from the entity selected earlier in the same item. A nested flow
could emulate this, but would require excessive navigation and make ordering
hard to understand.

## B — Home Assistant automation Action editor

`ActionSelector` currently imports and renders `ha-automation-action`. It gives
excellent add/edit/remove and drag ordering. The selector config only exposes
`optionsInSidebar`; it has no supported allow-list for action types.

Using it would expose conditions, delays, loops, parallel branches, templates,
and YAML fallback. Its runtime-value binding would require a template or magic
placeholder, both contrary to the fluks product model. Importing
`ha-automation-action` directly would depend on private files under
`panels/config/automation`, private properties/events, and internal action data
migration. This is a high Home Assistant upgrade risk and not a supported custom
integration API.

## C — Focused fluks editor

Recommended. The prototype custom element implements only three proven actions:

- Select option
- Set number
- Water-heater set temperature

It shows a numbered sequence, Up/Down, Edit, Remove, Add action, Cancel, and
Save control in one surface. Action forms are conditional, and value binding is
explicitly either Fixed value or Control value. The prototype has no fetch,
WebSocket, storage, Home Assistant service call, or backend call.

A production version should be a similarly small frontend surface backed by a
supported hass-fluks API. It should use Home Assistant entity registries and
service metadata through supported websocket commands, but it should not import
the private automation editor.

## Prototype data

```json
{
  "actions": [
    {
      "type": "selectOption",
      "entityId": "select.goodwe_operation_mode",
      "option": {"source": "fixed", "value": "eco_charge"}
    },
    {
      "type": "setNumber",
      "entityId": "number.goodwe_charge_target",
      "value": {"source": "control"}
    }
  ]
}
```

Bindings are typed objects. There is no `$controlValue`, YAML, template, or
arbitrary expression. A future optional typed transform can be added beside
`source`, for example a constrained scale/offset object, without changing action
ordering or introducing a language.

## Canonical gap

The live catalog currently exposes `battery.power` as a control and
`battery.soc` as fact-only. It has no battery target-SOC/charge-target canonical
control. Therefore the GoodWe sequence is a clearly marked UX fixture only; it
must not ship as a production Mapping for `battery.soc` or be misrepresented as
`battery.power`. `waterHeater.targetTemperature` is an existing control and is a
valid production-oriented UX example.

## Embedded review

Open an existing fluks ConfigEntry through Home Assistant Configure, select a
Device, open Controls, and select a catalog-declared control. The focused editor
is part of the embedded production configuration panel; there is no standalone
HTML demo.

### GoodWe

1. Choose **Battery power · GoodWe**.
2. Confirm the fixed Eco charge action precedes the Control value action.
3. Move the second action up and confirm the visible numbering changes.
4. Edit the number action and change Control value to Fixed value 70.
5. Remove or add an action, then Cancel; confirm the baseline returns.
6. Repeat changes and Save control; inspect the ordered JSON below the editor.

### Water heater

1. Choose **Water-heater target temperature**.
2. Edit the action and confirm its action, target, parameter, and Control value.
3. Save and inspect the structured output.

### Ordering

Add three actions, move Action 3 to Action 1, and verify the numbered cards make
the future top-to-bottom execution order unambiguous.
