<script>
  import { toasts } from '../lib/stores.js';
</script>

<div class="toasts" role="status" aria-live="polite">
  {#each $toasts as t (t.id)}
    <div class="toast {t.kind}">{t.message}</div>
  {/each}
</div>

<style>
  .toasts { position: fixed; right: 16px; bottom: 16px; display: flex; flex-direction: column; gap: 8px; z-index: 90; max-width: min(420px, calc(100vw - 32px)); }
  .toast { background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-panel); padding: 10px 14px; box-shadow: var(--shadow); animation: in 150ms ease-out; }
  .toast.error { border-color: var(--danger); }
  .toast.success { border-color: var(--success); }
  @keyframes in { from { opacity: 0; transform: translateY(6px); } }
</style>
