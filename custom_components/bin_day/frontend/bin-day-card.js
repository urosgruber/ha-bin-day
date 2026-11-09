// Bin Day card for Home Assistant. Plain custom element, no build step.
// The pure function buildModel() holds all logic and has Node tests.
// Dates come from the sensor attributes (HA's date); the browser clock is
// never used for dates.

const TEXTS = {
  en: {
    dayLong: ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"],
    dayShort: ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"],
    today: "today",
    tomorrow: "tomorrow",
    inDays: (n) => `in ${n} days`,
    prefixToday: "TODAY",
    prefixTomorrow: "TOMORROW",
    until: (t) => `until ${t}`,
    footnote: "* estimate – no data from the source",
    empty: "No collection data. Check the source.",
    noData: "No data",
    cardDescription: "Shows the next waste collection of a Bin Day address.",
    device: "Address (Bin Day device)",
    stripDays: "Days in the strip",
    hideTitle: "Hide the title",
  },
  sl: {
    dayLong: ["nedelja", "ponedeljek", "torek", "sreda", "četrtek", "petek", "sobota"],
    dayShort: ["ne", "po", "to", "sr", "če", "pe", "so"],
    today: "danes",
    tomorrow: "jutri",
    inDays: (n) => `čez ${n} dni`,
    prefixToday: "DANES",
    prefixTomorrow: "JUTRI",
    until: (t) => `do ${t}`,
    footnote: "* ocena – vir nima podatka",
    empty: "Ni podatkov o odvozu. Preveri vir podatkov.",
    noData: "Ni podatkov",
    cardDescription: "Prikaže naslednji odvoz odpadkov za naslov Bin Day.",
    device: "Naslov (naprava Bin Day)",
    stripDays: "Število dni v traku",
    hideTitle: "Skrij naslov",
  },
};

function texts(lang) {
  return String(lang || "").toLowerCase().startsWith("sl") ? TEXTS.sl : TEXTS.en;
}

const ISO_RE = /^\d{4}-\d{2}-\d{2}$/;

// Date arithmetic on ISO strings in UTC: no time zone of the browser applies.
function isoToUtc(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return Date.UTC(y, m - 1, d);
}

function addDays(iso, n) {
  const t = new Date(isoToUtc(iso) + n * 86400000);
  return t.toISOString().slice(0, 10);
}

function diffDays(from, to) {
  return Math.round((isoToUtc(to) - isoToUtc(from)) / 86400000);
}

function weekday(iso) {
  return new Date(isoToUtc(iso)).getUTCDay();
}

function dayMonth(iso) {
  const [, m, d] = iso.split("-").map(Number);
  return `${d}. ${m}.`;
}

