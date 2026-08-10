import type { Request } from "express";

import type { AuthenticatedUser } from "@risk-platform/contracts";

export interface SessionIdentity {
  sessionId: string;
  expiresAt: Date;
  user: AuthenticatedUser;
}

export interface AuthenticatedRequest extends Request {
  auth: SessionIdentity;
}
