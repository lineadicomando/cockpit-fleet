# cockpit-fleet — piano di sviluppo

Plugin Cockpit per l'orchestrazione di Ansible (ispirato a Semaphore UI) e per la
raccolta, la rappresentazione storica e la notifica di informazioni dai nodi
gestiti, acquisite solo tramite Ansible.

Stato: analisi conclusa l'8 ottobre 2026, nessun codice scritto.

---

## 1. Decisioni prese

| Tema | Decisione |
|---|---|
| Backend | Systemd-native: nessun demone, nessuna porta in ascolto |
| Permessi | Ruoli tramite gruppi Unix: `cockpit-fleet-admin`, `cockpit-fleet-operator`, `cockpit-fleet-viewer` |
| Identità delle run | Utente di sistema dedicato e non privilegiato `cockpit-fleet` |
| Scala | Oltre 200 nodi gestiti |
| Sorgenti dei progetti | Repository git, directory locale, editor nella UI, upload di archivio |
| Segreti | `systemd-creds`, cifrati con la chiave host (TPM2 se presente) |
| Ansible | Pacchetto Debian (`ansible-core`), collection extra per progetto via `ansible-galaxy` |
| Notifiche | Telegram, webhook generico, email SMTP, Matrix |
| Dati dai nodi | Check di salute, inventario/CMDB, aggiornamenti e sicurezza, drift di configurazione |
| Definizione dei check | Catalogo integrato + playbook utente con contratto di risultato |
| Storico | Stato corrente, cambi di stato e serie temporali in SQLite, con downsampling |
| Strategia di scala | Intervalli lunghi (5-15 min), una connessione per host, sharding su più unit |
| Nodi gestiti | Indipendenti dal sistema operativo: Linux, Windows (solo via SSH), qualunque cosa supporti Ansible |
| Controller | Solo Debian 13 (systemd 257, Cockpit 337) |
| Priorità | Prima il runner, poi la raccolta dati |
| Funzioni di run | Survey, dry-run con diff, comandi ad-hoc, workflow, approvazioni |
| Nome | `cockpit-fleet`: pacchetto `cockpit-fleet`, etichetta di menu "Fleet", directory `/usr/share/cockpit/fleet` |
| Repository | un Gitea privato |
| Trigger esterni | Fuori dal perimetro iniziale, tenuti come sviluppo futuro |

Il monitoraggio non vuole rifare Icinga: niente tempo reale e niente nodi
mission-critical. L'obiettivo è una prospettiva storica sui dati raccolti e un
alert ragionevolmente tempestivo.

Stack frontend e convenzioni ripresi da `cockpit-samba-ad-dc`: TypeScript, React 18,
PatternFly 6, esbuild, test con `node --test`, i18next con `po/it.po`, `Makefile` +
`Makefile.local`, pacchetto `.deb`, licenza LGPL-2.1.

## 2. Punti aperti

Ognuno ha un default con cui il piano procede; vanno confermati prima della fase
indicata.

- [ ] **Schedulazione con un solo timer statico** (fase 4). In fase di analisi si era
      parlato di un timer systemd per ogni schedulazione. Il piano usa invece un solo
      `cockpit-fleet-scheduler.timer` che ogni minuto esegue `fleetctl scheduler tick`.
      Motivi: generare unit per-schedulazione vuol dire far scrivere a root file di
      unit derivati da input utente; a 200+ nodi servono comunque coda, limite di
      concorrenza e sharding che i timer singoli non danno. Le espressioni restano
      quelle di systemd (`OnCalendar`), validate con `systemd-analyze calendar`.
- [ ] **`ansible.cfg` del progetto** (fase 2). Default: non viene usato direttamente;
      la run parte con un `ANSIBLE_CONFIG` generato, in cui si importano dal file del
      progetto solo le chiavi in allowlist (`collections_path`, `roles_path`, opzioni
      di visualizzazione). `vault_password_file` diventa una credenziale.
- [ ] **Chi approva** (fase 9). Default: un utente con ruolo admin diverso da chi ha
      lanciato la run. Alternativa: gruppo dedicato `cockpit-fleet-approver`.
