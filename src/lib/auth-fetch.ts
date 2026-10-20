import { readResponse, type ReadRequestOptions } from './read-request.ts';

/** Single-attempt transport; the caller's readResponse owns retries. */
export async function authFetch(input: RequestInfo | URL, init?: RequestInit) {
  const response = await fetch(input, { ...init, credentials: 'include' });
  if (response.status === 401 && typeof window !== 'undefined') {
    window.dispatchEvent(new Event('stock-session-expired'));
  }
  return response;
}

export function authRead<T>(
  input: RequestInfo | URL,
  init: RequestInit,
  consume: (response: Response) => Promise<T>,
  options: ReadRequestOptions = {},
): Promise<T> {
  return readResponse(input, init, consume, { ...options, fetcher: authFetch });
}
