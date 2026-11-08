<script>
  import { link } from '../lib/router.js';
  import { formatDate, pagesLabel } from '../lib/format.js';
  import Thumb from './Thumb.svelte';
  import Icon from './Icon.svelte';
  export let doc;
  export let snippet = '';
  export let match = '';

  $: place = doc.folder ? doc.folder : doc.path ? 'racine' : '';
  $: state = doc.status === 'pending' ? 'En attente' : doc.status === 'processing' ? 'Analyse en cours' : doc.status === 'error' ? 'Échec du traitement' : '';
</script>

<a class="row" href="/documents/{doc.id}" use:link>
  <Thumb {doc} width={52} />
  <div class="main">
    <div class="title">{doc.title}</div>
    {#if snippet}<div class="snippet muted">… {@html snippet} …</div>{:else if doc.summary}<div class="snippet muted one">{doc.summary}</div>{/if}
    <div class="chips">
      {#if place}<span class="chip" title={doc.path}><Icon name="folder" size={12} /><span>{place}</span></span>{/if}
      {#if doc.copies > 1}<span class="tag">{doc.copies} copies</span>{/if}
      {#each doc.tags.slice(0, 4) as t (t.id)}<span class="tag">{t.name}</span>{/each}
      {#if match}<span class="how muted">{match}</span>{/if}
    </div>
  </div>
  <div class="meta muted">
    {#if state}<span class="state" class:err={doc.status === 'error'}>{#if doc.status === 'error'}<Icon name="alert" size={14} />{/if}{state}</span>
    {:else}{pagesLabel(doc)}{/if}
  </div>
  <div class="date muted">{formatDate(doc.date || doc.created_at)}</div>
</a>

<style>
  .row { display: grid; grid-template-columns: 52px minmax(0, 1fr) auto 96px; gap: 16px; align-items: center; padding: 10px 8px; border-bottom: 1px solid var(--border); border-radius: var(--r-control); transition: background var(--t); }
  .row:hover { background: var(--surface); }
  .main { min-width: 0; display: flex; flex-direction: column; gap: 6px; }
  .title { font-weight: 600; font-size: 14.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .snippet { font-size: 13.5px; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .snippet.one { -webkit-line-clamp: 1; line-clamp: 1; }
  .chips { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; min-width: 0; }
  .how { font-size: 12px; }
  .meta { font-size: 13px; white-space: nowrap; }
  .date { font-size: 13px; text-align: right; font-variant-numeric: tabular-nums; }
  .state { display: inline-flex; align-items: center; gap: 5px; }
  .state.err { color: var(--danger); }
  @media (max-width: 720px) {
    .row { grid-template-columns: 52px minmax(0, 1fr); }
    .meta { display: none; }
    .date { grid-column: 2; text-align: left; margin-top: -8px; }
  }
</style>
