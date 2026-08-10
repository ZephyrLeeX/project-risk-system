import type { ApiResponse } from "@risk-platform/contracts";

const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || "/api"
).replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code = "REQUEST_FAILED",
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<ApiResponse<T>> {
  const headers = new Headers(init.headers);
  if (
    init.body &&
    !(init.body instanceof FormData) &&
    !headers.has("content-type")
  ) {
    headers.set("content-type", "application/json");
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  const payload = (await response.json().catch(() => null)) as
    | ApiResponse<T>
    | { message?: string | string[]; error?: string; code?: string }
    | null;

  if (!response.ok) {
    const rawMessage = payload?.message;
    const fallbackError =
      payload && "error" in payload ? payload.error : undefined;
    const errorCode =
      payload && "code" in payload && payload.code
        ? payload.code
        : "REQUEST_FAILED";
    const message = Array.isArray(rawMessage)
      ? rawMessage.join("；")
      : rawMessage || fallbackError || "请求失败，请稍后重试";
    throw new ApiError(
      message,
      response.status,
      errorCode,
    );
  }

  return payload as ApiResponse<T>;
}

export async function apiDownloadRequest(
  path: string,
  init: RequestInit = {},
  fallbackFileName = "下载文件",
): Promise<string> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as
      | { message?: string | string[]; error?: string }
      | null;
    const rawMessage = payload?.message;
    throw new ApiError(
      Array.isArray(rawMessage)
        ? rawMessage.join("；")
        : rawMessage || payload?.error || "文件下载失败",
      response.status,
    );
  }
  const disposition = response.headers.get("content-disposition") ?? "";
  const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const fileName = encodedName
    ? decodeURIComponent(encodedName)
    : fallbackFileName;
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  window.setTimeout(() => {
    anchor.remove();
    URL.revokeObjectURL(url);
  }, 1_000);
  return fileName;
}

export async function apiDownload(path: string): Promise<string> {
  return apiDownloadRequest(path, {}, "项目清单.xlsx");
}
