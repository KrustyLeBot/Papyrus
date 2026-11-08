<script>
  import { post, ApiError } from '../lib/api.js';
  import { me } from '../lib/stores.js';
  let user = '', password = '', error = '', busy = false;
  async function submit(e) {
    e.preventDefault();
    busy = true; error = '';
    try {
      const r = await post('/auth/login', { user, password }, { noAuthRedirect: true });
      password = '';
      const info = await fetch('/api/auth/me', { credentials: 'same-origin' }).then((x) => x.json());
      me.set({ ...info, user: r.user });
    } catch (err) { error = err instanceof ApiError ? err.message : 'Connexion impossible.'; }
    busy = false;
  }
</script>

<main class="wrap">
  <form class="card" on:submit={submit}>
    <div class="head">
      <img src="/favicon.svg" width="44" height="44" alt="" />
      <div><h1>Papyrus</h1><p class="muted">Archive de documents</p></div>
    </div>
    <label class="field"><span class="label">Identifiant</span>
      <input class="input" bind:value={user} autocomplete="username" autocapitalize="off" spellcheck="false" required data-autofocus />
    </label>
    <label class="field"><span class="label">Mot de passe</span>
      <input class="input" class:invalid={error} type="password" bind:value={password} autocomplete="current-password" required />
    </label>
    {#if error}<p class="err" role="alert">{error}</p>{/if}
    <button class="btn primary big" disabled={busy || !user || !password}>Se connecter</button>
  </form>
</main>

<style>
  .wrap { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 16px; }
  .card { width: 100%; max-width: 360px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-panel); padding: 30px; display: flex; flex-direction: column; gap: 16px; }
  .head { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }
  h1 { font-size: 20px; font-weight: 700; }
  .head p { font-size: 13px; }
  .big { height: 40px; }
  .err { color: var(--danger); font-size: 13.5px; }
</style>
