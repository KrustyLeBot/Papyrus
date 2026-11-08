<script>
  import { onDestroy } from 'svelte';
  import { get, post } from '../lib/api.js';
  import { navigate } from '../lib/router.js';
  import { queue, toast, refreshQueue, bumpDocs, loadGroups } from '../lib/stores.js';
  import { formatSize, formatNumber, plural } from '../lib/format.js';
  import Icon from '../components/Icon.svelte';

  let s = null, error = '', busy = '';
  async function load() {
    try { s = await get('/admin/status'); error = ''; await refreshQueue(); } catch (e) { error = e.message; }
  }
  load();
  const timer = setInterval(load, 3000);
  onDestroy(() => clearInterval(timer));
  async function action(name, path, ok) {
    busy = name;
    try { const r = await post(path); toast(typeof ok === 'function' ? ok(r) : ok, 'success'); await load(); bumpDocs(); loadGroups(); }
    catch (e) { toast(e.message, 'error'); }
    busy = '';
  }
  const MODEL = { loaded: 'Chargé en mémoire', unloaded: 'Présent, déchargé (rechargé au prochain besoin)', absent: 'Absent', downloading: 'Téléchargement en cours' };
  const STATUS = { pending: 'En attente', processing: 'En cours', done: 'Traités', error: 'En échec', password_required: 'Protégés' };
  $: errors = $queue.items.filter((i) => i.status === 'error');
  $: waiting = $queue.items.filter((i) => i.status !== 'error');
</script>

<div class="page">
  <header><h1>Administration</h1></header>
  {#if error}<div class="empty">{error}</div>{:else if !s}<div class="skeleton" style="height:320px;margin-top:20px"></div>{:else}
    {#if s.model.status === 'absent' || s.model.status === 'downloading'}
      <div class="notice"><Icon name="alert" /><span>{s.model.status === 'downloading' ? 'Le modèle d\'embeddings se télécharge.' : 'Le modèle d\'embeddings est absent.'} Papyrus fonctionne en mode dégradé : recherche plein texte, classement par règles et identifiants.{#if s.model.error} <span class="muted">({s.model.error})</span>{/if}</span></div>
    {/if}
    <section>
      <h2>Traitement</h2>
      <dl>
        <dt>Worker</dt><dd>{s.worker.alive ? 'Actif' : 'Arrêté'}{#if s.worker.current} · {s.worker.current.title || 'tâche'} : {s.worker.current.step}{:else} · au repos{/if}</dd>
        <dt>File</dt><dd>{plural(waiting.length, 'document en attente', 'documents en attente')}{#if s.worker.jobs.length} · tâches : {s.worker.jobs.join(', ')}{/if}</dd>
      </dl>
      {#if errors.length}
        <div class="errs">
          <div class="label">En échec ({errors.length})</div>
          {#each errors as e}<div class="er"><a href="/documents/{e.id}" on:click|preventDefault={() => navigate(`/documents/${e.id}`)}>{e.title}</a><span class="muted">{e.error}</span></div>{/each}
          <button class="btn small" disabled={busy === 'retry'} on:click={() => action('retry', '/admin/retry-errors', (r) => `${plural(r.documents, 'document relancé', 'documents relancés')}.`)}>Relancer les échecs</button>
        </div>
      {/if}
    </section>
    <section>
      <h2>Réanalyse</h2>
      <p class="muted">Après une mise à jour de Papyrus : relit chaque document (OCR, orientation, date, titre, mots-clés, aperçu, groupes). Les groupes, tags, titres et dates que tu as réglés à la main sont conservés. Les documents sont traités un par un.</p>
      <div class="btns"><button class="btn small" disabled={busy === 'all'} on:click={() => action('all', '/admin/reprocess-all', (r) => `${plural(r.documents, 'document mis', 'documents mis')} en file d'analyse.`)}><Icon name="refresh" />Réanalyser tous les documents</button></div>
    </section>
    <section>
      <h2>Modèle d'embeddings</h2>
      <dl><dt>État</dt><dd>{MODEL[s.model.status]}</dd>
        {#if s.model.missing_embeddings}<dt>À calculer</dt><dd>{plural(s.model.missing_embeddings, 'document sans empreinte', 'documents sans empreinte')}</dd>{/if}</dl>
      <div class="btns">
        {#if s.model.status === 'absent'}<button class="btn small" disabled={busy === 'dl'} on:click={() => action('dl', '/admin/download-model', 'Téléchargement lancé.')}><Icon name="download" />Télécharger le modèle</button>{/if}
        <button class="btn small" disabled={busy === 'reindex' || s.model.status === 'absent'} on:click={() => action('reindex', '/admin/reindex', 'Réindexation lancée.')}><Icon name="refresh" />Réindexer tous les documents</button>
      </div>
    </section>
    <section>
      <h2>Règles de classement</h2>
      <dl><dt>Source</dt><dd>{s.rules.source}</dd><dt>Types</dt><dd>{s.rules.types}</dd></dl>
      <div class="btns"><button class="btn small" disabled={busy === 'rules'} on:click={() => action('rules', '/admin/reload-rules', (r) => `Règles rechargées (${r.types} types).`)}><Icon name="refresh" />Recharger les règles</button></div>
    </section>
    <section>
      <h2>Statistiques</h2>
      <dl>
        <dt>Documents</dt><dd>{formatNumber(s.documents)}{#each Object.entries(s.by_status) as [k, v]}{#if k !== 'done'} · {v} {(STATUS[k] || k).toLowerCase()}{/if}{/each}</dd>
        <dt>Fichiers</dt><dd>{formatSize(s.files_bytes)}</dd>
        <dt>Base de données</dt><dd>{formatSize(s.db_bytes)}</dd>
        <dt>Groupes · tags</dt><dd>{s.groups} · {s.tags}</dd>
        <dt>Mémoire du processus</dt><dd>{s.memory_mb ? `${String(s.memory_mb).replace('.', ',')} Mo` : 'inconnue'}</dd>
        <dt>Comptes</dt><dd>{s.auth.disabled ? 'Authentification désactivée (AUTH_DISABLED)' : plural(s.auth.accounts, 'compte', 'comptes')}</dd>
        <dt>Version</dt><dd class="mono">{s.version} · build {s.build}</dd>
      </dl>
      {#each s.auth.problems as p}<p class="warn">{p}</p>{/each}
    </section>
  {/if}
</div>

<style>
  section { padding: 22px 0 18px; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 12px; }
  h2 { font-size: 15px; font-weight: 700; }
  dl { display: grid; grid-template-columns: 180px minmax(0, 1fr); gap: 6px 16px; margin: 0; font-size: 14px; }
  dt { color: var(--text-muted); }
  dd { margin: 0; }
  .btns { display: flex; gap: 8px; flex-wrap: wrap; }
  .notice { display: flex; gap: 10px; padding: 10px 14px; margin-top: 16px; border-radius: var(--r-control); background: var(--warning-soft); font-size: 13.5px; }
  .notice :global(.icon) { color: var(--warning); margin-top: 3px; }
  .errs { display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
  .er { display: flex; flex-direction: column; font-size: 13.5px; }
  .er a { font-weight: 600; }
  .warn { color: var(--warning); font-size: 13.5px; }
  @media (max-width: 720px) { dl { grid-template-columns: 1fr; gap: 0; } dt { margin-top: 8px; } }
</style>
