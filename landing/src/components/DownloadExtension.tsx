"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import release from "@/lib/extension-release.json";

export default function DownloadExtension({ className = "", compact = false }: { className?: string; compact?: boolean }) {
  const router = useRouter();
  const busy = useRef(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState("");

  return (
    <div className={`flex flex-col gap-2 ${className}`}>
      <a
        href={release.downloadPath}
        download={release.filename}
        aria-disabled={downloading}
        className={`btn btn-primary ${compact ? "btn-navsm tab:btn-nav" : "btn-sm"} focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-blue`}
        onClick={async (event) => {
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          if (busy.current) return;
          busy.current = true;
          setDownloading(true);
          setError("");
          try {
            const response = await fetch(release.downloadPath, { signal: AbortSignal.timeout(60_000) });
            if (!response.ok) throw new Error("Download unavailable");
            const blob = await response.blob();
            if (blob.size !== release.bytes) throw new Error("Incomplete download");
            const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
            const checksum = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
            if (checksum !== release.sha256) throw new Error("Download mismatch");
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement("a");
            anchor.href = url;
            anchor.download = release.filename;
            document.body.appendChild(anchor);
            anchor.click();
            anchor.remove();
            setTimeout(() => URL.revokeObjectURL(url), 60_000);
            router.push("/install?download=started");
          } catch {
            setError("Download could not start. Please try again.");
          } finally {
            busy.current = false;
            setDownloading(false);
          }
        }}
      >
        {downloading ? "Preparing download…" : compact ? "Download" : "Download for Chrome"}
        <span aria-hidden="true">↓</span>
      </a>
      {error && <p role="alert" className="max-w-[280px] font-rsr text-sm text-sand">{error}</p>}
    </div>
  );
}
