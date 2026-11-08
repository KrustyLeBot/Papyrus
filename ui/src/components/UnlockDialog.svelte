<script>
  import { post } from '../lib/api.js';
  import { unlockTarget, queue, toast, loadGroups, bumpDocs, refreshQueue } from '../lib/stores.js';
  import Modal from './Modal.svelte';
  import Icon from './Icon.svelte';

  let password = '', tryOthers = true, error = '', busy = false;
  $: doc = $unlockTarget;
  $: others = Math.max(0, $queue.locked - 1);

  function close() { password = ''; error = ''; unlockTarget.set(null); }
  async function submit() {
    busy = true; error = '';
    try {
      const r = await post(`/documents/${doc.id}/unlock`, { password, try_others: tryOthers });
      const n = 1 + r.others.length;
      toast(n > 1 ? `${n} PDF déverrouillés, analyse en cours.` : r.status === 'duplicate' ? 'Ce document était déjà présent.' : 'PDF déverrouillé, analyse en cours.', 'success');
      password = ''; unlockTarget.set(null);
      await refreshQueue(); await loadGroups(); bumpDocs();
    } catch (e) { error = e.message; }
    busy = false;
  }
</script>

{#if doc}
  <Modal label="Ce PDF est protégé" on:close={close}>
    <form on:submit|preventDefault={submit}>
      <div class="head">
        <span class="ic"><Icon name="lock" size={18} /></span>
        <div><h2>Ce PDF est protégé</h2><div class="mono muted name">{doc.original_name || doc.name}</div></div>
      </div>
      <p class="muted">Saisis le mot de passe d'ouverture. Papyrus gardera une copie déverrouillée ; le mot de passe n'est jamais enregistré.</p>
      <label class="field"><span class="label">Mot de passe</span>
        <input class="input" class:invalid={error} type="password" bind:value={password} autocomplete="off" data-autofocus required />
        {#if error}<span class="err" role="alert">{error} Tu peux réessayer.</span>{/if}</label>
      {#if others > 0}
        <label class="chk"><input type="checkbox" bind:checked={tryOthers} />{others > 1 ? `Essayer ce mot de passe sur les ${others} autres PDF en attente` : "Essayer ce mot de passe sur l'autre PDF en attente"}</label>
      {/if}
      <div class="foot"><button type="button" class="btn" on:click={close}>Plus tard</button><button class="btn primary" disabled={busy || !password}>Déverrouiller</button></div>
    </form>
  </Modal>
{/if}

<style>
  form { display: flex; flex-direction: column; gap: 14px; }
  .head { display: flex; gap: 12px; align-items: flex-start; }
  .ic { width: 36px; height: 36px; border-radius: var(--r-control); background: var(--warning-soft); color: var(--warning); display: flex; align-items: center; justify-content: center; flex: 0 0 36px; }
  h2 { font-size: 17px; font-weight: 700; }
  .name { word-break: break-all; }
  .err { color: var(--danger); font-size: 13px; }
  .chk { display: flex; gap: 10px; align-items: flex-start; font-size: 13.5px; }
  .chk input { accent-color: var(--accent); width: 16px; height: 16px; margin-top: 2px; }
  .foot { display: flex; gap: 8px; justify-content: flex-end; }
</style>
