import { writable, get } from 'svelte/store';

// Routes: / (timeline) · /search · /groups[/:id] · /documents/:id · /unlock · /admin
function parse(url) {
  const path = url.pathname.replace(/\/+$/, '') || '/';
  const query = Object.fromEntries(url.searchParams);
  let m;
  if (path === '/') return { name: 'timeline', params: {}, query };
  if (path === '/search') return { name: 'timeline', params: {}, query }; // the search bar filters the timeline
  if (path === '/groups') return { name: 'groups', params: {}, query };
  if ((m = path.match(/^\/groups\/(\d+)$/))) return { name: 'groups', params: { id: +m[1] }, query };
  if ((m = path.match(/^\/documents\/(\d+)$/))) return { name: 'document', params: { id: +m[1] }, query };
  if (path === '/unlock') return { name: 'unlock', params: {}, query };
  if (path === '/admin') return { name: 'admin', params: {}, query };
  return { name: 'notfound', params: {}, query };
}

export const route = writable(parse(new URL(window.location.href)));
// the path the user came from inside the app, so "back" from a document returns to the right list
export const previous = writable(null);

window.addEventListener('popstate', () => route.set(parse(new URL(window.location.href))));

export function navigate(to, { replace = false } = {}) {
  const current = window.location.pathname + window.location.search;
  if (to === current) return;
  previous.set(current);
  if (replace) history.replaceState({}, '', to); else history.pushState({}, '', to);
  route.set(parse(new URL(to, window.location.origin)));
  window.scrollTo(0, 0);
}

export function goBack(fallback = '/') {
  if (get(previous)) history.back(); else navigate(fallback);
}

// <a use:link href="/x"> navigates without reloading, keeping ctrl/middle click for new tabs
export function link(node) {
  const click = (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const href = node.getAttribute('href');
    if (!href || !href.startsWith('/') || node.target === '_blank') return;
    e.preventDefault();
    navigate(href);
  };
  node.addEventListener('click', click);
  return { destroy: () => node.removeEventListener('click', click) };
}
