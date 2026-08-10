import type { SessionIdentity } from "../auth/auth.types";

export interface AdminRequestContext {
  identity: SessionIdentity;
  clientIp?: string;
  userAgent?: string;
}
