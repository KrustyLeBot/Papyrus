<script>
  import { onMount } from 'svelte';
  import { route, link, navigate } from '../lib/router.js';
  import { tree, groups, queue, me, theme, drawerOpen, loadGroups, toast, bumpDocs, paletteOpen } from '../lib/stores.js';
  import { post } from '../lib/api.js';
  import Icon from './Icon.svelte';
  import TreeNode from './TreeNode.svelte';

  let open = new Set();
  try { open = new Set(JSON.parse(localStorage.getItem('papyrus.tree') || '[]')); } catch { /* ignore */ }
  const save = () => { try { localStorage.setItem('papyrus.tree', JSON.stringify([...open])); } catch { /* ignore */ } };

  function toggle(e) {
    const id = e.detail;
    open.has(id) ? open.delete(id) : open.add(id);
    open = open; save();
  }
  // the selected group's ancestors are opened so it is always visible
  $: if ($route.name === 'groups' && $route.params.id) {
    let g = $groups.find((x) => x.id === $route.params.id);
    let changed = false;
    while (g && g.parent_id != null) { if (!open.has(g.parent_id)) { open.add(g.parent_id); changed = true; } g = $groups.find((x) => x.id === g.parent_id); }
    if (changed) { open = open; save(); }
  }
  $: selected = $route.name === 'groups' ? $route.params.id ?? null : null;

  async function dropDoc(e) {
    const { group, ids, from } = e.detail;
    try {
      await post(`/groups/${group.id}/documents`, { doc_ids: ids, from_group: from ?? null });
      toast(`Classé dans « ${group.name} ».`, 'success');
      await loadGroups(); bumpDocs();
    } catch (err) { toast(err.message, 'error'); }
  }
  async function logout() {
    try { await post('/auth/logout'); } catch { /* ignore */ }
    me.set(null);
  }
  const nav = (n) => $route.name === n;
  function go() { drawerOpen.set(false); }
</script>

<aside class="side" class:open={$drawerOpen}>
  <a class="brand" href="/" use:link on:click={go}>
    <img src="/favicon.svg" width="28" height="28" alt="" />
    <span>Papyrus</span>
  </a>
  <nav aria-label="Navigation principale">
    <a class="nav" class:on={nav('timeline')} href="/" use:link on:click={go}><Icon name="clock" />Chronologie<span class="k">g t</span></a>
    <a class="nav" class:on={nav('groups') && !$route.params.id} href="/groups" use:link on:click={go}><Icon name="folder" />Groupes<span class="k">g g</span></a>
    {#if $queue.locked > 0}
      <a class="nav" class:on={nav('unlock')} href="/unlock" use:link on:click={go}><Icon name="lock" />À déverrouiller<span class="badge">{$queue.locked}</span></a>
    {/if}
  </nav>
  <div class="sec">Groupes</div>
  <div class="tree">
    {#each $tree as node (node.id)}
      <TreeNode {node} {selected} {open} on:toggle={toggle} on:drop-doc={dropDoc} />
    {:else}
      <p class="muted none">Les groupes se créent à l'import des documents.</p>
    {/each}
  </div>
  <div class="foot">
    <a class="nav" class:on={nav('admin')} href="/admin" use:link on:click={go}><Icon name="sliders" />Administration</a>
    <div class="who">
      <span class="avatar">{($me?.user || '?')[0].toUpperCase()}</span>
      <span class="uname">{$me?.user}</span>
      <button class="icon-btn" title="Palette de commandes (Ctrl+K)" aria-label="Ouvrir la palette de commandes" on:click={() => paletteOpen.set(true)}><span class="kbd">⌘K</span></button>
      <button class="icon-btn" aria-label={$theme === 'dark' ? 'Passer au thème clair' : 'Passer au thème sombre'} on:click={() => theme.set($theme === 'dark' ? 'light' : 'dark')}><Icon name={$theme === 'dark' ? 'sun' : 'moon'} /></button>
      {#if !$me?.auth_disabled}<button class="icon-btn" aria-label="Se déconnecter" title="Se déconnecter" on:click={logout}><Icon name="logout" /></button>{/if}
    </div>
  </div>
</aside>
{#if $drawerOpen}<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions --><div class="backdrop" on:click={() => drawerOpen.set(false)}></div>{/if}

<style>
  .side { width: 256px; flex: 0 0 256px; background: var(--surface); border-right: 1px solid var(--border); display: flex; flex-direction: column; padding: 12px 10px; gap: 2px; height: 100vh; position: sticky; top: 0; overflow: hidden; }
  .brand { display: flex; align-items: center; gap: 10px; padding: 4px 8px 14px; font-weight: 700; font-size: 16px; }
  .nav { display: flex; align-items: center; gap: 10px; height: 34px; padding: 0 10px; border-radius: var(--r-control); color: var(--text-muted); font-weight: 500; transition: background var(--t), color var(--t); }
  .nav:hover { background: var(--surface-2); color: var(--text); }
  .nav.on { background: var(--accent-soft); color: var(--text); }
  .k { margin-left: auto; font-family: var(--mono); font-size: 11px; color: var(--text-muted); }
  .badge { margin-left: auto; background: var(--warning-soft); color: var(--warning); font-size: 12px; font-weight: 600; padding: 0 7px; border-radius: var(--r-control); line-height: 20px; }
  .sec { font-size: 12px; font-weight: 600; color: var(--text-muted); padding: 18px 10px 6px; }
  .tree { flex: 1; overflow-y: auto; min-height: 0; margin: 0 -4px; padding: 0 4px; display: flex; flex-direction: column; gap: 1px; }
  .none { padding: 6px 10px; font-size: 13px; }
  .foot { border-top: 1px solid var(--border); padding-top: 10px; display: flex; flex-direction: column; gap: 2px; }
  .who { display: flex; align-items: center; gap: 6px; padding: 2px 4px 2px 8px; }
  .avatar { width: 26px; height: 26px; border-radius: 50%; background: var(--surface-2); display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: 12px; flex: 0 0 26px; }
  .uname { flex: 1; font-size: 13px; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; }
  .who .icon-btn { width: 30px; height: 30px; }
  .backdrop { display: none; }
  @media (max-width: 900px) {
    .side { position: fixed; z-index: 50; left: 0; top: 0; bottom: 0; transform: translateX(-100%); transition: transform 160ms ease-out; width: 288px; }
    .side.open { transform: none; box-shadow: var(--shadow); }
    .backdrop { display: block; position: fixed; inset: 0; background: var(--scrim); z-index: 40; }
  }
</style>
