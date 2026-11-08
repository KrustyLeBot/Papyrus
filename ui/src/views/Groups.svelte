<script>
  import { route, navigate } from '../lib/router.js';
  import { groups, tree, loadGroups, toast, bumpDocs, groupPath, rootColor, groupById } from '../lib/stores.js';
  import { get, post, patch, del } from '../lib/api.js';
  import { plural, formatNumber } from '../lib/format.js';
  import DocList from '../components/DocList.svelte';
  import Icon from '../components/Icon.svelte';
  import Modal from '../components/Modal.svelte';
  import Confirm from '../components/Confirm.svelte';

  let detail = null, children = true, dialog = null, busy = false, value = '', target = '', error = '', total = null;
  $: id = $route.params.id ?? null;
  $: current = id ? groupById($groups, id) : null;
  $: if (id) loadDetail(id, $groups.length); else detail = null;
  async function loadDetail(gid) {
    try { detail = await get(`/groups/${gid}`); } catch (e) { detail = null; if (e.status === 404) navigate('/groups', { replace: true }); }
  }
  $: crumbs = current ? groupPath($groups, current.id).split(' / ') : [];
  $: color = current ? rootColor($groups, current.id) : '#7C8AA5';
  $: subgroups = id ? $groups.filter((g) => g.parent_id === id) : $groups.filter((g) => g.parent_id == null);
  $: candidates = $groups.filter((g) => !g.inbox && g.id !== id);
  const KEYS = { siret: 'SIRET', siren: 'SIREN', che_uid: 'IDE', tva: 'TVA', cadastre_ref: 'Cadastre', property_address: 'Adresse', contract_no: 'Contrat', employer_name: 'Employeur', company: 'Société', year: 'Année' };

  function open(kind) { dialog = kind; error = ''; value = kind === 'rename' ? current.name : ''; target = ''; }
  async function run(fn, done) {
    busy = true; error = '';
    try { await fn(); await loadGroups(); bumpDocs(); dialog = null; if (done) toast(done, 'success'); }
    catch (e) { error = e.message; }
    busy = false;
  }
  const create = () => run(async () => {
    const g = await post('/groups', { name: value.trim(), parent_id: id });
    await loadGroups(); navigate(`/groups/${g.id}`);
  });
  const rename = () => run(() => patch(`/groups/${id}`, { name: value.trim() }), 'Groupe renommé.');
  const move = () => run(() => patch(`/groups/${id}`, { move: true, parent_id: target ? +target : null }), 'Groupe déplacé.');
  const merge = () => run(async () => { await post(`/groups/${id}/merge`, { into: +target }); navigate(`/groups/${target}`); }, 'Groupes fusionnés.');
  const remove = () => run(async () => { const up = current.parent_id; await del(`/groups/${id}`); navigate(up ? `/groups/${up}` : '/groups'); }, 'Groupe supprimé.');
  const reclassify = (global) => run(async () => {
    const r = await post('/groups/reclassify', { group_id: global ? null : id });
    toast(`${plural(r.documents, 'document reclassé', 'documents reclassés')}.`, 'success');
  });
  async function dropKey(k) {
    try { await del(`/groups/${id}/keys?key=${encodeURIComponent(k.key)}&value=${encodeURIComponent(k.value)}`); await loadDetail(id); }
    catch (e) { toast(e.message, 'error'); }
  }
  $: noMoveTargets = new Set();
</script>

