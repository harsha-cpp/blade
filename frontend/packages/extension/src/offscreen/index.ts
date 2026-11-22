import {
  hasAttachmentPayload,
  INSPECT_LOCAL_ATTACHMENT,
} from "../web-attachment";
import { scanLocalAttachment } from "./scan";

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (
    sender.id !== chrome.runtime.id ||
    sender.tab ||
    message?.type !== INSPECT_LOCAL_ATTACHMENT ||
    !hasAttachmentPayload(message)
  )
    return false;

  void scanLocalAttachment(message).then(reply);
  return true;
});
