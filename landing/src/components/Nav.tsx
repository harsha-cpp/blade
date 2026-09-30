import Link from "next/link";
import { links as appLinks } from "@/lib/links";
import Button from "./ui/Button";
import PrimaryCta from "./ui/PrimaryCta";
import DownloadExtension from "./DownloadExtension";

export default function Nav() {
  return (
    <header className="fixed inset-x-0 top-0 z-10 bg-ink">
      <div className="mx-auto flex h-[58px] max-w-[2560px] items-center gap-4 px-4 py-[13px] tab:h-[60px] tab:px-4 tab:py-[10px] lg:h-[69px] lg:px-5 lg:py-[13px]">
        <Link
          href="/"
          className="block h-[30px] w-[112px] shrink-0 lg:h-[43px] lg:w-[160px]"
          aria-label="Blade"
        >
          <img
            src="/img/wordmark.svg"
            alt=""
            width={160}
            height={43}
            className="h-full w-full"
          />
        </Link>

        <div className="flex flex-1 items-center justify-end gap-2 tab:gap-3">
          <DownloadExtension compact />
          <Button
            href={appLinks.signIn}
            variant="ghost"
            size="nav"
            className="hidden w-[86px] tab:flex"
          >
            Sign in
          </Button>
          <PrimaryCta
            variant="navcta"
            size="navsm"
            className="w-[132px] tab:hidden"
          />
          <PrimaryCta
            variant="navcta"
            size="nav"
            className="hidden w-[150px] tab:flex lg:w-[176px]"
          />
        </div>
      </div>
    </header>
  );
}
