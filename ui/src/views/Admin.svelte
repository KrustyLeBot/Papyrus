<script>
  import { onDestroy } from 'svelte';
  import { get, post, api } from '../lib/api.js';
  import { navigate } from '../lib/router.js';
  import { queue, toast, refreshQueue, bumpDocs, loadFolders } from '../lib/stores.js';
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
    try { const r = await post(path); toast(typeof ok === 'function' ? ok(r) : ok, 'success'); await load(); bumpDocs(); loadFolders(); }
    catch (e) { toast(e.message, 'error'); }
    busy = '';
  }
  let semMin = 0.86, prefix = true, fuzzy = true, searchDirty = false, searchLoaded = false;
  $: if (s && !searchLoaded) { semMin = s.search.semantic_min; prefix = s.search.prefix; fuzzy = s.search.fuzzy; searchLoaded = true; }
  async function saveSearch() {
    busy = 'search';
    try { await api('PUT', '/admin/search-settings', { semantic_min: Number(semMin), prefix, fuzzy }); searchDirty = false; toast('Réglages de recherche enregistrés.', 'success'); bumpDocs(); }
    catch (e) { toast(e.message, 'error'); }
    busy = '';
  }
  function ago(iso) {
    if (!iso) return 'jamais';
    const sec = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
    return sec < 90 ? `il y a ${sec} s` : sec < 5400 ? `il y a ${Math.round(sec / 60)} min` : `il y a ${Math.round(sec / 3600)} h`;
  }
  const MODEL = { loaded: 'Chargé en mémoire', unloaded: 'Présent, déchargé (rechargé au prochain besoin)', absent: 'Absent', downloading: 'Téléchargement en cours' };
  const STATUS = { pending: 'en attente', processing: 'en cours', done: 'traités', error: 'en échec', password_required: 'protégés', missing: 'introuvables' };
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
      <h2>Dossiers analysés</h2>
      <dl>
        <dt>Dossier source</dt><dd class="mono">{s.scan.source}</dd>
        <dt>Types de fichiers</dt><dd>{s.scan.extensions.join(', ')}</dd>
        <dt>État</dt><dd>{#if s.scan.running}Analyse en cours{#if s.scan.files} · {s.scan.hashed} / {s.scan.files} fichiers lus{/if}{:else}Au repos, nouvelle analyse toutes les {s.scan.interval} s{/if}</dd>
        {#if s.scan.last}<dt>Dernière analyse</dt><dd>{ago(s.scan.last.at)} ({s.scan.last.seconds} s) : {s.scan.last.new} nouveau{s.scan.last.new > 1 ? 'x' : ''}, {s.scan.last.relocated} déplacé{s.scan.last.relocated > 1 ? 's' : ''}, {s.scan.last.updated} modifié{s.scan.last.updated > 1 ? 's' : ''}, {s.scan.last.missing} introuvable{s.scan.last.missing > 1 ? 's' : ''}{#if s.scan.last.skipped}, {s.scan.last.skipped} trop gros{/if}{#if s.scan.last.waiting}, {s.scan.last.waiting} en cours de copie{/if}</dd>{/if}
        <dt>Emplacements suivis</dt><dd>{formatNumber(s.locations)} fichiers</dd>
      </dl>
      {#if s.scan.warning}<p class="warn">{s.scan.warning}</p>{/if}
      <p class="muted small">Papyrus ne fait que lire ces dossiers. Il reconnaît un fichier à son contenu (SHA-256) : tu peux le déplacer ou le renommer, sa fiche le suit. Un fichier disparu est masqué, pas supprimé : il revient tout seul s'il réapparaît.</p>
      <div class="btns">
        <button class="btn small" disabled={busy === 'scan' || s.scan.running} on:click={() => action('scan', '/scan', 'Analyse lancée.')}><Icon name="refresh" />Analyser maintenant</button>
        {#if s.by_status.missing}<button class="btn small" disabled={busy === 'purge'} on:click={() => action('purge', '/admin/purge-missing', (r) => `${plural(r.documents, 'document introuvable oublié', 'documents introuvables oubliés')}.`)}>Oublier les {s.by_status.missing} documents introuvables</button>{/if}
        {#if s.ignored}<button class="btn small" disabled={busy === 'unignore'} on:click={() => action('unignore', '/admin/unignore-all', (r) => `${plural(r.documents, 'document retiré réintégré', 'documents retirés réintégrés')}.`)}>Réintégrer les {s.ignored} documents retirés</button>{/if}
      </div>
    </section>
    <section>
      <h2>Traitement</h2>
      <dl>
        <dt>Worker</dt><dd>{s.worker.alive ? 'Actif' : 'Arrêté'}{#if s.worker.current}{' '}· {s.worker.current.title || 'tâche'} : {s.worker.current.step}{:else}{' '}· au repos{/if}</dd>
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
      <h2>Recherche</h2>
      <p class="muted">« salai » trouve « salaire » dès qu'on tape, et une faute de frappe est pardonnée. La recherche par sens ne garde que les documents assez proches : plus le seuil est haut, moins il y a de résultats approximatifs.</p>
      <div class="set">
        <label class="rng"><span>Similarité minimale (sens) <b class="mono">{Number(semMin).toFixed(2).replace('.', ',')}</b></span>
          <input type="range" min="0.75" max="0.95" step="0.01" bind:value={semMin} on:input={() => (searchDirty = true)} />
          <span class="muted small">0,86 par défaut. Plus bas : plus de résultats « par sens » (parfois hors sujet). Plus haut : seulement les documents très proches.</span></label>
        <label class="chk"><input type="checkbox" bind:checked={prefix} on:change={() => (searchDirty = true)} />Compléter le dernier mot en tapant (salai → salaire)</label>
        <label class="chk"><input type="checkbox" bind:checked={fuzzy} on:change={() => (searchDirty = true)} />Pardonner une faute de frappe (salare → salaire)</label>
      </div>
      <div class="btns"><button class="btn small primary" disabled={!searchDirty || busy === 'search'} on:click={saveSearch}>Enregistrer</button></div>
    </section>
    <section>
      <h2>Mots de passe des PDF</h2>
      <p class="muted">{s.passwords ? `Mot de passe retenu pour ${plural(s.passwords, 'document', 'documents')}.` : 'Aucun mot de passe retenu.'} Chiffrés avec ton mot de passe de connexion, liés à l'empreinte du document qu'ils ont ouvert, et réessayés tout seuls sur chaque PDF protégé que Papyrus trouve. Jamais affichés, jamais exportés.</p>
      <p class="muted">La clé n'existe qu'en mémoire, depuis ta connexion : après un redémarrage de Papyrus il faut te reconnecter une fois. Si tu changes ton mot de passe de connexion, les mots de passe retenus sont perdus et tu les retapes.{#if s.vault.locked} <span class="warn">Ils sont verrouillés pour l'instant : reconnecte-toi.</span>{/if}</p>
      {#if s.passwords}<div class="btns"><button class="btn small danger" disabled={busy === 'pw'} on:click={() => action('pw', '/admin/forget-passwords', 'Mots de passe oubliés.')}>Oublier tous les mots de passe</button></div>{/if}
    </section>
    <section>
      <h2>Réanalyse</h2>
      <p class="muted">Après une mise à jour de Papyrus : relit chaque document (OCR, orientation, date, titre, mots-clés, aperçu). Les tags, titres et dates que tu as réglés à la main sont conservés. Les documents sont traités un par un.</p>
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
        <dt>Taille des documents</dt><dd>{formatSize(s.files_bytes)}</dd>
        <dt>Base de données</dt><dd>{formatSize(s.db_bytes)}</dd>
        <dt>Tags</dt><dd>{s.tags}</dd>
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
  .set { display: flex; flex-direction: column; gap: 12px; max-width: 520px; }
  .rng { display: flex; flex-direction: column; gap: 6px; font-size: 14px; }
  .rng input { accent-color: var(--accent); }
  .chk { display: flex; gap: 10px; align-items: center; font-size: 14px; }
  .chk input { accent-color: var(--accent); width: 16px; height: 16px; }
  .small { font-size: 13px; }
  .notice { display: flex; gap: 10px; padding: 10px 14px; margin-top: 16px; border-radius: var(--r-control); background: var(--warning-soft); font-size: 13.5px; }
  .notice :global(.icon) { color: var(--warning); margin-top: 3px; }
  .errs { display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
  .er { display: flex; flex-direction: column; font-size: 13.5px; }
  .er a { font-weight: 600; }
  .warn { color: var(--warning); font-size: 13.5px; }
  @media (max-width: 720px) { dl { grid-template-columns: 1fr; gap: 0; } dt { margin-top: 8px; } }
</style>
