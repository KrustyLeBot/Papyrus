const MONTHS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'];
const SHORT = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'];

export function parseDay(iso) {
  if (!iso) return null;
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return y ? { y, m, d } : null;
}
export function formatDate(iso) {
  const p = parseDay(iso);
  return p ? `${p.d} ${SHORT[p.m - 1]} ${p.y}` : '';
}
export function monthKey(iso) {
  const p = parseDay(iso);
  return p ? `${p.y}-${String(p.m).padStart(2, '0')}` : 'inconnu';
}
export function monthLabel(key) {
  if (key === 'inconnu') return 'Sans date';
  const [y, m] = key.split('-').map(Number);
  const name = MONTHS[m - 1];
  return `${name[0].toUpperCase()}${name.slice(1)} ${y}`;
}
export function toInputDate(iso) {
  const p = parseDay(iso);
  return p ? `${String(p.d).padStart(2, '0')}/${String(p.m).padStart(2, '0')}/${p.y}` : '';
}
// "14/10/2026", "14.10.26" or "2026-10-14" → "2026-10-14", else null
export function fromInputDate(text) {
  const t = (text || '').trim();
  let m = t.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  let y, mo, d;
  if (m) [y, mo, d] = [+m[1], +m[2], +m[3]];
  else if ((m = t.match(/^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})$/))) {
    [d, mo, y] = [+m[1], +m[2], +m[3]];
    if (m[3].length === 2) y += y <= 69 ? 2000 : 1900;
  } else return null;
  const dt = new Date(Date.UTC(y, mo - 1, d));
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return null;
  return `${y}-${String(mo).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}
export function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} o`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} Ko`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 / 1024).toFixed(1).replace('.', ',')} Mo`;
  return `${(bytes / 1024 ** 3).toFixed(2).replace('.', ',')} Go`;
}
export function plural(n, one, many) { return `${n} ${n > 1 ? many : one}`; }
export function pagesLabel(d) { return d.pages ? `${d.pages} p.` : ''; }
export function formatNumber(n) { return new Intl.NumberFormat('fr-FR').format(n); }
export function dateTime(iso) {
  if (!iso) return '';
  const dt = new Date(iso);
  return isNaN(dt) ? '' : dt.toLocaleString('fr-FR', { dateStyle: 'medium', timeStyle: 'short' });
}
export function groupByMonth(items) {
  const out = [];
  for (const d of items) {
    const key = monthKey(d.date || d.created_at);
    const last = out[out.length - 1];
    if (last && last.key === key) last.docs.push(d);
    else out.push({ key, label: monthLabel(key), docs: [d] });
  }
  return out;
}
