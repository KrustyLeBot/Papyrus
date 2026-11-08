<script>
  import { onMount, onDestroy } from 'svelte';
  import { route, navigate } from './lib/router.js';
  import { get } from './lib/api.js';
  import { me, loadFolders, startQueuePolling, stopQueuePolling, paletteOpen, drawerOpen } from './lib/stores.js';
  import Sidebar from './components/Sidebar.svelte';
  import Topbar from './components/Topbar.svelte';
  import Toasts from './components/Toasts.svelte';
  import UnlockDialog from './components/UnlockDialog.svelte';
  import CommandPalette from './components/CommandPalette.svelte';
  import Icon from './components/Icon.svelte';
  import Login from './views/Login.svelte';
  import Timeline from './views/Timeline.svelte';
  import Search from './views/Search.svelte';
  import Document from './views/Document.svelte';
  import Unlock from './views/Unlock.svelte';
  import Admin from './views/Admin.svelte';

  let searchInput, degraded = false, offline = false;

  async function boot() {
    try { me.set(await get('/auth/me', { noAuthRedirect: true })); } catch (e) { me.set(e.status === 401 ? null : null); offline = e.status === 0; }
  }
  boot();
  $: if ($me) { loadFolders(); startQueuePolling(); checkModel(); } else stopQueuePolling();
  async function checkModel() {
    try { const s = await get('/admin/status'); degraded = s.model.status === 'absent' || s.model.status === 'downloading'; } catch { /* ignore */ }
  }
  onDestroy(stopQueuePolling);

  let chord = null, chordTimer;
  function typing(e) {
    const t = e.target;
    return t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
  }
  function keydown(e) {
    if (!$me) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); paletteOpen.update((v) => !v); return; }
    if (e.ctrlKey || e.metaKey || e.altKey || typing(e) || $paletteOpen || document.querySelector('[role=dialog]')) return;
    if (chord === 'g') {
      clearTimeout(chordTimer); chord = null;
      const to = { t: '/' }[e.key];
      if (to) { e.preventDefault(); navigate(to); }
      return;
    }
    if (e.key === '/') { e.preventDefault(); searchInput?.focus(); }
    else if (e.key === 'g') { chord = 'g'; clearTimeout(chordTimer); chordTimer = setTimeout(() => (chord = null), 1200); }
  }
  $: titles = { timeline: 'Chronologie', document: 'Document', unlock: 'À déverrouiller', admin: 'Administration' };
  $: document.title = ($me ? (titles[$route.name] ? titles[$route.name] + ' · ' : '') : 'Connexion · ') + 'Papyrus';
  $: tab = $route.name === 'timeline' ? 'timeline' : '';
</script>

<svelte:window on:keydown={keydown} />

{#if $me === undefined}
  <div class="boot">{#if offline}<span class="muted">Papyrus ne répond pas.</span>{/if}</div>
{:else if $me === null}
  <Login />
{:else}
  <div class="shell">
    <Sidebar />
    <div class="content">
      <Topbar bind:input={searchInput} />
      {#if degraded}
        <div class="banner"><span class="icon"><Icon name="alert" /></span>Mode dégradé : le modèle d'embeddings n'est pas disponible. La recherche plein texte et les tags fonctionnent ; la recherche par sens et les documents similaires reviendront avec le modèle.</div>
      {/if}
      <main id="main">
        {#if $route.name === 'timeline'}{#if $route.query.q}<Search />{:else}<Timeline />{/if}
        {:else if $route.name === 'document'}{#key $route.params.id}<Document />{/key}
        {:else if $route.name === 'unlock'}<Unlock />
        {:else if $route.name === 'admin'}<Admin />
        {:else}<div class="empty">Cette page n'existe pas.<button class="btn" on:click={() => navigate('/')}>Retour à la chronologie</button></div>{/if}
      </main>
    </div>
  </div>
  <nav class="tabs" aria-label="Navigation mobile">
    <button class:on={tab === 'timeline'} on:click={() => navigate('/')}><Icon name="clock" size={20} />Chronologie</button>
    <button on:click={() => drawerOpen.set(true)}><Icon name="folder" size={20} />Dossiers</button>
  </nav>
  <UnlockDialog />
  <CommandPalette />
{/if}
<Toasts />

<style>
  .boot { min-height: 100vh; display: flex; align-items: center; justify-content: center; }
  .shell { display: flex; min-height: 100vh; }
  .content { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  main { flex: 1; }
  .tabs { display: none; }
  @media (max-width: 900px) {
    .tabs { display: flex; position: fixed; left: 0; right: 0; bottom: 0; z-index: 30; height: 56px; background: var(--surface); border-top: 1px solid var(--border); }
    .tabs button { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px; border: 0; background: transparent; color: var(--text-muted); font-size: 11.5px; font-weight: 500; }
    .tabs button.on { color: var(--text); }
  }
</style>
