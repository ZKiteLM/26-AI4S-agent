export const API =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export type Paper = {
  id: string;
  title: string;
  authors: string[];
  year: number | null;
  preferred_version_id: string | null;
  arxiv_id: string | null;
  source: string;
};
export type Evidence = {
  evidence_id: string;
  paper_id: string;
  paper_title: string;
  paper_version_id: string;
  block_id: string;
  page_index: number;
  bbox: number[];
  quote: string;
};

export function token() {
  return typeof window === "undefined"
    ? ""
    : localStorage.getItem("research-token") || "";
}

function announce(
  phase: "start" | "end",
  id: string,
  path: string,
  method: string,
) {
  if (typeof window !== "undefined" && method !== "GET") {
    window.dispatchEvent(
      new CustomEvent("research-request", { detail: { phase, id, path } }),
    );
  }
}

export async function api<T>(
  path: string,
  options: RequestInit & { progress?: "global" | "inline" } = {},
): Promise<T> {
  const { progress = "global", ...requestOptions } = options;
  const method = (requestOptions.method || "GET").toUpperCase();
  const requestId = `${Date.now()}-${Math.random()}`;
  const headers: Record<string, string> = {
    Authorization: `Bearer ${token()}`,
  };
  if (!(requestOptions.body instanceof FormData))
    headers["Content-Type"] = "application/json";
  if (progress === "global") announce("start", requestId, path, method);
  try {
    const response = await fetch(`${API}/api/v1${path}`, {
      ...requestOptions,
      headers: { ...headers, ...requestOptions.headers },
      cache: "no-store",
    });
    if (response.status === 401) {
      if (typeof window !== "undefined") {
        localStorage.removeItem("research-token");
        window.location.replace("/login");
      }
      throw new Error("请先登录");
    }
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(
        typeof detail.detail === "string"
          ? detail.detail
          : `请求失败 (${response.status})`,
      );
    }
    return (await response.json()) as T;
  } finally {
    if (progress === "global") announce("end", requestId, path, method);
  }
}

export async function apiFile(path: string): Promise<Blob> {
  const requestId = `${Date.now()}-${Math.random()}`;
  announce("start", requestId, path, "FILE");
  try {
    const response = await fetch(`${API}/api/v1${path}`, {
      headers: { Authorization: `Bearer ${token()}` },
    });
    if (!response.ok) throw new Error(`文件读取失败 (${response.status})`);
    return await response.blob();
  } finally {
    announce("end", requestId, path, "FILE");
  }
}
