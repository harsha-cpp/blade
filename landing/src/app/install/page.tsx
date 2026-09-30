import type { Metadata } from "next";
import Nav from "@/components/Nav";
import InstallGuide from "@/components/InstallGuide";

export const metadata: Metadata = {
  title: "Install Blade for Chrome",
  description: "Extract Blade, load it in Chrome, and link your organization once with a verified organization code.",
};

export default function InstallPage() {
  return <><Nav /><main id="main" tabIndex={-1}><InstallGuide /></main></>;
}
