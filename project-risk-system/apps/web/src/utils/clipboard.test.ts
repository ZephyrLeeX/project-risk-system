import { afterEach, describe, expect, it, vi } from "vitest";

import { copyTextToClipboard } from "@/utils/clipboard";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("copyTextToClipboard", () => {
  it("returns true and writes the exact text when the clipboard accepts it", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(globalThis, "navigator", {
      value: { clipboard: { writeText } },
      configurable: true,
      writable: true,
    });

    await expect(copyTextToClipboard("s3cr3t-pw")).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith("s3cr3t-pw");
  });

  it("returns false without swallowing the reason when the write is rejected", async () => {
    const writeText = vi.fn().mockRejectedValue(new DOMException("denied"));
    Object.defineProperty(globalThis, "navigator", {
      value: { clipboard: { writeText } },
      configurable: true,
      writable: true,
    });

    await expect(copyTextToClipboard("s3cr3t-pw")).resolves.toBe(false);
    expect(writeText).toHaveBeenCalledTimes(1);
  });

  it("returns false when the clipboard API is entirely unavailable", async () => {
    Object.defineProperty(globalThis, "navigator", {
      value: {},
      configurable: true,
      writable: true,
    });

    await expect(copyTextToClipboard("s3cr3t-pw")).resolves.toBe(false);
  });
});
