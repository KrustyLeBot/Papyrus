import { writable, derived, get } from 'svelte/store';
import { get as apiGet, setUnauthorizedHandler } from './api.js';

export const me = writable(undefined); // undefined: not known yet, null: logged out
export const folders = writable([]); // flat: {path, count} per folder that holds documents
export const queue = writable({ items: [], current: null, jobs: [], locked: 0 });
export const admin = writable(null);
export const toasts = writable([]);
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

// ---- folders: the real folders of the source, as a tree with counts
export async function loadFolders() {
  try { folders.set((await apiGet('/folders')).items); } catch { /* the caller shows its own error */ }
}

// [{path:'Impots/2025', count:3}] → [{name:'Impots', path:'Impots', total:3, children:[{name:'2025', …}]}]
export function buildTree(items) {
  const root = { children: new Map(), count: 0, total: 0 };
  for (const f of items) {
    let node = root;
    const parts = f.path ? f.path.split('/') : [];
    let path = '';
    for (const part of parts) {
      path = path ? `${path}/${part}` : part;
      if (!node.children.has(part)) node.children.set(part, { name: part, path, count: 0, total: 0, children: new Map() });
      node = node.children.get(part);
    }
    node.count += f.count;
  }
  const finish = (node) => {
    const kids = [...node.children.values()].map(finish).sort((a, b) => a.name.localeCompare(b.name, 'fr', { sensitivity: 'base' }));
    node.children = kids;
    node.total = node.count + kids.reduce((n, k) => n + k.total, 0);
    return node;
  };
  const done = finish(root);
  return { rootCount: done.count, nodes: done.children };
}
export const tree = derived(folders, ($f) => buildTree($f));

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
    const scanned = (before.scan?.last?.at || '') !== (q?.scan?.last?.at || '');
    if (q && (before.items.length !== q.items.length || before.locked !== q.locked || scanned)) {
      loadFolders();
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
