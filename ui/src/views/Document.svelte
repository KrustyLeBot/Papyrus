<script>
  import { route, navigate, goBack, link } from '../lib/router.js';
  import { loadFolders, toast, bumpDocs, docsVersion, refreshQueue } from '../lib/stores.js';
  import { get, post, patch, del } from '../lib/api.js';
  import { formatDate, toInputDate, fromInputDate, formatSize, dateTime } from '../lib/format.js';
  import Icon from '../components/Icon.svelte';
  import Confirm from '../components/Confirm.svelte';
  import Thumb from '../components/Thumb.svelte';

  let doc = null, similar = [], error = '', title = '', dateText = '', dateError = '', showText = false, dialog = null, busy = false, newTag = '', addingTag = false;
  $: id = $route.params.id;
  $: load(id, $docsVersion);
  let timer;

  async function load(docId) {
    clearTimeout(timer);
    try {
      const d = await get(`/documents/${docId}`);
      if (d.id !== id) return;
      adopt(d);
      error = '';
      if (d.status === 'pending' || d.status === 'processing') timer = setTimeout(() => load(docId), 2000);
      get(`/documents/${docId}/similar?limit=10`).then((r) => { if (id === docId) similar = r.items; }).catch(() => {});
    } catch (e) { error = e.message; doc = null; }
  }
  function adopt(d) { doc = d; title = d.title; dateText = toInputDate(d.date); dateError = ''; }

  async function save(body) {
    try { adopt(await patch(`/documents/${id}`, body)); bumpDocs(); } catch (e) { toast(e.message, 'error'); }
  }
  const saveTitle = () => { const t = title.trim(); if (t && t !== doc.title) save({ title: t }); else title = doc.title; };
  function saveDate() {
    const iso = fromInputDate(dateText);
    if (!iso) { dateError = 'Date invalide (exemple : 14/10/2026).'; return; }
    dateError = '';
    if (iso !== doc.date) save({ doc_date: iso });
  }
  async function act(fn, ok) { busy = true; try { await fn(); if (ok) toast(ok, 'success'); } catch (e) { toast(e.message, 'error'); } busy = false; }
  const addTag = () => act(async () => { const n = newTag.trim(); if (!n) return; adopt(await post(`/documents/${id}/tags`, { name: n })); newTag = ''; addingTag = false; bumpDocs(); });
  const removeTag = (t) => act(async () => { adopt(await del(`/documents/${id}/tags/${t.id}`)); bumpDocs(); });
  const reprocess = () => act(async () => { await post(`/documents/${id}/reprocess`); await load(id); bumpDocs(); }, 'Analyse relancée.');
  const setHidden = (hide) => act(async () => { adopt(await patch(`/documents/${id}`, { hidden: hide })); await loadFolders(); bumpDocs(); refreshQueue(); }, hide ? 'Document masqué. Il est dans « Masqués ».' : 'Document affiché à nouveau.');
  const forget = () => act(async () => { await del(`/documents/${id}`); dialog = null; await loadFolders(); bumpDocs(); toast('Document retiré définitivement de Papyrus. Ton fichier n\'a pas bougé.', 'success'); navigate('/', { replace: true }); });

  const MONO = ['siret', 'siren', 'che_uid', 'tva', 'contract_no', 'cadastre_ref'];
  const KEYS = { siret: 'SIRET', siren: 'SIREN', che_uid: 'IDE', tva: 'TVA', cadastre_ref: 'Cadastre', property_address: 'Adresse', contract_no: 'Contrat', employer_name: 'Employeur', company: 'Société', year: 'Année' };
  $: shownKeys = doc ? doc.keys.filter((k) => k.key !== 'year' && !(k.key === 'siren' && doc.keys.some((x) => x.key === 'siret' && x.value.startsWith(k.value))) && (k.key !== 'company' || !doc.keys.some((x) => x.key === 'employer_name'))) : [];
  $: isImage = doc && ['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp'].includes(doc.ext);
  $: canFrame = doc && doc.ext === 'pdf';
  $: fileUrl = `/api/documents/${id}/file`;
  const DATE_SOURCE = { detected: 'détectée dans le texte', manual: 'saisie à la main', file: 'date de modification du fichier' };
</script>

