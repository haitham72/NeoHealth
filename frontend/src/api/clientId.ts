const CLIENT_ID_KEY = "regulense-client-id";

/** A UUID generated once per browser and reused across visits, so a visitor's chat
 * history can be grouped without any login. Not tied to IP: IPs are shared across
 * NATs/carriers and rotate, which would mix up or lose people's history. */
export function getClientId(): string {
  try {
    const existing = localStorage.getItem(CLIENT_ID_KEY);
    if (existing) return existing;
    const id = crypto.randomUUID();
    localStorage.setItem(CLIENT_ID_KEY, id);
    return id;
  } catch {
    return crypto.randomUUID();
  }
}