- [ ] **Cosa vede il viewer** (fase 1). Default: stato, storico e dati dei nodi sì;
      log completi delle run no, perché possono contenere dati sensibili.
- [ ] **Retention predefinita** (fase 8). Default: dati grezzi 14 giorni, aggregati
      orari 90 giorni, giornalieri 2 anni, log delle run 90 giorni.
- [ ] **Namespace della collection Ansible** (fase 2). Default: `lineadicomando.fleet`;
      `cockpit` non è un namespace nostro.
- [ ] **Nome dell'helper `fleetctl`** (fase 1). Sta in `/usr/libexec`, fuori dal
      `PATH`, ma è omonimo della CLI di FleetDM. Alternativa: `cockpit-fleetctl`.
- [ ] **RAM della VM controller**. `deb13-samba-dc` ha 2 GB; per i test di carico con
      molti fork Ansible conviene portarla a 4 GB.

## 3. Architettura

### 3.1 Componenti

```
Browser (React/PatternFly)
   │  cockpit.spawn(["sudo","-n","-u","cockpit-fleet","/usr/libexec/cockpit-fleet/fleetctl", …])
   ▼
fleetctl  (Python, utente cockpit-fleet)      ← unica API: sottocomandi chiusi, JSON su stdin/stdout
   │  SQLite /var/lib/cockpit-fleet/fleet.db (WAL)
   │  systemctl start cockpit-fleet-run@<id>.service   (consentito da regola polkit)
   ▼
cockpit-fleet-run@.service  (sandbox systemd, utente cockpit-fleet)
   │  ExecStartPre=+fleet-creds-prepare   (root: decifra solo i segreti della run)
   │  ExecStart=fleet-run                 (compone ed esegue la CLI Ansible)
   ▼
ansible-playbook / ansible-inventory / ansible-galaxy / ansible-vault
   │  callback plugin → eventi JSONL per task e per host
   ▼
ingest → SQLite → motore di stato → outbox → cockpit-fleet-notify.service
```

Unit statiche installate dal pacchetto, tutte di proprietà di root:

- `cockpit-fleet-run@.service`: una run di template, ad-hoc o shard di raccolta.
- `cockpit-fleet-prepare@.service`: clone/fetch git e `ansible-galaxy install`.
- `cockpit-fleet-scheduler.timer` + `.service`: tick ogni minuto.
- `cockpit-fleet-notify.service`: svuota l'outbox delle notifiche.
- `cockpit-fleet-maintenance.timer` + `.service`: retention, downsampling, vacuum.

### 3.2 Superficie privilegiata

Tre soli punti di ingresso, tutti con input validato e nessuna shell:

1. `fleetctl`, eseguito come `cockpit-fleet` via sudoers (`%cockpit-fleet-* ALL=(cockpit-fleet) NOPASSWD: …/fleetctl`).
   Ricava il chiamante da `SUDO_UID`, ne calcola il ruolo dai gruppi e registra
   ogni comando che modifica lo stato.
2. `fleet-secret`, eseguito come root via sudoers dai soli admin. Fa solo `set`,
   `delete` e `list` dei nomi: il valore entra da stdin e non è mai rileggibile.
3. `fleet-creds-prepare`, eseguito come root dalla unit della run (`ExecStartPre=+`).
   Legge la specifica della run e decifra nella directory runtime solo i segreti
   che quella run referenzia.

Regola polkit: l'utente `cockpit-fleet` può fare `start`/`stop` solo delle unit il
cui nome corrisponde a `cockpit-fleet-(run|prepare)@<id>.service`. Le unit
transitorie (`StartTransientUnit`) devono restare negate: permetterebbero un
`ExecStart` arbitrario come root.

### 3.3 Modello di minaccia

- Chi lancia un playbook è di fatto root sui nodi: per questo ruoli, approvazioni e audit.
- Un progetto è codice eseguito sul controller come `cockpit-fleet`: la sandbox della
  unit deve impedirgli di leggere i segreti di altri progetti e il database.
- I segreti non compaiono mai in argv, ambiente, log, eventi o risposte al frontend.
- La compromissione dell'account `cockpit-fleet` non deve dare root sul controller.
- Il frontend non passa mai argomenti liberi alla CLI Ansible.

