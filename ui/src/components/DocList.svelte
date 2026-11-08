<script>
  import { onDestroy, tick, createEventDispatcher } from 'svelte';
  import { get } from '../lib/api.js';
  import { qs } from '../lib/api.js';
  import { groupByMonth, plural } from '../lib/format.js';
  import { docsVersion } from '../lib/stores.js';
  import { takeRestore, listCount } from '../lib/router.js';
  import DocRow from './DocRow.svelte';
  export let query = {};
  export let emptyText = 'Aucun document.';
  const dispatch = createEventDispatcher();
  const PAGE = 50, MAX_PAGE = 200;

  let items = [], total = 0, loading = true, error = '', done = false, observer, token = 0;
  let qkey = null, vkey = $docsVersion;
  let restore = takeRestore(); // set when we come back to this list with the browser's back button

  // `want` documents, fetched page after page (the API gives at most MAX_PAGE at a time)
  async function fetchUpTo(want) {
    let got = [], totalNow = 0;
    while (got.length < want) {
      const r = await get('/documents' + qs({ ...query, offset: got.length, limit: Math.min(MAX_PAGE, want - got.length) }));
      got = got.concat(r.items);
      totalNow = r.total;
      if (!r.items.length || got.length >= r.total) break;
    }
    return { got, totalNow };
  }

  async function load(reset) {
    const my = ++token;
    if (reset) { items = []; done = false; loading = true; }
    error = '';
    try {
      const wanted = reset ? (restore ? Math.min(Math.max(restore.count, PAGE), 1000) : PAGE) : PAGE;
      const { got, totalNow } = reset ? await fetchUpTo(wanted) : await fetchMore();
      if (my !== token) return;
      items = reset ? got : [...items, ...got];
      total = totalNow;
      done = items.length >= totalNow;
      dispatch('total', total);
      if (reset && restore) {
        const y = restore.y;
        restore = null;
        loading = false;
        await tick();
        window.scrollTo(0, y);
      }
    } catch (e) { if (my === token) error = e.message; }
    if (my === token) loading = false;
  }
  async function fetchMore() {
    const r = await get('/documents' + qs({ ...query, offset: items.length, limit: PAGE }));
    return { got: r.items, totalNow: r.total };
  }

  // something changed behind the list's back (analysis finished, a scan found files): reload what is on screen in place,
  // without a skeleton and without moving the page
  async function refresh() {
    const my = ++token;
    try {
      const { got, totalNow } = await fetchUpTo(Math.max(items.length, PAGE));
      if (my !== token) return;
      items = got; total = totalNow; done = items.length >= totalNow;
      dispatch('total', total);
    } catch { /* the next change tries again */ }
  }

  $: if (JSON.stringify(query) !== qkey) { qkey = JSON.stringify(query); load(true); }
  $: if ($docsVersion !== vkey) { vkey = $docsVersion; if (qkey !== null && !loading) refresh(); }
  $: listCount.set(items.length);

  function observe(node) {
    observer = new IntersectionObserver((entries) => { if (entries[0].isIntersecting && !loading && !done && !error) { loading = true; load(false); } }, { rootMargin: '400px' });
    observer.observe(node);
    return { destroy: () => observer.disconnect() };
  }
  onDestroy(() => { token++; observer?.disconnect(); });
  $: months = groupByMonth(items);
</script>

{#if error}
  <div class="empty"><span>{error}</span><button class="btn" on:click={() => load(true)}>Réessayer</button></div>
{:else if loading && !items.length}
  <div class="sk">
    {#each [0, 1, 2, 3, 4] as i}
      <div class="skrow"><div class="skeleton" style="width:52px;height:69px"></div><div class="skcol"><div class="skeleton" style="width:{55 - i * 6}%;height:14px"></div><div class="skeleton" style="width:30%;height:12px"></div></div></div>
    {/each}
  </div>
{:else if !items.length}
  <div class="empty"><span>{emptyText}</span><slot name="empty" /></div>
{:else}
  {#each months as m (m.key)}
    <section>
      <div class="month"><h2>{m.label}</h2><span class="muted">{plural(m.docs.length, 'document', 'documents')}</span></div>
      {#each m.docs as d (d.id)}<DocRow doc={d} />{/each}
    </section>
  {/each}
  <div use:observe style="height:1px"></div>
  {#if loading}<div class="skrow"><div class="skeleton" style="width:52px;height:69px"></div></div>{/if}
{/if}

<style>
  .month { display: flex; align-items: baseline; gap: 10px; padding: 26px 8px 8px; border-bottom: 1px solid var(--border); }
  .month h2 { font-size: 15px; font-weight: 700; }
  .month span { font-size: 13px; }
  .skrow { display: flex; gap: 16px; padding: 10px 8px; align-items: center; }
  .skcol { display: flex; flex-direction: column; gap: 10px; flex: 1; }
</style>
