import { useEffect, useState } from "react";
import { readTabGuard, type TabGuard } from "../tab-protection";

type TabStatus = { tabId: number; guard: TabGuard | null };

export function TabProtection() {
  const [status, setStatus] = useState<TabStatus | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let generation = 0;
    let disposed = false;
    async function check() {
      const checkId = ++generation;
      const [tab] = await chrome.tabs.query({
        active: true,
        currentWindow: true,
      });
      let result: TabStatus | null = null;
      // Chrome may omit the URL. The content script can still answer by tab ID.
      if (tab?.id !== undefined && (!tab.url || /^https?:\/\//.test(tab.url))) {
        result = { tabId: tab.id, guard: await readTabGuard(tab.id) };
      }
      if (!disposed && checkId === generation) {
        setStatus(result);
        setRefreshing(false);
      }
    }
    const run = () => {
      void check().catch(() => {});
    };
    const updated = (
      _tabId: number,
      change: chrome.tabs.TabChangeInfo,
      tab: chrome.tabs.Tab,
    ) => {
      if (!tab.active) return;
      if (change.status === "loading") {
        generation += 1;
        setStatus(null);
      }
      if (change.status === "complete") run();
    };
    run();
    chrome.tabs.onUpdated.addListener(updated);
    chrome.tabs.onActivated.addListener(run);
    return () => {
      disposed = true;
      chrome.tabs.onUpdated.removeListener(updated);
      chrome.tabs.onActivated.removeListener(run);
    };
  }, []);

  if (!status) return null;
  const label =
    status.guard === "website"
      ? "Website protection active"
      : status.guard === "gmail"
        ? "Gmail protection active"
        : "Protection unverified on this tab";

  return (
    <div className={`status-card${status.guard ? "" : " is-unlinked"}`}>
      <span className="status-dot" aria-hidden="true" />
      <div>
        <div className="status-label" role="status">
          {label}
        </div>
        {status.guard === "website" && (
          <div className="status-desc">
            Blade checks text and attachments on this tab.
          </div>
        )}
        {!status.guard && (
          <>
            <div className="status-desc">
              Refresh the website after installing or updating Blade. Save any
              unsent work first.
            </div>
            <button
              type="button"
              className="settings-btn is-quiet tab-refresh-button"
              disabled={refreshing}
              onClick={async () => {
                setRefreshing(true);
                setFailed(false);
                try {
                  await chrome.tabs.reload(status.tabId);
                } catch {
                  setRefreshing(false);
                  setFailed(true);
                }
              }}
            >
              {refreshing ? "Refreshing…" : "Refresh this tab"}
            </button>
            {failed && (
              <div className="settings-error" role="alert">
                Refresh the site using Chrome.
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