<div class="page">
  {#if !id}
    <header><h1>Groupes</h1><span class="muted">{plural($groups.length, 'groupe', 'groupes')}</span>
      <button class="btn" style="margin-left:auto" on:click={() => open('create')}><Icon name="plus" />Nouveau groupe</button>
      <button class="btn" on:click={() => reclassify(true)} disabled={busy}><Icon name="wand" />Reclasser automatiquement</button></header>
    <p class="muted intro">Choisis un groupe dans l'arbre à gauche, ou ouvre-en un ci-dessous. Les groupes se créent tout seuls à l'import (type, puis employeur, adresse du bien, année…) et se corrigent à la main.</p>
    <div class="grid">
      {#each subgroups as g (g.id)}
        <a class="gl" href="/groups/{g.id}" on:click|preventDefault={() => navigate(`/groups/${g.id}`)}>
          <span class="dot" style="background:{g.color || '#7C8AA5'}"></span>
          <span class="gn">{g.name}</span>
          <span class="muted">{formatNumber(g.total)}</span>
        </a>
      {:else}
        <div class="empty">Aucun groupe pour l'instant. Importe des documents : Papyrus les range tout seul.</div>
      {/each}
    </div>
  {:else if current && detail}
    <div class="crumb muted">
      <a href="/groups" on:click|preventDefault={() => navigate('/groups')}>Groupes</a>
      {#each crumbs.slice(0, -1) as c}<span>/</span><span>{c}</span>{/each}
    </div>
    <header>
      <h1><span class="dot big" style="background:{color}"></span>{current.name}</h1>
      <span class="muted">{formatNumber(current.total)} document{current.total > 1 ? 's' : ''}{subgroups.length ? ', sous-groupes inclus' : ''}</span>
    </header>
    <div class="actions">
      {#if !current.inbox}
        <button class="btn small" on:click={() => open('rename')}><Icon name="edit" />Renommer</button>
        <button class="btn small" on:click={() => open('move')}><Icon name="move" />Déplacer</button>
        <button class="btn small" on:click={() => open('merge')}><Icon name="merge" />Fusionner avec…</button>
        <button class="btn small" on:click={() => open('create')}><Icon name="plus" />Sous-groupe</button>
      {/if}
      <button class="btn small" on:click={() => reclassify(false)} disabled={busy}><Icon name="wand" />Reclasser automatiquement</button>
      {#if !current.inbox}<button class="btn small danger" on:click={() => open('delete')}><Icon name="trash" />Supprimer</button>{/if}
    </div>

    {#if detail.keys.length}
      <section class="keys">
        <div class="label">Clés apprises <span class="muted">· servent à ranger les prochains imports ici</span></div>
        <div class="kl">
          {#each detail.keys as k}
            <span class="key" class:amb={k.ambiguous} title={k.ambiguous ? 'Présente dans plusieurs sous-groupes : ignorée pour le classement' : ''}>
              <span class="muted">{KEYS[k.key] || k.key}</span>
              <span class={['siret', 'siren', 'che_uid', 'tva', 'contract_no', 'cadastre_ref'].includes(k.key) ? 'mono' : ''}>{['siret', 'siren', 'che_uid', 'tva', 'contract_no', 'cadastre_ref'].includes(k.key) ? k.value : k.label}</span>
              <button aria-label="Oublier cette clé" on:click={() => dropKey(k)}><Icon name="x" size={12} /></button>
            </span>
          {/each}
        </div>
      </section>
    {/if}
    {#if subgroups.length}
      <div class="subs">
        {#each subgroups as g (g.id)}
          <a class="chip big-chip" href="/groups/{g.id}" on:click|preventDefault={() => navigate(`/groups/${g.id}`)}><Icon name="folder" size={14} /><span>{g.name}</span><span class="muted">{g.total}</span></a>
        {/each}
      </div>
      <label class="inc"><input type="checkbox" bind:checked={children} />Inclure les sous-groupes</label>
    {/if}
    <DocList query={{ group: id, children: children ? '1' : '0' }} from={id} emptyText="Aucun document dans ce groupe. Glisse-en depuis une autre liste, ou ajoute-le depuis sa fiche." />
  {:else}
    <div class="empty"><span class="muted">Chargement…</span></div>
  {/if}
</div>

{#if dialog === 'create'}
  <Modal title={id ? `Nouveau sous-groupe dans « ${current.name} »` : 'Nouveau groupe'} on:close={() => (dialog = null)}>
    <form on:submit|preventDefault={create}>
      <label class="field"><span class="label">Nom</span><input class="input" bind:value maxlength="120" data-autofocus required /></label>
      {#if error}<p class="err">{error}</p>{/if}
      <div class="foot"><button type="button" class="btn" on:click={() => (dialog = null)}>Annuler</button><button class="btn primary" disabled={busy || !value.trim()}>Créer le groupe</button></div>
    </form>
  </Modal>
{:else if dialog === 'rename'}
  <Modal title="Renommer le groupe" on:close={() => (dialog = null)}>
    <form on:submit|preventDefault={rename}>
      <label class="field"><span class="label">Nom</span><input class="input" bind:value maxlength="120" data-autofocus required /></label>
      {#if error}<p class="err">{error}</p>{/if}
      <div class="foot"><button type="button" class="btn" on:click={() => (dialog = null)}>Annuler</button><button class="btn primary" disabled={busy || !value.trim()}>Renommer</button></div>
    </form>
  </Modal>
{:else if dialog === 'move'}
  <Modal title="Déplacer le groupe" on:close={() => (dialog = null)}>
    <form on:submit|preventDefault={move}>
      <label class="field"><span class="label">Nouveau parent</span>
        <select class="input" bind:value={target}><option value="">Racine (aucun parent)</option>{#each candidates as g (g.id)}<option value={g.id}>{groupPath($groups, g.id)}</option>{/each}</select></label>
      {#if error}<p class="err">{error}</p>{/if}
      <div class="foot"><button type="button" class="btn" on:click={() => (dialog = null)}>Annuler</button><button class="btn primary" disabled={busy}>Déplacer le groupe</button></div>
    </form>
  </Modal>
{:else if dialog === 'merge'}
  <Modal title="Fusionner les groupes" on:close={() => (dialog = null)}>
    <form on:submit|preventDefault={merge}>
      <p class="muted">« {current.name} » disparaît : ses documents, ses clés et ses sous-groupes passent dans le groupe choisi.</p>
      <label class="field"><span class="label">Fusionner dans</span>
        <select class="input" bind:value={target} data-autofocus required><option value="" disabled>Choisir un groupe…</option>{#each candidates as g (g.id)}<option value={g.id}>{groupPath($groups, g.id)}</option>{/each}</select></label>
      {#if error}<p class="err">{error}</p>{/if}
      <div class="foot"><button type="button" class="btn" on:click={() => (dialog = null)}>Annuler</button><button class="btn primary" disabled={busy || !target}>Fusionner les groupes</button></div>
    </form>
  </Modal>
{:else if dialog === 'delete'}
  <Confirm title="Supprimer le groupe" confirm="Supprimer le groupe" danger {busy} on:close={() => (dialog = null)} on:confirm={remove}>
    Les documents de « {current.name} » remontent au groupe parent (ou dans « À classer »). Aucun document n'est supprimé.
    {#if error}<p class="err">{error}</p>{/if}
  </Confirm>
{/if}

<style>
  .crumb { font-size: 13px; display: flex; gap: 6px; margin-bottom: 4px; }
  .crumb a:hover { color: var(--text); }
  h1 { display: flex; align-items: center; gap: 10px; }
  .dot.big { width: 10px; height: 10px; }
  .actions { display: flex; gap: 8px; flex-wrap: wrap; margin: 14px 0; }
  .intro { margin: 8px 0 18px; max-width: 70ch; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 0 24px; }
  .gl { display: flex; align-items: center; gap: 10px; padding: 11px 8px; border-bottom: 1px solid var(--border); border-radius: var(--r-control); }
  .gl:hover { background: var(--surface); }
  .gn { flex: 1; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .keys { margin: 8px 0 14px; padding: 14px 16px; border: 1px solid var(--border); border-radius: var(--r-panel); background: var(--surface); display: flex; flex-direction: column; gap: 8px; }
  .kl { display: flex; gap: 6px; flex-wrap: wrap; }
  .key { display: inline-flex; align-items: center; gap: 8px; min-height: 26px; padding: 0 4px 0 9px; border-radius: var(--r-control); background: var(--surface-2); font-size: 12.5px; }
  .key.amb { text-decoration: line-through; color: var(--text-muted); }
  .key button { border: 0; background: transparent; color: var(--text-muted); width: 22px; height: 22px; display: inline-flex; align-items: center; justify-content: center; border-radius: 4px; padding: 0; }
  .key button:hover { background: var(--border); color: var(--text); }
  .subs { display: flex; gap: 8px; flex-wrap: wrap; margin: 4px 0 8px; }
  .big-chip { height: 30px; padding: 0 12px; font-size: 13px; }
  .big-chip:hover { background: var(--surface-2); }
  .inc { display: flex; align-items: center; gap: 8px; font-size: 13.5px; color: var(--text-muted); margin-bottom: 4px; }
  .inc input { accent-color: var(--accent); width: 16px; height: 16px; }
  form { display: flex; flex-direction: column; gap: 14px; }
  .foot { display: flex; gap: 8px; justify-content: flex-end; }
  .err { color: var(--danger); font-size: 13.5px; }
</style>
