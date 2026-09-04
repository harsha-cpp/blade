import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  extractAttachmentRefs,
  extractComposeData,
  extractDriveLinks,
  isDriveUrl,
  scanUntilStable,
} from "./compose";
import { buildLocalVerdict, emptyPolicy } from "./policy";

function composeFixture(inner = ""): HTMLElement {
  const compose = document.createElement("div");
  compose.innerHTML = `
    <input name="to" value="doc@hospital.org" />
    <input name="subjectbox" value="Discharge summary" />
    <div role="textbox" aria-label="Message Body">Please review the notes.</div>
    ${inner}
  `;
  document.body.append(compose);
  return compose;
}

function file(name: string): File {
  return new File(["x"], name, { type: "text/plain" });
}

beforeEach(() => {
  document.body.replaceChildren();
  document.title = "";
});

describe("compose extraction", () => {
  it("reads the subject, body and recipients of a draft", () => {
    const data = extractComposeData(composeFixture(), []);

    expect(data.subject).toBe("Discharge summary");
    expect(data.body).toContain("Please review the notes.");
    expect(data.recipients).toEqual(["doc@hospital.org"]);
  });

  it("keeps the fingerprint stable while the draft is untouched", () => {
    const compose = composeFixture();

    expect(extractComposeData(compose, []).fingerprint).toBe(
      extractComposeData(compose, []).fingerprint,
    );
  });

  it.each([
    [
      "the body",
      (compose: HTMLElement): void => {
        const body = compose.querySelector<HTMLElement>('[role="textbox"]');
        if (body) body.innerHTML = "Aadhaar 7534 7930 7460";
      },
    ],
    [
      "the subject",
      (compose: HTMLElement): void => {
        const subject = compose.querySelector<HTMLInputElement>(
          'input[name="subjectbox"]',
        );
        if (subject) subject.value = "Re: discharge summary";
      },
    ],
    [
      "a recipient",
      (compose: HTMLElement): void => {
        const to = compose.querySelector<HTMLInputElement>('input[name="to"]');
        if (to) to.value = "doc@hospital.org, stranger@gmail.com";
      },
    ],
  ])("changes the fingerprint when %s changes", (_label, mutate) => {
    const compose = composeFixture();
    const before = extractComposeData(compose, []).fingerprint;

    mutate(compose);

    expect(extractComposeData(compose, []).fingerprint).not.toBe(before);
  });

  it("changes the fingerprint when a file is attached", () => {
    const compose = composeFixture();
    const before = extractComposeData(compose, []).fingerprint;

    expect(
      extractComposeData(compose, [file("labs.txt")]).fingerprint,
    ).not.toBe(before);
  });

  it("changes the fingerprint when an attachment chip appears", () => {
    const compose = composeFixture();
    const before = extractComposeData(compose, []).fingerprint;
    const chip = document.createElement("span");
    chip.setAttribute(
      "download_url",
      "application/pdf:labs.pdf:https://mail.google.com/mail/u/0?view=att&id=1",
    );
    compose.append(chip);

    expect(extractComposeData(compose, []).fingerprint).not.toBe(before);
  });

  it("ignores attachments quoted from an earlier message", () => {
    const compose = composeFixture(
      '<blockquote class="gmail_quote"><span download_url="application/pdf:old.pdf:https://mail.google.com/old"></span></blockquote>',
    );

    expect(extractAttachmentRefs(compose)).toEqual([]);
  });

  it("drops a chip that was already captured as a file", () => {
    const compose = composeFixture(
      '<span download_url="text/plain:labs.txt:https://mail.google.com/labs"></span>',
    );

    expect(
      extractComposeData(compose, [file("labs.txt")]).unresolvedRefs,
    ).toEqual([]);
  });
});

