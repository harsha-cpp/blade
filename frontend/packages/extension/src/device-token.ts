export const DEVICE_TOKEN_STORAGE_KEY = "blade_device_token";
export const DEVICE_TOKEN_HEADER = "X-Blade-Device-Token";
export const DEVICE_TOKEN_PREFIX = "bladedev_";
export const DEVICE_TOKEN_MAX_CHARS = 256;

const DEVICE_TOKEN_SHAPE =
  /^bladedev_[A-Za-z0-9-]{8,48}\.[A-Za-z0-9_-]{16,180}$/;

const COPY_IT_AGAIN = "Copy it again from the dashboard Devices page.";

export type DeviceTokenCheck =
  | { ok: true; token: string }
  | { ok: false; message: string };

export function checkDeviceToken(raw: string): DeviceTokenCheck {
  const token = raw.trim();
  if (!token) return { ok: false, message: "Paste a device token first." };
  if (/\s/.test(token)) {
    return { ok: false, message: "Remove the spaces from the device token." };
  }
  if (token.length > DEVICE_TOKEN_MAX_CHARS) {
    return { ok: false, message: "That device token is too long." };
  }
  if (!token.startsWith(DEVICE_TOKEN_PREFIX)) {
    return {
      ok: false,
      message: `A device token starts with ${DEVICE_TOKEN_PREFIX}. ${COPY_IT_AGAIN}`,
    };
  }
  if (!DEVICE_TOKEN_SHAPE.test(token)) {
    return {
      ok: false,
      message: `That device token is not in the expected format. ${COPY_IT_AGAIN}`,
    };
  }
  return { ok: true, token };
}

export function maskDeviceToken(token: string): string {
  return `${DEVICE_TOKEN_PREFIX}...${token.slice(-4)}`;
}
