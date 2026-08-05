import { describe, expect, it } from "vitest";
import { NA, isUnavailable, money, percent, tone } from "../lib/api";

/**
 * The frontend's one job in the financial path is to not lie about
 * unavailability. These tests pin that down.
 */
describe("unavailable is never zero", () => {
  it("renders N/A for an unavailable money measure", () => {
    expect(money({ value: null, available: false, reason: "no baseline" })).toBe(NA);
  });

  it("renders N/A for an unavailable ratio, not 0%", () => {
    expect(percent({ value: null, available: false })).toBe(NA);
    expect(percent({ value: null, available: false })).not.toBe("0.0%");
  });

  it("still renders a real zero as zero", () => {
    expect(money({ value: 0, available: true })).toBe("$0.00");
    expect(percent({ value: 0, available: true })).toBe("0.0%");
  });

  it("distinguishes zero from unavailable", () => {
    expect(money({ value: 0, available: true })).not.toBe(money({ value: null, available: false }));
  });

  it("treats a missing measure as unavailable rather than zero", () => {
    expect(money(undefined)).toBe(NA);
    expect(money(null)).toBe(NA);
    expect(isUnavailable(undefined)).toBe(true);
  });

  it("never marks an unavailable measure as adverse", () => {
    expect(tone({ value: null, available: false })).toBe("none");
    expect(tone({ value: 0.5, available: true })).toBe("bad");
  });
});

describe("currency formatting", () => {
  it("formats to cents in AUD", () => {
    expect(money({ value: "3701892.60", available: true })).toBe("$3,701,892.60");
  });

  it("shows negatives in accounting parentheses", () => {
    expect(money({ value: -659271.01, available: true })).toBe("($659,271.01)");
  });

  it("accepts the string decimals the API sends without losing cents", () => {
    expect(money({ value: "3979534.545", available: true })).toBe("$3,979,534.55");
  });
});

describe("percentages", () => {
  it("scales a ratio to a percentage", () => {
    expect(percent({ value: 0.9733, available: true })).toBe("97.3%");
  });
  it("handles achievement above target", () => {
    expect(percent({ value: 1.1683, available: true })).toBe("116.8%");
  });
});
