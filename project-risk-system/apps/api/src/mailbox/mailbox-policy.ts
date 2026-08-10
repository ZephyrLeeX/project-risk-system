import { BadRequestException } from "@nestjs/common";
import { lookup } from "node:dns/promises";
import { isIP } from "node:net";

export function isPrivateMailboxAddress(address: string): boolean {
  const value = address.toLocaleLowerCase();
  if (value.includes(":")) {
    return (
      value === "::1" ||
      value.startsWith("fc") ||
      value.startsWith("fd") ||
      /^fe[89ab]/.test(value) ||
      value.startsWith("::ffff:127.") ||
      value.startsWith("::ffff:10.") ||
      value.startsWith("::ffff:192.168.")
    );
  }
  const octets = value.split(".").map(Number);
  if (octets.length !== 4 || octets.some((item) => Number.isNaN(item))) return true;
  const [first, second] = octets;
  if (first === undefined) return true;
  return (
    first === 0 ||
    first === 10 ||
    first === 127 ||
    (first === 169 && second === 254) ||
    (first === 172 && second !== undefined && second >= 16 && second <= 31) ||
    (first === 192 && second === 168) ||
    first >= 224
  );
}

export async function resolvePublicMailboxHost(host: string): Promise<string> {
  const normalized = host.trim().replace(/^\[|\]$/g, "").toLocaleLowerCase();
  if (
    !normalized ||
    normalized === "localhost" ||
    normalized.endsWith(".localhost") ||
    normalized.endsWith(".local")
  ) {
    throw new BadRequestException("IMAP服务器不能指向本机或内网");
  }
  let addresses: Array<{ address: string; family: number }>;
  try {
    addresses = isIP(normalized)
      ? [{ address: normalized, family: isIP(normalized) }]
      : await lookup(normalized, { all: true });
  } catch {
    throw new BadRequestException("无法解析IMAP服务器地址");
  }
  if (!addresses.length || addresses.some(({ address }) => isPrivateMailboxAddress(address))) {
    throw new BadRequestException("IMAP服务器不能指向本机或内网");
  }
  return addresses[0]!.address;
}

export function maskMailboxEmail(email: string): string {
  const [local, domain] = email.split("@");
  if (!local || !domain) return email;
  return `${local.slice(0, 2)}***@${domain}`;
}

export function cleanMailboxKeywords(values: string[]): string[] {
  return [...new Set(values.map((item) => item.trim()).filter(Boolean))].slice(0, 8);
}
