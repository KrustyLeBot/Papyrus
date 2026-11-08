<script>
  import { createEventDispatcher } from 'svelte';
  import { link, navigate } from '../lib/router.js';
  import { formatNumber } from '../lib/format.js';
  import Icon from './Icon.svelte';
  export let node;
  export let depth = 0;
  export let selected = null;
  export let open; // Set of expanded group ids
  export let color = null;
  const dispatch = createEventDispatcher();
  let over = false;

  $: dotColor = color || node.color || '#7C8AA5';
  $: expanded = open.has(node.id);
  $: hasChildren = node.children.length > 0;

  function toggle(e) { e.preventDefault(); e.stopPropagation(); dispatch('toggle', node.id); }
  function dragover(e) {
    if (e.dataTransfer?.types.includes('application/x-papyrus-doc')) { e.preventDefault(); over = true; }
  }
  function drop(e) {
    over = false;
    const raw = e.dataTransfer?.getData('application/x-papyrus-doc');
    if (!raw) return;
    e.preventDefault();
    dispatch('drop-doc', { group: node, ...JSON.parse(raw) });
  }
</script>

<div class="row" class:sel={selected === node.id} class:over style="padding-left:{6 + depth * 16}px"
  on:dragover={dragover} on:dragleave={() => (over = false)} on:drop={drop}>
  {#if hasChildren}
    <button class="chev" aria-label={expanded ? 'Replier' : 'Déplier'} on:click={toggle}><Icon name={expanded ? 'chevronDown' : 'chevronRight'} size={14} /></button>
  {:else}<span class="chev"></span>{/if}
  <a href="/groups/{node.id}" use:link class="name">
    {#if depth === 0}<span class="dot" style="background:{dotColor}"></span>{/if}
    <span class="label">{node.name}</span>
    <span class="n">{formatNumber(node.total)}</span>
  </a>
</div>
{#if hasChildren && expanded}
  <div>
    {#each node.children as child (child.id)}
      <svelte:self node={child} depth={depth + 1} {selected} {open} color={dotColor} on:toggle on:drop-doc />
    {/each}
  </div>
{/if}

<style>
  .row { display: flex; align-items: center; min-height: 30px; border-radius: var(--r-control); padding-right: 6px; }
  .row:hover { background: var(--surface-2); }
  .row.sel { background: var(--accent-soft); }
  .row.over { outline: 2px solid var(--accent); outline-offset: -2px; background: var(--accent-soft); }
  .chev { width: 22px; height: 22px; flex: 0 0 22px; display: inline-flex; align-items: center; justify-content: center; border: 0; background: transparent; color: var(--text-muted); border-radius: 4px; padding: 0; }
  button.chev:hover { color: var(--text); background: var(--border); }
  .name { flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; height: 30px; font-size: 13.5px; }
  .label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
  .n { color: var(--text-muted); font-size: 12px; font-variant-numeric: tabular-nums; }
  .row:not(.sel) .label { color: var(--text); }
</style>
