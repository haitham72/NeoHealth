const API_URL = (import.meta.env.VITE_API_URL ?? "").replace(/\/+$/, "");

/** Builds an API URL while preserving relative URLs for the Vite dev proxy. */
export function apiUrl(path: string): string {
  return `${API_URL}/${path.replace(/^\/+/, "")}`;
}
