export const TAB_PROTECTION_REQUEST = "BLADE_TAB_PROTECTION";

export type TabGuard = "gmail" | "website";

export function announceTabGuard(guard: TabGuard): void {
  chrome.runtime.onMessage?.addListener((message, sender, respond) => {
    if (
      sender.id !== chrome.runtime.id ||
      message?.type !== TAB_PROTECTION_REQUEST
    )
      return;
    respond({ guard, version: chrome.runtime.getManifest().version });
  });
}

export async function readTabGuard(tabId: number): Promise<TabGuard | null> {
  try {
    const response = await chrome.tabs.sendMessage(
      tabId,
      { type: TAB_PROTECTION_REQUEST },
      { frameId: 0 },
    );
    if (response?.version !== chrome.runtime.getManifest().version) return null;
    return response.guard === "website" || response.guard === "gmail"
      ? response.guard
      : null;
  } catch {
    return null;
  }
}