### 3.4 Layout su disco

```
/usr/share/cockpit/fleet/                 frontend
/usr/libexec/cockpit-fleet/               fleetctl, fleet-run, fleet-secret, fleet-creds-prepare
/usr/lib/python3/dist-packages/cockpit_fleet/
/usr/share/cockpit-fleet/collections/     collection lineadicomando.fleet (callback, fleet_result, check)
/etc/cockpit-fleet/config.toml            limiti, percorsi locali ammessi, proxy
/etc/credstore.encrypted/cockpit-fleet/   segreti cifrati (root, 0700)
/var/lib/cockpit-fleet/fleet.db            stato e storico
/var/lib/cockpit-fleet/projects/<id>/     sorgenti e revisioni
/var/lib/cockpit-fleet/runs/<id>/         spec.json, events.jsonl, stdout.log
```

### 3.5 Struttura del repository

```
src/            frontend (app.tsx, components/, lib/, locales/)
backend/        pacchetto Python cockpit_fleet + eseguibili
collection/     ansible_collections/lineadicomando/fleet
system/         unit systemd, sysusers.d, tmpfiles.d, polkit, sudoers.d
debian/         packaging
test/           test TS (node --test) e mock di cockpit
backend/tests/  test Python
test/integration/  scenari sulla VM
po/ docs/ scripts/
```

## 4. Ambiente di test

- Controller: VM libvirt `deb13-samba-dc`, `maint@VM`, Cockpit su `:9090`.
- Nodi Windows via SSH (`ansible_shell_type: powershell`): `teacher` .10,
  `student01` .11, `student02` .12, dall'inventario
  `ansible-win_edulab/inventories/school`.
- Nodi Linux: container con sshd sulla VM controller e un clone di `debian13-base`.
- Carico: 200+ host simulati con alias di inventario che puntano agli stessi pochi
  container, per provare scheduler, sharding e volume del database.
- Deploy: `make vm-deploy` per il solo frontend, `make vm-deb-deploy` per tutto il
  resto (unit, helper e regole richiedono il pacchetto).

---

## Fase 0 — Fondamenta e spike

Obiettivo: repository avviato e le quattro incognite tecniche risolte prima di
scrivere il backend.

- [ ] `git init`, `.gitignore`, `LICENSE` (LGPL-2.1), `README.md`, `SECURITY.md`
- [ ] Scaffold frontend da `cockpit-samba-ad-dc`: `package.json`, `build.js`,
      `tsconfig.json`, `manifest.json`, `index.html`, i18n, mock di `cockpit` per i test
- [ ] `Makefile` e `Makefile.local` con `build`, `watch`, `check`, `vm-deploy`, `deb`, `vm-deb-deploy`
- [ ] Scheletro `debian/` che installa una pagina vuota e crea utente e gruppi (`sysusers.d`)
- [ ] CI GitHub Actions: `tsc --noEmit`, build, test TS, test Python, `ruff`, `lintian`
- [ ] **Spike S1, polkit**: `cockpit-fleet` avvia e ferma `cockpit-fleet-run@x.service`;
      verificare che `systemd-run` e ogni unit con altro nome vengano rifiutati
- [ ] **Spike S2, segreti**: `fleet-secret set` cifra con `systemd-creds`;
      `ExecStartPre=+` decifra nella directory runtime; dentro la sandbox lo store
      cifrato e gli altri segreti non sono leggibili
- [ ] **Spike S3, sandbox**: `ansible-playbook` funziona con `ProtectSystem=strict`,
      `ProtectHome`, `PrivateTmp`, `NoNewPrivileges`, `ReadWritePaths` limitato alla
      directory della run; SSH con ControlPersist e `sshpass` funzionano
- [ ] **Spike S4, Windows**: dal controller, `ansible.windows.setup` e un comando
      PowerShell sui tre nodi Windows via SSH; misurare il tempo per host
- [ ] **Spike S5, eventi**: callback plugin che scrive JSONL; misurare dimensione e
      costo con 200 host simulati
- [ ] Documentare gli esiti in `docs/architecture.md`

