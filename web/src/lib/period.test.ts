import { describe, expect, it } from "vitest";

import { monthsBetween, presetRange, previousMonth, todayInBrazil } from "./period";

describe("períodos do painel", () => {
  it("presets começam no 1º dia do mês", () => {
    expect(presetRange("6m", "2026-11-06")).toEqual({ start: "2026-06-01", end: "2026-11-06" });
    expect(presetRange("3m", "2026-02-15")).toEqual({ start: "2025-12-01", end: "2026-02-15" });
    expect(presetRange("12m", "2026-11-06").start).toBe("2025-12-01");
    expect(presetRange("ano", "2026-11-06").start).toBe("2026-01-01");
  });

  it("lista todos os meses, inclusive sem gasto", () => {
    expect(monthsBetween("2025-11-20", "2026-02-01")).toEqual(["2025-11", "2025-12", "2026-01", "2026-02"]);
  });

  it("mês anterior atravessa o ano", () => {
    expect(previousMonth("2026-01")).toBe("2025-12");
  });

  it("hoje é calculado no fuso de Brasília", () => {
    // 02:00 UTC on 11/7 is still 11/6 in Brasília (UTC-3).
    expect(todayInBrazil(new Date("2026-11-07T02:00:00Z"))).toBe("2026-11-06");
  });
});
