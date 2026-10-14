// Thin fetch wrapper: same-origin cookies, CSRF header, structured errors.

export class ApiError extends Error {
  status: number;
  code: string;
  details?: unknown;
  requestId?: string;
  constructor(status: number, code: string, message: string, details?: unknown, requestId?: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }
}

function csrfToken(): string {
  const m = document.cookie.match(/(?:^|;\s*)aegis_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

type Listener = (e: ApiError) => void;
const authListeners = new Set<Listener>();
export function onAuthError(fn: Listener) {
  authListeners.add(fn);
  return () => { authListeners.delete(fn); };
}

async function parse(res: Response): Promise<any> {
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res.text();
}

export async function request<T = any>(method: string, path: string, body?: unknown, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  if (method !== "GET") headers["X-CSRF-Token"] = csrfToken();
  let res: Response;
  try {
    res = await fetch(path, { method, headers, body: payload, credentials: "same-origin", ...init });
  } catch {
    throw new ApiError(0, "NETWORK_UNREACHABLE", "Local AEGIS service unreachable. Check that the backend is running.");
  }
  if (!res.ok) {
    const data = await parse(res).catch(() => ({}));
    const e = data?.error || {};
    const err = new ApiError(res.status, e.code || `HTTP_${res.status}`, e.message || res.statusText, e.details, e.request_id);
    if (res.status === 401 && !path.startsWith("/api/auth/login")) authListeners.forEach((l) => l(err));
    throw err;
  }
  return parse(res) as Promise<T>;
}

export const api = {
  get: <T = any>(p: string) => request<T>("GET", p),
  post: <T = any>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  put: <T = any>(p: string, b?: unknown) => request<T>("PUT", p, b ?? {}),
  patch: <T = any>(p: string, b?: unknown) => request<T>("PATCH", p, b ?? {}),
  del: <T = any>(p: string) => request<T>("DELETE", p),
};

/** Upload with progress (XHR gives upload progress events; fetch does not). */
export function uploadFiles(path: string, files: File[], onProgress: (pct: number) => void): Promise<any> {
  return new Promise((resolve, reject) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f, f.name));
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-CSRF-Token", csrfToken());
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => {
      let data: any = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else {
        const e = data?.error || {};
        reject(new ApiError(xhr.status, e.code || `HTTP_${xhr.status}`, e.message || "Upload failed", e.details));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "NETWORK_UNREACHABLE", "Upload failed: service unreachable."));
    xhr.send(fd);
  });
}

export async function downloadFile(path: string, fallbackName: string) {
  const res = await fetch(path, { credentials: "same-origin" });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data?.error?.code || "DOWNLOAD_FAILED", data?.error?.message || "Download failed");
  }
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  const name = /filename="([^"]+)"/.exec(cd)?.[1] || fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
