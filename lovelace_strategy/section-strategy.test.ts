import { describe, expect, it, vi } from "vitest";
import { STATE_NOT_RUNNING } from "home-assistant-js-websocket";
import { HomeAssistant } from "./ha_type_stubs";
import { KeymasterSectionStrategy } from "./section-strategy";
import { KeymasterSectionStrategyConfig } from "./types";

function createMockHass(overrides: Partial<HomeAssistant> = {}): HomeAssistant {
  return {
    callWS: vi.fn(),
    config: { state: "RUNNING" },
    ...overrides,
  } as unknown as HomeAssistant;
}

describe("KeymasterSectionStrategy", () => {
  describe("generate", () => {
    it("returns starting view when HA is not running", async () => {
      const hass = createMockHass({
        config: { state: STATE_NOT_RUNNING } as unknown as HomeAssistant["config"],
      });

      const result = await KeymasterSectionStrategy.generate(
        { type: "custom:keymaster", config_entry_id: "abc123", slot_num: 1 },
        hass,
      );

      expect(result.cards).toHaveLength(1);
      expect(result.cards![0]).toEqual({ type: "starting" });
    });

    it("returns error when neither config_entry_id nor lock_name provided", async () => {
      const hass = createMockHass();

      const result = await KeymasterSectionStrategy.generate(
        {
          type: "custom:keymaster",
          slot_num: 1,
        } as unknown as KeymasterSectionStrategyConfig,
        hass,
      );

      expect(result.cards).toHaveLength(1);
      expect(result.cards![0]).toHaveProperty("type", "markdown");
      expect(
        (result.cards![0] as { content: string }).content,
      ).toContain("Either config_entry_id or lock_name is required");
    });

    it("returns error when slot_num is missing", async () => {
      const hass = createMockHass();

      const result = await KeymasterSectionStrategy.generate(
        {
          type: "custom:keymaster",
          config_entry_id: "abc123",
        } as unknown as KeymasterSectionStrategyConfig,
        hass,
      );

      expect(result.cards).toHaveLength(1);
      expect(result.cards![0]).toHaveProperty("type", "markdown");
      expect(
        (result.cards![0] as { content: string }).content,
      ).toContain("slot_num is required");
    });

    it("calls websocket and returns config", async () => {
      const mockSection = { type: "grid", cards: [{ type: "entities" }] };
      const hass = createMockHass({
        callWS: vi.fn().mockResolvedValue(mockSection),
      });

      const result = await KeymasterSectionStrategy.generate(
        { type: "custom:keymaster", config_entry_id: "abc123", slot_num: 1 },
        hass,
      );

      expect(hass.callWS).toHaveBeenCalledWith({
        type: "keymaster/get_section_config",
        config_entry_id: "abc123",
        slot_num: 1,
      });
      expect(result).toEqual(mockSection);
    });

    it("handles websocket errors", async () => {
      const hass = createMockHass({
        callWS: vi.fn().mockRejectedValue(new Error("WS Error")),
      });

      const result = await KeymasterSectionStrategy.generate(
        { type: "custom:keymaster", lock_name: "Front Door", slot_num: 1 },
        hass,
      );

      expect(result.cards).toHaveLength(1);
      expect(
        (result.cards![0] as { content: string }).content,
      ).toContain("WS Error");
    });

    it("handles non-Error catch values", async () => {
      const hass = createMockHass({
        callWS: vi.fn().mockRejectedValue("string error"),
      });

      const result = await KeymasterSectionStrategy.generate(
        { type: "custom:keymaster", lock_name: "Front Door", slot_num: 1 },
        hass,
      );

      expect(result.cards).toHaveLength(1);
      expect(
        (result.cards![0] as { content: string }).content,
      ).toContain("Failed to load section");
    });
  });
});
