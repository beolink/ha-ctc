/* CTC EcoZenith: the page's cards, with an explanation for every value.
 *
 * Served by the integration at /ctc_ecozenith/ctc-ecozenith-card.js and loaded as a
 * Lovelace resource. The page itself is built in Python (dashboard_views.py) and
 * uses two cards from here:
 *
 *   custom:ctc-ecozenith-tile   Home Assistant's own tile, left as it is, with the
 *                               value's explanation on hover and written out under
 *                               the tile when its name is tapped.
 *   custom:ctc-ecozenith-rows   a list of values, name on the left and value on the
 *                               right, explained the same way.
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
  `;

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
        .frame { height: var(--ctc-height, auto); }
        .explained { cursor: help; }
        ha-card.detail { margin-top: 8px; padding: 12px 16px; }
        ${DETAIL_STYLE}
      `;
      this._frame = document.createElement("div");
      this._frame.className = "frame";
      this._detail = document.createElement("ha-card");
      this._detail.className = "detail";
      this._detail.hidden = true;
      this.shadowRoot.append(style, this._frame, this._detail);
      this._frame.addEventListener("click", (event) => this._clicked(event));
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
        this._frame.appendChild(this._tile);
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
      if (explained) fillDetail(this._detail, config, { ...config, entity: config.tile.entity }, this);
      this._setOpen(this._open && explained);
    }

    set hass(hass) {
      this._hass = hass;
      if (this._tile) this._tile.hass = hass;
    }

    set layout(layout) {
      this._layout = layout;
      if (this._tile) this._tile.layout = layout;
    }

    set preview(preview) {
      if (this._tile) this._tile.preview = preview;
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
          flex: 1;
          min-width: 0;
          margin-left: 16px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          color: var(--primary-text-color);
        }
        .value {
          flex: none;
          margin-left: 16px;
          text-align: right;
          white-space: nowrap;
          color: var(--primary-text-color);
        }
        .detail { padding: 0 16px 10px 72px; }
        ${DETAIL_STYLE}
      `;
      const card = document.createElement("ha-card");
      this._rows = this._config.rows.map((item) => {
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
        element.append(icon, name, value);
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
        card.append(element, detail);
        return { item, element, detail, icon, value, seen: undefined };
      });
      this.shadowRoot.replaceChildren(style, card);
    }

    _update() {
      const hass = this._hass;
      if (!hass) return;
      for (const row of this._rows) {
        try {
          const stateObj = hass.states[row.item.entity];
          const hidden =
            Boolean(row.item.hide_unavailable) && (!stateObj || HIDDEN_STATES.has(stateObj.state));
          row.element.hidden = hidden;
          if (hidden) row.detail.hidden = true;
          else if (this._open.has(row.item.entity)) row.detail.hidden = false;
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
    }
  }

  if (!customElements.get("ctc-ecozenith-tile")) {
    customElements.define("ctc-ecozenith-tile", CtcEcoZenithTile);
  }
  if (!customElements.get("ctc-ecozenith-rows")) {
    customElements.define("ctc-ecozenith-rows", CtcEcoZenithRows);
  }
})();
