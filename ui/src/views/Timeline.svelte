<script>
  import { route, navigate } from '../lib/router.js';
  import { queue, toast, refreshQueue } from '../lib/stores.js';
  import { get, post } from '../lib/api.js';
  import { formatNumber } from '../lib/format.js';
  import DocList from '../components/DocList.svelte';
  import Icon from '../components/Icon.svelte';

  let total = null, types = [], asking = false;
  get('/types').then((r) => (types = r.items)).catch(() => {});
  $: hidden = $route.query.hidden === '1';
  $: folder = $route.query.folder ?? '';
  $: year = $route.query.year || '';
  $: type = $route.query.type || '';
  $: years = Array.from({ length: 15 }, (_, i) => String(new Date().getFullYear() - i));
  $: query = { hidden: hidden ? '1' : '', folder, type, date_from: year ? `${year}-01-01` : '', date_to: year ? `${year}-12-31` : '' };
  $: crumbs = folder && folder !== '.' ? folder.split('/').map((name, i, a) => ({ name, path: a.slice(0, i + 1).join('/') })) : [];
  function set(next) {
    const p = new URLSearchParams();
    const f = 'folder' in next ? next.folder : folder;
    const y = 'year' in next ? next.year : year;
    const t = 'type' in next ? next.type : type;
    if (f) p.set('folder', f);
    if (y) p.set('year', y);
    if (t) p.set('type', t);
    navigate('/' + (p.toString() ? '?' + p : ''));
  }
  async function scanNow() {
    asking = true;
    try { await post('/scan'); toast('Analyse des dossiers lancée.', 'success', 2500); setTimeout(refreshQueue, 800); } catch (e) { toast(e.message, 'error'); }
    asking = false;
  }
  $: scan = $queue.scan;
</script>

<div class="page">
  {#if crumbs.length || folder === '.'}
    <nav class="crumbs" aria-label="Dossier">
      <a href="/" on:click|preventDefault={() => set({ folder: '' })}>Tous les dossiers</a>
      {#each crumbs as c, i}<span>/</span>{#if i < crumbs.length - 1}<a href="/?folder={encodeURIComponent(c.path)}" on:click|preventDefault={() => set({ folder: c.path })}>{c.name}</a>{:else}<span class="here">{c.name}</span>{/if}{/each}
      {#if folder === '.'}<span>/</span><span class="here">À la racine</span>{/if}
    </nav>
  {/if}
  <header>
    <h1>{hidden ? 'Masqués' : crumbs.length ? crumbs[crumbs.length - 1].name : folder === '.' ? 'À la racine' : 'Chronologie'}</h1>
    {#if total !== null}<span class="muted">{formatNumber(total)} document{total > 1 ? 's' : ''}, triés par date du document{crumbs.length ? ', sous-dossiers inclus' : ''}</span>{/if}
  </header>
  {#if hidden}<p class="muted hint">Ces documents sont gardés (analyse, tags, suivi des déplacements) mais n'apparaissent ni dans la chronologie, ni dans les dossiers, ni dans la recherche. Ouvre-en un pour l'afficher à nouveau.</p>{/if}
  <div class="filters" role="group" aria-label="Filtres">
    <select class="filter" aria-label="Type de document" value={type} on:change={(e) => set({ type: e.target.value })}>
      <option value="">Tous les types</option>
      {#each types as t}<option value={t.name}>{t.name}</option>{/each}
    </select>
    <select class="filter" aria-label="Année" value={year} on:change={(e) => set({ year: e.target.value })}>
      <option value="">Toutes les années</option>
      {#each years as y}<option value={y}>{y}</option>{/each}
    </select>
    {#if type || year}<button class="btn ghost small" on:click={() => set({ type: '', year: '' })}>Effacer les filtres</button>{/if}
  </div>
  <DocList {query} on:total={(e) => (total = e.detail)}
    emptyText={hidden ? 'Aucun document masqué.' : folder || type || year ? 'Aucun document ne correspond à ces filtres.' : 'Aucun document pour l\'instant. Papyrus lit tes dossiers : les PDF et DOCX apparaissent ici après l\'analyse.'}>
    <svelte:fragment slot="empty">
      {#if !folder && !type && !year && !hidden}
        <p class="muted small">Dossier analysé : <span class="mono">{scan?.source || '/source'}</span>{#if scan?.warning}<br /><span class="warn">{scan.warning}</span>{/if}</p>
        <button class="btn primary" on:click={scanNow} disabled={asking || scan?.running}><Icon name="refresh" />Analyser maintenant</button>
      {/if}
    </svelte:fragment>
  </DocList>
</div>

<style>
  .crumbs { display: flex; gap: 6px; flex-wrap: wrap; font-size: 13px; color: var(--text-muted); margin-bottom: 4px; }
  .crumbs a:hover { color: var(--text); text-decoration: underline; }
  .crumbs .here { color: var(--text); }
  .filters { display: flex; gap: 8px; flex-wrap: wrap; margin: 14px 0 0; align-items: center; }
  .filter { height: 30px; padding: 0 10px; border-radius: var(--r-control); border: 1px solid var(--border); background: var(--surface); color: var(--text); font-size: 13px; }
  .small { font-size: 13px; text-align: center; }
  .hint { margin: 6px 0 0; max-width: 70ch; font-size: 13.5px; }
  .warn { color: var(--warning); }
</style>
