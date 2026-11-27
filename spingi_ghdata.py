#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spingi_ghdata.py - Pubblica su gh-data anche quando un altro job ha appena
pubblicato (27/11).

IL PROBLEMA. Di notte i job di statarea.yml (statarea, xgaura, soccersite,
ratingbet, betimate, footymetrics, asianodds, webpronostici, sportsmole)
clonano gh-data, scrivono i loro file e pubblicano quasi insieme. Chi arriva
secondo deve rimettere il suo commit sopra quello dell'altro (git pull
--rebase). Ma alcuni file li scrivono in due: gli archivi CSV delle fonti
(xgaura_archivio.csv e gli altri li aggiorna anche il job footymetrics), i
file FootyMetrics (anche il job principale). Sulle stesse righe il rebase si
ferma con un conflitto, il vecchio ciclo lo annullava in silenzio e il push
veniva rifiutato quattro volte su quattro ("non-fast-forward"): il 27/11 sono
falliti cosi' betimate e footymetrics.

COSA FA, dentro il clone di gh-data, dopo il commit del job:
  1. archivi CSV: in caso di conflitto si tengono le righe di tutti e due
     (merge=union, sono archivi a cui si aggiungono righe);
  2. tutti gli altri file: in caso di conflitto vince la versione di questo
     job, che li ha appena rifatti (rebase -X theirs);
  3. se il rebase si ferma lo stesso (un file cancellato da una parte e
     cambiato dall'altra, o il ramo riscritto): si riparte dal ramo com'e'
     adesso e ci si rimettono sopra i file di questo job - i CSV uniti riga
     per riga, gli altri sostituiti, i cancellati cancellati;
  4. push; se non va (GitHub occupato, un altro push nel frattempo), di nuovo
     da capo, fino a TENTATIVI volte, con una pausa che cresce.
Stampa sempre il motivo di ogni passo non riuscito.

Uso (dentro il clone, remote "origin" gia' impostato, commit gia' fatto):
    python spingi_ghdata.py gh-data
Esce con 0 se ha pubblicato (o non c'era niente da pubblicare), 1 se no.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

TENTATIVI = 5


def git(*args, ok=False):
    r = subprocess.run(["git"] + list(args), capture_output=True, text=True)
    if r.returncode != 0 and not ok:
        msg = (r.stderr or r.stdout or "").strip().splitlines()
        print("   git {}: {}".format(args[0], " | ".join(msg[-3:]) if msg else "errore"))
    return r


def radice():
    """Il commit da cui si e' partiti: nel clone a profondita' 1 e' l'unico
    senza genitori visibili."""
    r = git("rev-list", "--max-parents=0", "HEAD")
    righe = r.stdout.split()
    return righe[-1] if righe else None


def unisci_csv(remoto, nostro):
    """Righe del ramo, poi quelle nostre che il ramo non ha; l'intestazione
    una volta sola."""
    try:
        with open(remoto, encoding="utf-8", newline="") as f:
            a = f.read().splitlines()
    except OSError:
        a = []
    with open(nostro, encoding="utf-8", newline="") as f:
        b = f.read().splitlines()
    visti = set(a)
    return "\n".join(a + [x for x in b if x not in visti]) + "\n"


def rifai_sopra(ramo, base):
    """Riparte dal ramo com'e' adesso e ci rimette i file di questo job."""
    cambi = git("diff", "--name-status", "--no-renames", base, "HEAD").stdout.splitlines()
    msg = git("log", "-1", "--format=%s").stdout.strip() or "Update"
    salvati = tempfile.mkdtemp()
    nostri, via = [], []
    for riga in cambi:
        pezzi = riga.split("\t")
        if len(pezzi) < 2:
            continue
        stato, nome = pezzi[0], pezzi[-1]
        if stato.startswith("D"):
            via.append(nome)
        elif os.path.isfile(nome):
            dest = os.path.join(salvati, nome)
            os.makedirs(os.path.dirname(dest) or salvati, exist_ok=True)
            shutil.copy2(nome, dest)
            nostri.append(nome)
    if git("reset", "-q", "--hard", "FETCH_HEAD").returncode != 0:
        return False
    for nome in nostri:
        src = os.path.join(salvati, nome)
        if nome.lower().endswith(".csv") and os.path.exists(nome):
            testo = unisci_csv(nome, src)
            with open(nome, "w", encoding="utf-8", newline="") as f:
                f.write(testo)
        else:
            os.makedirs(os.path.dirname(nome) or ".", exist_ok=True)
            shutil.copy2(src, nome)
    for nome in via:
        if os.path.exists(nome):
            os.remove(nome)
    shutil.rmtree(salvati, ignore_errors=True)
    git("add", "-A")
    if git("diff", "--cached", "--quiet", ok=True).returncode == 0:
        print("   rifatto sopra: niente di nuovo rispetto al ramo")
        return True
    return git("commit", "-q", "-m", msg + " (rifatto sopra)").returncode == 0


def main():
    ramo = sys.argv[1] if len(sys.argv) > 1 else "gh-data"
    # i CSV d'archivio si uniscono riga per riga invece di fermare il rebase
    attr = os.path.join(".git", "info", "attributes")
    os.makedirs(os.path.dirname(attr), exist_ok=True)
    with open(attr, "a", encoding="utf-8") as f:
        f.write("*.csv merge=union\n")
    base = radice()
    for t in range(1, TENTATIVI + 1):
        if git("fetch", "-q", "origin", ramo).returncode == 0:
            r = git("rebase", "-q", "-X", "theirs", "FETCH_HEAD", ok=True)
            if r.returncode != 0:
                righe = (r.stderr or r.stdout or "").strip().splitlines()
                print("   rebase fermo ({}): riparto dal ramo e rimetto sopra i file".format(
                    righe[-1] if righe else "conflitto"))
                git("rebase", "--abort", ok=True)
                if not base or not rifai_sopra(ramo, base):
                    print("   non sono riuscito a rimettere sopra i file")
            # da qui in poi i commit di questo job sono quelli sopra il ramo letto ora
            base = git("rev-parse", "FETCH_HEAD").stdout.strip() or base
        p = git("push", "-q", "origin", "HEAD:" + ramo)
        if p.returncode == 0:
            print("Pubblicato su {}{}.".format(ramo, "" if t == 1 else " al tentativo {}".format(t)))
            return 0
        if t < TENTATIVI:
            attesa = 15 * t
            print("Push non riuscito (tentativo {}/{}): riprovo fra {} secondi.".format(t, TENTATIVI, attesa))
            time.sleep(attesa)
    print("::error::Push su {} non riuscito dopo {} tentativi.".format(ramo, TENTATIVI))
    return 1


if __name__ == "__main__":
    sys.exit(main())
