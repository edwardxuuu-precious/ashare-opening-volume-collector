export type ReadRequestOptions = {
  fetcher?: typeof fetch;
  /** One deadline for the request, its retries, and response-body consumption. */
  timeoutMs?: number;
  maxAttempts?: number;
  retryBaseMs?: number;
  retryMaxMs?: number;
};

const RETRYABLE_STATUS = new Set([429, 500, 502, 503, 504]);
const MAX_ATTEMPTS = 3;

function abortError() {
  return new DOMException('Aborted', 'AbortError');
}

/** Stop waiting even when a custom fetcher or body reader ignores cancellation. */
function abortable<T>(work: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const abort = () => {
      signal.removeEventListener('abort', abort);
      reject(abortError());
    };
    work.then(
      (value) => {
        signal.removeEventListener('abort', abort);
        if (signal.aborted) reject(abortError());
        else resolve(value);
      },
      (error) => {
        signal.removeEventListener('abort', abort);
        reject(error);
      },
    );
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) abort();
  });
}

function wait(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', abort);
      reject(abortError());
    };
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', abort);
      resolve();
    }, ms);
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) abort();
  });
}

function retryAfterMs(response: Response) {
  const value = response.headers?.get('Retry-After')?.trim();
  if (!value) return 0;
  const delay = /^\d+(?:\.\d+)?$/.test(value)
    ? Number(value) * 1000
    : Date.parse(value) - Date.now();
  return Number.isFinite(delay) ? Math.max(0, delay) : 0;
}

/**
 * Retry only transient GET transport failures. The consumer runs once after a
 * successful response or the final HTTP failure, so JSON/schema errors never
 * trigger another request. Authentication and writes belong to the fetcher.
 */
export async function readResponse<T>(
  input: RequestInfo | URL,
  init: RequestInit,
  consume: (response: Response) => Promise<T>,
  options: ReadRequestOptions = {},
): Promise<T> {
  const externalSignal =
    init.signal ?? (input instanceof Request ? input.signal : undefined);
  if (externalSignal?.aborted) throw abortError();
  const controller = new AbortController();
  const abort = () => controller.abort();
  externalSignal?.addEventListener('abort', abort, { once: true });
  const timeoutMs = options.timeoutMs ?? 25000;
  const deadline = Date.now() + timeoutMs;
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const method = (
    init.method ?? (input instanceof Request ? input.method : 'GET')
  ).toUpperCase();
  const attempts =
    method === 'GET'
      ? Math.max(1, Math.min(MAX_ATTEMPTS, options.maxAttempts ?? MAX_ATTEMPTS))
      : 1;
  const baseDelay = Math.max(0, options.retryBaseMs ?? 350);
  const maxDelay = Math.max(0, options.retryMaxMs ?? 10000);
  const fetcher = options.fetcher ?? fetch;
  const signal = controller.signal;
  try {
    for (let attempt = 1; attempt <= attempts; attempt++) {
      if (signal.aborted) throw abortError();
      const backoff = Math.min(
        maxDelay,
        baseDelay * 2 ** (attempt - 1) * (0.75 + Math.random() * 0.5),
      );
      let response: Response;
      try {
        response = await abortable(
          Promise.resolve().then(() => {
            if (signal.aborted) throw abortError();
            return fetcher(input, { ...init, signal });
          }),
          signal,
        );
      } catch (error) {
        // Browsers report connection/DNS failures as TypeError. Abort and
        // consumer exceptions are deliberately outside this retry branch.
        if (
          !(error instanceof TypeError) ||
          signal.aborted ||
          attempt >= attempts ||
          Date.now() + backoff >= deadline
        )
          throw error;
        await wait(backoff, signal);
        continue;
      }
      if (RETRYABLE_STATUS.has(response.status) && attempt < attempts) {
        const delay = Math.max(backoff, retryAfterMs(response));
        // A long server-directed wait must not cause an early retry or exceed
        // this read's budget. Return its failure for the caller to describe.
        if (delay <= maxDelay && Date.now() + delay < deadline) {
          void response.body?.cancel().catch(() => {});
          await wait(delay, signal);
          continue;
        }
      }
      return await abortable(
        Promise.resolve().then(() => {
          if (signal.aborted) throw abortError();
          return consume(response);
        }),
        signal,
      );
    }
    throw Error('数据服务暂时不可用，请重试。');
  } catch (error) {
    if (externalSignal?.aborted) throw abortError();
    if (timedOut) {
      const timeout = Error('数据服务响应超时，请重试。');
      timeout.name = 'TimeoutError';
      throw timeout;
    }
    throw error;
  } finally {
    clearTimeout(timer);
    externalSignal?.removeEventListener('abort', abort);
  }
}