// WCAG relative luminance of a #rgb or #rrggbb colour; null for other formats.
function luminance(color) {
  let hex = String(color || "").trim();
  if (/^#[0-9a-f]{3}$/i.test(hex)) hex = "#" + [...hex.slice(1)].map((c) => c + c).join("");
  if (!/^#[0-9a-f]{6}$/i.test(hex)) return null;
  const lin = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}

function foreground(color) {
  const l = luminance(color);
  return l !== null && l > 0.5 ? "#1c1c1c" : "#ffffff";
}

/**
 * Build the view model of the card.
 *
 * input: {title, today: "YYYY-MM-DD", done, until, stripDays,
 *         types: [{key, name, icon, color, status, estimate, days, dates}]}
 */
export function buildModel(input, lang) {
  const t = texts(lang);
  const today = input.today;
  const stripDays = Number(input.stripDays) || 14;
  const minDays = input.done ? 1 : 0;

  const noData = [];
  const items = [];
  for (const [order, type] of (input.types || []).entries()) {
    if (type.status === "never") continue;
    if (type.status === "no_data") {
      noData.push(type.name);
      continue;
    }
    const offsets = (type.dates || [])
      .filter((d) => ISO_RE.test(d))
      .map((d) => diffDays(today, d));
    if (!offsets.length && Number.isInteger(type.days)) offsets.push(type.days);
    let days = Number.isInteger(type.days) && type.days >= minDays ? type.days : null;
    const valid = offsets.filter((n) => n >= minDays).sort((a, b) => a - b);
    if (days === null) days = valid.length ? valid[0] : null;
    if (days === null) continue;
    items.push({ ...type, order, days, offsets: valid, estimate: !!type.estimate });
  }
  items.sort((a, b) => a.days - b.days || a.order - b.order);

  const star = (list) => (list.some((i) => i.estimate) ? "*" : "");
  const longDate = (iso, list) => `${t.dayLong[weekday(iso)]}, ${dayMonth(iso)}${star(list)}`;
  const shortDate = (iso, list) => `${t.dayShort[weekday(iso)]} ${dayMonth(iso)}${star(list)}`;
  const rel = (n) => (n === 0 ? t.today : n === 1 ? t.tomorrow : t.inDays(n));

  if (!items.length) {
    return {
      title: input.title,
      state: "empty",
      accent: null,
      headline: null,
      rest: [],
      noData,
      strip: [],
      footnote: false,
      until: input.until || null,
    };
  }

  const first = items[0];
  const group = items.filter((i) => i.days === first.days);
  const firstIso = addDays(today, first.days);
  const state = first.days === 0 ? "today" : first.days === 1 ? "tomorrow" : "quiet";
  const due = state !== "quiet";

  const strip = [];
  for (let k = 0; k < stripDays; k++) {
    const iso = addDays(today, k);
    const dots = items
      .filter((i) => i.offsets.includes(k))
      .map((i) => ({ color: i.color, estimate: i.estimate }));
    strip.push({ label: t.dayShort[weekday(iso)], iso, today: k === 0, dots });
  }

  return {
    title: input.title,
    state,
    accent: due ? { bg: first.color, fg: foreground(first.color) } : null,
    headline: {
      names: group.map((i) => i.name),
      icon: first.icon,
      color: first.color,
      dateText: longDate(firstIso, group),
      relText: rel(first.days),
      prefix: state === "today" ? t.prefixToday : state === "tomorrow" ? t.prefixTomorrow : "",
    },
    rest: items
      .filter((i) => i.days !== first.days)
      .map((i) => ({
        name: i.name,
        icon: i.icon,
        color: i.color,
        dateText: shortDate(addDays(today, i.days), [i]),
        relText: rel(i.days),
      })),
    noData,
    strip,
    footnote: items.some((i) => i.estimate),
    until: input.until || null,
  };
}

// ---------------------------------------------------------------------------
// Browser part. Node imports this module for the tests, so it defines the
// elements only when a custom element registry exists.

const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );

// A colour goes into a style attribute: allow only plain colour syntax.
const safeColor = (c) => (/^[#a-zA-Z0-9(),.%\s-]+$/.test(String(c || "")) ? c : "#757575");

const CSS = `
:host{display:block}
ha-card{padding:16px;text-align:left;height:100%;box-sizing:border-box}
.hdr{font-size:var(--ha-font-size-l,16px);font-weight:var(--ha-font-weight-medium,500);line-height:var(--ha-line-height-condensed,20px);color:inherit;padding:0 0 12px;text-align:left;overflow-wrap:anywhere}
.rest ha-icon{--mdc-icon-size:16px;width:16px;height:16px;flex:0 0 16px;margin:0;display:inline-flex}
.headline{display:flex;align-items:center;gap:12px}
.ico{width:40px;height:40px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex:none;background:rgba(255,255,255,.2)}
.ico ha-icon{--mdc-icon-size:22px;width:22px;height:22px;margin:0;display:inline-flex}
.big{font-size:17px;font-weight:600;line-height:1.25;overflow-wrap:anywhere}
.sub{font-size:13px;opacity:.75}
.rest{font-size:13px;opacity:.75;margin-top:8px;padding-top:6px;border-top:1px solid rgba(127,127,127,.25);display:flex;align-items:center;gap:6px;overflow-wrap:anywhere}
.strip{display:grid;gap:2px;margin-top:10px}
.d{text-align:center;font-size:10px;opacity:.6}
.d.today{opacity:1;font-weight:700}
.d i{display:block;height:8px;width:8px;border-radius:50%;margin:3px auto 0;background:rgba(127,127,127,.3)}
.foot{font-size:11px;opacity:.6;margin-top:6px}
`;

function renderHtml(model, hideTitle, lang) {
  const t = texts(lang);
  let html = hideTitle ? "" : `<div class="hdr">${esc(model.title)}</div>`;
  const noDataLine = model.noData.length
    ? `<div class="rest">${esc(t.noData)}: ${esc(model.noData.join(", "))}</div>`
    : "";
  if (model.state === "empty") {
    return html + `<div class="sub">${esc(t.empty)}</div>` + noDataLine;
  }
  const h = model.headline;
  const due = model.state !== "quiet";
  const names = h.names.join(" + ");
  const big = due ? `${h.prefix} · ${names}` : names;
  let sub;
  if (due) sub = model.until ? `${h.dateText} · ${t.until(model.until)}` : h.dateText;
  else sub = `${h.dateText} · ${h.relText}`;
  const c = safeColor(h.color);
  const icoStyle = due ? "" : `style="background:color-mix(in srgb, ${c} 20%, transparent);color:${c}"`;
  html +=
    `<div class="headline"><div class="ico" ${icoStyle}><ha-icon icon="${esc(h.icon)}"></ha-icon></div>` +
    `<div><div class="big">${esc(big)}</div><div class="sub">${esc(sub)}</div></div></div>`;
  for (const r of model.rest) {
    const color = due ? "inherit" : safeColor(r.color);
    html +=
      `<div class="rest"><ha-icon icon="${esc(r.icon)}" style="color:${color}"></ha-icon>` +
      `<span>${esc(r.name)} · ${esc(r.dateText)} · ${esc(r.relText)}</span></div>`;
  }
  html += noDataLine;
  html += `<div class="strip" style="grid-template-columns:repeat(${model.strip.length},1fr)">`;
  for (const day of model.strip) {
    let dot = "";
    if (day.dots.length) {
      const colors = day.dots.map((d) => safeColor(d.color));
      const allEstimate = day.dots.every((d) => d.estimate);
      if (due) {
        dot = allEstimate
          ? `style="background:transparent;box-shadow:inset 0 0 0 2px currentColor"`
          : `style="background:currentColor"`;
      } else if (allEstimate) {
        dot = `style="background:transparent;box-shadow:inset 0 0 0 2px ${colors[0]}"`;
      } else {
        const fill =
          colors.length > 1 ? `linear-gradient(90deg, ${colors[0]} 50%, ${colors[1]} 50%)` : colors[0];
        dot = `style="background:${fill}"`;
      }
    }
    html += `<div class="d${day.today ? " today" : ""}">${esc(day.label)}<i ${dot}></i></div>`;
  }
  html += "</div>";
  if (model.footnote) html += `<div class="foot">${esc(t.footnote)}</div>`;
  return html;
}

// The Bin Day devices: devices that own at least one bin_day entity.
function binDayDevices(hass) {
  const ids = [];
  for (const e of Object.values(hass?.entities || {})) {
    if (e.platform === "bin_day" && e.device_id && !ids.includes(e.device_id)) ids.push(e.device_id);
  }
  return ids;
}

// Read the entities of one device and turn them into the model input.
function readInput(hass, deviceId, stripDays) {
  const entityIds = Object.values(hass.entities || {})
    .filter((e) => e.platform === "bin_day" && e.device_id === deviceId)
    .map((e) => e.entity_id)
    .filter((id) => id.startsWith("sensor."));
  const typeStates = [];
  let next = null;
  for (const id of entityIds) {
    const s = hass.states[id];
    if (!s) continue;
    if (s.attributes.type_key !== undefined) typeStates.push(s);
    else if (s.attributes.entry_id !== undefined) next = s;
  }
  typeStates.sort((a, b) => (a.attributes.order ?? 0) - (b.attributes.order ?? 0));
  let today = next && ISO_RE.test(next.attributes.today || "") ? next.attributes.today : null;
  if (!today) {
    // Fall back to a type sensor: its date minus its days is HA's today.
    const s = typeStates.find((x) => ISO_RE.test(x.state) && Number.isInteger(x.attributes.days));
    if (s) today = addDays(s.state, -s.attributes.days);
  }
  const device = hass.devices?.[deviceId];
  return {
    entityIds,
    input: {
      title: device ? device.name_by_user || device.name : "Bin Day",
      today,
      done: !!next?.attributes.done,
      until: next?.attributes.until || null,
      stripDays,
      types: typeStates.map((s) => ({
        key: s.attributes.type_key,
        name: s.attributes.type_name,
        icon: s.attributes.icon || "mdi:delete",
        color: s.attributes.color || "#757575",
        status: s.attributes.status,
        estimate: !!s.attributes.estimate,
        days: s.attributes.days,
        dates: s.attributes.dates || [],
      })),
    },
  };
}

if (globalThis.customElements && !customElements.get("bin-day-card")) {
  class BinDayCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._signature = null;
    }

    static getConfigElement() {
      return document.createElement("bin-day-card-editor");
    }

    static getStubConfig(hass) {
      const first = binDayDevices(hass)[0];
      return first ? { device_id: first } : {};
    }

    setConfig(config) {
      if (!config) throw new Error("Invalid configuration");
      const days = Number(config.strip_days ?? 14);
      if (!Number.isInteger(days) || days < 7 || days > 21) {
        throw new Error("strip_days must be a whole number from 7 to 21");
      }
      this._config = { ...config, strip_days: days };
      this._signature = null;
      if (this._hass) this._render();
    }

    set hass(hass) {
      this._hass = hass;
      if (this._config) this._render();
    }

    getCardSize() {
      return 3;
    }

    _render() {
      const hass = this._hass;
      const lang = hass.locale?.language || hass.language || "en";
      const deviceId = this._config.device_id || binDayDevices(hass)[0];
      const { entityIds, input } = deviceId
        ? readInput(hass, deviceId, this._config.strip_days)
        : { entityIds: [], input: { title: "Bin Day", today: null, types: [] } };
      // Render again only when one of the entities changed.
      const signature = JSON.stringify([
        lang,
        input.title,
        this._config,
        entityIds.map((id) => hass.states[id]?.last_updated),
      ]);
      if (signature === this._signature) return;
      this._signature = signature;
      const model = input.today
        ? buildModel(input, lang)
        : buildModel({ ...input, today: "1970-01-01", types: [] }, lang);
      const a = model.accent;
      const cardStyle = a
        ? `background:${safeColor(a.bg)};color:${a.fg}`
        : "background:var(--ha-card-background, var(--card-background-color));color:var(--primary-text-color)";
      this.shadowRoot.innerHTML =
        `<style>${CSS}</style><ha-card style="${cardStyle}">` +
        renderHtml(model, !!this._config.hide_title, lang) +
        "</ha-card>";
    }
  }

  class BinDayCardEditor extends HTMLElement {
    setConfig(config) {
      this._config = config;
      this._render();
    }

    set hass(hass) {
      this._hass = hass;
      this._render();
    }

    _render() {
      if (!this._hass || !this._config) return;
      if (!this._form) {
        this._form = document.createElement("ha-form");
        this._form.schema = [
          { name: "device_id", selector: { device: { integration: "bin_day" } } },
          { name: "strip_days", selector: { number: { min: 7, max: 21, mode: "box" } } },
          { name: "hide_title", selector: { boolean: {} } },
        ];
        this._form.addEventListener("value-changed", (ev) => {
          const config = { ...this._config, ...ev.detail.value };
          for (const key of Object.keys(config)) {
            if (config[key] === undefined || config[key] === "") delete config[key];
          }
          this._config = config;
          this.dispatchEvent(
            new CustomEvent("config-changed", { detail: { config }, bubbles: true, composed: true }),
          );
        });
        this.appendChild(this._form);
      }
      const t = texts(this._hass.locale?.language || this._hass.language);
      const labels = { device_id: t.device, strip_days: t.stripDays, hide_title: t.hideTitle };
      this._form.computeLabel = (s) => labels[s.name] || s.name;
      this._form.hass = this._hass;
      this._form.data = { strip_days: 14, hide_title: false, ...this._config };
    }
  }

  customElements.define("bin-day-card", BinDayCard);
  customElements.define("bin-day-card-editor", BinDayCardEditor);

  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "bin-day-card",
    name: "Bin Day",
    description: texts(navigator.language).cardDescription,
    preview: true,
  });
}
