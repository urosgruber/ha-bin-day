// Tests for the pure model of the Bin Day card. Run: node --test tests/js/
import { test } from "node:test";
import assert from "node:assert/strict";

import { buildModel } from "../../custom_components/bin_day/frontend/bin-day-card.js";

const TODAY = "2026-11-09"; // a Monday

function addDays(iso, n) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}

function type(key, days, extra = {}) {
  const base = {
    mko: { name: "Mešani komunalni odpadki", icon: "mdi:trash-can", color: "#00a136" },
    emb: { name: "Embalaža", icon: "mdi:recycle", color: "#ffdd00" },
    bio: { name: "Biološki odpadki", icon: "mdi:leaf", color: "#8d6e63" },
  }[key];
  const estimate = extra.estimate ?? false;
  return {
    key,
    ...base,
    status: days === null ? "never" : estimate ? "estimate" : "real",
    estimate,
    days,
    dates: days === null ? [] : [addDays(TODAY, days)],
    ...extra,
  };
}

function input(types, extra = {}) {
  return { title: "Home", today: TODAY, done: false, until: null, types, stripDays: 14, ...extra };
}

test("model: quiet day", () => {
  const m = buildModel(input([type("mko", 4), type("emb", 11)]), "sl");
  assert.equal(m.state, "quiet");
  assert.equal(m.accent, null);
  assert.deepEqual(m.headline.names, ["Mešani komunalni odpadki"]);
  assert.equal(m.headline.prefix, "");
  assert.equal(m.rest.length, 1);
  assert.equal(m.rest[0].name, "Embalaža");
  assert.equal(m.strip.length, 14);
  const coloured = m.strip.map((s, i) => (s.dots.length ? i : -1)).filter((i) => i >= 0);
  assert.deepEqual(coloured, [4, 11]);
  assert.deepEqual(m.strip[4].dots, [{ color: "#00a136", estimate: false }]);
  assert.equal(m.headline.dateText, "petek, 13. 11.");
  assert.equal(m.headline.relText, "čez 4 dni");
  assert.equal(m.footnote, false);
});

test("model: tomorrow accent", () => {
  const m = buildModel(input([type("mko", 1), type("emb", 8)]), "sl");
  assert.equal(m.state, "tomorrow");
  assert.deepEqual(m.accent, { bg: "#00a136", fg: "#ffffff" });
  assert.equal(m.headline.prefix, "JUTRI");
  assert.equal(m.headline.relText, "jutri");
});

test("model: yellow uses dark text", () => {
  const m = buildModel(input([type("emb", 0), type("mko", 3)]), "en");
  assert.equal(m.state, "today");
  assert.deepEqual(m.accent, { bg: "#ffdd00", fg: "#1c1c1c" });
  assert.equal(m.headline.prefix, "TODAY");
  assert.equal(m.headline.relText, "today");
  assert.equal(m.headline.dateText, "Monday, 9. 11.");
});

test("model: done drops today", () => {
  const m = buildModel(input([type("emb", 0), type("mko", 7)], { done: true }), "en");
  assert.equal(m.state, "quiet");
  assert.deepEqual(m.headline.names, ["Mešani komunalni odpadki"]);
  assert.equal(m.rest.length, 0);
  assert.equal(m.strip[0].dots.length, 0);
});

test("model: two types same day", () => {
  const m = buildModel(input([type("mko", 1), type("emb", 1)]), "sl");
  assert.equal(m.state, "tomorrow");
  assert.deepEqual(m.headline.names, ["Mešani komunalni odpadki", "Embalaža"]);
  assert.equal(m.rest.length, 0);
  assert.deepEqual(
    m.strip[1].dots.map((d) => d.color),
    ["#00a136", "#ffdd00"],
  );
});

test("model: estimate", () => {
  const m = buildModel(input([type("emb", 5, { estimate: true })]), "sl");
  assert.ok(m.headline.dateText.endsWith("*"));
  assert.deepEqual(m.strip[5].dots, [{ color: "#ffdd00", estimate: true }]);
  assert.equal(m.footnote, true);
});

test("model: never hidden, no_data listed", () => {
  const noData = { ...type("emb", null), status: "no_data" };
  const m = buildModel(input([type("mko", 2), noData, type("bio", null)]), "en");
  const shown = [...m.headline.names, ...m.rest.map((r) => r.name)];
  assert.ok(!shown.includes("Biološki odpadki"));
  assert.ok(!m.noData.includes("Biološki odpadki"));
  assert.deepEqual(m.noData, ["Embalaža"]);
});

test("model: empty", () => {
  const m = buildModel(input([type("mko", null), { ...type("emb", null), status: "no_data" }]), "en");
  assert.equal(m.state, "empty");
  assert.equal(m.accent, null);
});

test("model: uses server today, not browser clock", () => {
  const RealDate = globalThis.Date;
  const FAKE = RealDate.UTC(2030, 5, 15, 12, 0, 0);
  class FakeDate extends RealDate {
    constructor(...args) {
      super(...(args.length ? args : [FAKE]));
    }
    static now() {
      return FAKE;
    }
  }
  globalThis.Date = FakeDate;
  try {
    const m = buildModel(input([type("mko", 4)]), "sl");
    assert.equal(m.strip[0].label, "po");
    assert.equal(m.strip[0].iso, "2026-11-09");
    assert.equal(m.strip[0].today, true);
    assert.equal(m.strip[1].today, false);
    assert.equal(m.headline.dateText, "petek, 13. 11.");
  } finally {
    globalThis.Date = RealDate;
  }
});
