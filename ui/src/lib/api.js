// The JSON API. Mutating requests carry the CSRF token (double-submit cookie).
export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

function csrf() {
  const m = document.cookie.match(/(?:^|;\s*)papyrus_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : '';
}

export let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

export async function api(method, path, body, opts = {}) {
  const headers = {};
  let payload;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
  if (method !== 'GET') headers['X-CSRF-Token'] = csrf();
  let res;
  try {
    res = await fetch('/api' + path, { method, headers, body: payload, credentials: 'same-origin', signal: opts.signal });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new ApiError(0, 'Papyrus ne répond pas. Vérifie la connexion.');
  }
  if (res.status === 401 && !opts.noAuthRedirect) onUnauthorized();
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new ApiError(res.status, (data && data.detail) || `Erreur ${res.status}`);
  return data;
}

export const get = (p, o) => api('GET', p, undefined, o);
export const post = (p, b, o) => api('POST', p, b ?? {}, o);
export const patch = (p, b) => api('PATCH', p, b);
export const del = (p) => api('DELETE', p);

export function qs(params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '' && v !== false) q.set(k, v === true ? '1' : v);
  const s = q.toString();
  return s ? '?' + s : '';
}
