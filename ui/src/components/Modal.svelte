<script>
  import { createEventDispatcher, onMount, onDestroy } from 'svelte';
  export let title = '';
  export let width = 460;
  export let label = '';
  const dispatch = createEventDispatcher();
  let box;
  let previouslyFocused;

  function key(e) {
    if (e.key === 'Escape') { e.stopPropagation(); dispatch('close'); }
    if (e.key === 'Tab' && box) {
      const f = [...box.querySelectorAll('button:not(:disabled), [href], input:not(:disabled), select, textarea, [tabindex]:not([tabindex="-1"])')];
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }
  onMount(() => {
    previouslyFocused = document.activeElement;
    const target = box.querySelector('[data-autofocus]') || box.querySelector('input, button');
    target?.focus();
  });
  onDestroy(() => previouslyFocused?.focus?.());
</script>

<svelte:window on:keydown={key} />
<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
<div class="scrim" on:mousedown|self={() => dispatch('close')}>
  <div class="dlg" bind:this={box} role="dialog" aria-modal="true" aria-label={label || title} style="width:{width}px">
    {#if title}<h2>{title}</h2>{/if}
    <slot />
  </div>
</div>

<style>
  .scrim { position: fixed; inset: 0; background: var(--scrim); display: flex; align-items: center; justify-content: center; z-index: 60; padding: 16px; animation: fade 120ms ease-out; }
  .dlg { max-width: 100%; max-height: calc(100vh - 32px); overflow: auto; background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-panel);
    box-shadow: var(--shadow); padding: 22px; display: flex; flex-direction: column; gap: 14px; }
  h2 { font-size: 17px; font-weight: 700; }
  @keyframes fade { from { opacity: 0; } }
</style>
