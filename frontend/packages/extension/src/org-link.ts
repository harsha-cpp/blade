export const LINK_ORG_MESSAGE = "LINK_ORG";

export type OrgLinkResult =
  | { ok: true; orgCode: string; organizationName: string }
  | { ok: false; message: string };

export function normalizeOrgCode(value: string): string | null {
  const code = value.trim().toUpperCase();
  return /^BLD-[A-Z0-9]{6,64}$/.test(code) ? code : null;
}

export async function requestOrgLink(code: string): Promise<OrgLinkResult> {
  try {
    const result: OrgLinkResult | undefined = await chrome.runtime.sendMessage({
      type: LINK_ORG_MESSAGE,
      code,
    });
    return (
      result ?? {
        ok: false,
        message: "Blade could not connect. Reload the extension and try again.",
      }
    );
  } catch {
    return {
      ok: false,
      message: "Blade could not connect. Reload the extension and try again.",
    };
  }
}
