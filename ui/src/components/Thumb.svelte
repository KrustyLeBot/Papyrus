<script>
  export let doc;
  export let width = 52;
  export let ratio = 1.32;
  let failed = false;
  $: height = Math.round(width * ratio);
  const color = '#7C8AA5';
  $: pending = doc.status === 'pending' || doc.status === 'processing';
</script>

<div class="thumb" style="width:{width}px;height:{height}px">
  {#if doc.thumb && !failed}
    <img src={doc.thumb} alt="" loading="lazy" on:error={() => (failed = true)} />
  {:else}
    <div class="ph" class:skeleton={pending} style="padding:{Math.round(width * 0.14)}px">
      {#if !pending}
        <b style="background:{color}"></b>
        {#each [90, 70, 85, 50, 80] as w}<i style="width:{w}%"></i>{/each}
      {/if}
    </div>
  {/if}
</div>

<style>
  .ph { width: 100%; height: 100%; display: flex; flex-direction: column; gap: 3px; }
  b { display: block; height: 6px; width: 60%; border-radius: 2px; margin-bottom: 3px; }
  i { display: block; height: 3px; border-radius: 2px; background: var(--paper-line); }
</style>
