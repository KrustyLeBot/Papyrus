<script>
  import { tick } from 'svelte';
  import { paletteOpen, uploadOpen, theme, groups, toast, bumpDocs, loadGroups, queue, groupPath } from '../lib/stores.js';
  import { navigate } from '../lib/router.js';
  import { get, post, qs } from '../lib/api.js';
  import { formatDate } from '../lib/format.js';
  import Modal from './Modal.svelte';

  let q = '', docs = [], index = 0, timer, listEl;
  const norm = (s) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

  $: actions = [
    { label: 'Importer des documents', hint: 'u', run: () => uploadOpen.set(true) },
    { label: 'Aller à la chronologie', hint: 'g t', run: () => navigate('/') },
    { label: 'Aller aux groupes', hint: 'g g', run: () => navigate('/groups') },
    ...($queue.locked ? [{ label: `Déverrouiller les PDF protégés (${$queue.locked})`, run: () => navigate('/unlock') }] : []),
    { label: 'Ouvrir l\'administration', run: () => navigate('/admin') },
    { label: 'Reclasser automatiquement tous les documents', run: async () => { try { const r = await post('/groups/reclassify', { group_id: null }); toast(`${r.documents} document(s) reclassé(s).`, 'success'); await loadGroups(); bumpDocs(); } catch (e) { toast(e.message, 'error'); } } },
    { label: 'Recharger les règles de classement', run: async () => { try { const r = await post('/admin/reload-rules'); toast(`Règles rechargées (${r.types} types).`, 'success'); } catch (e) { toast(e.message, 'error'); } } },
    { label: $theme === 'dark' ? 'Passer au thème clair' : 'Passer au thème sombre', run: () => theme.set($theme === 'dark' ? 'light' : 'dark') },
  ];
  $: needle = norm(q.trim());
  $: shownActions = needle ? actions.filter((a) => norm(a.label).includes(needle)) : actions;
  $: shownGroups = needle ? $groups.filter((g) => !g.inbox && norm(groupPath($groups, g.id)).includes(needle)).slice(0, 5) : [];
  $: entries = [
    ...shownActions.map((a) => ({ section: 'Actions', label: a.label, hint: a.hint, run: a.run })),
    ...shownGroups.map((g) => ({ section: 'Groupes', label: groupPath($groups, g.id), hint: String(g.total), run: () => navigate(`/groups/${g.id}`) })),
    ...docs.map((d) => ({ section: 'Documents', label: d.title, hint: `${d.groups[0]?.path || ''} · ${formatDate(d.date)}`, run: () => navigate(`/documents/${d.id}`) })),
    ...(needle.length > 1 ? [{ section: 'Recherche', label: `Rechercher « ${q.trim()} » dans tous les documents`, run: () => navigate(`/?q=${encodeURIComponent(q.trim())}`) }] : []),
  ];
  $: if (index >= entries.length) index = Math.max(0, entries.length - 1);

  $: search(q);
  function search(text) {
    clearTimeout(timer);
    if (text.trim().length < 2) { docs = []; return; }
    timer = setTimeout(async () => {
      try { docs = (await get('/search' + qs({ q: text.trim(), limit: 6 }))).items; } catch { docs = []; }
    }, 140);
  }
  function close() { paletteOpen.set(false); q = ''; docs = []; index = 0; }
  function choose(e) { close(); e.run(); }
  async function key(e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); index = (index + 1) % Math.max(entries.length, 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); index = (index - 1 + entries.length) % Math.max(entries.length, 1); }
    else if (e.key === 'Enter' && entries[index]) { e.preventDefault(); choose(entries[index]); }
    await tick();
    listEl?.querySelector('.on')?.scrollIntoView({ block: 'nearest' });
  }
</script>

{#if $paletteOpen}
  <Modal label="Palette de commandes" width={640} on:close={close}>
    <div class="pal">
      <input class="in" bind:value={q} on:keydown={key} placeholder="Chercher un document, un groupe, une action…" aria-label="Commande ou recherche" data-autofocus autocomplete="off" />
      <div class="list" bind:this={listEl} role="listbox">
        {#each entries as e, i}
          {#if i === 0 || entries[i - 1].section !== e.section}<div class="sec">{e.section}</div>{/if}
          <button class="it" class:on={i === index} role="option" aria-selected={i === index} on:click={() => choose(e)} on:mousemove={() => (index = i)}>
            <span class="lb">{e.label}</span>{#if e.hint}<span class="hint">{e.hint}</span>{/if}
          </button>
        {:else}<div class="none muted">Rien ne correspond.</div>{/each}
      </div>
      <div class="foot muted"><span><span class="kbd">↑↓</span> naviguer</span><span><span class="kbd">↵</span> ouvrir</span><span><span class="kbd">Échap</span> fermer</span></div>
    </div>
  </Modal>
{/if}

<style>
  .pal { display: flex; flex-direction: column; margin: -22px; }
  .in { height: 52px; padding: 0 18px; border: 0; border-bottom: 1px solid var(--border); background: transparent; color: var(--text); font-size: 15px; outline: 0; }
  .list { max-height: 52vh; overflow-y: auto; padding: 4px 0; }
  .sec { font-size: 12px; font-weight: 600; color: var(--text-muted); padding: 10px 18px 4px; }
  .it { display: flex; gap: 12px; align-items: center; width: calc(100% - 12px); margin: 0 6px; height: 38px; padding: 0 12px; border: 0; border-radius: var(--r-control); background: transparent; text-align: left; }
  .it.on { background: var(--accent-soft); }
  .lb { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .hint { color: var(--text-muted); font-size: 12.5px; white-space: nowrap; max-width: 45%; overflow: hidden; text-overflow: ellipsis; }
  .none { padding: 22px 18px; text-align: center; }
  .foot { display: flex; gap: 16px; padding: 10px 18px; border-top: 1px solid var(--border); font-size: 12px; }
</style>