<div class="wrap">
  <div class="bar">
    <button class="btn ghost small" on:click={() => goBack('/')}><Icon name="chevronRight" size={14} /><span class="back">Retour</span></button>
    {#if doc}<span class="crumb">{doc.title}</span>{/if}
    {#if doc}
      <div class="acts">
        <a class="btn" href="{fileUrl}?download=1" download><Icon name="download" /><span class="hide">Télécharger l'original</span></a>
        <button class="btn" disabled={busy || doc.status === 'processing'} on:click={reprocess}><Icon name="refresh" /><span class="hide">Relancer l'analyse</span></button>
        {#if doc.hidden}
          <button class="btn" disabled={busy} on:click={() => setHidden(false)}><Icon name="eye" /><span class="hide">Afficher à nouveau</span></button>
          <button class="btn danger" on:click={() => (dialog = 'forget')}><Icon name="trash" /><span class="hide">Retirer définitivement</span></button>
        {:else}
          <button class="btn" disabled={busy} on:click={() => setHidden(true)}><Icon name="eyeOff" /><span class="hide">Masquer</span></button>
        {/if}
      </div>
    {/if}
  </div>

  {#if error}
    <div class="empty">{error}<a class="btn" href="/" use:link>Retour à la chronologie</a></div>
  {:else if !doc}
    <div class="body"><div class="skeleton" style="flex:1;min-height:600px"></div><div class="panel"><div class="skeleton" style="height:300px"></div></div></div>
  {:else}
    {#if doc.hidden}<div class="banner" style="padding:9px 20px"><span class="icon"><Icon name="eyeOff" /></span>Ce document est masqué : absent de la chronologie, des dossiers et de la recherche. Rien n'est supprimé.</div>{/if}
    {#if doc.status === 'error'}<div class="banner" style="padding:9px 20px"><span class="icon"><Icon name="alert" /></span>Le traitement a échoué : {doc.error}. Tu peux relancer l'analyse.</div>
    {:else if doc.status === 'password_required'}<div class="banner" style="padding:9px 20px"><span class="icon"><Icon name="lock" /></span>Ce PDF est protégé par un mot de passe : <a href="/unlock" use:link style="text-decoration:underline">le déverrouiller</a>.</div>
    {:else if doc.status === 'missing'}<div class="banner" style="padding:9px 20px"><span class="icon"><Icon name="alert" /></span>Le fichier n'est plus dans les dossiers analysés. Il reviendra de lui-même s'il réapparaît.</div>
    {:else if doc.status === 'pending' || doc.status === 'processing'}<div class="banner" style="padding:9px 20px"><span class="icon"><Icon name="clock" /></span>{doc.status === 'processing' ? 'Analyse en cours…' : 'En attente d\'analyse…'} Le texte, la date et les tags apparaissent ici dès que c'est prêt.</div>{/if}
    <div class="body">
      <section class="preview" aria-label="Aperçu">
        {#if doc.status === 'password_required' || doc.status === 'missing'}
          <div class="paper"><div class="muted note">Aperçu indisponible.</div></div>
        {:else if canFrame}
          <iframe title="Aperçu de {doc.title}" src="{fileUrl}#toolbar=1&navpanes=0"></iframe>
        {:else if isImage}
          <img class="img" src={fileUrl} alt="Aperçu de {doc.title}" />
        {:else}
          <div class="paper"><div class="muted note">Aperçu non disponible pour ce format : voici le texte extrait.</div><pre>{doc.text || '(aucun texte)'}</pre></div>
        {/if}
      </section>

      <aside class="panel">
        <div class="blk">
          <label class="field"><span class="label">Titre</span>
            <input class="input" bind:value={title} on:blur={saveTitle} on:keydown={(e) => e.key === 'Enter' && e.target.blur()} maxlength="300" /></label>
          <label class="field"><span class="label">Date du document</span>
            <div class="row"><input class="input date" class:invalid={dateError} bind:value={dateText} on:blur={saveDate} on:keydown={(e) => e.key === 'Enter' && e.target.blur()} placeholder="jj/mm/aaaa" inputmode="numeric" />
              <span class="muted small">{DATE_SOURCE[doc.date_source] || ''}</span></div>
            {#if dateError}<span class="err">{dateError}</span>{/if}</label>
          <div class="field"><span class="label">{doc.locations.length > 1 ? `Emplacements (${doc.locations.length} copies)` : 'Emplacement'}</span>
            <div class="places">
              {#each doc.locations as l (l.path)}
                <a class="place" href="/?folder={encodeURIComponent(l.folder || '.')}" use:link title="Voir ce dossier">
                  <Icon name="folder" size={14} /><span class="mono path">{l.path}</span>
                </a>
              {:else}<span class="muted small">Plus dans les dossiers analysés.</span>{/each}
            </div>
            <span class="muted small">C'est toi qui ranges : déplace ou renomme le fichier, Papyrus le suit (même contenu, même fiche).</span></div>
          <div class="field"><span class="label">Tags</span>
            <div class="chips">
              {#each doc.tags as t (t.id)}<span class="tag lg">{t.name}<button aria-label="Retirer le tag {t.name}" on:click={() => removeTag(t)}><Icon name="x" size={12} /></button></span>{/each}
              {#if addingTag}
                <form on:submit|preventDefault={addTag}><input class="input tagin" bind:value={newTag} maxlength="60" placeholder="Nouveau tag" data-autofocus on:blur={() => { if (!newTag) addingTag = false; }} /></form>
              {:else}<button class="add" on:click={() => { addingTag = true; setTimeout(() => document.querySelector('.tagin')?.focus(), 0); }}>+ Tag</button>{/if}
            </div></div>
        </div>

        {#if doc.summary}
          <div class="blk">
            <h3>Aperçu du contenu</h3>
            <p class="digest">{doc.summary}</p>
            <p class="muted small">Phrases extraites du texte du document, pas générées.</p>
          </div>
        {/if}

        {#if shownKeys.length || doc.sensitive.length}
          <div class="blk">
            <h3>Identifiants détectés</h3>
            {#each shownKeys as k}
              <div class="kv"><span class="muted">{KEYS[k.key] || k.key}</span><span class:mono={MONO.includes(k.key)}>{MONO.includes(k.key) ? k.value : k.label}{#if MONO.includes(k.key) && k.label && k.label !== k.value && !k.label.startsWith('SIRET') && !k.label.startsWith('Section') && !k.label.startsWith('Contrat') && !k.label.startsWith('Prêt')}<span class="muted sans"> · {k.label}</span>{/if}</span></div>
            {/each}
            {#each doc.sensitive as s}<div class="kv"><span class="muted">{s.label}</span><span class="mono">{s.masked}</span></div>{/each}
          </div>
        {/if}

        {#if similar.length}
          <div class="blk">
            <h3>Documents similaires</h3>
            {#each similar as s (s.id)}
              <a class="sim" href="/documents/{s.id}" use:link>
                <Thumb doc={s} width={30} ratio={1.33} />
                <span class="st"><span class="sn">{s.title}</span><span class="muted small">{s.folder || 'racine'} · {formatDate(s.date)}</span></span>
                <span class="muted small score">{String(s.score).replace('.', ',')}</span>
              </a>
            {/each}
          </div>
        {/if}

        <div class="blk">
          <button class="fold" aria-expanded={showText} on:click={() => (showText = !showText)}><Icon name={showText ? 'chevronDown' : 'chevronRight'} />Texte extrait ({doc.ocr ? 'OCR' : 'texte du fichier'}, {doc.pages} page{doc.pages > 1 ? 's' : ''})</button>
          {#if showText}<pre class="text">{doc.text || '(aucun texte extrait)'}</pre>{/if}
          <p class="muted small">Repéré le {dateTime(doc.created_at)} · {doc.original_name} · {formatSize(doc.size)}{#if doc.unlocked_at} · déverrouillé le {dateTime(doc.unlocked_at)}{/if}</p>
        </div>
      </aside>
    </div>
  {/if}
</div>

{#if dialog === 'forget'}
  <Confirm title="Retirer définitivement de Papyrus" confirm="Retirer définitivement" danger {busy} on:close={() => (dialog = null)} on:confirm={forget}>
    « {doc?.title} » est effacé de Papyrus (analyse, tags, titre saisi) et ne sera plus jamais ré-indexé, même si une copie traîne ailleurs. <strong>Ton fichier reste où il est, intact.</strong> Pour garder la fiche sans la voir, laisse-la simplement masquée. Pour réintégrer après coup : Administration → Réintégrer les documents retirés.
  </Confirm>
{/if}

<style>
  .wrap { display: flex; flex-direction: column; min-height: calc(100vh - 60px); }
  .bar { min-height: 56px; display: flex; align-items: center; gap: 10px; padding: 8px 20px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
  .back :global(.icon) { transform: rotate(180deg); }
  .bar > button :global(svg) { transform: rotate(180deg); }
  .crumb { font-weight: 600; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
  .acts { margin-left: auto; display: flex; gap: 8px; }
  .body { display: flex; flex-wrap: wrap; flex: 1; }
  .preview { flex: 999 1 520px; min-width: 0; background: var(--bg); padding: 20px; display: flex; justify-content: center; }
  iframe { width: 100%; max-width: 900px; min-height: 78vh; border: 1px solid var(--border); border-radius: 4px; background: var(--paper); }
  .img { max-width: 100%; max-height: 82vh; object-fit: contain; align-self: flex-start; border-radius: 4px; border: 1px solid var(--border); background: var(--paper); }
  .paper { background: var(--paper); color: #1B1340; width: 100%; max-width: 760px; padding: 28px 32px; border-radius: 4px; align-self: flex-start; }
  .paper pre { white-space: pre-wrap; font: 13px/1.6 var(--mono); margin: 12px 0 0; }
  .note { font-size: 13px; color: #625C74; }
  .panel { flex: 1 1 380px; max-width: 440px; border-left: 1px solid var(--border); background: var(--surface); display: flex; flex-direction: column; }
  .blk { padding: 18px 20px; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 12px; }
  .blk:last-child { border-bottom: 0; }
  h3 { font-size: 13.5px; font-weight: 700; }
  .row { display: flex; gap: 10px; align-items: center; }
  .date { max-width: 150px; }
  .small { font-size: 12.5px; }
  .err { color: var(--danger); font-size: 12.5px; }
  .places { display: flex; flex-direction: column; gap: 4px; }
  .place { display: flex; align-items: center; gap: 8px; padding: 5px 8px; margin: 0 -8px; border-radius: var(--r-control); color: var(--text); }
  .place:hover { background: var(--surface-2); }
  .place :global(.icon) { color: var(--text-muted); }
  .path { word-break: break-all; }
  .chips { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
  .tag.lg { height: 26px; font-size: 13px; padding: 0 4px 0 9px; gap: 6px; color: var(--text); }
  .tag button { border: 0; background: transparent; color: var(--text-muted); width: 20px; height: 20px; display: inline-flex; align-items: center; justify-content: center; border-radius: 4px; padding: 0; }
  .tag button:hover { background: var(--border); color: var(--text); }
  .add { height: 26px; padding: 0 9px; border-radius: var(--r-control); font-size: 13px; border: 1px dashed var(--border); color: var(--text-muted); background: transparent; }
  .add:hover { color: var(--text); border-color: var(--text-muted); }
  .tagin { height: 26px; width: 130px; font-size: 13px; }
  .kv { display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 10px; font-size: 13px; align-items: baseline; word-break: break-word; }
  .sans { font-family: var(--font); }
  .digest { font-size: 13.5px; line-height: 1.55; }
  .sim { display: flex; align-items: center; gap: 10px; padding: 5px; margin: 0 -5px; border-radius: var(--r-control); }
  .sim:hover { background: var(--surface-2); }
  .st { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .sn { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .score { font-variant-numeric: tabular-nums; }
  .fold { display: flex; align-items: center; gap: 8px; border: 0; background: transparent; font-weight: 700; font-size: 13.5px; padding: 0; text-align: left; }
  .text { max-height: 360px; overflow: auto; white-space: pre-wrap; font: 12.5px/1.6 var(--mono); background: var(--surface-2); padding: 12px; border-radius: var(--r-control); margin: 0; }
  @media (max-width: 900px) { .hide { display: none; } .panel { max-width: none; border-left: 0; border-top: 1px solid var(--border); } .preview { padding: 12px; } iframe { min-height: 60vh; } }
</style>
