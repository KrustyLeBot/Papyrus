<script>
  import { route, link, navigate } from '../lib/router.js';
  import { tree, folders, queue, me, theme, drawerOpen, paletteOpen } from '../lib/stores.js';
  import { formatNumber } from '../lib/format.js';
  import Icon from './Icon.svelte';
  import TreeNode from './TreeNode.svelte';

  let open = new Set();
  try { open = new Set(JSON.parse(localStorage.getItem('papyrus.tree') || '[]')); } catch { /* ignore */ }
  const save = () => { try { localStorage.setItem('papyrus.tree', JSON.stringify([...open])); } catch { /* ignore */ } };
  function toggle(e) {
    const p = e.detail;
    open.has(p) ? open.delete(p) : open.add(p);
    open = open; save();
  }
  $: selected = $route.name === 'timeline' ? ($route.query.folder ?? null) : null;
  // the selected folder's ancestors are opened so it is always visible
  $: if (selected) {
    const parts = selected.split('/');
    let changed = false;
    for (let i = 1; i < parts.length; i++) { const p = parts.slice(0, i).join('/'); if (!open.has(p)) { open.add(p); changed = true; } }
    if (changed) { open = open; save(); }
  }
  async function logout() {
    try { await fetch('/api/auth/logout', { method: 'POST', headers: { 'X-CSRF-Token': (document.cookie.match(/papyrus_csrf=([^;]+)/) || [])[1] || '' } }); } catch { /* ignore */ }
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
    <a class="nav" class:on={nav('timeline') && !selected} href="/" use:link on:click={go}><Icon name="clock" />Chronologie<span class="k">g t</span></a>
    {#if $queue.locked > 0}
      <a class="nav" class:on={nav('unlock')} href="/unlock" use:link on:click={go}><Icon name="lock" />À déverrouiller<span class="badge">{$queue.locked}</span></a>
    {/if}
  </nav>
  <div class="sec">Dossiers</div>
  <div class="tree">
    {#each $tree.nodes as node (node.path)}
      <TreeNode {node} {selected} {open} on:toggle={toggle} on:click={go} />
    {:else}
      <p class="muted none">Les dossiers qui contiennent des PDF ou des DOCX apparaissent ici.</p>
    {/each}
    {#if $tree.rootCount > 0}
      <a class="rootdocs" class:on={selected === ''} href="/?folder=" use:link on:click={go}><Icon name="file" size={15} /><span>À la racine</span><span class="n">{formatNumber($tree.rootCount)}</span></a>
    {/if}
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
  .side { width: 268px; flex: 0 0 268px; background: var(--surface); border-right: 1px solid var(--border); display: flex; flex-direction: column; padding: 12px 10px; gap: 2px; height: 100vh; position: sticky; top: 0; overflow: hidden; }
  .brand { display: flex; align-items: center; gap: 10px; padding: 4px 8px 14px; font-weight: 700; font-size: 16px; }
  .nav { display: flex; align-items: center; gap: 10px; height: 34px; padding: 0 10px; border-radius: var(--r-control); color: var(--text-muted); font-weight: 500; transition: background var(--t), color var(--t); }
  .nav:hover { background: var(--surface-2); color: var(--text); }
  .nav.on { background: var(--accent-soft); color: var(--text); }
  .k { margin-left: auto; font-family: var(--mono); font-size: 11px; color: var(--text-muted); }
  .badge { margin-left: auto; background: var(--warning-soft); color: var(--warning); font-size: 12px; font-weight: 600; padding: 0 7px; border-radius: var(--r-control); line-height: 20px; }
  .sec { font-size: 12px; font-weight: 600; color: var(--text-muted); padding: 18px 10px 6px; }
  .tree { flex: 1; overflow-y: auto; min-height: 0; margin: 0 -4px; padding: 0 4px; display: flex; flex-direction: column; gap: 1px; }
  .none { padding: 6px 10px; font-size: 13px; }
  .rootdocs { display: flex; align-items: center; gap: 8px; height: 30px; padding: 0 6px 0 28px; border-radius: var(--r-control); font-size: 13.5px; color: var(--text-muted); }
  .rootdocs:hover { background: var(--surface-2); }
  .rootdocs.on { background: var(--accent-soft); color: var(--text); }
  .rootdocs span:nth-child(2) { flex: 1; }
  .rootdocs .n { font-size: 12px; font-variant-numeric: tabular-nums; }
  .foot { border-top: 1px solid var(--border); padding-top: 10px; display: flex; flex-direction: column; gap: 2px; }
  .who { display: flex; align-items: center; gap: 6px; padding: 2px 4px 2px 8px; }
  .avatar { width: 26px; height: 26px; border-radius: 50%; background: var(--surface-2); display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: 12px; flex: 0 0 26px; }
  .uname { flex: 1; font-size: 13px; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; }
  .who .icon-btn { width: 30px; height: 30px; }
  .backdrop { display: none; }
  @media (max-width: 900px) {
    .side { position: fixed; z-index: 50; left: 0; top: 0; bottom: 0; transform: translateX(-100%); transition: transform 160ms ease-out; width: 296px; }
    .side.open { transform: none; box-shadow: var(--shadow); }
    .backdrop { display: block; position: fixed; inset: 0; background: var(--scrim); z-index: 40; }
  }
</style>
