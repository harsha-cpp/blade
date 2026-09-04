const GMAIL_HOST = "mail.google.com";

export interface ManifestContentScript {
  js?: string[] | undefined;
  matches?: string[] | undefined;
  all_frames?: boolean | undefined;
}

export interface InjectionTarget {
  files: string[];
  allFrames: boolean;
}

function coversGmail(script: ManifestContentScript): boolean {
  return (script.matches ?? []).some((pattern) => pattern.includes(GMAIL_HOST));
}

function toTarget(
  script: ManifestContentScript | undefined,
): InjectionTarget | null {
  const files = script?.js ?? [];
  if (files.length === 0) return null;
  return { files: [...files], allFrames: script?.all_frames === true };
}

export function gmailTarget(
  scripts: ManifestContentScript[],
): InjectionTarget | null {
  return toTarget(scripts.find(coversGmail));
}

export function webGuardTarget(
  scripts: ManifestContentScript[],
): InjectionTarget | null {
  return toTarget(scripts.find((script) => !coversGmail(script)));
}

export function isGmailUrl(url: string): boolean {
  return url.startsWith(`https://${GMAIL_HOST}/`);
}

export function isInjectableUrl(url: string): boolean {
  return url.startsWith("http://") || url.startsWith("https://");
}

export function targetForUrl(
  url: string,
  targets: { gmail: InjectionTarget | null; webGuard: InjectionTarget | null },
): InjectionTarget | null {
  if (!isInjectableUrl(url)) return null;
  return isGmailUrl(url) ? targets.gmail : targets.webGuard;
}

export async function injectIntoOpenTabs(): Promise<number> {
  const scripts = (chrome.runtime.getManifest().content_scripts ??
    []) as ManifestContentScript[];
  const targets = {
    gmail: gmailTarget(scripts),
    webGuard: webGuardTarget(scripts),
  };
  if (!targets.gmail && !targets.webGuard) return 0;

  const tabs = await chrome.tabs.query({});
  let injected = 0;

  for (const tab of tabs) {
    const tabId = tab.id;
    const url = tab.url ?? tab.pendingUrl ?? "";
    if (tabId === undefined || tabId < 0) continue;
    const target = targetForUrl(url, targets);
    if (!target) continue;
    try {
      await chrome.scripting.executeScript({
        target: { tabId, allFrames: target.allFrames },
        files: target.files,
      });
      injected += 1;
    } catch {
      continue;
    }
  }

  return injected;
}
