<script>
  import { onMount, onDestroy } from 'svelte';
  import { route, navigate } from './lib/router.js';
  import { get } from './lib/api.js';
  import { me, loadGroups, startQueuePolling, stopQueuePolling, uploadOpen, paletteOpen, drawerOpen, queue } from './lib/stores.js';
  import Sidebar from './components/Sidebar.svelte';
  import Topbar from './components/Topbar.svelte';
  import Toasts from './components/Toasts.svelte';
  import UploadManager from './components/UploadManager.svelte';
  import UnlockDialog from './components/UnlockDialog.svelte';
  import CommandPalette from './components/CommandPalette.svelte';
  import Icon from './components/Icon.svelte';
  import Login from './views/Login.svelte';
  import Timeline from './views/Timeline.svelte';
  import Search from './views/Search.svelte';
  import Groups from './views/Groups.svelte';
  import Document from './views/Document.svelte';
  import Unlock from './views/Unlock.svelte';
  import Admin from './views/Admin.svelte';

  let searchInput, degraded = false, offline = false;

  async function boot() {
    try { me.set(await get('/auth/me', { noAuthRedirect: true })); } catch (e) { me.set(e.status === 401 ? null : null); offline = e.status === 0; }
  }
  boot();
  $: if ($me) { loadGroups(); startQueuePolling(); checkModel(); } else stopQueuePolling();
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
    if (e.ctrlKey || e.metaKey || e.altKey || typing(e) || $paletteOpen || $uploadOpen || document.querySelector('[role=dialog]')) return;
    if (chord === 'g') {
      clearTimeout(chordTimer); chord = null;
      const to = { t: '/', s: '/search', g: '/groups' }[e.key];
      if (to) { e.preventDefault(); navigate(to); }
      return;
    }
    if (e.key === '/') { e.preventDefault(); searchInput?.focus(); }
    else if (e.key === 'u') { e.preventDefault(); uploadOpen.set(true); }
    else if (e.key === 'g') { chord = 'g'; clearTimeout(chordTimer); chordTimer = setTimeout(() => (chord = null), 1200); }
  }
  $: titles = { timeline: 'Chronologie', search: 'Recherche', groups: 'Groupes', document: 'Document', unlock: 'À déverrouiller', admin: 'Administration' };
  $: document.title = ($me ? (titles[$route.name] ? titles[$route.name] + ' · ' : '') : 'Connexion · ') + 'Papyrus';
  $: tab = $route.name === 'timeline' ? 'timeline' : $route.name === 'search' ? 'search' : $route.name === 'groups' ? 'groups' : '';
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
        <div class="banner"><span class="icon"><Icon name="alert" /></span>Mode dégradé : le modèle d'embeddings n'est pas disponible. La recherche plein texte et le classement par règles fonctionnent ; la recherche par sens et les documents similaires reviendront avec le modèle.</div>
      {/if}
      <main id="main">
        {#if $route.name === 'timeline'}<Timeline />
        {:else if $route.name === 'search'}<Search />
        {:else if $route.name === 'groups'}<Groups />
        {:else if $route.name === 'document'}{#key $route.params.id}<Document />{/key}
        {:else if $route.name === 'unlock'}<Unlock />
        {:else if $route.name === 'admin'}<Admin />
        {:else}<div class="empty">Cette page n'existe pas.<button class="btn" on:click={() => navigate('/')}>Retour à la chronologie</button></div>{/if}
      </main>
    </div>
  </div>
  <nav class="tabs" aria-label="Navigation mobile">
    <button class:on={tab === 'timeline'} on:click={() => navigate('/')}><Icon name="clock" size={20} />Chronologie</button>
    <button class:on={tab === 'search'} on:click={() => navigate('/search')}><Icon name="search" size={20} />Recherche</button>
    <button class:on={tab === 'groups'} on:click={() => navigate('/groups')}><Icon name="folder" size={20} />Groupes</button>
  </nav>
  <button class="fab" on:click={() => uploadOpen.set(true)} aria-label="Importer des documents"><Icon name="upload" size={18} />Importer</button>
  <UploadManager />
  <UnlockDialog />
  <CommandPalette />
{/if}
<Toasts />

<style>
  .boot { min-height: 100vh; display: flex; align-items: center; justify-content: center; }
  .shell { display: flex; min-height: 100vh; }
  .content { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  main { flex: 1; }
  .tabs, .fab { display: none; }
  @media (max-width: 900px) {
    .tabs { display: flex; position: fixed; left: 0; right: 0; bottom: 0; z-index: 30; height: 56px; background: var(--surface); border-top: 1px solid var(--border); }
    .tabs button { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px; border: 0; background: transparent; color: var(--text-muted); font-size: 11.5px; font-weight: 500; }
    .tabs button.on { color: var(--text); }
    .fab { display: inline-flex; position: fixed; right: 16px; bottom: 72px; z-index: 30; height: 46px; padding: 0 18px; align-items: center; gap: 8px; border-radius: var(--r-control); border: 0; background: var(--accent); color: var(--on-accent); font-weight: 600; box-shadow: 0 8px 24px #00000055; }
  }
</style>
