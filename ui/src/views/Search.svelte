<script>
  import { tick } from 'svelte';
  import { route, navigate, takeRestore } from '../lib/router.js';
  import { docsVersion } from '../lib/stores.js';
  import { get, qs } from '../lib/api.js';
  import DocRow from '../components/DocRow.svelte';
  import Icon from '../components/Icon.svelte';

  let items = [], total = 0, ms = 0, semantic = false, loading = false, error = '', ctl, facets = { years: [], types: [], tags: [] }, corrections = {};
  let restore = takeRestore();
  $: q = $route.query.q || '';
  $: exact = $route.query.exact === '1';
  $: folder = $route.query.folder || '';
  $: tag = $route.query.tag || '';
  $: type = $route.query.type || '';
  $: from = $route.query.from || '';
  $: to = $route.query.to || '';


  async function run(...deps) {
    ctl?.abort();
    ctl = new AbortController();
    loading = true; error = '';
    try {
      const r = await get('/search' + qs({ q, exact, folder, tag, type, date_from: from, date_to: to, limit: 60 }), { signal: ctl.signal });
      items = r.items; total = r.total; ms = r.elapsed_ms ?? 0; semantic = !!r.semantic; corrections = r.corrections || {}; facets = r.facets || facets;
      loading = false;
      if (restore) { const y = restore.y; restore = null; await tick(); window.scrollTo(0, y); }
    } catch (e) { if (e.name !== 'AbortError') { error = e.message; loading = false; } }
  }
  $: run(q, exact, folder, tag, type, from, to, $docsVersion);

  function set(k, v) {
    const p = new URLSearchParams($route.query);
    if (v === '' || v === false || v == null) p.delete(k); else p.set(k, v === true ? '1' : v);
    navigate('/?' + p, { replace: true });
  }
  $: year = from && to && from.slice(0, 4) === to.slice(0, 4) ? from.slice(0, 4) : '';
  $: hasFilter = tag || type || from || to;
  // each list offers what exists given the other filters (the server counts it over the results of this search)
  const withChosen = (list, value, key) => (value && !list.some((x) => x[key] === value) ? [...list, { [key]: value, count: 0 }] : list);
  function setYear(v) {
    const p = new URLSearchParams($route.query);
    p.delete('from'); p.delete('to');
    if (v) { p.set('from', `${v}-01-01`); p.set('to', `${v}-12-31`); }
    navigate('/?' + p, { replace: true });
  }
</script>

<div class="page">
  <header><h1>Résultats pour « {q} »</h1><button class="btn ghost small" on:click={() => navigate('/' + qs({ folder }))}>Revenir à la chronologie</button></header>
  <div class="filters" role="group" aria-label="Filtres">
    <select class="filter" aria-label="Tag" value={tag} on:change={(e) => set('tag', e.target.value)}>
      <option value="">Tous les tags</option>
      {#each withChosen(facets.tags, tag, 'name') as t}<option value={t.name}>{t.name} ({t.count})</option>{/each}
    </select>
    <select class="filter" aria-label="Type" value={type} on:change={(e) => set('type', e.target.value)}>
      <option value="">Tous les types</option>
      {#each withChosen(facets.types, type, 'name') as t}<option value={t.name}>{t.name} ({t.count})</option>{/each}
    </select>
    <select class="filter" aria-label="Année" value={year} on:change={(e) => setYear(e.target.value)}>
      <option value="">Toutes les années</option>
      {#each withChosen(facets.years, year, 'year') as y}<option value={y.year}>{y.year} ({y.count})</option>{/each}
    </select>
    {#if hasFilter}<button class="btn ghost small" on:click={() => navigate('/' + qs({ q, exact, folder }))}>Effacer les filtres</button>{/if}
    <label class="exact"><input type="checkbox" checked={exact} on:change={(e) => set('exact', e.target.checked)} />Exact seulement</label>
  </div>

  {#if error}
    <div class="empty">{error}</div>
  {:else if loading && !items.length}
    <div class="empty"><span class="muted">Recherche…</span></div>
  {:else}
    {#if q}<p class="count muted">{total} résultat{total > 1 ? 's' : ''} en {ms} ms · {semantic && !exact ? 'texte + sens' : 'texte'}{#if folder} · dans « {folder === '.' ? 'la racine' : folder} »{/if}</p>{/if}
    {#each Object.entries(corrections) as [word, near]}<p class="count muted">Aussi cherché pour « {word} » : {near.slice(0, 3).join(', ')}</p>{/each}
    {#each items as d (d.id)}
      <DocRow doc={d} snippet={d.snippet || ''} match={q ? d.match : ''} />
    {:else}
      <div class="empty">{q ? `Aucun document ne correspond à « ${q} ».` : 'Aucun document pour ces filtres.'}
        {#if q && !exact}<span class="hint">Essaie moins de mots, ou un préfixe : fact*</span>{/if}</div>
    {/each}
  {/if}
</div>

<style>
  .filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 14px 0 8px; }
  .filter { height: 30px; padding: 0 10px; border-radius: var(--r-control); border: 1px solid var(--border); background: var(--surface); color: var(--text); font-size: 13px; max-width: 200px; }
  .filter.date { display: inline-flex; align-items: center; gap: 6px; padding: 0 8px; color: var(--text-muted); }
  .filter.date input { background: transparent; border: 0; color: var(--text); font-size: 13px; width: 120px; }
  .exact { margin-left: auto; display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text-muted); }
  .exact input { accent-color: var(--accent); width: 16px; height: 16px; }
  .count { font-size: 13px; margin: 8px 8px 4px; }
  .hint { font-size: 13px; }
</style>
