<script>
  import { get } from '../lib/api.js';
  import { unlockTarget, docsVersion, queue } from '../lib/stores.js';
  import { dateTime, formatSize } from '../lib/format.js';
  import Icon from '../components/Icon.svelte';

  let items = [], loaded = false;
  $: load($docsVersion, $queue.locked);
  async function load() { try { items = (await get('/locked')).items; } catch { items = []; } loaded = true; }
</script>

<div class="page">
  <header><h1>À déverrouiller</h1><span class="muted">{items.length} PDF protégé{items.length > 1 ? 's' : ''}</span></header>
  <p class="muted intro">Ces PDF attendent leur mot de passe : ils ne sont ni analysés ni indexés. Une fois déverrouillés, Papyrus ne garde que la copie sans mot de passe.</p>
  {#each items as d (d.id)}
    <div class="row">
      <span class="ic"><Icon name="lock" /></span>
      <div class="info"><div class="t">{d.original_name}</div><div class="muted s">Importé le {dateTime(d.created_at)} · {formatSize(d.size)}</div></div>
      <button class="btn" on:click={() => unlockTarget.set(d)}>Saisir le mot de passe</button>
    </div>
  {:else}
    {#if loaded}<div class="empty">Aucun PDF en attente de mot de passe.</div>{/if}
  {/each}
</div>

<style>
  .intro { margin: 8px 0 16px; max-width: 70ch; }
  .row { display: flex; align-items: center; gap: 14px; padding: 12px 8px; border-bottom: 1px solid var(--border); }
  .ic { color: var(--warning); display: flex; }
  .info { flex: 1; min-width: 0; }
  .t { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .s { font-size: 13px; }
</style>