Fatto quando: il `.deb` si installa sulla VM, la pagina vuota compare in Cockpit e i
cinque spike hanno un esito scritto.

## Fase 1 — Nucleo del backend

- [ ] `fleetctl`: dispatcher di sottocomandi, input JSON da stdin, output JSON, codici di errore stabili
- [ ] Validatori comuni: identificatori, percorsi (niente `..`, niente symlink in uscita), pattern host, tag
- [ ] Ruoli: chiamante da `SUDO_UID`, gruppi con `getgrouplist`, ruolo minimo dichiarato per ogni comando
- [ ] Schema SQLite con migrazioni versionate, WAL, `busy_timeout`
- [ ] Audit: tabella + journal con campi strutturati (chi, cosa, quando, esito)
- [ ] `config.toml`: concorrenza massima, fork, percorsi locali ammessi, proxy in uscita
- [ ] `sudoers.d`, regola polkit, `tmpfiles.d`, unit statiche con hardening
- [ ] Frontend: `lib/fleet.ts` (wrapper tipizzato di `fleetctl`), gestione errori,
      rilevamento del ruolo, navigazione, stato vuoto, pagina "Verifica installazione"
- [ ] Test: unit Python su validatori, ruoli e migrazioni; unit TS sul wrapper

Fatto quando: un viewer, un operator e un admin vedono in Cockpit tre insiemi di
azioni diversi e ogni tentativo fuori ruolo è rifiutato dal backend e registrato.

## Fase 2 — Runner minimo

Obiettivo: lanciare un playbook da un progetto e seguirne il log.

- [ ] **Progetti, directory locale**: percorso ammesso solo se sotto le radici in `config.toml`
- [ ] **Progetti, git**: clone/fetch in `cockpit-fleet-prepare@`, ref fissabile, SHA
      registrato in ogni run; hook disabilitati, submodule spenti di default,
      protocollo `file` negato; deploy key o token come segreti
- [ ] `ANSIBLE_CONFIG` generato per run, con import in allowlist dal progetto
- [ ] **Inventari**: file del progetto o gestito dalla UI; `ansible-inventory --list`
      per mettere in cache host e gruppi; errori di parsing mostrati in UI
- [ ] **Credenziali**: chiave SSH, utente/password, become, vault; solo riferimenti
      nel database, valori via `fleet-secret`
- [ ] Passaggio dei segreti: `--private-key`, `--connection-password-file`,
      `--become-password-file`, `--vault-id` su file nella directory runtime
- [ ] **known_hosts**: file gestito, `StrictHostKeyChecking=yes`; flusso "scansiona,
      mostra impronta, conferma" riservato agli admin
- [ ] **Template**: playbook, inventario, credenziali, limit, tag, extra-vars (file JSON), fork, verbosità
- [ ] **`fleet-run`**: costruisce la riga di comando da `spec.json`, mai da input diretto
- [ ] **Callback plugin** `lineadicomando.fleet.events`: eventi JSONL, risultati troncati, `no_log` rispettato
- [ ] **Run**: stati `queued`, `running`, `successful`, `failed`, `canceled`; annullamento con `systemctl stop`
- [ ] UI: elenco e dettaglio di progetti, inventari, credenziali, template
- [ ] UI run: log live con colori ANSI, vista per host e per task, riepilogo `PLAY RECAP`, cronologia con filtri
- [ ] Test di integrazione: run su container Linux e sui nodi Windows

Fatto quando: un operator lancia un template dal browser, chiude la scheda, la
riapre e ritrova la run completata con log ed esito per host.

## Fase 3 — Runner completo

- [ ] **Survey**: campi tipizzati (testo, numero, scelta, booleano, segreto), default e validazione lato backend
- [ ] **Dry-run**: `--check --diff`, vista delle differenze per host
- [ ] **Editor nella UI**: progetto gestito come repo git locale, commit automatico
      con l'utente Cockpit come autore, `--syntax-check` al salvataggio, `ansible-lint` se installato
- [ ] **Upload archivio**: estrazione sicura (niente path traversal, symlink, device;
      limiti di dimensione e numero di file), revisione immutabile con sha256
