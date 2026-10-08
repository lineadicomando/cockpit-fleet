# cockpit-fleet — piano

Plugin Cockpit che esegue Ansible dal browser (stile Semaphore UI) e che raccoglie,
storicizza e notifica dati dai nodi usando solo Ansible. Non è un sostituto di
Icinga: niente tempo reale, alert "ragionevolmente tempestivi".

## Stato

Fasi 0, 1, 2 concluse l'8 ottobre 2026. **Prossima: fase 3.**
`main` è allineato con `origin`; la CI su Gitea è verde.

Per riprendere: leggere `CLAUDE.md` (regole, comandi, ambiente di test), poi
`docs/architecture.md` (cosa esiste e perché). Questo file dice solo cosa manca.

Cosa funziona oggi: progetti (git o directory locale), inventari, credenziali con
segreti cifrati, chiavi host attendibili, template, run con coda, annullamento,
output live, vista per task ed esito per host; ruoli da gruppi Unix e audit.
Provato su nodi Linux (container) e Windows via SSH.

Stato della VM di test `deb13-samba-dc`: pacchetto `cockpit-fleet` installato, con
`ansible-core`, `sshpass`, `podman`. Resta il segreto `it-win-key` (chiave del
controller, autorizzata su `win11-pc00`). Nessun dato o account di prova.

## Decisioni

| Tema | Decisione |
|---|---|
| Nome | `cockpit-fleet`, menu "Fleet", collection `lineadicomando.fleet` |
| Licenza | `LGPL-2.1-or-later` (come `cockpit-samba-ad-dc`), da riconfermare alla pubblicazione |
| Repository | un Gitea privato; GitHub quando sarà stabile |
| Backend | Nessun demone né porta: unit systemd avviate a richiesta |
| Identità | Account di sistema `cockpit-fleet`; root solo in `fleet-unit` e `fleet-secret` |
| Ruoli | Gruppi `cockpit-fleet-admin`, `-operator`, `-viewer` |
| Viewer | Vede stato ed esito per host delle run, non l'output |
| Operator | Lancia template; può restringere gli host e chiedere la prova a vuoto, non allargare il limite né cambiare le variabili |
| Segreti | `systemd-creds`; solo PID 1 li decifra, per la singola run |
| Ansible | Pacchetto Debian `ansible-core`; collection extra nel progetto |
| `ansible.cfg` del progetto | Non usato direttamente: passano solo le chiavi in allowlist |
| Sorgenti dei progetti | Git, directory locale (fatte); editor nella UI, upload (fase 3) |
| Notifiche | Telegram, webhook generico, SMTP, Matrix |
| Dati dai nodi | Check di salute, CMDB, aggiornamenti, drift |
| Check | Catalogo integrato + playbook utente con contratto di risultato |
| Storico | SQLite: stato, cambi di stato, serie temporali con downsampling |
| Scala | Oltre 200 nodi: intervalli 5-15 min, una connessione per host, sharding |
| Nodi gestiti | Qualunque cosa supporti Ansible; Windows solo via SSH |
| Controller | Solo Debian 13 |
| Trigger esterni | Fuori perimetro, sviluppo futuro |

## Punti aperti (default applicabile, da confermare prima della fase indicata)

- **Fase 4, schedulazione**: un solo timer statico che ogni minuto esegue
  `fleetctl scheduler tick`, non un timer per schedulazione (root non deve scrivere
  unit da input utente; coda e sharding servono comunque). Espressioni `OnCalendar`.
- **Fase 8, retention**: grezzi 14 giorni, orari 90 giorni, giornalieri 2 anni, log
  delle run 90 giorni.
- **Fase 9, chi approva**: un admin diverso da chi lancia; alternativa, gruppo
  `cockpit-fleet-approver`.
- **Nome di `fleetctl`**: omonimo della CLI di FleetDM (è in `/usr/libexec`, fuori
  dal `PATH`). Alternativa: `cockpit-fleetctl`.

## Residui delle fasi concluse

- Token git su https mai provato contro un repository privato reale (serve un
  token dedicato); https pubblico e ssh con chiave sono provati.
- Le directory delle run (`stdout.log`, `events.jsonl`) non vengono mai cancellate:
  la pulizia arriva con la retention (fase 8).
