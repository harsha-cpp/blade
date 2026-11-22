"use client";

import { useState } from "react";
import Link from "next/link";
import DownloadExtension from "./DownloadExtension";
import release from "@/lib/extension-release.json";
import { links } from "@/lib/links";

const steps = [
  { label: "Extract", title: "Give Blade a home.", body: "Find the ZIP in Downloads and extract it. Move the blade-extension folder somewhere you will keep it, such as Documents.", detail: "Select the extracted folder during installation. Keep it there: Chrome reads the extension from this folder." },
  { label: "Open Chrome", title: "Open your extensions.", body: "In desktop Chrome, paste chrome://extensions into the address bar. Turn on Developer mode in the top right.", detail: "Use Chrome 120 or newer, in the profile you use for work. If your organization disables Developer mode, ask your administrator to install Blade." },
  { label: "Load Blade", title: "Load the folder.", body: "Click Load unpacked, then select blade-extension, the extracted folder containing manifest.json. Blade Healthcare DLP will appear in your extensions.", detail: "Open Chrome’s puzzle-piece menu and pin Blade. The ZIP itself cannot be loaded. If Chrome cannot find the manifest, select the folder inside the extracted download." },
  { label: "Link once", title: "Connect your organization.", body: "Open Blade from the toolbar. Paste the organization code from dashboard Settings into Organization code, then click Link. Wait for Protection on and your organization’s name.", detail: "Use the code starting with BLD-, rather than the internal organization ID. Your administrator can give you the code. An invalid code or a connection failure leaves your existing link unchanged." },
  { label: "Ready", title: "One link. Every session.", body: "Blade saves your organization link in this Chrome profile. After installing or updating Blade, refresh any work tabs you already had open, including ChatGPT and Gemini.", detail: "Open Blade on a website and check for Website protection active before attaching a file. If protection is unverified, save any unsent work and use Refresh this tab." },
];

function Illustration({ step }: { step: number }) {
  return <div aria-hidden="true" className="flex min-h-[270px] w-full items-center justify-center border border-stone/40 bg-ink-4 p-6 tab:min-h-[380px] tab:p-12">
    {step === 0 ? <div className="w-full max-w-[310px] space-y-6">
      <div className="flex items-center gap-4 border border-stone/40 p-5"><span className="text-3xl text-blue">↓</span><div><p className="text-lg">Blade for Chrome</p><p className="font-mono text-xs text-stone">ZIP · v{release.version}</p></div></div>
      <div className="text-center text-blue">↓ Extract</div>
      <div className="border-l-2 border-blue bg-ink-2 p-5"><p className="text-lg">▱ blade-extension</p><p className="mt-3 pl-5 font-mono text-xs text-sand">manifest.json<br />assets/<br />icons/</p></div>
    </div> : step === 1 || step === 2 ? <div className="w-full max-w-[390px] border border-stone/40 bg-ink">
      <div className="border-b border-stone/40 bg-ink-2 px-4 py-3 font-mono text-xs text-sand">chrome://extensions</div>
      <div className="flex items-center justify-between gap-3 p-4"><span>Extensions</span><span className="flex items-center gap-2 text-xs text-sand">Developer mode <span className="inline-flex h-5 w-9 justify-end rounded-full bg-blue p-1"><span className="h-3 w-3 rounded-full bg-cream" /></span></span></div>
      <div className="px-4 pb-5"><span className={`inline-block border px-3 py-2 text-sm ${step === 2 ? "border-blue bg-blue" : "border-stone/50"}`}>Load unpacked</span></div>
      {step === 2 && <div className="mx-4 mb-4 border border-blue/60 p-4"><p className="text-lg">Blade Healthcare DLP</p><p className="mt-2 text-xs text-sand">v{release.version} <span className="float-right text-blue">Enabled ✓</span></p></div>}
    </div> : <div className="w-full max-w-[290px] border border-stone/40 bg-ink p-6">
      <div className="mb-6 flex justify-between"><span className="font-mono text-xl">BLADE</span><span className="font-mono text-xs text-stone">v{release.version}</span></div>
      <div className="mb-5 border-l-2 border-blue bg-ink-2 p-3"><p className="text-sm">● {step === 4 ? "Protection on" : "Not linked"}</p><p className="mt-1 text-xs text-sand">{step === 4 ? "Checking outgoing mail for your organization." : "Link this browser to your organization."}</p></div>
      <p className="mb-2 text-xs text-sand">Organization code</p><div className="flex gap-2"><span className="flex-1 border border-stone/50 p-2 font-mono text-xs">BLD-••••••</span><span className="bg-blue px-3 py-2 text-xs">{step === 4 ? "✓" : "Link"}</span></div>
      {step === 4 && <p className="mt-4 text-xs text-sand">Saved for this Chrome profile.</p>}
    </div>}
  </div>;
}

