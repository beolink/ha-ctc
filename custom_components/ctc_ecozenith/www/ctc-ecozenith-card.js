/* CTC EcoZenith: the page's cards, with an explanation for every value.
 *
 * Served by the integration at /ctc_ecozenith/ctc-ecozenith-card.js and loaded as a
 * Lovelace resource. The page itself is built in Python (dashboard_views.py) and
 * uses two cards from here:
 *
 *   custom:ctc-ecozenith-tile   Home Assistant's own tile, left as it is, with an "i"
 *                               right after the name that writes the explanation out
 *                               under the tile. Tapping the name does the same.
 *   custom:ctc-ecozenith-rows   a list of values, name on the left and value on the
 *                               right, each with its own "i". A list can carry
 *                               headings, and a filter over the whole list, which is
 *                               what the tab with every value needs.
 *
 * The explanation is shown on hover through the title attribute, and also on tap,
 * because a hover text alone never reaches a phone or a screen reader, which is
 * what the NIBE page settled on. Every string goes into the page as text, never as
 * HTML.
 */

(() => {
  const HIDDEN_STATES = new Set(["unavailable", "unknown"]);

  /** The parts of a tile that do something of their own when tapped: its
   *  features, and its icon (ha-tile-icon since 2025, and before that the
   *  icon-container around ha-tile-icon or ha-tile-image). */
  const OWN_TAP = ["hui-card-feature", "ha-tile-icon", "ha-tile-image", "ha-control-"];

  const DETAIL_STYLE = `
    /* A display rule of any card, or ha-card's own :host rule, would otherwise
       beat the browser's [hidden] and leave a closed explanation on the page. */
    [hidden] { display: none !important; }
    .detail {
      color: var(--secondary-text-color);
      font-size: 14px;
      line-height: 1.45;
      white-space: normal;
    }
    .meta {
      display: block;
      margin-top: 4px;
      font-size: 12px;
    }
    .meta a {
      color: var(--primary-color);
      cursor: pointer;
      text-decoration: underline;
    }
    .why {
      flex: none;
      background: none;
      border: none;
      padding: 0 4px;
      cursor: pointer;
      color: var(--primary-color, #03a9f4);
      font: inherit;
      line-height: 1;
    }
    .why:focus-visible {
      outline: 1px solid var(--primary-color, #03a9f4);
      outline-offset: 2px;
      border-radius: 50%;
    }
  `;

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

  function moreInfo(element, entityId) {
    element.dispatchEvent(
      new CustomEvent("hass-more-info", {
        bubbles: true,
        composed: true,
        detail: { entityId },
      })
    );
  }

  /** The explanation, then where the value comes from and a way to its dialog. */
  function fillDetail(container, config, item, host) {
    container.replaceChildren();
    const text = document.createElement("span");
    text.textContent = item.explanation || "";
    container.appendChild(text);
    const meta = document.createElement("span");
    meta.className = "meta";
    if (item.source) meta.append(item.source, " · ");
    const link = document.createElement("a");
    link.textContent = config.more_info || "More info";
    link.setAttribute("role", "button");
    link.tabIndex = 0;
    const open = (event) => {
      event.preventDefault();
      event.stopPropagation();
      moreInfo(host, item.entity);
    };
    link.addEventListener("click", open);
    link.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") open(event);
    });
    meta.appendChild(link);
    container.appendChild(meta);
  }

  class CtcEcoZenithTile extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._open = false;
      const style = document.createElement("style");
      style.textContent = `
        :host { display: block; }
        .frame { position: relative; height: var(--ctc-height, auto); }
        .explained { cursor: help; }
        /* Right after the name, where the NIBE page puts it. The tile draws the
           name itself, so the button is placed against the measured text, and
           falls back to the corner the tile leaves empty. */
        .why {
          position: absolute;
          top: 2px;
          right: 2px;
          z-index: 1;
          padding: 2px 4px;
          font-size: 15px;
        }
        .why.beside {
          top: auto;
          right: auto;
          transform: translateY(-50%);
        }
        ha-card.detail { margin-top: 8px; padding: 12px 16px; }
        ${DETAIL_STYLE}
      `;
      this._frame = document.createElement("div");
      this._frame.className = "frame";
      this._why = explainButton("", () => this._toggle());
      this._why.hidden = true;
      this._detail = document.createElement("ha-card");
      this._detail.className = "detail";
      this._detail.hidden = true;
      this.shadowRoot.append(style, this._frame, this._detail);
      this._frame.appendChild(this._why);
      this._frame.addEventListener("click", (event) => this._clicked(event));
      if (window.ResizeObserver) {
        // A narrower column moves the name, and with it the button beside it.
        this._watch = new ResizeObserver(() => this._placeSoon());
        this._watch.observe(this._frame);
      }
      this.addEventListener("keydown", (event) => {
        if (event.target === this && (event.key === "Enter" || event.key === " ")) {
          event.preventDefault();
          this._toggle();
        }
      });
    }

    setConfig(config) {
      if (!config || !config.tile || !config.tile.entity) {
        throw new Error("ctc-ecozenith-tile needs a tile with an entity");
      }
      this._config = config;
      if (!customElements.get("hui-tile-card")) {
        // Lovelace defines its tile with the dashboard; ask to be built again then.
        customElements.whenDefined("hui-tile-card").then(() => {
          this.dispatchEvent(new Event("ll-rebuild", { bubbles: true, composed: true }));
        });
        return;
      }
      if (!this._tile) {
        this._tile = document.createElement("hui-tile-card");
        this._frame.insertBefore(this._tile, this._why);
      }
      this._tile.setConfig(config.tile);
      if (this._hass) this._tile.hass = this._hass;
      if (this._layout) this._tile.layout = this._layout;
      const rows = this._tileRows();
      if (typeof rows === "number") {
        this._frame.style.setProperty(
          "--ctc-height",
          `calc(${rows} * var(--row-height, 56px) + ${rows - 1} * var(--row-gap, 8px))`
        );
      } else {
        this._frame.style.removeProperty("--ctc-height");
      }
      const explained = Boolean(config.explanation);
      this.title = config.explanation || "";
      this.tabIndex = explained ? 0 : -1;
      this._frame.classList.toggle("explained", explained && this._namesExplain());
      this._why.hidden = !explained;
      this._why.setAttribute("aria-label", config.explain || "Explanation");
      if (explained) fillDetail(this._detail, config, { ...config, entity: config.tile.entity }, this);
      this._setOpen(this._open && explained);
      this._placeSoon();
    }

    set hass(hass) {
      this._hass = hass;
      if (this._tile) this._tile.hass = hass;
      // A new state can change the name's width, and with it where the "i" goes.
      this._placeSoon();
    }

    set layout(layout) {
      this._layout = layout;
      if (this._tile) this._tile.layout = layout;
    }

    set preview(preview) {
      if (this._tile) this._tile.preview = preview;
    }

    connectedCallback() {
      this._placeSoon();
    }

    disconnectedCallback() {
      if (this._pending) cancelAnimationFrame(this._pending);
      this._pending = 0;
    }

    getCardSize() {
      const size = this._tile && this._tile.getCardSize ? this._tile.getCardSize() : 1;
      return (typeof size === "number" ? size : 1) + (this._open ? 1 : 0);
    }

    getGridOptions() {
      const inner = (this._tile && this._tile.getGridOptions && this._tile.getGridOptions()) || {};
      // The frame keeps the tile at its own height; the card as a whole grows
      // when the explanation opens underneath.
      const options = { ...inner, rows: "auto" };
      delete options.min_rows;
      delete options.max_rows;
      return options;
    }

    /** Measure once per frame at most: a tile is laid out by Home Assistant, and
     *  every state in the house sets hass again. */
    _placeSoon() {
      if (this._pending || this._why.hidden) return;
      this._pending = requestAnimationFrame(() => {
        this._pending = 0;
        this._place();
      });
    }

    /** The name's own text box, inside Home Assistant's tile. Read, never
     *  written: the tile is left exactly as it is. */
    _nameText() {
      const root = this._tile && this._tile.shadowRoot;
      const info = root && root.querySelector("ha-tile-info");
      if (!info) return null;
      return (
        info.querySelector("span.primary") ||
        info.querySelector(".primary") ||
        (info.shadowRoot && info.shadowRoot.querySelector(".primary")) ||
        null
      );
    }

    _place() {
      const name = this._nameText();
      const frame = this._frame.getBoundingClientRect();
      if (!name || !frame.width) return this._corner();
      const range = document.createRange();
      range.selectNodeContents(name);
      const text = range.getBoundingClientRect();
      // A name that does not fit is cut with an ellipsis, and the button then
      // stands at the end of the room the name had.
      const room = name.getBoundingClientRect();
      if (!text.width || !room.width) return this._corner();
      const right = Math.min(text.right, room.right);
      const width = this._why.offsetWidth || 22;
      this._why.classList.add("beside");
      this._why.style.left = `${Math.round(
        Math.min(right - frame.left + 2, Math.max(0, frame.width - width - 2))
      )}px`;
      this._why.style.top = `${Math.round(text.top - frame.top + text.height / 2)}px`;
    }

    _corner() {
      this._why.classList.remove("beside");
      this._why.style.left = "";
      this._why.style.top = "";
    }

    _tileRows() {
      const inner = this._tile && this._tile.getGridOptions && this._tile.getGridOptions();
      return inner ? inner.rows : undefined;
    }

    /** Whether tapping the name explains: a tile whose tap does something does not. */
    _namesExplain() {
      const action = this._config.tile.tap_action;
      return !action || action.action === "none";
    }

    _clicked(event) {
      if (!this._config || !this._config.explanation || !this._namesExplain()) return;
      for (const element of event.composedPath()) {
        if (element === this._frame) break;
        const name = element.localName || "";
        if (OWN_TAP.some((prefix) => name.startsWith(prefix))) return;
        if (element.classList && element.classList.contains("icon-container")) return;
      }
      this._toggle();
    }

    _toggle() {
      this._setOpen(!this._open);
    }

    _setOpen(open) {
      this._open = open;
      this._detail.hidden = !open;
      this.setAttribute("aria-expanded", String(open));
    }
  }

  class CtcEcoZenithRows extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._open = new Set();
      this._rows = [];
      this._headings = [];
      this._query = "";
    }

    setConfig(config) {
      if (!config || !Array.isArray(config.rows)) {
        throw new Error("ctc-ecozenith-rows needs rows");
      }
      this._config = config;
      this._build();
      if (this._hass) this._update();
    }

    set hass(hass) {
      this._hass = hass;
      this._update();
    }

    getCardSize() {
      return 1 + this._rows.filter((row) => !row.element.hidden).length;
    }

    getGridOptions() {
      return { columns: 12, min_columns: 6, rows: "auto" };
    }

    _build() {
      const style = document.createElement("style");
      style.textContent = `
        ha-card { padding: 8px 0; }
        .row {
          display: flex;
          align-items: center;
          min-height: 40px;
          padding: 0 16px;
          cursor: pointer;
          outline: none;
        }
        .row:focus-visible { background: var(--secondary-background-color); }
        .icon {
          flex: none;
          width: 40px;
          display: flex;
          justify-content: center;
          color: var(--state-icon-color);
        }
        .name {
          flex: 0 1 auto;
          min-width: 0;
          margin-left: 16px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          color: var(--primary-text-color);
        }
        /* The "i" stands right after the name, and the value keeps the right
           edge to itself. */
        .gap { flex: 1 1 auto; min-width: 8px; }
        .value {
          flex: none;
          margin-left: 16px;
          text-align: right;
          white-space: nowrap;
          color: var(--primary-text-color);
        }
        .detail { padding: 0 16px 10px 72px; }
        .heading {
          padding: 16px 16px 4px 16px;
          font-size: 14px;
          font-weight: 500;
          color: var(--secondary-text-color);
          text-transform: uppercase;
          letter-spacing: .05em;
        }
        .search {
          display: flex;
          align-items: center;
          margin: 4px 16px 8px 16px;
          padding: 0 8px;
          border-radius: 8px;
          background: var(--secondary-background-color, rgba(127,127,127,.12));
        }
        .search input {
          flex: 1;
          min-width: 0;
          border: none;
          outline: none;
          background: none;
          padding: 8px 6px;
          font: inherit;
          color: var(--primary-text-color);
        }
        .empty { padding: 8px 16px 12px 16px; color: var(--secondary-text-color); }
        ${DETAIL_STYLE}
      `;
      const card = document.createElement("ha-card");
      if (this._config.filter) card.appendChild(this._searchField());
      this._rows = [];
      this._headings = [];
      for (const item of this._config.rows) {
        if (item && item.heading !== undefined) {
          const heading = document.createElement("div");
          heading.className = "heading";
          heading.textContent = item.heading;
          card.appendChild(heading);
          this._headings.push({ element: heading, rows: [] });
          continue;
        }
        const row = this._buildRow(item);
        card.append(row.element, row.detail);
        this._rows.push(row);
        if (this._headings.length) this._headings[this._headings.length - 1].rows.push(row);
      }
      this._empty = document.createElement("div");
      this._empty.className = "empty";
      this._empty.textContent = this._config.empty || "";
      this._empty.hidden = true;
      card.appendChild(this._empty);
      this.shadowRoot.replaceChildren(style, card);
    }

    _searchField() {
      const search = document.createElement("div");
      search.className = "search";
      const icon = document.createElement("ha-icon");
      icon.icon = "mdi:magnify";
      const input = document.createElement("input");
      input.type = "search";
      input.placeholder = this._config.filter === true ? "" : String(this._config.filter);
      input.setAttribute("aria-label", input.placeholder);
      input.value = this._query;
      input.addEventListener("input", () => {
        this._query = input.value.trim().toLowerCase();
        this._show();
      });
      search.append(icon, input);
      return search;
    }

    _buildRow(item) {
      const element = document.createElement("div");
      element.className = "row";
      element.tabIndex = 0;
      element.setAttribute("role", "button");
      element.setAttribute("aria-expanded", "false");
      element.title = item.explanation || "";
      const icon = document.createElement("ha-state-icon");
      icon.className = "icon";
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = item.name || item.entity;
      const value = document.createElement("span");
      value.className = "value";
      const detail = document.createElement("div");
      detail.className = "detail";
      detail.hidden = true;
      fillDetail(detail, this._config, item, this);
      const toggle = () => {
        const open = detail.hidden;
        detail.hidden = !open;
        element.setAttribute("aria-expanded", String(open));
        if (open) this._open.add(item.entity);
        else this._open.delete(item.entity);
      };
      element.append(icon, name);
      if (item.explanation) {
        element.appendChild(explainButton(this._config.explain, toggle));
      }
      const gap = document.createElement("span");
      gap.className = "gap";
      element.append(gap, value);
      element.addEventListener("click", toggle);
      element.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          toggle();
        }
      });
      if (this._open.has(item.entity)) {
        detail.hidden = false;
        element.setAttribute("aria-expanded", "true");
      }
      return { item, element, detail, icon, value, seen: undefined, gone: false };
    }

    _update() {
      const hass = this._hass;
      if (!hass) return;
      for (const row of this._rows) {
        try {
          const stateObj = hass.states[row.item.entity];
          row.gone =
            Boolean(row.item.hide_unavailable) && (!stateObj || HIDDEN_STATES.has(stateObj.state));
          // Only when the state itself changed: every update of any entity in
          // Home Assistant sets hass again.
          if (!stateObj || row.seen === stateObj) continue;
          row.seen = stateObj;
          row.icon.hass = hass;
          row.icon.stateObj = stateObj;
          row.value.textContent = hass.formatEntityState
            ? hass.formatEntityState(stateObj)
            : `${stateObj.state} ${stateObj.attributes.unit_of_measurement || ""}`.trim();
        } catch (err) {
          row.value.textContent = "";
        }
      }
      this._show();
    }

    /** What is on show: what has a value to show, and what the search asks for. */
    _show() {
      let shown = 0;
      for (const row of this._rows) {
        const hidden = row.gone || !this._matches(row);
        row.element.hidden = hidden;
        if (hidden) row.detail.hidden = true;
        else {
          row.detail.hidden = !this._open.has(row.item.entity);
          shown += 1;
        }
      }
      // A heading with nothing under it says nothing.
      for (const heading of this._headings) {
        heading.element.hidden = !heading.rows.some((row) => !row.element.hidden);
      }
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

  if (!customElements.get("ctc-ecozenith-tile")) {
    customElements.define("ctc-ecozenith-tile", CtcEcoZenithTile);
  }
  if (!customElements.get("ctc-ecozenith-rows")) {
    customElements.define("ctc-ecozenith-rows", CtcEcoZenithRows);
  }
})();
