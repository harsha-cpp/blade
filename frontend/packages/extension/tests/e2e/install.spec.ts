import {
  test,
  expect,
  chromium,
  type BrowserContext,
  type Worker,
} from "@playwright/test";
import { mkdtemp, rm, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";

const root = resolve(import.meta.dirname, "../../../../..");
const release = JSON.parse(
  await readFile(join(root, "landing/src/lib/extension-release.json"), "utf8"),
);
const website = process.env["BLADE_LANDING_URL"] ?? "http://127.0.0.1:3100";

async function launch(
  profile: string,
  extension: string,
): Promise<BrowserContext> {
  return chromium.launchPersistentContext(profile, {
    channel: "chromium",
    headless: true,
    args: [
      `--disable-extensions-except=${extension}`,
      `--load-extension=${extension}`,
    ],
  });
}

async function backendFixture(
  context: BrowserContext,
): Promise<{ worker: Worker; popupUrl: string }> {
  const worker =
    context.serviceWorkers()[0] ??
    (await context.waitForEvent("serviceworker"));
  // Exercise the published extension with deterministic API responses, including failure cases.
  // A live success check requires an administrator-issued production credential.
  await worker.evaluate(() => {
    const nativeFetch = globalThis.fetch;
    globalThis.fetch = async (input, init) => {
      const url = String(input);
      if (!url.startsWith("https://api.tryblade.in/"))
        return nativeFetch(input, init);
      if (url.includes("BLD-OFFLINE"))
        throw new TypeError("Test network unavailable");
      if (url.includes("/public/orgs/")) {
        if (!url.includes("/BLD-ABC123/"))
          return Response.json({ detail: "unknown org code" }, { status: 404 });
        return Response.json({
          organization: { name: "Test Hospital", org_code: "BLD-ABC123" },
          domains: [
            {
              domain: "hospital.example",
              direction: "both",
              classification: "internal",
            },
          ],
          blocked_domains: [],
          fail_open: false,
        });
      }
      if (url.endsWith("/devices/me"))
        return Response.json({ detail: "invalid token" }, { status: 401 });
      if (url.endsWith("/events"))
        return Response.json({ id: "test-event" }, { status: 201 });
      return Response.json({}, { status: 404 });
    };
  });
  const id = new URL(worker.url()).host;
  return { worker, popupUrl: `chrome-extension://${id}/src/popup/index.html` };
}

test("landing download opens the installation guide and serves the verified ZIP", async ({
  page,
}, testInfo) => {
  await page.goto(website);
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download for Chrome" }).first().click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe(release.filename);
  const saved = testInfo.outputPath(release.filename);
  await download.saveAs(saved);
  expect(
    createHash("sha256")
      .update(await readFile(saved))
      .digest("hex"),
  ).toBe(release.sha256);
  await expect(page).toHaveURL(/\/install\?download=started$/);
  await expect(
    page.getByRole("heading", { name: "Give Blade a home." }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Next step" }).click();
  await expect(
    page.getByRole("heading", { name: "Open your extensions." }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Next step" }).click();
  await expect(
    page.getByRole("heading", { name: "Load the folder." }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Next step" }).click();
  await expect(
    page.getByRole("heading", { name: "Connect your organization." }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Next step" }).click();
  await expect(
    page.getByRole("heading", { name: "One link. Every session." }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("install-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("install-mobile.png"),
    fullPage: true,
  });
});

test("a failed download stays on the landing page and offers retry", async ({
  page,
}) => {
  await page.route(`**${release.downloadPath}`, (route) =>
    route.fulfill({ status: 503, body: "Unavailable" }),
  );
  await page.goto(website);
  await page.getByRole("link", { name: "Download for Chrome" }).first().click();
  await expect(
    page.getByRole("alert").filter({ hasText: "Download could not start." }),
  ).toHaveText("Download could not start. Please try again.");
  await expect(page).toHaveURL(`${website}/`);
});

test("downloaded extension validates linking once, preserves it across restart, and blocks patient identifiers", async ({}, testInfo) => {
  const directory = await mkdtemp(join(tmpdir(), "blade-install-"));
  const profile = join(directory, "profile");
  execFileSync("unzip", [
    "-q",
    join(root, `landing/public${release.downloadPath}`),
    "-d",
    directory,
  ]);
  const extension = join(directory, "blade-extension");
  let context = await launch(profile, extension);
  try {
    let { worker, popupUrl } = await backendFixture(context);
    const popup = await context.newPage();
    await popup.goto(popupUrl);
    await expect(popup.getByText("Not linked", { exact: true })).toBeVisible();
    await popup.getByLabel("Organization code").fill("BLD-INVALID");
    await popup.getByRole("button", { name: "Link", exact: true }).click();
    await expect(popup.getByRole("alert")).toContainText("not found");
    expect(
      await worker.evaluate(
        async () =>
          (await chrome.storage.local.get("blade_org_code")).blade_org_code,
      ),
    ).toBeUndefined();
    await popup.getByLabel("Organization code").fill(" bld-abc123 ");
    await popup.getByRole("button", { name: "Link", exact: true }).click();
    await expect(
      popup.getByText("Protection on", { exact: true }),
    ).toBeVisible();
    await expect(
      popup.getByRole("button", { name: "Link", exact: true }),
    ).toBeDisabled();
    await popup.getByLabel("Organization code").fill("BLD-OFFLINE");
    await popup.getByRole("button", { name: "Link", exact: true }).click();
    await expect(popup.getByRole("alert")).toContainText("Could not reach");
    expect(
      await worker.evaluate(
        async () =>
          (await chrome.storage.local.get("blade_org_code")).blade_org_code,
      ),
    ).toBe("BLD-ABC123");
    await popup
      .getByLabel("Device token")
      .fill("bladedev_0192abcd.secretsecretsecretsecret");
    await popup.getByRole("button", { name: "Save", exact: true }).click();
    await expect(
      popup.getByText(/This device token was rejected/),
    ).toBeVisible();
    await popup.screenshot({ path: testInfo.outputPath("popup.png") });
    await context.close();
    context = await launch(profile, extension);
    ({ worker, popupUrl } = await backendFixture(context));
    expect(
      await worker.evaluate(
        async () =>
          (await chrome.storage.local.get("blade_org_code")).blade_org_code,
      ),
    ).toBe("BLD-ABC123");
    const reopened = await context.newPage();
    await reopened.goto(popupUrl);
    await reopened.evaluate(() =>
      chrome.runtime.sendMessage({ type: "REFRESH_CONFIG" }),
    );
    await expect(
      reopened.getByText("Protection on", { exact: true }),
    ).toBeVisible();
    await expect(reopened.getByLabel("Organization code")).toHaveValue(
      "BLD-ABC123",
    );
    await expect(
      reopened.getByRole("button", { name: "Link", exact: true }),
    ).toBeDisabled();
    await context.route("https://blade-test.example/**", (route) =>
      route.fulfill({
        contentType: "text/html",
        body: '<!doctype html><title>Test form</title><textarea aria-label="Message"></textarea><button>Send</button>',
      }),
    );
    const web = await context.newPage();
    await web.goto("https://blade-test.example/");
    await web
      .getByLabel("Message")
      .pressSequentially("Patient MRN: 4471902", { delay: 50 });
    await expect(web.getByLabel("Message")).not.toHaveValue(
      "Patient MRN: 4471902",
    );
    await web.getByLabel("Message").fill("");
    await web.getByLabel("Message").pressSequentially("Ordinary work message");
    await expect(web.getByLabel("Message")).toHaveValue(
      "Ordinary work message",
    );
  } finally {
    await context.close();
    await rm(directory, { recursive: true, force: true });
  }
});

test("production organization links in the published extension and survives Chrome restart", async ({}, testInfo) => {
  const code = process.env["BLADE_E2E_ORG_CODE"];
  test.skip(!code, "A production test organization code is required");
  const directory = await mkdtemp(join(tmpdir(), "blade-live-install-"));
  execFileSync("unzip", [
    "-q",
    join(root, `landing/public${release.downloadPath}`),
    "-d",
    directory,
  ]);
  const profile = join(directory, "profile");
  const extension = join(directory, "blade-extension");
  let context = await launch(profile, extension);
  try {
    const worker =
      context.serviceWorkers()[0] ??
      (await context.waitForEvent("serviceworker"));
    const popupUrl = `chrome-extension://${new URL(worker.url()).host}/src/popup/index.html`;
    const popup = await context.newPage();
    await popup.goto(popupUrl);
    await popup.getByLabel("Organization code").fill(code!);
    await popup.getByRole("button", { name: "Link", exact: true }).click();
    await expect(popup.getByText("Protection on", { exact: true })).toBeVisible(
      { timeout: 15_000 },
    );
    await expect(
      popup.getByRole("button", { name: "Link", exact: true }),
    ).toBeDisabled();
    await context.close();
    context = await launch(profile, extension);
    const restoredWorker =
      context.serviceWorkers()[0] ??
      (await context.waitForEvent("serviceworker"));
    expect(new URL(restoredWorker.url()).host).toBe(new URL(worker.url()).host);
    const restored = await context.newPage();
    await restored.goto(popupUrl);
    await expect(
      restored.getByText("Protection on", { exact: true }),
    ).toBeVisible({ timeout: 15_000 });
    await expect(restored.getByLabel("Organization code")).toHaveValue(code!);
    await expect(
      restored.getByRole("button", { name: "Link", exact: true }),
    ).toBeDisabled();
    if (process.env["BLADE_E2E_SCAN"] === "1") {
      const scan = await restored.evaluate(async () =>
        chrome.runtime.sendMessage({
          type: "BACKEND_REQUEST",
          kind: "scan-finalize",
          timeoutMs: 20_000,
          clientScanId: crypto.randomUUID(),
          subject: "Blade synthetic installation check",
          body: "Synthetic test only. Patient MRN: 4471902.",
          recipients: ["blade-smoke@example.net"],
          userEmail: null,
          attachmentScanIds: [],
        }),
      );
      expect(scan.ok, JSON.stringify(scan)).toBe(true);
      expect(scan.status).toBe(200);
      expect(scan.body.entities.length).toBeGreaterThan(0);
      expect(scan.body.action).not.toBe("allow");
    }
    await restored.getByLabel("Organization code").fill("BLD-INVALID");
    await restored.getByRole("button", { name: "Link", exact: true }).click();
    await expect(restored.getByRole("alert")).toContainText("not found", {
      timeout: 15_000,
    });
    await expect(
      restored.getByText("Protection on", { exact: true }),
    ).toBeVisible();
    // Do not include the organization credential in screenshots or committed artifacts.
    await restored.getByLabel("Organization code").fill("");
    await restored.screenshot({
      path: testInfo.outputPath("production-link.png"),
    });
  } finally {
    await context.close();
    await rm(directory, { recursive: true, force: true });
  }
});
