const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000/api';

export async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, options);
  const type = response.headers.get('content-type') || '';
  const body = type.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) throw new Error(typeof body === 'string' ? body : body.detail || `Request failed (${response.status})`);
  return body;
}
export function jsonOptions(method, data) { return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }; }
export function uploadOptions(file) { const form = new FormData(); form.append('file', file); return { method: 'POST', body: form }; }
export { API };
