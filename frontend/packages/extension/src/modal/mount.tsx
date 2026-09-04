import type { Verdict } from "@bladedlp/shared";
import { Component, type ReactNode } from "react";
import ReactDOM from "react-dom/client";
import WarningModal from "./WarningModal";
import styles from "./styles.css?inline";
import { QUARANTINE_POLL_TIMEOUT_MS } from "../config";
import { sendBackendRequest } from "../backend-client";
import { BACKEND_REQUEST_MESSAGE } from "../messages";

interface ComposeView {
  getElement(): HTMLElement;
  send(): void;
}

interface QuarantineStatus {
  status: "pending" | "approved" | "rejected";
}

interface BoundaryProps {
  onFailure: () => void;
  children: ReactNode;
}

interface BoundaryState {
  failed: boolean;
}

export class ModalErrorBoundary extends Component<
  BoundaryProps,
  BoundaryState
> {
  override state: BoundaryState = { failed: false };

  static getDerivedStateFromError(): BoundaryState {
    return { failed: true };
  }

  override componentDidCatch(error: unknown): void {
    console.error("[Blade] the warning modal could not be shown", error);
    setTimeout(this.props.onFailure, 0);
  }

  override render(): ReactNode {
    return this.state.failed ? null : this.props.children;
  }
}

export function mountWarningModal(
  view: ComposeView,
  verdict: Verdict,
): () => void {
  const host = document.createElement("div");
  host.style.position = "fixed";
  host.style.inset = "0";
  host.style.zIndex = "2147483647";
  host.style.pointerEvents = "none";
  document.body.appendChild(host);

  const shadow = host.attachShadow({ mode: "closed" });

  const styleEl = document.createElement("style");
  styleEl.textContent = styles;
  shadow.appendChild(styleEl);

  const container = document.createElement("div");
  container.style.position = "fixed";
  container.style.inset = "0";
  container.style.pointerEvents = "auto";
  shadow.appendChild(container);

  const root = ReactDOM.createRoot(container);

  let disposed = false;
  const cleanup = (): void => {
    if (disposed) return;
    disposed = true;
    root.unmount();
    host.remove();
  };

  const quarantineId = verdict.quarantine_id;
  const pollQuarantine = quarantineId
    ? async (): Promise<QuarantineStatus> =>
        sendBackendRequest<QuarantineStatus>({
          type: BACKEND_REQUEST_MESSAGE,
          kind: "quarantine-status",
          timeoutMs: QUARANTINE_POLL_TIMEOUT_MS,
          quarantineId,
        })
    : undefined;

  root.render(
    <ModalErrorBoundary onFailure={cleanup}>
      <WarningModal
        verdict={verdict}
        onClose={cleanup}
        onSend={() => {
          cleanup();
          view.send();
        }}
        pollQuarantine={pollQuarantine}
      />
    </ModalErrorBoundary>,
  );

  return cleanup;
}