- [ ] **Galaxy**: `requirements.yml` installato nel progetto, cache per hash del file
- [ ] **Comandi ad-hoc**: solo admin, allowlist dei moduli in `config.toml`, eseguiti
      come playbook generato di un solo task (argomenti in file, non in argv)
- [ ] **Inventari dinamici**: plugin di inventario YAML; script eseguibili spenti di default
- [ ] `ansible-doc` per l'aiuto sui moduli nell'editor e nell'ad-hoc
- [ ] Mascheramento dei segreti noti nei log salvati
- [ ] Esportazione e importazione della configurazione (senza segreti)

Fatto quando: il progetto `ansible-win_edulab` gira da cockpit-fleet senza modifiche
al repository, con il vault importato come credenziale.

## Fase 4 — Schedulazione e coda

- [ ] `cockpit-fleet-scheduler.timer` ogni minuto, `fleetctl scheduler tick`
- [ ] Schedulazioni con espressione `OnCalendar`, fuso orario, prossima esecuzione
      calcolata al salvataggio e dopo ogni scatto
- [ ] Import da sintassi cron a 5 campi; costruttore con preset nella UI
- [ ] Recupero delle esecuzioni perse a controller spento (configurabile per schedulazione)
- [ ] Coda con priorità e limite globale di run concorrenti; alla fine di una run parte la successiva
- [ ] Regola anti-sovrapposizione: salta, accoda o annulla la precedente
- [ ] Ritardo casuale configurabile per distribuire il carico
- [ ] UI: elenco schedulazioni, prossime esecuzioni, attiva/disattiva, "esegui ora"

Fatto quando: dieci schedulazioni allo stesso minuto rispettano il limite di
concorrenza e nessuna va persa dopo un riavvio del controller.

## Fase 5 — Notifiche

- [ ] Outbox in SQLite con ritentativi e backoff; `cockpit-fleet-notify.service` in sandbox con sola rete in uscita
- [ ] Canali: Telegram (token, chat id), webhook generico (template JSON), SMTP, Matrix; solo libreria standard Python
- [ ] Token e password come segreti; pulsante "invia prova"
- [ ] Regole: tipo di evento × ambito (progetto, template, gruppo di host) × canale
- [ ] Eventi del runner: run riuscita, fallita, con modifiche, annullata, approvazione richiesta
- [ ] Modelli di messaggio in italiano e inglese, con link alla pagina Cockpit
- [ ] Raggruppamento per finestra temporale e limite di frequenza, per evitare raffiche
- [ ] Supporto al proxy in uscita

Fatto quando: una run fallita arriva sui quattro canali e un canale irraggiungibile
viene ritentato senza bloccare gli altri.

## Fase 6 — Raccolta dati e check

Obiettivo: stato di salute della flotta, indipendente dal sistema operativo.

- [ ] **Registro host**: derivato dagli inventari, identità `(inventario, hostname)`,
      piattaforma rilevata dai facts (`os_family`, `ansible_connection`, `ansible_network_os`)
- [ ] **Contratto di risultato**: `check`, `state` (0-3), `summary`, `metrics[]`
      (`name`, `value`, `unit`, `min`, `max`), `details`
- [ ] Modulo `lineadicomando.fleet.fleet_result` per i check scritti dall'utente
- [ ] **Catalogo**, con implementazione per piattaforma e stesso schema in uscita:
  - [ ] raggiungibilità e tempo di risposta
  - [ ] spazio disco e inode
  - [ ] carico CPU e memoria
  - [ ] servizi (systemd, servizi Windows)
  - [ ] uptime e riavvio richiesto
  - [ ] scadenza certificati
  - [ ] sincronizzazione dell'orologio
- [ ] Soglie valutate sul controller in fase di ingest, modificabili senza nuova raccolta
- [ ] Assegnazione dei check a gruppi di inventario, con intervallo e parametri
- [ ] **Pianificazione**: check in scadenza raggruppati per host, host divisi in
      shard, una run per shard, `strategy: free`, timeout per task
- [ ] **Motore di stato**: stato soft/hard dopo N tentativi, host irraggiungibile che
      sopprime gli alert dei suoi check, rilevamento del flapping
