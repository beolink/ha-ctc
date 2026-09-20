/* CTC EcoZenith: the page's cards, with an explanation for every value.
 *
 * Served by the integration at /ctc_ecozenith/ctc-ecozenith-card.js and loaded as a
 * Lovelace resource. The page itself is built in Python (dashboard_views.py) and uses
 * four cards from here, all of them drawn by hand rather than out of Home Assistant's
 * own tiles, because a heat pump has a hundred values and they have to fit on a screen:
 *
 *   custom:ctc-ecozenith-chips      what the pump is doing right now, a line of chips
 *   custom:ctc-ecozenith-readings   the key figures, a small name over a big number
 *   custom:ctc-ecozenith-controls   one row per control: name on the left, the thing
 *                                   it is on the right, a list, a slider or a field
 *   custom:ctc-ecozenith-rows       a list of values, name on the left and value on
 *                                   the right, in as many columns as there is room
 *                                   for, with headings and a filter over the lot
 *
 * Every value carries a blue "i" right after its name. It writes the explanation out
 * underneath, together with where the value comes from and a way into Home Assistant's
 * own dialog for the entity, which is what the NIBE page settled on. The explanation
 * is also the hover text, since a hover alone never reaches a phone or a screen reader.
 * Every string goes into the page as text, never as HTML.
 */

