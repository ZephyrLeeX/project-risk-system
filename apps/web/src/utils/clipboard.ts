/**
 * Copy ``text`` to the system clipboard.
 *
 * Thin wrapper around the async Clipboard API so callers can render a
 * success/failure affordance without touching the raw promise. Never logs or
 * surfaces ``text`` itself — callers are responsible for keeping secrets out
 * of their own UI copy (e.g. showing "已复制" instead of the one-time
 * password).
 *
 * @returns ``true`` when the write succeeded, ``false`` when the browser
 * rejected it (permissions, insecure context, clipboard unavailable).
 */
export async function copyTextToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
