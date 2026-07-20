import type { AuditRequest, AuditResult, Preset } from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function formatDetail(detail: unknown): string | null {
  if (typeof detail === "string") {
    return detail;
  }

  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (typeof item === "string") {
          return item;
        }
        if (item && typeof item === "object" && "msg" in item && typeof item.msg === "string") {
          return item.msg;
        }
        return null;
      })
      .filter((message): message is string => Boolean(message));
    return messages.length ? messages.join(" ") : null;
  }

  if (detail && typeof detail === "object" && "message" in detail && typeof detail.message === "string") {
    return detail.message;
  }

  return null;
}

export interface AuditOptions {
  preset?: Preset | null;
  customOverrides?: Record<string, boolean> | null;
}

export async function runAudit(
  url: string,
  includeProtocols: boolean,
  includeAccountAuth: boolean,
  includeEcommerce: boolean,
  options: AuditOptions = {},
): Promise<AuditResult> {
  const payload: AuditRequest = {
    url,
    include_protocols: includeProtocols,
    include_account_auth: includeAccountAuth,
    include_ecommerce: includeEcommerce,
  };

  if (options.preset !== undefined) {
    payload.preset = options.preset;
  }
  if (options.customOverrides !== undefined && options.customOverrides !== null) {
    payload.custom_overrides = options.customOverrides;
  }

  let response: Response;
  try {
    response = await fetch(`${API_URL}/v1/audit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    throw new Error("Audit service is unreachable. Confirm the backend is running, then retry the scan.");
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = formatDetail(body.detail);
    throw new Error(detail ?? `Audit failed (${response.status}). Retry the scan or check the URL.`);
  }

  return response.json();
}