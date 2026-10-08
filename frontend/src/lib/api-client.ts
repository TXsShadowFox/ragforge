/** Browser helpers: call the API through the dashboard's server (/api/v1/...). */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryAfter: number | null;

  constructor(status: number, code: string, message: string, retryAfter: number | null) {
    super(message);
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
  }
}

type ErrorBody = {
  error?: { code?: string; message?: string; details?: Array<{ message?: string }> };
};

/** Read an error response in the API's format into an ApiError with a readable message. */
export async function toApiError(response: Response): Promise<ApiError> {
  let code = `http_${response.status}`;
  let message = "Something went wrong. Please try again.";
  try {
    const { error } = (await response.json()) as ErrorBody;
    code = error?.code ?? code;
    message = error?.message ?? message;
    const details = (error?.details ?? [])
      .map((detail) => detail.message?.replace(/^Value error, /, ""))
      .filter(Boolean);
    if (details.length > 0) message = details.join(" ");
  } catch {
    // not JSON: keep the general message
  }
  const retryAfter = Number(response.headers.get("retry-after")) || null;
  if (code === "rate_limited" && retryAfter) {
    message = `Too many requests. Please try again in ${retryAfter} seconds.`;
  }
  return new ApiError(response.status, code, message, retryAfter);
}

/**
 * The login is gone (expired token): go to the login page, with a full page load that
 * clears the old data from memory (router.push() would keep visited pages alive).
 */
export function goToLoginIfLoggedOut(error: ApiError): void {
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  if (error.status === 401) window.location.assign("/login");
}

/** Call `/v1<path>` of the API. Throws ApiError for error responses. */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/v1${path}`, init);
  if (!response.ok) {
    const error = await toApiError(response);
    goToLoginIfLoggedOut(error);
    throw error;
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

/** fetch() options for sending JSON. */
export function sendJson(method: "POST" | "PUT" | "PATCH" | "DELETE", data: unknown): RequestInit {
  return { method, headers: { "content-type": "application/json" }, body: JSON.stringify(data) };
}

/** A message for the user, for any error. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Could not reach the server. Check your connection and try again.";
}

let lastId = 0;

/** A new id for list items in this page (works on plain HTTP too, unlike crypto.randomUUID). */
export function newId(): string {
  lastId += 1;
  return `item-${lastId}`;
}
