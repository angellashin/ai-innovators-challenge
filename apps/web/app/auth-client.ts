export function authToken() {
  if (typeof window === "undefined") return "";
  return sessionStorage.getItem("replan.authToken") || "";
}

export function withAuth(init: RequestInit = {}): RequestInit {
  const headers = new Headers(init.headers);
  const token = authToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
    headers.set("X-Replan-Session", token);
  }
  return { ...init, headers };
}

export function clearAuth() {
  if (typeof window !== "undefined") sessionStorage.removeItem("replan.authToken");
}