- [ ] Presa in carico (acknowledge) e finestre di manutenzione
- [ ] Notifiche su cambio di stato, ripristino e promemoria periodico
- [ ] UI: panoramica con contatori, elenco problemi, griglia host × check con filtri
      e paginazione, dettaglio host con cronologia degli stati

Fatto quando: lo stesso check "spazio disco" dà risultati confrontabili su Debian e
su Windows, e riempire un disco di test produce una notifica entro due intervalli.

## Fase 7 — Inventario, aggiornamenti, drift

- [ ] **CMDB**: `setup` e facts di pacchetti e servizi; ultimo snapshot per host,
      colonne normalizzate (OS, versione, kernel, CPU, RAM, dischi, IP, seriale)
- [ ] Storico delle variazioni sui campi significativi
- [ ] UI CMDB: tabella filtrabile, dettaglio host, distribuzione per OS e versione, esportazione CSV/JSON
- [ ] **Aggiornamenti**: apt, dnf, zypper, Windows Update in sola ricerca; distinzione
      degli aggiornamenti di sicurezza; vista di flotta
- [ ] Collegamento da "aggiornamenti pendenti" a un template che li applica
- [ ] **Drift**: template marcato "conformità", eseguito in `--check --diff` a
      intervalli; host non conforme se risultano modifiche; diff conservato e consultabile
- [ ] Notifiche: nuovi aggiornamenti di sicurezza, host non conforme, variazione hardware

Fatto quando: la CMDB mostra insieme nodi Linux e Windows e una modifica manuale a
un file gestito compare come drift al ciclo successivo.

## Fase 8 — Serie temporali e scala

- [ ] Tabella metriche stretta, aggregati orari e giornalieri, retention configurabile
- [ ] Job di manutenzione: downsampling, cancellazione, vacuum incrementale
- [ ] Grafici (`@patternfly/react-charts`): metrica per host, confronto tra host, andamento dei problemi
- [ ] Dimensionamento degli shard e dei fork in base a durata misurata e RAM disponibile
- [ ] Fact caching e riuso delle connessioni SSH tra cicli
- [ ] Protezione dal sovraccarico: se un ciclo non finisce prima del successivo, si salta e si segnala
- [ ] Pagina "Salute del sistema": durata dei cicli, coda, dimensione del database
- [ ] Test di carico con 200, 500 e 1000 host simulati; limiti misurati scritti nella documentazione

Fatto quando: 500 host simulati con 10 check ogni 10 minuti girano per 24 ore senza
arretrato e con la UI reattiva.

## Fase 9 — Workflow e approvazioni

- [ ] **Approvazioni**: template marcato critico, run in `pending_approval`, conferma
      di un secondo utente autorizzato, scadenza, notifica, traccia nell'audit
- [ ] **Workflow**: grafo di template con archi su successo, fallimento, sempre
- [ ] Passaggio di variabili tra i passi (`set_stats`)
- [ ] Editor del grafo e vista dell'esecuzione nella UI
- [ ] Annullamento e ripresa da un passo fallito

Fatto quando: un workflow di tre passi con approvazione al secondo si ferma, viene
approvato da un altro utente e termina.

## Fase 10 — Rilascio 0.1.0

- [ ] Revisione di sicurezza completa: superficie privilegiata, sandbox, sudoers, polkit, estrazione archivi, git
- [ ] Test negativi automatizzati: injection negli argomenti, path traversal, escalation di ruolo, lettura dei segreti
- [ ] Accessibilità e tema scuro
- [ ] Traduzione italiana completa
- [ ] Documentazione: installazione, modello di sicurezza, scrittura di un check, backup e ripristino
- [ ] `lintian` pulito, upgrade e rimozione del pacchetto provati (`purge` compreso)
- [ ] Screenshot, `CHANGELOG`, tag `v0.1.0`

## Sviluppi futuri

- Trigger esterni: webhook git, avvio di un template da un cambio di stato (auto-remediation)
- Modalità pull opzionale (`ansible-pull`) per flotte molto grandi
- Backend di segreti esterni (OpenBao, Bitwarden)
- Esportazione delle metriche verso Prometheus o InfluxDB
- Execution environment in container
- Controller su altre distribuzioni