describe("Google Drive chips", () => {
  const DRIVE_CHIP =
    '<a href="https://drive.google.com/file/d/1a2b3c/view?usp=drive_link">Discharge summary.pdf</a>';

  it("counts a Drive chip that Blade cannot read", () => {
    const data = extractComposeData(composeFixture(DRIVE_CHIP), []);

    expect(data.driveLinks).toEqual([
      "https://drive.google.com/file/d/1a2b3c/view?usp=drive_link",
    ]);
  });

  it("counts a Google Docs chip too", () => {
    const compose = composeFixture(
      '<a href="https://docs.google.com/document/d/1a2b3c/edit">Ward notes</a>',
    );

    expect(extractDriveLinks(compose)).toHaveLength(1);
  });

  it("ignores ordinary links and links quoted from an earlier message", () => {
    const compose = composeFixture(
      '<a href="https://hospital.org/rota">Rota</a>' +
        '<blockquote class="gmail_quote"><a href="https://drive.google.com/file/d/old/view">Old</a></blockquote>',
    );

    expect(extractDriveLinks(compose)).toEqual([]);
  });

  it("is not fooled by a lookalike host", () => {
    expect(isDriveUrl("https://drive.google.com.evil.test/file/d/1/view")).toBe(
      false,
    );
    expect(isDriveUrl("http://drive.google.com/file/d/1/view")).toBe(false);
    expect(isDriveUrl("https://drive.google.com/file/d/1/view")).toBe(true);
  });

  it("counts the same chip once", () => {
    const compose = composeFixture(DRIVE_CHIP + DRIVE_CHIP);

    expect(extractDriveLinks(compose)).toHaveLength(1);
  });

  it("changes the fingerprint when a Drive chip is added mid scan", () => {
    const compose = composeFixture();
    const before = extractComposeData(compose, []).fingerprint;
    compose.insertAdjacentHTML("beforeend", DRIVE_CHIP);

    expect(extractComposeData(compose, []).fingerprint).not.toBe(before);
  });

  it("never settles as an allow while a Drive chip is unread", () => {
    const data = extractComposeData(composeFixture(DRIVE_CHIP), []);
    const verdict = buildLocalVerdict({
      entities: [],
      recipients: ["doc@hospital.org"],
      policy: {
        ...emptyPolicy(),
        approvedDomains: new Set(["hospital.org"]),
        hasConfig: true,
      },
      unscannedAttachments: data.driveLinks.length,
    });

    expect(verdict.action).toBe("warn");
    expect(verdict.matched_policy_ids).toContain("unscannable-attachment");
  });
});

describe("a draft that changes while it is being checked", () => {
  const draft = (fingerprint: string) => ({ fingerprint });

  it("keeps the verdict of a draft that stayed put", async () => {
    const scan = vi.fn(async () => "allow");

    const settled = await scanUntilStable(() => draft("a"), scan, 3);

    expect(settled).toEqual({ data: { fingerprint: "a" }, verdict: "allow" });
    expect(scan).toHaveBeenCalledOnce();
  });

  it("throws the verdict away and checks the new draft", async () => {
    const reads = [draft("a"), draft("b"), draft("b"), draft("b")];
    const scan = vi.fn(
      async (data: { fingerprint: string }) => data.fingerprint,
    );
    const onDraftChanged = vi.fn();

    const settled = await scanUntilStable(
      () => reads.shift() ?? draft("b"),
      scan,
      3,
      onDraftChanged,
    );

    expect(settled).toEqual({ data: { fingerprint: "b" }, verdict: "b" });
    expect(scan).toHaveBeenCalledTimes(2);
    expect(onDraftChanged).toHaveBeenCalledOnce();
  });

  it("gives up rather than sending a draft it never managed to check", async () => {
    let counter = 0;
    const scan = vi.fn(async () => "allow");

    const settled = await scanUntilStable(
      () => draft(`fingerprint-${(counter += 1)}`),
      scan,
      3,
    );

    expect(settled).toBeNull();
    expect(scan).toHaveBeenCalledTimes(3);
  });
});