(() => {
  const HIDDEN_STATES = new Set(["unavailable", "unknown"]);
  //: A number control with more steps than a slider has pixels is a lottery to
  //: aim at, so it gets a field to type in instead: a room setpoint in tenths
  //: of a degree from 10 to 30 is 200 steps on 130 pixels.
  const SLIDER_STEPS = 130;

  const STYLE = `
    /* A display rule of a card, or ha-card's own :host rule, would otherwise beat
       the browser's [hidden] and leave closed explanations on the page. */
    [hidden] { display: none !important; }
    :host { display: block; }
    ha-card { padding: 12px 16px; }
    .why {
      flex: none; background: none; border: none; padding: 0 2px; cursor: pointer;
      color: var(--primary-color, #03a9f4); font: inherit; line-height: 1;
    }
    .why:focus-visible {
      outline: 1px solid var(--primary-color, #03a9f4); outline-offset: 2px; border-radius: 50%;
    }
    .note {
      color: var(--secondary-text-color); font-size: .85em; line-height: 1.45; padding-top: 4px;
    }
    .note .meta { display: block; margin-top: 4px; font-size: .9em; opacity: .85; }
    .note a { color: var(--primary-color); cursor: pointer; text-decoration: underline; }
  `;

  function moreInfo(element, entityId) {
    element.dispatchEvent(
      new CustomEvent("hass-more-info", { bubbles: true, composed: true, detail: { entityId } })
    );
  }

  /** The blue "i" that opens an explanation. Every value the page shows has one:
   *  a value is only worth reading if you know what it is. */
  function explainButton(label, toggle) {
    const why = document.createElement("button");
    why.className = "why";
    why.type = "button";
    why.textContent = "ⓘ";
    why.setAttribute("aria-label", label || "Explanation");
    why.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      toggle();
    });
    return why;
  }

  function withUnit(value, unit) {
    return unit ? `${value} ${unit}` : String(value);
  }

  /** Whether the user is holding this very control. The cards live in a shadow
   *  root, where the document's own idea of what has focus is the card itself,
   *  so the question has to be put to the root the control is in. */
  function holding(element) {
    const root = element.getRootNode();
    return Boolean(root) && root.activeElement === element;
  }

  /** What the cards have in common: the explanation, the state, the service call. */
  class CtcCard extends HTMLElement {
    constructor(style) {
      super();
      this.attachShadow({ mode: "open" });
      this._open = new Set();
      this._style = style;
    }

    setConfig(config) {
      this._config = config || {};
      this._build();
      if (this._hass) this._update();
    }

    set hass(hass) {
      this._hass = hass;
      this._update();
    }

    get hass() {
      return this._hass;
    }

    getCardSize() {
      return 2;
    }

    getGridOptions() {
      return { columns: "full", rows: "auto" };
    }

    _shell() {
      const style = document.createElement("style");
      style.textContent = `${STYLE}${this._style || ""}`;
      const card = document.createElement("ha-card");
      this.shadowRoot.replaceChildren(style, card);
      return card;
    }

    /** The name, the "i" after it, and the note the "i" opens. */
    _named(item, note, tag = "span") {
      const name = document.createElement(tag);
      name.className = "label";
      name.append(item.name || item.entity);
      if (item.explanation) {
        name.title = item.explanation;
        name.appendChild(
          explainButton(this._config.explain, () => this._toggle(item, note))
        );
      }
      return name;
    }

    _toggle(item, note) {
      const open = !this._open.has(item.entity);
      if (open) this._open.add(item.entity);
      else this._open.delete(item.entity);
      this._fill(note, item, open);
    }

    /** The explanation, then where the value comes from and a way to its dialog. */
    _fill(note, item, open) {
      note.hidden = !open;
      if (!open) return;
      note.replaceChildren();
      note.append(item.explanation || "");
      const meta = document.createElement("span");
      meta.className = "meta";
      if (item.source) meta.append(item.source, " · ");
      const link = document.createElement("a");
      link.textContent = this._config.more_info || "More info";
      link.setAttribute("role", "button");
      link.tabIndex = 0;
      const openDialog = (event) => {
        event.preventDefault();
        event.stopPropagation();
        moreInfo(this, item.entity);
      };
      link.addEventListener("click", openDialog);
      link.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") openDialog(event);
      });
      meta.appendChild(link);
      note.appendChild(meta);
    }

    _state(entityId) {
      return this._hass ? this._hass.states[entityId] : undefined;
    }

    _text(stateObj) {
      if (!stateObj) return "";
      try {
        return this._hass.formatEntityState
          ? this._hass.formatEntityState(stateObj)
          : withUnit(stateObj.state, stateObj.attributes.unit_of_measurement);
      } catch (err) {
        return stateObj.state;
      }
    }

    _missing(item) {
      const stateObj = this._state(item.entity);
      return Boolean(item.hide_unavailable) && (!stateObj || HIDDEN_STATES.has(stateObj.state));
    }

    _call(entityId, value) {
      const domain = String(entityId).split(".")[0];
      if (domain === "select") {
        this._hass.callService("select", "select_option", { entity_id: entityId, option: value });
      } else if (domain === "number") {
        this._hass.callService("number", "set_value", { entity_id: entityId, value: Number(value) });
      } else if (domain === "button") {
        this._hass.callService("button", "press", { entity_id: entityId });
      }
    }
  }

  /* ------------------------------------------------------------------ chips */

  const CHIPS_STYLE = `
    .chips { display: flex; flex-wrap: wrap; gap: 8px; }
    .chip {
      display: flex; gap: 6px; align-items: baseline; padding: 6px 12px; border-radius: 999px;
      background: var(--secondary-background-color, #f1f3f4);
    }
    .chip .label { color: var(--secondary-text-color); font-size: .8em; }
    .chip .value { color: var(--primary-text-color); font-weight: 500; }
    .chips + .note { padding-top: 10px; }
  `;

  class CtcEcoZenithChips extends CtcCard {
    constructor() {
      super(CHIPS_STYLE);
    }

    _build() {
      const card = this._shell();
      const strip = document.createElement("div");
      strip.className = "chips";
      const note = document.createElement("div");
      note.className = "note";
      note.hidden = true;
      this._items = (this._config.items || []).map((item) => {
        const chip = document.createElement("div");
        chip.className = "chip";
        const value = document.createElement("span");
        value.className = "value";
        chip.append(this._named(item, note), value);
        strip.appendChild(chip);
        return { item, chip, value };
      });
      card.append(strip, note);
    }

    _update() {
      if (!this._hass) return;
      for (const row of this._items) {
        row.chip.hidden = this._missing(row.item);
        row.value.textContent = this._text(this._state(row.item.entity));
      }
    }
  }

  /* --------------------------------------------------------------- readings */

  const READINGS_STYLE = `
    .tiles { display: grid; gap: 14px 24px; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); }
    .tile .label { display: block; color: var(--secondary-text-color); font-size: .8em; overflow-wrap: anywhere; }
    .tile .big { font-size: 1.6rem; font-weight: 500; color: var(--primary-text-color); }
    .tile .note { padding-top: 2px; }
  `;

  class CtcEcoZenithReadings extends CtcCard {
    constructor() {
      super(READINGS_STYLE);
    }

    _build() {
      const card = this._shell();
      const grid = document.createElement("div");
      grid.className = "tiles";
      this._items = (this._config.items || []).map((item) => {
        const tile = document.createElement("div");
        tile.className = "tile";
        const note = document.createElement("div");
        note.className = "note";
        note.hidden = true;
        const value = document.createElement("div");
        value.className = "big";
        tile.append(this._named(item, note, "div"), value, note);
        grid.appendChild(tile);
        return { item, tile, value };
      });
      card.append(grid);
    }

    _update() {
      if (!this._hass) return;
      for (const row of this._items) {
        row.tile.hidden = this._missing(row.item);
        row.value.textContent = this._text(this._state(row.item.entity));
      }
    }
  }

  /* --------------------------------------------------------------- controls */

  const CONTROLS_STYLE = `
    .control {
      display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 4px 12px;
      padding: 8px 0; border-bottom: 1px solid var(--divider-color, #eee);
    }
    .control:last-of-type { border-bottom: none; }
    .control .label { color: var(--primary-text-color); overflow-wrap: break-word; min-width: 0; }
    .widget { display: flex; align-items: center; gap: 8px; justify-self: end; }
    .widget[data-pending="1"] { opacity: .5; }
    select, input[type="number"] {
      font: inherit; padding: 6px 8px; border-radius: 8px; max-width: 190px;
      border: 1px solid var(--divider-color, #ccc);
      background: var(--card-background-color, #fff); color: var(--primary-text-color);
    }
    input[type="range"] { width: 130px; accent-color: var(--primary-color, #03a9f4); }
    .reading { color: var(--primary-text-color); font-weight: 500; min-width: 56px; text-align: right; }
    .widget button {
      font: inherit; padding: 6px 14px; border-radius: 8px; cursor: pointer;
      border: 1px solid var(--primary-color, #03a9f4);
      background: transparent; color: var(--primary-color, #03a9f4);
    }
    .note { grid-column: 1 / -1; }
  `;

  class CtcEcoZenithControls extends CtcCard {
    constructor() {
      super(CONTROLS_STYLE);
    }

    _build() {
      const card = this._shell();
      this._items = (this._config.items || []).map((item) => {
        const row = document.createElement("div");
        row.className = "control";
        const note = document.createElement("div");
        note.className = "note";
        note.hidden = true;
        const widget = document.createElement("span");
        widget.className = "widget";
        widget.dataset.pending = "0";
        row.append(this._named(item, note), widget, note);
        card.appendChild(row);
        return { item, row, widget, update: null };
      });
    }

    _update() {
      if (!this._hass) return;
      for (const row of this._items) {
        if (!row.update) {
          // The control is built from the entity's own range and options, so it
          // waits for the first state rather than guessing at an empty one.
          if (!this._state(row.item.entity)) continue;
          row.update = this._widget(row.widget, row.item);
        }
        row.update();
      }
    }

    /** Build the control itself and return how to keep it current. A control the
     *  user is holding is left alone: Home Assistant sends a new state while a
     *  slider is being dragged, and writing it back would fight the thumb. */
    _widget(container, item) {
      const entityId = item.entity;
      const domain = String(entityId).split(".")[0];
      const send = (value) => {
        container.dataset.pending = "1";
        this._call(entityId, value);
        setTimeout(() => { container.dataset.pending = "0"; }, 6000);
      };

      if (domain === "button") {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = this._config.press || "Press";
        button.addEventListener("click", () => send(null));
        container.appendChild(button);
        return () => {
          const stateObj = this._state(entityId);
          button.disabled = !stateObj || stateObj.state === "unavailable";
        };
      }

      if (domain === "select") {
        const select = document.createElement("select");
        select.addEventListener("change", () => send(select.value));
        container.appendChild(select);
        return () => {
          const stateObj = this._state(entityId);
          const options = (stateObj && stateObj.attributes.options) || [];
          if (select.options.length !== options.length ||
              [...select.options].some((option, i) => option.value !== options[i])) {
            select.replaceChildren();
            for (const option of options) {
              const choice = document.createElement("option");
              choice.value = option;
              choice.textContent = option;
              select.appendChild(choice);
            }
          }
          if (!holding(select) && stateObj) {
            select.value = stateObj.state;
            if (select.value === stateObj.state) container.dataset.pending = "0";
          }
          select.disabled = !stateObj || stateObj.state === "unavailable";
        };
      }

      const attributes = (this._state(entityId) || {}).attributes || {};
      const step = Number(attributes.step) || 1;
      const steps = (Number(attributes.max) - Number(attributes.min)) / step;
      if (item.widget === "slider" || (item.widget !== "field" && steps > 0 && steps <= SLIDER_STEPS)) {
        const slider = document.createElement("input");
        slider.type = "range";
        slider.min = attributes.min;
        slider.max = attributes.max;
        slider.step = step;
        const reading = document.createElement("span");
        reading.className = "reading";
        slider.addEventListener("input", () => {
          reading.textContent = withUnit(slider.value, attributes.unit_of_measurement);
        });
        slider.addEventListener("change", () => send(slider.value));
        container.append(slider, reading);
        return () => {
          const stateObj = this._state(entityId);
          if (!stateObj) return;
          if (!holding(slider)) {
            slider.value = stateObj.state;
            reading.textContent = this._text(stateObj);
            if (String(slider.value) === String(stateObj.state)) container.dataset.pending = "0";
          }
          slider.disabled = stateObj.state === "unavailable";
        };
      }

      const field = document.createElement("input");
      field.type = "number";
      if (attributes.min !== undefined) field.min = attributes.min;
      if (attributes.max !== undefined) field.max = attributes.max;
      field.step = attributes.step || "any";
      const unit = document.createElement("span");
      unit.className = "reading";
      unit.textContent = attributes.unit_of_measurement || "";
      field.addEventListener("change", () => send(field.value));
      container.append(field, unit);
      return () => {
        const stateObj = this._state(entityId);
        if (!stateObj) return;
        if (!holding(field)) {
          field.value = stateObj.state;
          if (String(field.value) === String(stateObj.state)) container.dataset.pending = "0";
        }
        field.disabled = stateObj.state === "unavailable";
      };
    }
  }

  /* ------------------------------------------------------------------- rows */

  const ROWS_STYLE = `
    .toolbar { display: flex; gap: 12px; align-items: center; padding-bottom: 10px; }
    .toolbar input {
      flex: 1; max-width: 420px; padding: 8px 12px; font: inherit;
      border: 1px solid var(--divider-color, #ccc); border-radius: 8px;
      background: var(--card-background-color, #fff); color: var(--primary-text-color);
    }
    .count { color: var(--secondary-text-color); font-size: .9em; }
    .grid { display: grid; gap: 0 24px; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); }
    .heading {
      grid-column: 1 / -1; font-size: .8rem; font-weight: 500; margin: 14px 0 4px;
      color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .04em;
    }
    .heading:first-child { margin-top: 0; }
    .row {
      display: grid; grid-template-columns: 1fr auto; gap: 2px 12px; align-items: baseline;
      padding: 9px 4px; border-bottom: 1px solid var(--divider-color, #eee);
      border-radius: 4px; cursor: pointer;
    }
    .row:hover, .row:focus-visible { background: var(--secondary-background-color, #f5f5f5); outline: none; }
    .row .label { color: var(--primary-text-color); overflow-wrap: anywhere; }
    .value { color: var(--primary-text-color); font-weight: 500; white-space: nowrap; text-align: right; }
    .note { grid-column: 1 / -1; }
    .empty { color: var(--secondary-text-color); padding: 16px 4px; }
  `;

  class CtcEcoZenithRows extends CtcCard {
    constructor() {
      super(ROWS_STYLE);
      this._query = "";
    }

    setConfig(config) {
      if (!config || !Array.isArray(config.rows)) {
        throw new Error("ctc-ecozenith-rows needs rows");
      }
      super.setConfig(config);
    }

    getCardSize() {
      return 1 + this._items.filter((row) => !row.element.hidden).length;
    }

    _build() {
      const card = this._shell();
      if (this._config.filter) card.appendChild(this._searchField());
      const grid = document.createElement("div");
      grid.className = "grid";
      this._items = [];
      this._headings = [];
      for (const item of this._config.rows) {
        if (item && item.heading !== undefined) {
          const heading = document.createElement("div");
          heading.className = "heading";
          heading.textContent = item.heading;
          grid.appendChild(heading);
          this._headings.push({ element: heading, rows: [] });
          continue;
        }
        const row = this._row(item);
        grid.appendChild(row.element);
        this._items.push(row);
        if (this._headings.length) this._headings[this._headings.length - 1].rows.push(row);
      }
      this._empty = document.createElement("div");
      this._empty.className = "empty";
      this._empty.textContent = this._config.empty || "";
      this._empty.hidden = true;
      card.append(grid, this._empty);
    }

    _searchField() {
      const toolbar = document.createElement("div");
      toolbar.className = "toolbar";
      const input = document.createElement("input");
      input.type = "search";
      input.placeholder = this._config.filter === true ? "" : String(this._config.filter);
      input.setAttribute("aria-label", input.placeholder);
      input.value = this._query;
      input.addEventListener("input", () => {
        this._query = input.value.trim().toLowerCase();
        this._show();
      });
      this._count = document.createElement("span");
      this._count.className = "count";
      toolbar.append(input, this._count);
      return toolbar;
    }

    _row(item) {
      const element = document.createElement("div");
      element.className = "row";
      element.tabIndex = 0;
      element.setAttribute("role", "button");
      element.setAttribute("aria-expanded", "false");
      const note = document.createElement("div");
      note.className = "note";
      note.hidden = true;
      const value = document.createElement("span");
      value.className = "value";
      element.append(this._named(item, note), value, note);
      const toggle = () => {
        this._toggle(item, note);
        element.setAttribute("aria-expanded", String(!note.hidden));
      };
      element.addEventListener("click", toggle);
      element.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          toggle();
        }
      });
      return { item, element, note, value, seen: undefined, gone: false };
    }

    _update() {
      if (!this._hass) return;
      for (const row of this._items) {
        const stateObj = this._state(row.item.entity);
        row.gone = this._missing(row.item);
        // Only when the state itself changed: every update of any entity in
        // Home Assistant sets hass again.
        if (!stateObj || row.seen === stateObj) continue;
        row.seen = stateObj;
        row.value.textContent = this._text(stateObj);
      }
      this._show();
    }

    /** What is on show: what has a value to show, and what the search asks for. */
    _show() {
      let shown = 0;
      for (const row of this._items) {
        const hidden = row.gone || !this._matches(row);
        row.element.hidden = hidden;
        if (hidden) row.note.hidden = true;
        else shown += 1;
      }
      // A heading with nothing under it says nothing.
      for (const heading of this._headings) {
        heading.element.hidden = !heading.rows.some((row) => !row.element.hidden);
      }
      if (this._count) this._count.textContent = `${shown} / ${this._items.length}`;
      if (this._empty) this._empty.hidden = !(this._config.filter && this._query && !shown);
    }

    /** A row is searched by everything it says: its name, its explanation, where
     *  the value comes from, and the value itself. */
    _matches(row) {
      if (!this._query) return true;
      const item = row.item;
      const haystack = [
        item.name, item.explanation, item.source, item.entity, row.value.textContent,
      ].join(" ").toLowerCase();
      return this._query.split(/\s+/).every((word) => haystack.includes(word));
    }
  }

  const CARDS = {
    "ctc-ecozenith-chips": CtcEcoZenithChips,
    "ctc-ecozenith-readings": CtcEcoZenithReadings,
    "ctc-ecozenith-controls": CtcEcoZenithControls,
    "ctc-ecozenith-rows": CtcEcoZenithRows,
  };
  for (const [name, card] of Object.entries(CARDS)) {
    if (!customElements.get(name)) customElements.define(name, card);
  }
})();
