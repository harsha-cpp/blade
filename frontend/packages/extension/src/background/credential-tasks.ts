let pending: Promise<unknown> = Promise.resolve();

export function serializeCredentialTask<T>(task: () => Promise<T>): Promise<T> {
  const next = pending.then(task);
  pending = next.catch(() => {});
  return next;
}
