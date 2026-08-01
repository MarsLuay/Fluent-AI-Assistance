export async function loadFluentJson<T>(
  path: string,
  isExpected: (payload: unknown) => payload is T
): Promise<T | null> {
  try {
    const response = await fetch(path);
    if (!response.ok) return null;
    const payload = await response.json();
    return isExpected(payload) ? payload : null;
  } catch {
    return null;
  }
}
