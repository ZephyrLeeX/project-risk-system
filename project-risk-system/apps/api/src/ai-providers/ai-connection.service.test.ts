import { describe, expect, it } from "vitest";

import { AiConnectionService } from "./ai-connection.service";

const request = {
  model: "risk-model",
  apiKey: "sk-test-secret",
  timeoutSeconds: 1,
  retryCount: 0,
};

describe("AiConnectionService", () => {
  it("requires HTTPS endpoints", async () => {
    await expect(new AiConnectionService().test({ ...request, endpoint: "http://example.com/v1" })).rejects.toThrow("HTTPS");
  });

  it.each([
    "https://localhost/v1",
    "https://127.0.0.1/v1",
    "https://10.0.0.8/v1",
    "https://192.168.1.8/v1",
    "https://[::1]/v1",
  ])("blocks local and private endpoints: %s", async (endpoint) => {
    await expect(new AiConnectionService().test({ ...request, endpoint })).rejects.toThrow("本机或内网");
  });
});
