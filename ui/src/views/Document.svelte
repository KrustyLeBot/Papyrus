<script>
  import { route, navigate, goBack, link } from '../lib/router.js';
  import { groups, loadGroups, toast, bumpDocs, docsVersion, groupPath } from '../lib/stores.js';
  import { get, post, patch, del } from '../lib/api.js';
  import { formatDate, toInputDate, fromInputDate, formatSize, dateTime } from '../lib/format.js';
  import Icon from '../components/Icon.svelte';
  import Modal from '../components/Modal.svelte';
  import Confirm from '../components/Confirm.svelte';
  import Thumb from '../components/Thumb.svelte';

  let doc = null, similar = [], error = '', title = '', dateText = '', dateError = '', showText = false, dialog = null, pick = '', busy = false, newTag = '', addingTag = false;
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
    try { adopt(await patch(`/documents/${id}`, body)); bumpDocs(); await loadGroups(); } catch (e) { toast(e.message, 'error'); }
  }
  const saveTitle = () => { const t = title.trim(); if (t && t !== doc.title) save({ title: t }); else title = doc.title; };
  function saveDate() {
    const iso = fromInputDate(dateText);
    if (!iso) { dateError = 'Date invalide (exemple : 14/10/2026).'; return; }
    dateError = '';
    if (iso !== doc.date) save({ doc_date: iso });
  }
  async function act(fn, ok) { busy = true; try { await fn(); if (ok) toast(ok, 'success'); } catch (e) { toast(e.message, 'error'); } busy = false; }
  const addGroup = () => act(async () => { await post(`/groups/${pick}/documents`, { doc_ids: [id] }); dialog = null; adopt(await get(`/documents/${id}`)); await loadGroups(); bumpDocs(); }, 'Document classé.');
  const removeGroup = (g) => act(async () => { await del(`/groups/${g.id}/documents/${id}`); adopt(await get(`/documents/${id}`)); await loadGroups(); bumpDocs(); });
  const accept = (s) => act(async () => { adopt(await post(`/documents/${id}/suggestions/${s.group_id}/accept`)); await loadGroups(); bumpDocs(); }, 'Document classé.');
  const addTag = () => act(async () => { const n = newTag.trim(); if (!n) return; adopt(await post(`/documents/${id}/tags`, { name: n })); newTag = ''; addingTag = false; bumpDocs(); });
  const removeTag = (t) => act(async () => { adopt(await del(`/documents/${id}/tags/${t.id}`)); bumpDocs(); });
  const reprocess = () => act(async () => { await post(`/documents/${id}/reprocess`); await load(id); bumpDocs(); }, 'Analyse relancée.');
  const remove = () => act(async () => { await del(`/documents/${id}`); dialog = null; await loadGroups(); bumpDocs(); toast('Document supprimé.', 'success'); navigate('/', { replace: true }); });
  const toggleLock = () => save({ locked: !doc.locked });

  $: available = $groups.filter((g) => !g.inbox && !doc?.groups.some((x) => x.id === g.id));
  const MONO = ['siret', 'siren', 'che_uid', 'tva', 'contract_no', 'cadastre_ref'];
  const KEYS = { siret: 'SIRET', siren: 'SIREN', che_uid: 'IDE', tva: 'TVA', cadastre_ref: 'Cadastre', property_address: 'Adresse', contract_no: 'Contrat', employer_name: 'Employeur', company: 'Société', year: 'Année' };
  $: shownKeys = doc ? doc.keys.filter((k) => k.key !== 'year' && !(k.key === 'siren' && doc.keys.some((x) => x.key === 'siret' && x.value.startsWith(k.value))) && k.key !== 'company' || (k.key === 'company' && !doc.keys.some((x) => x.key === 'employer_name'))) : [];
  $: isImage = doc && ['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp'].includes(doc.ext);
  $: canFrame = doc && doc.ext === 'pdf';
  $: fileUrl = `/api/documents/${id}/file`;
</script>

