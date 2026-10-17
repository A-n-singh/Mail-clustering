import { describe, expect, it } from "vitest";
import { fmtBytes, qs } from "./api.js";

describe("api helpers", () => {
  it("formats bytes", () => {
    expect(fmtBytes(500)).toBe("500 B");
    expect(fmtBytes(2048)).toBe("2.0 KB");
    expect(fmtBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });
  it("builds query strings and skips empty filters", () => {
    expect(qs({ sender: "amz", label: "", min_score: 0 })).toBe("?sender=amz");
    expect(qs({})).toBe("");
  });
});
