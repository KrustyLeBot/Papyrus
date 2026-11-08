import { writable, derived, get } from 'svelte/store';
import { get as apiGet, setUnauthorizedHandler } from './api.js';

export const me = writable(undefined); // undefined: not known yet, null: logged out
export const groups = writable([]);
export const queue = writable({ items: [], current: null, jobs: [], locked: 0 });
export const admin = writable(null);
export const toasts = writable([]);
export const uploadOpen = writable(false);
export const paletteOpen = writable(false);
export const drawerOpen = writable(false);
export const unlockTarget = writable(null); // the locked document whose password dialog is open
export const theme = writable(document.documentElement.dataset.theme || 'dark');

setUnauthorizedHandler(() => me.set(null));

theme.subscribe((t) => {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem('papyrus.theme', t); } catch { /* private mode */ }
});

let toastId = 0;
export function toast(message, kind = 'info', ms = 4500) {
  const id = ++toastId;
  toasts.update((t) => [...t, { id, message, kind }]);
  setTimeout(() => toasts.update((t) => t.filter((x) => x.id !== id)), ms);
}

// ---- groups: the tree, with counts, kept fresh after each change
export async function loadGroups() {
  try { groups.set((await apiGet('/groups')).items); } catch { /* the caller shows its own error */ }
}

export function buildTree(items) {
  const byParent = new Map();
  for (const g of items) {
    const k = g.parent_id ?? null;
    if (!byParent.has(k)) byParent.set(k, []);
    byParent.get(k).push(g);
  }
  const build = (parent) => (byParent.get(parent) || []).map((g) => ({ ...g, children: build(g.id) }));
  return build(null);
}
export const tree = derived(groups, ($g) => buildTree($g));
export function groupById(items, id) { return items.find((g) => g.id === id); }
export function rootColor(items, id) {
  let g = groupById(items, id);
  const seen = new Set();
  while (g && g.parent_id != null && !seen.has(g.id)) { seen.add(g.id); g = groupById(items, g.parent_id); }
  return g?.color || '#7C8AA5';
}
export function groupPath(items, id) {
  const names = [];
  let g = groupById(items, id);
  const seen = new Set();
  while (g && !seen.has(g.id)) { seen.add(g.id); names.unshift(g.name); g = g.parent_id != null ? groupById(items, g.parent_id) : null; }
  return names.join(' / ');
}

// ---- processing queue: polled while something is waiting, slowly otherwise
let timer = null;
export async function refreshQueue() {
  try {
    const q = await apiGet('/queue');
    queue.set(q);
    return q;
  } catch { return null; }
}
export function startQueuePolling() {
  stopQueuePolling();
  const tick = async () => {
    const before = get(queue);
    const q = await refreshQueue();
    const busy = q && (q.items.some((i) => i.status !== 'error') || q.current);
    if (q && (before.items.length !== q.items.length || before.locked !== q.locked)) {
      loadGroups();
      docsVersion.update((n) => n + 1);
    }
    timer = setTimeout(tick, busy ? 1500 : 8000);
  };
  tick();
}
export function stopQueuePolling() { if (timer) clearTimeout(timer); timer = null; }
// bumped whenever documents changed behind the lists' back (processing finished, upload…)
export const docsVersion = writable(0);
export const bumpDocs = () => docsVersion.update((n) => n + 1);
