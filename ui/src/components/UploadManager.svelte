<script>
  import { onMount, onDestroy } from 'svelte';
  import { uploadOpen, unlockTarget, toast, loadGroups, bumpDocs, refreshQueue } from '../lib/stores.js';
  import { api } from '../lib/api.js';
  import Modal from './Modal.svelte';
  import Icon from './Icon.svelte';

  let dragging = false, depth = 0, busy = false, results = [], input, camera;
  const touch = typeof matchMedia !== 'undefined' && matchMedia('(pointer: coarse)').matches;
  const LABEL = { queued: 'Ajouté, analyse en cours', duplicate: 'Déjà présent, ignoré', password_required: 'Protégé par un mot de passe', rejected: 'Refusé' };

  async function send(files) {
    files = [...files];
    if (!files.length) return;
    busy = true; results = [];
    uploadOpen.set(true);
    const form = new FormData();
    for (const f of files) form.append('files', f, f.name);
    try {
      const r = await api('POST', '/upload', form);
      results = r.results;
      const locked = r.results.filter((x) => x.status === 'password_required');
      const queued = r.results.filter((x) => x.status === 'queued').length;
      if (queued) toast(`${queued} document${queued > 1 ? 's' : ''} ajouté${queued > 1 ? 's' : ''}.`, 'success');
      await refreshQueue(); await loadGroups(); bumpDocs();
      if (locked.length) { uploadOpen.set(false); unlockTarget.set({ id: locked[0].id, original_name: locked[0].name }); }
    } catch (e) { results = files.map((f) => ({ name: f.name, status: 'rejected', message: e.message })); }
    busy = false;
  }
  function pick(e) { send(e.target.files); e.target.value = ''; }

  // dropping files anywhere in the app
  const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes('Files');
  function enter(e) { if (hasFiles(e)) { depth++; dragging = true; } }
  function leave(e) { if (hasFiles(e) && --depth <= 0) { depth = 0; dragging = false; } }
  function over(e) { if (hasFiles(e)) e.preventDefault(); }
  function drop(e) { if (!hasFiles(e)) return; e.preventDefault(); depth = 0; dragging = false; send(e.dataTransfer.files); }
  function close() { uploadOpen.set(false); results = []; }
  $: if ($uploadOpen === false) results = [];
</script>

<svelte:window on:dragenter={enter} on:dragleave={leave} on:dragover={over} on:drop={drop} />
<input bind:this={input} type="file" multiple hidden accept=".pdf,.jpg,.jpeg,.png,.webp,.tif,.tiff,.heic,.heif,.docx,.txt,.md,application/pdf,image/*" on:change={pick} />
<input bind:this={camera} type="file" accept="image/*" capture="environment" hidden on:change={pick} />

{#if dragging}
  <div class="overlay"><div class="ov"><Icon name="upload" size={28} /><span>Dépose les fichiers pour les importer</span></div></div>
{/if}

{#if $uploadOpen}
  <Modal title="Importer" width={520} on:close={close}>
    {#if !results.length && !busy}
      <div class="drop">
        <Icon name="upload" size={22} />
        <span class="strong">Glisse des fichiers n'importe où dans Papyrus</span>
        <span class="muted small">PDF, images, DOCX, TXT · jusqu'à 200 Mo par fichier</span>
        <div class="btns">
          <button class="btn primary" data-autofocus on:click={() => input.click()}>Choisir des fichiers</button>
          {#if touch}<button class="btn" on:click={() => camera.click()}><Icon name="camera" />Photographier</button>{/if}
        </div>
      </div>
    {:else if busy}
      <p class="muted">Envoi en cours…</p>
    {:else}
      <ul class="res">
        {#each results as r}
          <li><span class="nm">{r.name}</span><span class="st {r.status}">{r.message && r.status === 'rejected' ? r.message : LABEL[r.status]}</span></li>
        {/each}
      </ul>
      <div class="foot"><button class="btn" on:click={() => (results = [])}>Importer d'autres fichiers</button><button class="btn primary" data-autofocus on:click={close}>Terminer</button></div>
    {/if}
  </Modal>
{/if}

<style>
  .overlay { position: fixed; inset: 0; z-index: 80; background: var(--scrim); display: flex; align-items: center; justify-content: center; pointer-events: none; }
  .ov { border: 2px dashed var(--accent); border-radius: var(--r-panel); background: var(--surface); padding: 40px 56px; display: flex; flex-direction: column; align-items: center; gap: 12px; font-weight: 600; }
  .drop { border: 1px dashed var(--border); border-radius: var(--r-panel); padding: 32px 20px; display: flex; flex-direction: column; align-items: center; gap: 8px; text-align: center; color: var(--text-muted); }
  .strong { color: var(--text); font-weight: 600; }
  .small { font-size: 13px; }
  .btns { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; justify-content: center; }
  .res { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
  .res li { display: flex; gap: 12px; justify-content: space-between; padding: 9px 0; border-bottom: 1px solid var(--border); font-size: 13.5px; }
  .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
  .st { color: var(--text-muted); white-space: nowrap; }
  .st.queued { color: var(--success); }
  .st.password_required { color: var(--warning); }
  .st.rejected { color: var(--danger); white-space: normal; text-align: right; }
  .foot { display: flex; gap: 8px; justify-content: flex-end; }
</style>
