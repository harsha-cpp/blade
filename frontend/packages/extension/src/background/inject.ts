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

export interface InjectionSummary {
  injected: number;
  skipped: number;
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

export function isGmailUrl(url: string): boolean {
  return url.startsWith(`https://${GMAIL_HOST}/`);
}

export async function injectIntoOpenTabs(): Promise<InjectionSummary> {
  const scripts = (chrome.runtime.getManifest().content_scripts ??
    []) as ManifestContentScript[];
  const target = gmailTarget(scripts);
  if (!target) return { injected: 0, skipped: 0 };

  const tabs = await chrome.tabs.query({});
  const summary: InjectionSummary = { injected: 0, skipped: 0 };

  for (const tab of tabs) {
    const tabId = tab.id;
    const url = tab.url ?? tab.pendingUrl ?? "";
    if (tabId === undefined || tabId < 0) continue;
    if (!isGmailUrl(url)) {
      summary.skipped += 1;
      continue;
    }
    try {
      await chrome.scripting.executeScript({
        target: { tabId, allFrames: target.allFrames },
        files: target.files,
      });
      summary.injected += 1;
    } catch {
      summary.skipped += 1;
    }
  }

  return summary;
}

export function injectionSummaryLine(summary: InjectionSummary): string {
  return `[Blade] injected the Gmail guard into ${summary.injected} open tab(s); ${summary.skipped} tab(s) keep protection from their next navigation or reload`;
}