export default function InstallGuide() {
  const [step, setStep] = useState(0);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const current = steps[step];
  return <div className="mx-auto max-w-[1240px] px-5 pb-16 pt-[110px] tab:px-10 tab:pt-[145px]">
    <div className="mb-10 flex flex-wrap items-center justify-between gap-4 border-b border-stone/40 pb-5">
      <p className="font-mono text-xs text-blue">BLADE FOR CHROME / v{release.version}</p>
      <Link href="/" className="text-sm text-sand hover:text-cream">← Back to Blade</Link>
    </div>
    <h1 className="h2-72 max-w-[820px]">A few clicks to a protected browser.</h1>
    <p className="mt-5 max-w-[620px] font-rsr text-lg leading-7 text-sand">Save the download if Chrome asks, then follow these steps. You only link your organization once per Chrome profile.</p>
    <nav aria-label="Installation steps" className="my-10 grid grid-cols-5 gap-2 border-b border-stone/40 pb-5">
      {steps.map((item, index) => <button key={item.label} type="button" aria-current={step === index ? "step" : undefined} onClick={() => setStep(index)} className={`cursor-pointer text-left focus-visible:outline-2 focus-visible:outline-blue ${step === index ? "text-cream" : "text-stone hover:text-sand"}`}><span className={`mb-2 block font-mono text-xs ${step === index ? "text-blue" : ""}`}>0{index + 1}</span><span className="text-xs tab:text-base">{item.label}</span></button>)}
    </nav>
    <div className="grid gap-8 tab:grid-cols-2 tab:gap-12">
      <Illustration step={step} />
      <div className="flex flex-col items-start" aria-live="polite" aria-atomic="true">
        <p className="mb-4 font-mono text-xs text-blue">STEP 0{step + 1} / 05</p>
        <h2 className="text-[36px] leading-[1.08] tracking-[-1px] tab:text-[44px]">{current.title}</h2>
        <p className="mt-5 font-rsr text-lg leading-7 text-sand">{current.body}</p>
        <p className="mt-4 font-rsr text-sm leading-6 text-sand/75">{current.detail}</p>
        {step === 1 && <><button type="button" onClick={async () => { try { await navigator.clipboard.writeText("chrome://extensions"); setCopied(true); setCopyError(false); } catch { setCopyError(true); } }} className="mt-5 cursor-pointer border border-stone/50 px-4 py-3 font-mono text-xs hover:border-blue">{copied ? "Copied ✓" : "Copy chrome://extensions"}</button>{copyError && <p className="mt-2 text-sm">Select and copy: chrome://extensions</p>}</>}
        {step === 3 && <a href={`${links.appUrl}/settings`} className="mt-5 text-sm text-blue underline underline-offset-4">Find your organization code in Settings ↗</a>}
        {step === 3 && <p className="mt-4 font-rsr text-sm leading-6 text-sand/75">Already have an admin-issued device token? Paste it under Device token and press Save instead. That also links your organization in one step.</p>}
        {step === 4 && <a href={links.appUrl} className="btn btn-primary btn-sm mt-6">Open dashboard ↗</a>}
        <div className="mt-8 flex w-full items-center justify-between gap-4 border-t border-stone/40 pt-5">
          <button type="button" disabled={step === 0} onClick={() => setStep(step - 1)} className="cursor-pointer px-2 py-3 text-sm disabled:cursor-default disabled:opacity-30">← Previous</button>
          {step < steps.length - 1 && <button type="button" onClick={() => setStep(step + 1)} className="btn btn-primary btn-sm">Next step →</button>}
        </div>
      </div>
    </div>
    <div className="mt-12 flex flex-col gap-5 border-t border-stone/40 pt-6 tab:flex-row tab:items-center tab:justify-between">
      <div><p className="text-sm">Need the file again?</p><p className="mt-2 font-rsr text-sm text-sand">Desktop Chrome · ZIP · {(release.bytes / 1024 / 1024).toFixed(1)} MB</p><Link href={links.requestAccess} className="mt-3 inline-block text-sm text-blue underline underline-offset-4">Need an organization? Request access</Link></div>
      <DownloadExtension />
    </div>
  </div>;
}
