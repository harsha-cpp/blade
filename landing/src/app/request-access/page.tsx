import type { Metadata } from "next";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import RequestAccessForm from "@/components/RequestAccessForm";
import Reveal from "@/components/ui/Reveal";
import Button from "@/components/ui/Button";
import { links } from "@/lib/links";

export const metadata: Metadata = {
  title: "Request access to Blade",
  description:
    "Blade is rolled out one hospital at a time. Tell us about your organization and the team sets it up with you.",
};

const whatHappensNext: [string, string][] = [
  [
    "We read it",
    "A person on the team reads every request and replies within two working days.",
  ],
  [
    "We set the organization up",
    "The team creates your organization and hands the organization code to the administrator you name.",
  ],
  [
    "You invite the rest",
    "Your administrator invites clinicians and desks, links the extension, and classifies your domains.",
  ],
];

export default function RequestAccess() {
  return (
    <div className="relative flex w-full flex-col items-center overflow-clip bg-ink">
      <Nav />

      <main
        id="main"
        tabIndex={-1}
        className="flex w-full flex-col items-center"
      >
        <section className="relative flex w-full max-w-[1460px] flex-col items-center justify-center gap-10 px-5 pb-[60px] pt-[140px] tab:px-10 tab:pt-[168px]">
          <div className="flex w-full max-w-[900px] flex-col items-center justify-center gap-4 tab:gap-8">
            <Reveal
              mode="mount"
              duration={1}
              distance={0}
              ease="var(--ease-framer)"
            >
              <p className="eyebrow text-blue">Request access</p>
            </Reveal>
            <Reveal
              mode="mount"
              delay={0.15}
              duration={1.2}
              distance={0}
              ease="var(--ease-framer)"
            >
              <h1 className="h2-72 whitespace-pre-wrap text-center text-cream">
                Bring Blade to your hospital
              </h1>
            </Reveal>
            <Reveal
              mode="mount"
              delay={0.3}
              duration={1.2}
              distance={0}
              ease="var(--ease-framer)"
              className="flex max-w-[720px] flex-col"
            >
              <p className="body-24 whitespace-pre-wrap text-center text-sand">
                Blade is rolled out one hospital at a time, so signup is not
                open. Tell us who you are and the team sets your organization up
                with you, hands the organization code to your administrator, and
                stays with you through the first scanned send.
              </p>
            </Reveal>
          </div>
        </section>

        <div className="w-full bg-ink-2">
          <div className="border-y border-stone/50">
            <div className="mx-auto grid max-w-[1440px] grid-cols-1 tab:grid-cols-3 lg:max-w-[1800px]">
              {whatHappensNext.map(([label, body], i) => (
                <div
                  key={label}
                  className={`flex flex-col gap-3 px-5 py-8 tab:px-8 tab:py-12 ${
                    i > 0
                      ? "border-t border-stone/50 tab:border-l tab:border-t-0"
                      : ""
                  }`}
                >
                  <p className="mono-16 text-blue">{label}</p>
                  <p className="font-rsr text-[16px] leading-[24px] tracking-[-0.32px] text-cream/60 lg:text-[20px] lg:leading-[30px] lg:tracking-[-0.4px]">
                    {body}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </div>

        <section className="flex w-full flex-col items-center justify-center gap-10 overflow-clip px-5 pb-[70px] pt-20 tab:px-5 tab:py-[120px] desk:px-10 lg:py-40">
          <div className="flex w-full max-w-[1360px] flex-col items-start justify-center gap-10 lg:max-w-[1720px] desk:grid desk:grid-cols-[1fr_1fr] desk:gap-20">
            <div className="flex w-full max-w-[560px] flex-col gap-4 tab:gap-8">
              <p className="eyebrow text-blue">The form</p>
              <h2 className="h2-72 whitespace-pre-wrap text-cream">
                Tell us about your hospital
              </h2>
              <p className="body-20 max-w-[480px] whitespace-pre-wrap text-sand">
                Only the first three fields are needed. The rest helps the team
                size the rollout before the call.
              </p>
              <p className="font-rsr text-[14px] leading-[22px] tracking-[-0.28px] text-stone">
                Already set up by your hospital? Sign in instead.
              </p>
              <Button
                href={links.signIn}
                variant="outline"
                size="sm"
                className="w-full tab:w-auto tab:self-start"
              >
                Sign in
              </Button>
            </div>

            <div className="flex w-full justify-start desk:justify-end">
              <RequestAccessForm />
            </div>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
