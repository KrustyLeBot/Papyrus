<script>
  import { route, navigate } from '../lib/router.js';
  import { groups, docsVersion } from '../lib/stores.js';
  import { get, qs } from '../lib/api.js';
  import DocRow from '../components/DocRow.svelte';
  import Icon from '../components/Icon.svelte';

  let items = [], total = 0, ms = 0, semantic = false, loading = false, error = '', ctl, tags = [], types = [];
  $: q = $route.query.q || '';
  $: exact = $route.query.exact === '1';
  $: group = $route.query.group || '';
  $: tag = $route.query.tag || '';
  $: type = $route.query.type || '';
  $: from = $route.query.from || '';
  $: to = $route.query.to || '';
  $: roots = $groups.filter((g) => !g.inbox);

  get('/tags').then((r) => (tags = r.items.slice(0, 80))).catch(() => {});
  get('/types').then((r) => (types = r.items)).catch(() => {});

  async function run(...deps) {
    ctl?.abort();
    ctl = new AbortController();
    loading = true; error = '';
    try {
      const r = await get('/search' + qs({ q, exact, group, tag, type, date_from: from, date_to: to, limit: 60 }), { signal: ctl.signal });
      items = r.items; total = r.total; ms = r.elapsed_ms ?? 0; semantic = !!r.semantic;
      loading = false;
    } catch (e) { if (e.name !== 'AbortError') { error = e.message; loading = false; } }
  }
  $: run(q, exact, group, tag, type, from, to, $docsVersion);

  function set(k, v) {
    const p = new URLSearchParams($route.query);
    if (v === '' || v === false || v == null) p.delete(k); else p.set(k, v === true ? '1' : v);
    navigate('/search?' + p, { replace: true });
  }
  $: hasFilter = group || tag || type || from || to;
</script>

<div class="page">
  <header><h1>{q ? `Résultats pour « ${q} »` : 'Recherche'}</h1></header>
  <div class="filters" role="group" aria-label="Filtres">
    <select class="filter" aria-label="Groupe" value={group} on:change={(e) => set('group', e.target.value)}>
      <option value="">Tous les groupes</option>
      {#each roots as g (g.id)}<option value={g.id}>{g.parent_id ? '— ' : ''}{g.name}</option>{/each}
    </select>
    <select class="filter" aria-label="Tag" value={tag} on:change={(e) => set('tag', e.target.value)}>
      <option value="">Tous les tags</option>
      {#each tags as t (t.id)}<option value={t.name}>{t.name} ({t.count})</option>{/each}
    </select>
    <select class="filter" aria-label="Type" value={type} on:change={(e) => set('type', e.target.value)}>
      <option value="">Tous les types</option>
      {#each types as t}<option value={t.name}>{t.name}</option>{/each}
    </select>
    <label class="filter date"><span>Du</span><input type="date" value={from} on:change={(e) => set('from', e.target.value)} /></label>
    <label class="filter date"><span>au</span><input type="date" value={to} on:change={(e) => set('to', e.target.value)} /></label>
    {#if hasFilter}<button class="btn ghost small" on:click={() => navigate('/search' + qs({ q, exact }))}>Effacer les filtres</button>{/if}
    <label class="exact"><input type="checkbox" checked={exact} on:change={(e) => set('exact', e.target.checked)} />Exact seulement</label>
  </div>

  {#if error}
    <div class="empty">{error}</div>
  {:else if loading && !items.length}
    <div class="empty"><span class="muted">Recherche…</span></div>
  {:else}
    {#if q}<p class="count muted">{total} résultat{total > 1 ? 's' : ''} en {ms} ms · {semantic && !exact ? 'texte + sens' : 'texte'}</p>{/if}
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