<div class="wrap">
  <div class="bar">
    <button class="btn ghost small" on:click={() => goBack('/')}><Icon name="chevronRight" size={14} /><span class="back">Retour</span></button>
    {#if doc}<span class="crumb">{doc.title}</span>{/if}
    {#if doc}
      <div class="acts">
        <a class="btn" href="{fileUrl}?download=1" download><Icon name="download" /><span class="hide">Télécharger l'original</span></a>
        <button class="btn" disabled={busy || doc.status === 'processing'} on:click={reprocess}><Icon name="refresh" /><span class="hide">Relancer l'analyse</span></button>
        <button class="btn danger" on:click={() => (dialog = 'delete')}><Icon name="trash" /><span class="hide">Supprimer</span></button>
      </div>
    {/if}
  </div>

  {#if error}
    <div class="empty">{error}<a class="btn" href="/" use:link>Retour à la chronologie</a></div>
  {:else if !doc}
    <div class="body"><div class="skeleton" style="flex:1;min-height:600px"></div><div class="panel"><div class="skeleton" style="height:300px"></div></div></div>
  {:else}
    {#if doc.status === 'error'}<div class="banner" style="padding:9px 20px"><span class="icon"><Icon name="alert" /></span>Le traitement a échoué : {doc.error}. Le fichier est conservé ; tu peux relancer l'analyse.</div>
    {:else if doc.status === 'pending' || doc.status === 'processing'}<div class="banner" style="padding:9px 20px"><span class="icon"><Icon name="clock" /></span>{doc.status === 'processing' ? 'Analyse en cours…' : 'En attente de traitement…'} Le texte, la date et les groupes apparaissent ici dès que c'est prêt.</div>{/if}
    <div class="body">
      <section class="preview" aria-label="Aperçu">
        {#if canFrame}
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
              <span class="muted small">{doc.date_source === 'detected' ? 'détectée dans le texte' : doc.date_source === 'manual' ? 'saisie à la main' : "date d'import"}</span></div>
            {#if dateError}<span class="err">{dateError}</span>{/if}</label>
          <div class="field"><span class="label">Groupes</span>
            <div class="chips">
              {#each doc.groups as g (g.id)}
                <span class="chip lg"><span class="dot" style="background:{g.color}"></span><a href="/groups/{g.id}" use:link>{g.path}</a>
                  {#if doc.groups.length > 1 || !g.inbox}<button aria-label="Retirer du groupe {g.path}" on:click={() => removeGroup(g)}><Icon name="x" size={12} /></button>{/if}</span>
              {/each}
              <button class="add" on:click={() => { dialog = 'group'; pick = ''; }}>+ Ajouter à un groupe</button>
            </div>
            {#if doc.suggestion_applied}<span class="muted small">Groupe proposé par similarité avec tes autres documents, à confirmer.</span>{/if}
            {#if doc.suggestions.length}
              <div class="sugg"><span class="muted small">Groupes suggérés :</span>
                {#each doc.suggestions as s (s.group_id)}<button class="chip lg act" on:click={() => accept(s)}><span class="dot" style="background:{s.group.color}"></span><span>{s.group.path}</span></button>{/each}</div>
            {/if}</div>
          <div class="field"><span class="label">Tags</span>
            <div class="chips">
              {#each doc.tags as t (t.id)}<span class="tag lg">{t.name}<button aria-label="Retirer le tag {t.name}" on:click={() => removeTag(t)}><Icon name="x" size={12} /></button></span>{/each}
              {#if addingTag}
                <form on:submit|preventDefault={addTag}><input class="input tagin" bind:value={newTag} maxlength="60" placeholder="Nouveau tag" data-autofocus on:blur={() => { if (!newTag) addingTag = false; }} /></form>
              {:else}<button class="add" on:click={() => { addingTag = true; setTimeout(() => document.querySelector('.tagin')?.focus(), 0); }}>+ Tag</button>{/if}
            </div></div>
          <label class="lockrow"><input type="checkbox" checked={doc.locked} on:change={toggleLock} />
            <span><span>Verrouiller le classement</span><span class="muted small block">Le reclassement automatique ne touchera plus ce document.</span></span></label>
        </div>

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
                <span class="st"><span class="sn">{s.title}</span><span class="muted small">{formatDate(s.date)}</span></span>
                <span class="muted small score">{String(s.score).replace('.', ',')}</span>
              </a>
            {/each}
          </div>
        {/if}

        <div class="blk">
          <button class="fold" aria-expanded={showText} on:click={() => (showText = !showText)}><Icon name={showText ? 'chevronDown' : 'chevronRight'} />Texte extrait ({doc.ocr ? 'OCR' : 'texte du fichier'}, {doc.pages} page{doc.pages > 1 ? 's' : ''})</button>
          {#if showText}<pre class="text">{doc.text || '(aucun texte extrait)'}</pre>{/if}
          <p class="muted small">Importé le {dateTime(doc.created_at)} · {doc.original_name} · {formatSize(doc.size)}{#if doc.unlocked_at} · déverrouillé le {dateTime(doc.unlocked_at)}{/if}</p>
        </div>
      </aside>
    </div>
  {/if}
</div>

{#if dialog === 'group'}
  <Modal title="Ajouter à un groupe" on:close={() => (dialog = null)}>
    <form on:submit|preventDefault={addGroup} class="mform">
      <label class="field"><span class="label">Groupe</span>
        <select class="input" bind:value={pick} required data-autofocus><option value="" disabled>Choisir un groupe…</option>{#each available as g (g.id)}<option value={g.id}>{groupPath($groups, g.id)}</option>{/each}</select></label>
      <p class="muted small">Papyrus retient l'adresse, le SIRET… de ce document pour ranger les prochains au même endroit.</p>
      <div class="foot"><button type="button" class="btn" on:click={() => (dialog = null)}>Annuler</button><button class="btn primary" disabled={!pick || busy}>Ajouter au groupe</button></div>
    </form>
  </Modal>
{:else if dialog === 'delete'}
  <Confirm title="Supprimer le document" confirm="Supprimer le document" danger {busy} on:close={() => (dialog = null)} on:confirm={remove}>
    « {doc?.title} » et son fichier original seront supprimés définitivement.
  </Confirm>
{/if}

<style>
  .wrap { display: flex; flex-direction: column; min-height: calc(100vh - 60px); }
  .bar { min-height: 56px; display: flex; align-items: center; gap: 10px; padding: 8px 20px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
  .bar :global(.icon) { transform-origin: center; }
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
  .block { display: block; }
  .err { color: var(--danger); font-size: 12.5px; }
  .chips { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
  .chip.lg, .tag.lg { height: 26px; font-size: 13px; padding: 0 4px 0 9px; gap: 6px; }
  .chip.lg a:hover { text-decoration: underline; }
  .chip button, .tag button { border: 0; background: transparent; color: var(--text-muted); width: 20px; height: 20px; display: inline-flex; align-items: center; justify-content: center; border-radius: 4px; padding: 0; }
  .chip button:hover, .tag button:hover { background: var(--border); color: var(--text); }
  .chip.act { padding: 0 9px; background: transparent; }
  .chip.act:hover { background: var(--accent-soft); }
  .sugg { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 2px; }
  .add { height: 26px; padding: 0 9px; border-radius: var(--r-control); font-size: 13px; border: 1px dashed var(--border); color: var(--text-muted); background: transparent; }
  .add:hover { color: var(--text); border-color: var(--text-muted); }
  .tagin { height: 26px; width: 130px; font-size: 13px; }
  .lockrow { display: flex; gap: 10px; align-items: flex-start; font-size: 13.5px; }
  .lockrow input { accent-color: var(--accent); width: 16px; height: 16px; margin-top: 3px; }
  .kv { display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 10px; font-size: 13px; align-items: baseline; word-break: break-word; }
  .sans { font-family: var(--font); }
  .sim { display: flex; align-items: center; gap: 10px; padding: 5px; margin: 0 -5px; border-radius: var(--r-control); }
  .sim:hover { background: var(--surface-2); }
  .st { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .sn { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .score { font-variant-numeric: tabular-nums; }
  .fold { display: flex; align-items: center; gap: 8px; border: 0; background: transparent; font-weight: 700; font-size: 13.5px; padding: 0; text-align: left; }
  .text { max-height: 360px; overflow: auto; white-space: pre-wrap; font: 12.5px/1.6 var(--mono); background: var(--surface-2); padding: 12px; border-radius: var(--r-control); margin: 0; }
  .mform { display: flex; flex-direction: column; gap: 14px; }
  .foot { display: flex; gap: 8px; justify-content: flex-end; }
  @media (max-width: 900px) { .hide { display: none; } .panel { max-width: none; border-left: 0; border-top: 1px solid var(--border); } .preview { padding: 12px; } iframe { min-height: 60vh; } }
</style>
