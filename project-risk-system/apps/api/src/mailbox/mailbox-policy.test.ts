import { describe, expect, it } from "vitest";

import {
  cleanMailboxKeywords,
  isPrivateMailboxAddress,
  maskMailboxEmail,
  resolvePublicMailboxHost,
} from "./mailbox-policy";

describe("mailbox policy", () => {
  it.each(["127.0.0.1", "10.2.3.4", "172.16.1.1", "192.168.3.8", "::1", "fd00::1"])(
    "recognizes private mailbox destinations: %s",
    (address) => expect(isPrivateMailboxAddress(address)).toBe(true),
  );

  it.each(["localhost", "127.0.0.1", "10.0.0.8", "192.168.1.8", "[::1]"])(
    "blocks local or private IMAP hosts: %s",
    async (host) => {
      await expect(resolvePublicMailboxHost(host)).rejects.toThrow("本机或内网");
    },
  );

  it("masks mailbox addresses and normalizes unique keywords", () => {
    expect(maskMailboxEmail("liufeng@example.com")).toBe("li***@example.com");
    expect(cleanMailboxKeywords([" 项目周报 ", "项目周报", "风险周报"])).toEqual([
      "项目周报",
      "风险周报",
    ]);
  });
});
