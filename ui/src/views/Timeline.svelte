<script>
  import { route, navigate } from '../lib/router.js';
  import { groups, uploadOpen } from '../lib/stores.js';
  import { formatNumber } from '../lib/format.js';
  import DocList from '../components/DocList.svelte';
  import Icon from '../components/Icon.svelte';

  let total = null;
  $: groupId = $route.query.group ? +$route.query.group : null;
  $: year = $route.query.year || '';
  $: roots = $groups.filter((g) => g.parent_id == null && !g.inbox);
  $: inbox = $groups.find((g) => g.inbox);
  $: years = [...new Set(Array.from({ length: 12 }, (_, i) => String(new Date().getFullYear() - i)))];
  $: query = { group: groupId, date_from: year ? `${year}-01-01` : '', date_to: year ? `${year}-12-31` : '' };
  function set(next) {
    const p = new URLSearchParams();
    const g = 'group' in next ? next.group : groupId;
    const y = 'year' in next ? next.year : year;
    if (g) p.set('group', g);
    if (y) p.set('year', y);
    navigate('/' + (p.toString() ? '?' + p : ''));
  }
  $: allCount = $groups.filter((g) => g.parent_id == null).reduce((n, g) => n + g.total, 0);
</script>

<div class="page">
  <header>
    <h1>Chronologie</h1>
    {#if total !== null}<span class="muted">{formatNumber(total)} document{total > 1 ? 's' : ''}, triés par date du document</span>{/if}
  </header>
  <div class="filters" role="group" aria-label="Filtres">
    <button class="filter" class:on={!groupId} on:click={() => set({ group: null })}>Tous</button>
    {#each roots as g (g.id)}
      <button class="filter" class:on={groupId === g.id} on:click={() => set({ group: g.id })}><span class="dot" style="background:{g.color}"></span>{g.name}</button>
    {/each}
    {#if inbox && inbox.count > 0}<button class="filter" class:on={groupId === inbox.id} on:click={() => set({ group: inbox.id })}>À classer<span class="n">{inbox.count}</span></button>{/if}
    <select class="filter year" aria-label="Année" value={year} on:change={(e) => set({ year: e.target.value })}>
      <option value="">Toutes les années</option>
      {#each years as y}<option value={y}>{y}</option>{/each}
    </select>
  </div>
  <DocList {query} on:total={(e) => (total = e.detail)}>
    <button slot="empty" class="btn primary" on:click={() => uploadOpen.set(true)}><Icon name="upload" />Importer des documents</button>
  </DocList>
</div>

<style>
  .filters { display: flex; gap: 8px; flex-wrap: wrap; margin: 14px 0 0; }
  .filter { display: inline-flex; align-items: center; gap: 6px; height: 30px; padding: 0 10px; border-radius: var(--r-control); border: 1px solid var(--border); color: var(--text-muted); font-size: 13px; background: transparent; transition: background var(--t); }
  .filter:hover { background: var(--surface-2); }
  .filter.on { background: var(--accent-soft); border-color: transparent; color: var(--text); }
  .filter .n { color: var(--text-muted); font-size: 12px; }
  .year { appearance: none; padding-right: 10px; }
</style>