- Chi entra con l'accesso amministrativo di Cockpit senza essere in un gruppo
  compare nell'audit come `root`.
- I test di integrazione girano solo a mano sulla VM, non in CI.
- Un inventario ha una sola credenziale vault (i template ne hanno più d'una).

## Fase 3 — Runner completo

- [ ] **Survey**: campi tipizzati (testo, numero, scelta, booleano, segreto), default,
      validazione nel backend; è il modo in cui un operator potrà dare variabili
- [ ] **Vista delle differenze** per host nella prova a vuoto (`--check --diff` e il
      diff negli eventi esistono già)
- [ ] **Editor nella UI**: progetto come repo git locale, commit automatico con
      l'utente Cockpit come autore, `--syntax-check` al salvataggio, `ansible-lint` se c'è
- [ ] **Upload archivio**: estrazione sicura (path traversal, symlink, device, limiti),
      revisione immutabile con sha256
- [ ] **Galaxy**: `requirements.yml` installato nel progetto in un job sandbox, cache
      per hash. Oggi `ansible.windows` va messa a mano nel progetto
- [ ] **Comandi ad-hoc**: solo admin, allowlist dei moduli in `config.toml`, eseguiti
      come playbook generato di un task (argomenti in file, non in argv)
- [ ] **Inventari dinamici**: plugin YAML (da provare, il job è già in sandbox);
      script eseguibili spenti di default
- [ ] `ansible-doc` per l'aiuto sui moduli
- [ ] Mascheramento dei segreti noti nei log salvati
- [ ] Esportazione e importazione della configurazione, senza segreti

Fatto quando: `ansible-win_edulab` gira da cockpit-fleet senza modifiche al
repository, con il suo vault come credenziale.

## Fase 4 — Schedulazione

Il limite di run concorrenti e l'avvio della successiva in coda esistono già.

- [ ] `cockpit-fleet-scheduler.timer` + `fleetctl scheduler tick`
- [ ] Schedulazioni `OnCalendar` con fuso orario; prossima esecuzione calcolata al
      salvataggio e a ogni scatto (`systemd-analyze calendar`)
- [ ] Import da cron a 5 campi; costruttore con preset nella UI
- [ ] Recupero delle esecuzioni perse a controller spento, per schedulazione
- [ ] Priorità nella coda; anti-sovrapposizione (salta, accoda, annulla la precedente)
- [ ] Ritardo casuale per distribuire il carico
- [ ] UI: elenco, prossime esecuzioni, attiva/disattiva, "esegui ora"

Fatto quando: dieci schedulazioni allo stesso minuto rispettano il limite di
concorrenza e nessuna va persa dopo un riavvio.

## Fase 5 — Notifiche

- [ ] Outbox in SQLite con ritentativi e backoff; `cockpit-fleet-notify.service` in
      sandbox con sola rete in uscita
- [ ] Canali Telegram, webhook (template JSON), SMTP, Matrix; solo libreria standard
- [ ] Token come segreti; pulsante "invia prova"; supporto al proxy
- [ ] Regole: tipo di evento × ambito (progetto, template, gruppo) × canale
- [ ] Eventi: run riuscita, fallita, con modifiche, annullata, approvazione richiesta
- [ ] Messaggi in italiano e inglese con link alla pagina Cockpit
- [ ] Raggruppamento per finestra temporale e limite di frequenza

Fatto quando: una run fallita arriva sui quattro canali e un canale irraggiungibile
viene ritentato senza bloccare gli altri.

## Fase 6 — Raccolta dati e check

- [ ] **Registro host** dagli inventari, identità `(inventario, hostname)`, piattaforma
      dai facts
- [ ] **Contratto di risultato**: `check`, `state` (0-3), `summary`, `metrics[]`
      (`name`, `value`, `unit`, `min`, `max`), `details`; modulo
      `lineadicomando.fleet.fleet_result` per i check dell'utente
- [ ] **Catalogo** con una implementazione per piattaforma e uno schema solo:
      raggiungibilità, disco e inode, CPU e memoria, servizi, uptime e riavvio
      richiesto, scadenza certificati, orologio
- [ ] **Vincolo misurato**: su Windows ogni task costa 4-5 s. Tutti i check di un nodo
      in un solo task con un solo script
- [ ] Soglie valutate sul controller all'ingest, modificabili senza nuova raccolta
- [ ] Assegnazione dei check a gruppi, con intervallo e parametri
- [ ] **Pianificazione**: check in scadenza raggruppati per host, host in shard, una
      run per shard, `strategy: free`, timeout per task
- [ ] **Motore di stato**: soft/hard dopo N tentativi, host irraggiungibile che
      sopprime gli alert dei suoi check, flapping
- [ ] Presa in carico e finestre di manutenzione
- [ ] Notifiche su cambio di stato, ripristino, promemoria
- [ ] UI: contatori, elenco problemi, griglia host × check, dettaglio host

Fatto quando: "spazio disco" dà risultati confrontabili su Debian e Windows, e un
disco di test pieno produce una notifica entro due intervalli.

## Fase 7 — Inventario, aggiornamenti, drift

- [ ] **CMDB**: facts, pacchetti, servizi; ultimo snapshot per host, colonne
      normalizzate, storico delle variazioni
- [ ] UI CMDB: tabella filtrabile, dettaglio host, distribuzione per OS, export CSV/JSON
- [ ] **Aggiornamenti**: apt, dnf, zypper, Windows Update in sola ricerca; quelli di
      sicurezza distinti; collegamento a un template che li applica
- [ ] **Drift**: template "conformità" in `--check --diff` a intervalli; diff conservato
- [ ] Notifiche: aggiornamenti di sicurezza, host non conforme, variazione hardware

Fatto quando: la CMDB mostra insieme Linux e Windows e una modifica manuale a un
file gestito compare come drift al ciclo successivo.

## Fase 8 — Serie temporali e scala

- [ ] Tabella metriche stretta, aggregati orari e giornalieri, retention
- [ ] Manutenzione: downsampling, cancellazione (anche delle directory delle run), vacuum
- [ ] Grafici (`@patternfly/react-charts`)
- [ ] Shard e fork dimensionati su durata misurata e RAM
- [ ] Fact caching, riuso delle connessioni SSH tra cicli
- [ ] Un ciclo che non finisce prima del successivo viene saltato e segnalato
- [ ] Pagina "Salute del sistema": durata dei cicli, coda, dimensione del database
- [ ] Test di carico con 200, 500, 1000 host simulati. **Vincolo misurato**: circa
      0,6 s per host su 2 vCPU, il limite è la CPU del controller. La VM ha 2 GB:
      portarla a 4 per questi test

Fatto quando: 500 host simulati con 10 check ogni 10 minuti girano 24 ore senza
arretrato e con la UI reattiva.

## Fase 9 — Workflow e approvazioni

- [ ] **Approvazioni**: template critico, run in `pending_approval`, conferma di un
      secondo utente, scadenza, notifica, audit
- [ ] **Workflow**: grafo di template con archi su successo, fallimento, sempre;
      variabili tra i passi (`set_stats`); editor e vista dell'esecuzione
- [ ] Annullamento e ripresa da un passo fallito

Fatto quando: un workflow di tre passi con approvazione al secondo si ferma, viene
approvato da un altro utente e termina.

## Fase 10 — Rilascio 0.1.0

- [ ] Revisione di sicurezza: helper root, sandbox, sudoers, estrazione archivi, git
- [ ] Test negativi automatizzati: injection, path traversal, escalation di ruolo,
      lettura dei segreti (molti esistono già in `test/integration/`)
- [ ] Accessibilità; traduzione italiana completa
- [ ] Documentazione: installazione, modello di sicurezza, scrivere un check, backup
- [ ] Upgrade e rimozione del pacchetto provati, `purge` compreso
- [ ] Screenshot, `CHANGELOG`, tag `v0.1.0`
- [ ] Workflow per GitHub Actions (l'attuale è solo per Gitea)
- [ ] Pubblicazione su GitHub: licenza riconfermata, cronologia senza segreti né
      dettagli di ambienti reali

## Sviluppi futuri

Trigger esterni (webhook git, auto-remediation su cambio di stato); modalità pull
(`ansible-pull`) per flotte molto grandi; segreti su backend esterni (OpenBao,
Bitwarden); export verso Prometheus o InfluxDB; execution environment in container;
controller su altre distribuzioni.
