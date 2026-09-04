import { describe, expect, it } from "vitest";
import { checkDeviceToken, maskDeviceToken } from "./device-token";

const VALID =
  "bladedev_0193f2c4-8e4a-7f11-9c33-4a2b1d6e5f70.qkR3s7Xm2wPzT8vLbN4hGdJyQ1eF6uCa";

describe("device token format", () => {
  it("accepts a token issued by the dashboard", () => {
    expect(checkDeviceToken(VALID)).toEqual({ ok: true, token: VALID });
  });

  it("accepts a token pasted with surrounding whitespace", () => {
    expect(checkDeviceToken(`  ${VALID}\n`)).toEqual({
      ok: true,
      token: VALID,
    });
  });

  it("asks for a token when the field is empty", () => {
    const checked = checkDeviceToken("   ");
    expect(checked.ok).toBe(false);
    expect(checked).toMatchObject({ message: "Paste a device token first." });
  });

  it("rejects a token with whitespace inside it", () => {
    const checked = checkDeviceToken(VALID.replace(".", " . "));
    expect(checked.ok).toBe(false);
    expect(checked).toMatchObject({
      message: "Remove the spaces from the device token.",
    });
  });

  it("names the prefix when something else was pasted", () => {
    const checked = checkDeviceToken("BLD-ABC123");
    expect(checked.ok).toBe(false);
    if (!checked.ok) expect(checked.message).toContain("bladedev_");
  });

  it("rejects a token that carries no secret", () => {
    expect(checkDeviceToken("bladedev_0193f2c4-8e4a-7f11").ok).toBe(false);
    expect(checkDeviceToken(`${VALID.split(".")[0]}.`).ok).toBe(false);
  });

  it("rejects a token with more than one separator", () => {
    expect(checkDeviceToken(`${VALID}.extra`).ok).toBe(false);
  });

  it("rejects a token beyond the stored length bound", () => {
    const checked = checkDeviceToken(`${VALID}${"a".repeat(300)}`);
    expect(checked.ok).toBe(false);
    expect(checked).toMatchObject({
      message: "That device token is too long.",
    });
  });

  it("shows only the prefix and the last four characters", () => {
    const masked = maskDeviceToken(VALID);
    expect(masked).toBe("bladedev_...6uCa");
    expect(masked).not.toContain("qkR3s7Xm");
  });
});
