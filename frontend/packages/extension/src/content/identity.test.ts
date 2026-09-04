import { beforeEach, describe, expect, it } from "vitest";
import { extractUserEmail } from "./identity";

beforeEach(() => {
  document.body.textContent = "";
  document.title = "";
});

describe("sender identity", () => {
  it("reads the account switcher label", () => {
    document.body.innerHTML =
      '<a href="https://accounts.google.com/SignOutOptions" aria-label="Google Account: Dr A Rao (a.rao@hospital.org)"></a>';
    expect(extractUserEmail(document)).toBe("a.rao@hospital.org");
  });

  it("falls back to the mailbox address in the document title", () => {
    document.title = "Inbox (12) - doctor@hospital.org - Gmail";
    expect(extractUserEmail(document)).toBe("doctor@hospital.org");
  });

  it("prefers an explicit send-as alias on the draft", () => {
    document.title = "Inbox - personal@hospital.org - Gmail";
    const compose = document.createElement("div");
    compose.innerHTML = '<input name="from" value="oncology@hospital.org" />';
    document.body.appendChild(compose);
    expect(extractUserEmail(document, compose)).toBe("oncology@hospital.org");
  });

  it("returns undefined rather than a fake user when Gmail markup changes", () => {
    document.title = "Gmail";
    document.body.innerHTML =
      '<a href="https://accounts.google.com/SignOutOptions"></a>';
    expect(extractUserEmail(document)).toBeUndefined();
  });

  it("never returns a placeholder address", () => {
    document.title = "Inbox - unknown - Gmail";
    document.body.innerHTML = '<img alt="unknown@unknown" />';
    expect(extractUserEmail(document)).toBeUndefined();
  });

  it("normalises case", () => {
    document.body.innerHTML =
      '<div aria-label="Google Account: A (A.Rao@Hospital.org)"></div>';
    expect(extractUserEmail(document)).toBe("a.rao@hospital.org");
  });
});

describe("where the sender identity may be read from", () => {
  it("ignores an avatar or label elsewhere on the page", () => {
    document.title = "Gmail";
    document.body.innerHTML =
      '<div class="message-list"><img alt="patient.family@gmail.com" />' +
      '<span aria-label="From patient.family@gmail.com"></span></div>';

    expect(extractUserEmail(document)).toBeUndefined();
  });

  it("reads the avatar inside the account switcher", () => {
    document.title = "Gmail";
    document.body.innerHTML =
      '<a href="https://accounts.google.com/SignOutOptions">' +
      '<img alt="Profile picture of a.rao@hospital.org" /></a>';

    expect(extractUserEmail(document)).toBe("a.rao@hospital.org");
  });

  it("reads the address shown on the compose From row", () => {
    document.title = "Gmail";
    const compose = document.createElement("div");
    compose.innerHTML =
      '<div aria-label="From"><span aria-label="oncology@hospital.org"></span></div>';
    document.body.appendChild(compose);

    expect(extractUserEmail(document, compose)).toBe("oncology@hospital.org");
  });
});
