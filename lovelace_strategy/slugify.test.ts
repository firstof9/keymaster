import { describe, expect, it } from "vitest";
import { slugify } from "./slugify";

describe("slugify", () => {
  it("slugifies simple strings", () => {
    expect(slugify("Hello World")).toBe("hello-world");
  });

  it("handles empty input", () => {
    expect(slugify("")).toBe("");
  });

  it("handles strings that result in empty slug", () => {
    expect(slugify("!!!")).toBe("unknown");
  });

  it("handles special characters", () => {
    expect(slugify("àáâä")).toBe("aaaa");
  });
});
