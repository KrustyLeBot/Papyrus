<script>
  import { onDestroy, createEventDispatcher } from 'svelte';
  import { get, qs } from '../lib/api.js';
  import { groupByMonth, plural } from '../lib/format.js';
  import { docsVersion } from '../lib/stores.js';
  import DocRow from './DocRow.svelte';
  export let query = {};
  export let from = null;
  export let emptyText = 'Aucun document. Glisse des fichiers ici ou clique sur Importer.';
  const dispatch = createEventDispatcher();
  const PAGE = 50;

  let items = [], total = 0, loading = true, error = '', done = false, sentinel, observer, token = 0;
  let key = '';

  async function load(reset) {
    const my = ++token;
    if (reset) { items = []; done = false; loading = true; }
    error = '';
    try {
      const r = await get('/documents' + qs({ ...query, offset: reset ? 0 : items.length, limit: PAGE }));
      if (my !== token) return;
      items = reset ? r.items : [...items, ...r.items];
      total = r.total;
      done = items.length >= r.total;
      dispatch('total', total);
    } catch (e) { if (my === token) error = e.message; }
    if (my === token) loading = false;
  }

  // reload from the top when the filters or the documents change
  $: { const k = JSON.stringify(query) + ':' + $docsVersion; if (k !== key) { key = k; load(true); } }

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
      {#each m.docs as d (d.id)}<DocRow doc={d} {from} />{/each}
    </section>
  {/each}
  <div use:observe bind:this={sentinel} style="height:1px"></div>
  {#if loading}<div class="skrow"><div class="skeleton" style="width:52px;height:69px"></div></div>{/if}
{/if}

<style>
  .month { display: flex; align-items: baseline; gap: 10px; padding: 26px 8px 8px; border-bottom: 1px solid var(--border); }
  .month h2 { font-size: 15px; font-weight: 700; }
  .month span { font-size: 13px; }
  .skrow { display: flex; gap: 16px; padding: 10px 8px; align-items: center; }
  .skcol { display: flex; flex-direction: column; gap: 10px; flex: 1; }
</style>
