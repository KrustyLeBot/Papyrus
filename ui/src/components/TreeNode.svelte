<script>
  import { createEventDispatcher } from 'svelte';
  import { link } from '../lib/router.js';
  import { formatNumber } from '../lib/format.js';
  import Icon from './Icon.svelte';
  export let node;
  export let depth = 0;
  export let selected = null; // the folder path shown in the timeline
  export let open; // Set of expanded folder paths
  const dispatch = createEventDispatcher();

  $: hasChildren = node.children.length > 0;
  $: expanded = open.has(node.path);
  $: active = selected === node.path;
  function toggle(e) { e.preventDefault(); e.stopPropagation(); dispatch('toggle', node.path); }
</script>

<div class="row" class:sel={active} style="padding-left:{6 + depth * 14}px">
  {#if hasChildren}
    <button class="chev" aria-label={expanded ? 'Replier' : 'Déplier'} aria-expanded={expanded} on:click={toggle}><Icon name={expanded ? 'chevronDown' : 'chevronRight'} size={14} /></button>
  {:else}<span class="chev"></span>{/if}
  <a href="/?folder={encodeURIComponent(node.path)}" use:link class="name" title={node.path} on:click>
    <Icon name="folder" size={15} />
    <span class="label">{node.name}</span>
    <span class="n">{formatNumber(node.total)}</span>
  </a>
</div>
{#if hasChildren && expanded}
  <div>
    {#each node.children as child (child.path)}
      <svelte:self node={child} depth={depth + 1} {selected} {open} on:toggle on:click />
    {/each}
  </div>
{/if}

<style>
  .row { display: flex; align-items: center; min-height: 30px; border-radius: var(--r-control); padding-right: 6px; }
  .row:hover { background: var(--surface-2); }
  .row.sel { background: var(--accent-soft); }
  .chev { width: 22px; height: 22px; flex: 0 0 22px; display: inline-flex; align-items: center; justify-content: center; border: 0; background: transparent; color: var(--text-muted); border-radius: 4px; padding: 0; }
  button.chev:hover { color: var(--text); background: var(--border); }
  .name { flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; height: 30px; font-size: 13.5px; }
  .name :global(.icon) { color: var(--text-muted); }
  .label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
  .n { color: var(--text-muted); font-size: 12px; font-variant-numeric: tabular-nums; }
</style>
