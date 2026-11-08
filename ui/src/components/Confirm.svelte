<script>
  import { createEventDispatcher } from 'svelte';
  import Modal from './Modal.svelte';
  export let title;
  export let confirm; // the button says what it does: « Supprimer le document »
  export let danger = false;
  export let busy = false;
  const dispatch = createEventDispatcher();
</script>

<Modal {title} on:close={() => dispatch('close')}>
  <div class="muted"><slot /></div>
  <div class="actions">
    <button class="btn" on:click={() => dispatch('close')}>Annuler</button>
    <button class="btn primary" class:danger-fill={danger} disabled={busy} data-autofocus on:click={() => dispatch('confirm')}>{confirm}</button>
  </div>
</Modal>

<style>
  .actions { display: flex; gap: 8px; justify-content: flex-end; }
  .danger-fill { background: var(--danger); border-color: var(--danger); }
  .danger-fill:hover:not(:disabled) { background: var(--danger); filter: brightness(1.1); }
</style>
