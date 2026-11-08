<script>
  import { route, navigate } from '../lib/router.js';
  import { queue, uploadOpen, drawerOpen, paletteOpen } from '../lib/stores.js';
  import Icon from './Icon.svelte';

  let q = '';
  export let input = null;
  $: if ($route.name === 'search') q = $route.query.q || '';
  function submit(e) {
    e.preventDefault();
    const text = q.trim();
    navigate(text ? `/search?q=${encodeURIComponent(text)}` : '/search');
  }
  $: busy = $queue.items.filter((i) => i.status !== 'error').length + ($queue.current && !$queue.items.some((i) => i.id === $queue.current.id) ? 1 : 0);
  $: step = $queue.current?.step || '';
</script>

<header class="top">
  <button class="icon-btn menu" aria-label="Ouvrir le menu" on:click={() => drawerOpen.set(true)}><Icon name="menu" size={20} /></button>
  <form class="search" on:submit={submit} role="search">
    <Icon name="search" />
    <input bind:this={input} bind:value={q} placeholder="Rechercher dans vos documents" aria-label="Rechercher" />
    <span class="kbd hide-sm">/</span>
  </form>
  <div class="right">
    {#if busy > 0}
      <a class="proc" href="/admin" on:click|preventDefault={() => navigate('/admin')} title={step}>
        <span class="bar"><span></span></span>
        <span>Traitement de {busy} document{busy > 1 ? 's' : ''}</span>
      </a>
    {/if}
    <button class="btn primary import" on:click={() => uploadOpen.set(true)}><Icon name="upload" />Importer</button>
  </div>
</header>

<style>
  .top { min-height: 60px; display: flex; align-items: center; gap: 12px; padding: 8px 28px; border-bottom: 1px solid var(--border); position: sticky; top: 0; background: var(--bg); z-index: 20; }
  .search { display: flex; align-items: center; gap: 8px; height: 36px; padding: 0 12px; border-radius: var(--r-control); background: var(--surface-2); border: 1px solid var(--border); color: var(--text-muted); flex: 1 1 auto; max-width: 560px; }
  .search:focus-within { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
  .search input { flex: 1; min-width: 0; background: transparent; border: 0; outline: 0; color: var(--text); }
  .right { margin-left: auto; display: flex; align-items: center; gap: 14px; }
  .proc { display: flex; align-items: center; gap: 10px; font-size: 12.5px; color: var(--text-muted); }
  .bar { width: 64px; height: 4px; border-radius: 2px; background: var(--surface-2); overflow: hidden; position: relative; }
  .bar span { position: absolute; inset: 0 auto 0 0; width: 40%; background: var(--accent); animation: slide 1.4s ease-in-out infinite; }
  @keyframes slide { 0% { left: -40%; } 100% { left: 100%; } }
  .menu { display: none; }
  @media (max-width: 900px) { .menu { display: inline-flex; } .top { padding: 8px 12px; } .hide-sm, .proc span:last-child, .import { display: none; } }
</style>
