# FE Gradiva – ChatGPT MCP for TrueNAS

Private, read-only FE study-materials MCP server for TrueNAS SCALE 25.10. Syncthing continues managing the FE dataset. Google Drive is unnecessary.

**Release:** `1.2.1` (do not assume deployed on TrueNAS until you explicitly update the app).

## Features

- Read `.pdf`, `.md`, `.txt`, `.py`, `.tex`, `.csv`, `.json` and `.log` files from one restricted FE root.
- Index document text and handwritten-page occupancy in persistent SQLite FTS5; inotify plus 180-second fallback rescan.
- Skip unfilled pages of large square-grid notebooks. Contents of handwriting are **not OCR'd**; manually inspected pages may be labeled in the index.
- Find documents by subject/type, report ambiguous names and penalize empty 1000-page templates (`resolve_document`).
- Find exact exercise references (`find_exercise`); image-only pages explicitly require visual verification.
- Render a full PDF page (`view_pdf_page`) or a normalized 0–1 crop (`view_pdf_region`) as bounded JPEG/PNG.
- File-change dates are **not** evidence of studying; page comparisons only track changes observed *since initial indexing*.
- Nothing writes to the FE dataset; optional `annotate_page` saves labels *only to the separate SQLite index*.

## TrueNAS deployment

1. Leave existing v1.0 running until the image is published and tests are green.
2. In **Apps → Installed**, back up the working Compose configuration of `fe-gradiva-chatgpt`.
3. Ensure these paths exist on the TrueNAS host:

   ```sh
   sudo install -d -m 750 /mnt/tank/apps/fe-mcp/index
   sudo test -d /mnt/tank/sync/FE && echo 'FE folder OK'
   sudo test -s /mnt/tank/apps/fe-mcp/secrets/openai_api_key && echo 'Tunnel key OK'
   ```
4. Copy [deploy/truenas-compose.yaml](deploy/truenas-compose.yaml) to **Apps → Installed → fe-gradiva-chatgpt → Edit**, replacing `tunnel_TVOJ_ID` with your already-created OpenAI tunnel ID. Preserve the secret **as a file**; never paste API keys into YAML/GitHub.
5. `Save` to start `ghcr.io/lukacek/fe-chatgpt:1.2.1` (the workflow builds from `Dockerfile` on pushes to `main`). The GHCR package must be public for anonymous pull; if private, authenticate TrueNAS against GHCR before updating.
6. Verify `fe-server` shows healthy and `tunnel-client` shows Up. ChatGPT → Plugins → FE Gradiva → **Refresh** to discover the new tools.

   ```sh
   sudo docker ps -a --filter name=ix-fe-gradiva-chatgpt
   sudo docker logs --tail 80 ix-fe-gradiva-chatgpt-fe-server-1
   sudo docker logs --tail 80 ix-fe-gradiva-chatgpt-tunnel-client-1
   ```

### Rollback

Restore the backed-up v1.0 YAML in the same TrueNAS app and Save. The new index is only in `/mnt/tank/apps/fe-mcp/index`; it is independent of the source FE folder. **Do not run `docker system prune --volumes`**. Do not start two tunnel clients with the same tunnel ID.

## API tools

`server_info`, `index_status`, `refresh_index`, `list_files`, `recent_files`, `recent_activity`, `file_info`, `populated_pages`, `search_files`, `find_study_context`, `resolve_document`, `find_exercise`, `read_document`, `view_pdf_page`, `view_pdf_region`, `annotate_page`.

**Source verification:** If a PDF is scanned or handwritten, the tools cannot know exercise text without viewing the original image; `find_exercise` will not invent a match. Responses should cite `path` and `page`.

## Local development

```sh
python -m pip install -r requirements.txt pytest
pytest -q
docker build -t fe-chatgpt:local .
```

GitHub Actions checks Python tests and container build. On a successful push to main, it publishes the `1.2.0` and `latest` tags to `ghcr.io/lukacek/fe-chatgpt` using `GITHUB_TOKEN` with `packages: write`. No API secret is stored in the repository. When changing code after release, use a new immutable version tag rather than silently replacing 1.1.1.

### Security

