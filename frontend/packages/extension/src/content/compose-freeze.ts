import { isBladeSurface } from "./compose-scope";

const PASSTHROUGH_KEYS = new Set([
  "Tab",
  "Escape",
  "Shift",
  "Control",
  "Alt",
  "Meta",
  "CapsLock",
  "ArrowLeft",
  "ArrowRight",
  "ArrowUp",
  "ArrowDown",
  "Home",
  "End",
  "PageUp",
  "PageDown",
]);

const COPY_KEYS = new Set(["c", "a"]);

export function isPassiveKey(event: KeyboardEvent): boolean {
  if (PASSTHROUGH_KEYS.has(event.key)) return true;
  return (
    (event.ctrlKey || event.metaKey) &&
    !event.altKey &&
    COPY_KEYS.has(event.key.toLowerCase())
  );
}

export function freezeCompose(
  compose: Element,
  sendGroup?: Element | null,
): () => void {
  const swallow = (event: Event): void => {
    event.preventDefault();
    event.stopImmediatePropagation();
  };

  const onKeyDown = (event: Event): void => {
    if (isPassiveKey(event as KeyboardEvent)) return;
    swallow(event);
  };

  const onPointer = (event: Event): void => {
    const target = event.target instanceof Element ? event.target : null;
    if (isBladeSurface(target)) return;
    if (sendGroup && target && sendGroup.contains(target)) return;
    swallow(event);
  };

  compose.addEventListener("keydown", onKeyDown, true);
  compose.addEventListener("beforeinput", swallow, true);
  compose.addEventListener("paste", swallow, true);
  compose.addEventListener("drop", swallow, true);
  compose.addEventListener("pointerdown", onPointer, true);
  compose.addEventListener("click", onPointer, true);
  compose.setAttribute("aria-busy", "true");

  return () => {
    compose.removeEventListener("keydown", onKeyDown, true);
    compose.removeEventListener("beforeinput", swallow, true);
    compose.removeEventListener("paste", swallow, true);
    compose.removeEventListener("drop", swallow, true);
    compose.removeEventListener("pointerdown", onPointer, true);
    compose.removeEventListener("click", onPointer, true);
    compose.removeAttribute("aria-busy");
  };
}