The TrueNAS FE mount is read-only. No ports are published on the host. A dedicated internal Docker network connects the MCP service and tunnel client; only the tunnel client reaches the external OpenAI tunnel. Indexed text and page annotations remain on NAS in the mounted SQLite volume. Requests to the MCP plugin share selected content with ChatGPT. Restrict access to your ChatGPT account and tunnel credentials.


## v1.1.1 corrections

- Graph-paper occupancy detection compares pages against a blank reference learned only after two reference pages agree. Uncertain pages are **kept**.
- The existing SQLite notebook rows are automatically reclassified once on upgrade without deleting page labels or source files.
- Exercise search verifies a numbered **VAJA/NALOGA** heading; a subpart such as `1.1(b)` must be inside that exercise block. Numeric mentions alone no longer count.
- Document resolution requires a title/path match; kind or page count alone cannot produce an unrelated result.
- On first startup, `index_status` may show indexing in progress. Wait until it finishes before testing blank-page counts.


## v1.2.0 — spremljanje dejanskega znanja in učnega zaostajanja v ChatGPT

Obstoječa e.FE mapa ostane **read-only**. Osebne učne ugotovitve niso
vsebine profesorjevih PDF-jev, niso ChatGPT Memory in niso ugibanja na
podlagi časa odpiranja datoteke. Zapisujejo se v povsem ločeno datoteko
\`/mnt/tank/apps/fe-mcp/index/study-tracker.sqlite3\` (v vsebniku
\`/var/lib/fe-index/study-tracker.sqlite3\`). Obstoječi indeks
\`fe-index.sqlite3\` in ročni Samsung Notes se ne spreminjajo.

### ChatGPT MCP orodja

- **\`record_learning_attempt\`**: avtomatsko zabeleži dejansko opaženo
  reševanje naloge. Izid mora biti \`incorrect\`, \`guided\`,
  \`independent_with_difficulty\` ali \`independent\`. Shrani kratko
  dokazilo, konkretne težave, oznako naloge in čas na strežniku.
  \`assessment=practice\` (navadna vaja), \`delayed_recall\`
  (nov samostojen preizkus po časovnem razmiku) ali \`self_report\`
  (izjava učenca, **ni dokaz uspešno rešene naloge**).
- **\`get_knowledge_state\`**: datum zadnje vaje, trenutna ocena na
  podlagi dokazov, problematična področja, zapadla ponovitev. Izrecno
  pove, ali je šlo za en uspeh, več uspehov istega dne, samostojni
  preizkus na **drugi** nalogi čez vsaj 1 dan ali preverjanje po vsaj
  7 dneh. Niti preizkus po 7 dneh še ne pomeni trajnega znanja.
- **\`get_learning_history\`**: časovnica vaj in dokazi, tudi
  razveljavljeni napačni samodejni vnosi.
- **\`void_learning_attempt\`**: popravi napačno avtomatsko oceno z
  revizijsko sledjo.
- **\`get_due_reviews\`**: konservativen seznam ponovitev; pri enkratni
  samostojni rešitvi čez 1–2 dni, pri reševanju s pomočjo ali napakah
  prej, nato razmiki rastejo šele z novimi dokazi.
- **\`record_lesson_progress\`** in **\`get_course_progress\`**:
  razdelita potrjeno izvedene lekcije od načrtovanih predavanj in
  lastnega napredka. **Objavljen PDF ni dokaz izvedenega predavanja**,
  datum spremembe datoteke pa ni dokaz učenja.
- **\`get_study_plan\`**: pripravi predlog učenja iz zapadlih ponovitev
  in dejansko potrjenega zaostanka. Če še ni vnesenih podatkov o
  izvedenih predavanjih, tega odkrito pove, ne izmisli si zaostanka.

### Samodejni vnosi v klepetu — pomembne omejitve

MCP opis strežnika usmerja ChatGPT, da med **dejanskim
reševanjem** samodejno zapiše opaženo uspešnost, brez dodatnih obrazcev.
Za pravilno rešitev po namigu uporabi \`guided\`, za samostojen poskus
\`independent\`. Model ne sme pripisati uspeha, če je odgovor podal sam,
če je učenec le prebral razlago ali pogledal profesorjevo datoteko.

**Samodejnost je odvisna od tega, ali ChatGPT v posameznem pogovoru
dejansko pokliče MCP orodje.** Sam strežnik ne vidi vseh pogovorov,
ne posluša besedila klepetov in ne more samodejno rekonstruirati
preteklih vaj. Med učenjem je smiselno preveriti, ali je bil zapis
dejansko uspešno izveden, še posebej ob prvih preizkusih.

Za dokaz obdržanega znanja je potrebna **druga naloga in
neodvisna ponovitev čez čas**, ne enkrat rešena naloga in ne
subjektivna samoocena. Hevristični intervali za ponavljanje niso
znanstveno natančna verjetnost pomnjenja.

### Namestitev in varnost

1. V TrueNAS Apps nastavi samo sliko strežnika FE Gradiva na
   \`ghcr.io/lukacek/fe-chatgpt:1.2.0\`.
2. Ohraniti moraš **isti** že obstoječi RW mount:
   \`/mnt/tank/apps/fe-mcp/index:/var/lib/fe-index:rw\`.
   Po želji nastavi \`FE_STUDY_DB=/var/lib/fe-index/study-tracker.sqlite3\`.
3. Ne spreminjaj tunela, ID-ja tunela, secret mounta in RO mounta FE.
4. Po uspešnem zagonu v ChatGPT osveži plugin **FE Gradiva**,
   preveri \`server_info.version\` in testno pokliči \`get_knowledge_state\`.
5. Če želiš povrnitev na 1.1.1, lahko aplikacijo vrneš brez brisanja baze;
   podatki v ločenem study-tracker.sqlite3 ostanejo shranjeni.

Noben osebni zapis učnega napredka ne sme končati v javnem GitHub
repozitoriju ali v Syncthingovi FE mapi. Uporabi pravice mape in
poskrbi za redno varnostno kopiranje \`/mnt/tank/apps/fe-mcp/index\`.


## v1.2.1 — obravnava napačnih/neobstoječih poti brez MCP napak

- Tooli \`list_files\`, \`file_info\`, \`read_document\`, \`populated_pages\`
  in \`annotate_page\` za pričakovane napake poti, tipa datoteke ali
  strani vrnejo **strukturiran rezultat** z \`ok: false\`, \`error\`,
  \`message\` in \`path\` namesto izjeme MCP \`INVALID_ARGUMENT\`.
- \`list_files\` ohrani svoj seznam kot izhod: ob napaki vsebuje **en
  objekt napake**; prazna obstoječa mapa še vedno vrne \`[]\`.
- Napaka \`not_found\` ne pomeni nedelovanja MCP ali indeksa;
  preveri obstoječo pot z \`list_files\` nadrejene mape.
- \`recent_files\` in rekurzivni \`list_files\` med istočasnim delovanjem
  Syncthing/Moodle Sync preskočita datoteko, ki je izginila med pregledom.
- Izpisi napak ne vključujejo notranjih poti, piškotkov ali izvirnih
  sporočil neznanih izjem. Resnične programske/SQLite napake se ne skrijejo.
- Varnostne omejitve za \`../\`, simbolne povezave in nedovoljene tipe
  datotek ostanejo nespremenjene.
- Slika: \`ghcr.io/lukacek/fe-chatgpt:1.2.1\`; ohrani oba volumna in
  obstoječo konfiguracijo \`tunnel-client\`.

## v1.2.2 — razumljive napake pri beleženju znanja

- Pri `record_learning_attempt`, `record_lesson_progress` in
  `void_learning_attempt` pričakovano napačni vhodni podatki vrnejo
  `{"ok": false, "recorded": false, "error": "...", "message": "..."}`,
  ne več nejasnega `Error executing tool ...`.
- Pojasni nedovoljen `outcome`/`assessment`, neobstoječ `source_path`
  (za naloge iz klepeta ga pusti praznega) ter ponovno uporabo
  `attempt_id` z drugačno vsebino. Ne izpiše zasebnih poti ali evidence.
- Ne spremeni pravil ocenjevanja ali že zabeleženih poskusov; napake
  SQLite in drugi nepričakovani programski problemi ostanejo vidne.
- Ohranjen je popravek v1.2.1 za napačne/neobstoječe poti pri branju.
- Docker slika: `ghcr.io/lukacek/fe-chatgpt:1.2.2`. Nadgradi le
  oznako slike strežnika, ohrani oba volumna in isto tunelsko povezavo.
