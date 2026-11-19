# -*- coding: utf-8 -*-
"""
prezzi_equi.py - Il prezzo giusto di ogni giocata, dal mercato.

    python prezzi_equi.py --dati ghdata --xlsx output --kodi ghdata

A COSA SERVE
Non sceglie le partite: calcola quanto VALE ogni selezione, cosi' il
confronto con la quota del bookmaker si fa prima di giocare invece che
dopo. Se Sisal paga piu' della quota equa stai prendendo valore, se paga
meno stai pagando margine, e la differenza si legge in un colpo d'occhio.

PERCHE' IL PREZZO LO FA IL MERCATO E NON LE FONTI
Misurato sul nostro archivio: la media delle fonti perde contro la linea
di chiusura de-viggata, Brier 0.6004 contro 0.5755, differenza +0.0249
con errore standard 0.0092 su 230 partite - t = +2.71, fuori dal rumore.
Partire dalle percentuali dei siti vorrebbe dire partire dalla stima
peggiore. Qui si parte da P1/PX/P2 Shin e dalla linea O/U di AsianOdds.

COME NASCE LA PROBABILITA' CONGIUNTA
Dalla linea O/U si ricava il totale di gol atteso; da P1 Shin lo si
divide fra casa e ospite; poi si somma la griglia dei risultati. Il
passaggio per la griglia serve perche' esito e gol sono CORRELATI: se
vince il favorito e' piu' probabile che ci siano gol, e moltiplicare le
due probabilita' come se fossero indipendenti sbaglia - di solito in
difetto, quindi facendo sembrare la combo peggiore di com'e'.

QUANTO CI SI PUO' FIDARE, MISURATO SU 563 PARTITE FINITE
  Gol/NoGol : tarato. Nella fascia 55-65%, su 238 partite, previsto
              60.0% e uscito 60.1%. E' il mercato piu' affidabile.
  Over 2.5  : il modello lo sottostima. Previsto 56.6%, uscito 61.3%,
              scarto +4.6 con errore standard 2.1 (t = 2.2). I gol veri
              hanno superato quelli attesi dalla linea di +0.18 a
              partita, a TUTTE le linee dal 2.25 al 3.5. Non si sa
              ancora se sia un mese ricco di gol, la sovradispersione
              dei totali rispetto a Poisson, o un effetto di come si
              incrociano gli archivi: 563 partite non bastano a
              separarle. La direzione pero' e' una sola, e va detta: le
              probabilita' di Over qui sotto sono semmai BASSE, non alte.
  1X e X2   : le meno affidabili. Poisson indipendente sottostima i
              pareggi, quindi le doppie chance che includono la X
              risultano un po' sotto il vero e la loro quota equa un po'
              troppo generosa.

IL MARGINE E' LA COLONNA DELLA FIDUCIA, non un dettaglio. Dove AsianOdds
quota con l'1.7% il prezzo e' stretto e la de-vig e' solida; al 7% lo
stesso numero balla. Le righe si ordinano per probabilita', ma si
leggono guardando prima quella colonna.
"""

import argparse
import glob
import json
import math
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
try:
    from zoneinfo import ZoneInfo
    _ROMA = ZoneInfo("Europe/Rome")
except Exception:            # senza tzdata: l'ora di Roma si approssima con UTC+1
    _ROMA = timezone(timedelta(hours=1))

# Campionati che Max considera principali. NON e' un filtro di qualita'
# del prezzo - per quello c'e' il margine - serve solo a poter guardare
# prima quelli che conosce, quando le righe sono qualche migliaio.
PRINCIPALI = {
    "England: Premier League", "England: Championship", "Italy: Serie A",
    "Italy: Serie B", "Spain: Laliga", "Spain: Laliga2",
    "Germany: Bundesliga", "Germany: Bundesliga 2", "France: Ligue 1",
    "France: Ligue 2", "Netherlands: Eredivisie", "Portugal: Liga Portugal",
    "Belgium: Jupiler Pro League", "Scotland: Premiership",
    "Turkey: Super Lig", "Greece: Super League", "Denmark: Superliga",
    "Norway: Eliteserien", "Sweden: Allsvenskan", "Switzerland: Super League",
    "Austria: Bundesliga", "Japan: J1 League", "Usa: Mls",
    "Europe: Uefa Champions League", "Europe: Uefa Europa League",
    "Europe: Uefa Conference League",
}

# Fascia in cui una combo ha senso guardarla. Sotto il 35% la quota e'
# alta ma la si perde quasi sempre; sopra l'80% la quota non copre il
# margine. Fuori di qui le righe sarebbero migliaia e nessuna utile.
BANDA = (0.35, 0.80)

COMBO = [
    ("1 + Over 2.5",  lambda a, b: a > b and a + b > 2.5),
    ("1 + Under 2.5", lambda a, b: a > b and a + b < 2.5),
    ("2 + Over 2.5",  lambda a, b: a < b and a + b > 2.5),
    ("2 + Under 2.5", lambda a, b: a < b and a + b < 2.5),
    ("1X + Over 2.5", lambda a, b: a >= b and a + b > 2.5),
    ("X2 + Over 2.5", lambda a, b: a <= b and a + b > 2.5),
    ("1 + Over 1.5",  lambda a, b: a > b and a + b > 1.5),
    ("2 + Over 1.5",  lambda a, b: a < b and a + b > 1.5),
    ("1 + Gol",       lambda a, b: a > b and a >= 1 and b >= 1),
    ("2 + Gol",       lambda a, b: a < b and a >= 1 and b >= 1),
    # CON IL NOGOL E CON L'UNDER IN DOPPIA, aggiunte il 29 ottobre: mancavano,
    # e sono giocate comuni ("vittoria a zero", "1X Under"). Su un favorito
    # netto o in una partita da pochi gol stanno spesso nella BANDA.
    # 1X + NoGol vuol dire "l'ospite non segna", X2 + NoGol "la casa non
    # segna": la X con NoGol e' solo lo 0-0.
    ("1 + NoGol",      lambda a, b: a > b and b == 0),
    ("2 + NoGol",      lambda a, b: a < b and a == 0),
    ("1X + NoGol",     lambda a, b: a >= b and (a == 0 or b == 0)),
    ("X2 + NoGol",     lambda a, b: a <= b and (a == 0 or b == 0)),
    ("1X + Under 2.5", lambda a, b: a >= b and a + b < 2.5),
    ("X2 + Under 2.5", lambda a, b: a <= b and a + b < 2.5),
    # (19/11) chieste da Max: la doppia con l'Over 1.5 e con l'Under 4.5,
    # le giocate "di copertura" piu' comuni. Con la loro speculare X2, come
    # tutte le altre. Valgono le stesse regole: si vedono solo dentro la
    # BANDA (35-80%), quindi su un favorito netto l'1X + Under 4.5, che
    # supera spesso l'80%, puo' non comparire.
    ("1X + Over 1.5",  lambda a, b: a >= b and a + b > 1.5),
    ("X2 + Over 1.5",  lambda a, b: a <= b and a + b > 1.5),
    ("1X + Under 4.5", lambda a, b: a >= b and a + b < 4.5),
    ("X2 + Under 4.5", lambda a, b: a <= b and a + b < 4.5),
]

# ---------------------------------------------------------------- schedina
# I campionati che i concessionari italiani offrono in modo affidabile.
#
# QUESTA LISTA E' CURATA A MANO E NON VERIFICATA SUI SITI. Non c'e' modo
# di leggere il palinsesto di Sisal o Lottomatica da qui: sono i tornei
# che quei due offrono di norma, non un elenco certificato. Se ne manca
# uno che giochi di solito, si aggiunge una riga qui sotto - e se uno non
# c'e' sulla schedina, si toglie. E' l'unico punto da modificare.
#
# Restano FUORI di proposito femminili, riserve e giovanili: la copertura
# dei concessionari li' e' a macchia di leopardo e una riga che non si
# puo' giocare in una lista di giocabili e' peggio di una riga assente.
#
# Il nome si confronta normalizzato perche' le due fonti lo scrivono
# diverso: AsianOdds "Italy: Serie A", FootyMetrics "Italy Serie A".
SISAL = {
    # Italia
    "italy serie a", "italy serie b", "italy serie c", "italy coppa italia",
    # Inghilterra
    "england premier league", "england championship", "england league one",
    "england league two", "england carabao cup", "england fa cup",
    "england enterprise national league", "england national league",
    # Spagna
    "spain laliga", "spain laliga2", "spain la liga", "spain la liga 2",
    "spain copa del rey",
    # Germania
    "germany bundesliga", "germany 2 bundesliga", "germany dfb pokal",
    # Francia
    "france ligue 1", "france ligue 2", "france coupe de france",
    # resto d'Europa, prime divisioni
    "netherlands eredivisie", "netherlands eerste divisie",
    "portugal liga portugal", "portugal liga portugal 2",
    "belgium jupiler pro league", "belgium pro league",
    "scotland premiership", "scotland championship",
    "turkey super lig", "turkey 1 lig", "greece super league",
    "switzerland super league", "austria bundesliga",
    "denmark superliga", "norway eliteserien", "norway obos ligaen",
    "sweden allsvenskan", "sweden superettan", "finland veikkausliiga",
    "poland ekstraklasa", "czech republic chance liga", "croatia 1 hnl",
    "romania superliga", "bulgaria first league", "serbia superliga",
    "russia premier league", "ukraine premier league",
    "slovakia nike liga", "cyprus cyprus league", "cyprus league",
    "hungary nb i", "israel ligat haal",
    # coppe europee
    "europe uefa champions league", "europe uefa europa league",
    "europe uefa conference league", "europe champions league",
    "europe europa league", "europe conference league",
    # fuori Europa
    "brazil serie a", "brazil serie b",
    "argentina liga profesional", "argentina liga profesional de futbol",
    "argentina liga profesional clausura",
    "mexico liga mx", "mexico liga mx apertura",
    "united states major league soccer", "usa major league soccer",
    "usa mls", "japan j1 league", "japan j2 league",
    "south korea k league 1", "saudi arabia pro league",
    "uruguay primera division", "colombia primera a",
    "colombia primera a clausura", "peru liga 1", "peru liga 1 clausura",
    "chile primera division", "ecuador liga pro",
    "ecuador liga pro championship group", "australia a league",
    # (14/11) I CAMPIONATI DELLA SEZIONE "Campionati" DEL SITO: Max li gioca.
    # Scritti come li scrive Pinnacle (API-Football), senza accenti.
    "albania superliga", "argentina liga profesional argentina",
    "austria 2 liga", "belarus premier league", "croatia hnl",
    "cyprus 1 division", "czech republic czech liga", "czech republic fnl",
    "denmark 1 division", "england national league north",
    "england national league south", "france national 1", "germany 3 liga",
    "greece super league 1", "israel ligat ha al", "italy serie c girone a",
    "italy serie c girone b", "italy serie c girone c",
    "italy serie d girone a", "italy serie d girone b", "italy serie d girone c",
    "italy serie d girone d", "italy serie d girone e", "italy serie d girone f",
    "italy serie d girone g", "italy serie d girone h", "italy serie d girone i",
    "japan j3 league", "japan j league cup", "south korea k league 2",
    "lithuania a lyga", "macedonia first league", "norway 1 division",
    "peru primera division", "portugal primeira liga", "portugal segunda liga",
    "romania liga i", "slovakia super liga", "slovenia 1 snl",
    "south korea k league 1", "spain segunda division", "switzerland challenge league",
    "turkey super lig", "usa major league soccer", "venezuela primera division",
    "finland veikkausliiga", "ireland premier division", "ireland first division",
    "scotland league one", "scotland league two", "brazil serie c",
    "mexico liga de expansion mx", "uruguay primera division",
    # nazionali maggiori (le giovanili restano fuori, vedi giovanili())
    "world uefa nations league", "world concacaf nations league",
}

# Competizioni di nazionali: qualificazioni ai Mondiali e agli Europei,
# riconosciute dalle parole (i nomi cambiano a ogni ciclo).
_NAZIONALI = (("world cup", "qualification"), ("euro championship", "qualification"),
              ("africa cup of nations", "qualification"))

_RX_PULIZIA = re.compile(r"[^a-z0-9]+")


_SIGLE_SQ = {"fc", "cf", "sc", "cd", "ac", "as", "ca", "sd", "ud", "cs",
             "fk", "nk", "hnk", "sk", "if", "ff", "bk", "jk", "ik", "kv",
             "club", "de"}


def chiave_partita(casa, ospite, campionato=""):
    """Chiave per riconoscere la stessa partita fra le due fonti.

    Serve perche' i nomi non coincidono mai del tutto: AsianOdds scrive
    "Bayern Munich" e "Club Brugge KV", FootyMetrics "Bayern Munchen" e
    "Club Brugge". Senza accenti, senza sigle e senza punteggiatura le
    due grafie si incontrano, e la partita non finisce due volte in
    lista - che era il difetto piu' visibile della prima versione.
    """
    import unicodedata

    # NFKD toglie gli accenti ma non le lettere che sono un segno a se':
    # la o barrata norvegese resta tale, e "Tromso" e "Tromso barrata"
    # restavano due partite diverse. Si traducono a mano.
    _LETTERE = {"\u00f8": "o", "\u00e6": "ae", "\u00df": "ss",
                "\u0111": "d", "\u0142": "l", "\u00fe": "th"}

    def pulisci(x):
        x = str(x or "").lower()
        for a, b in _LETTERE.items():
            x = x.replace(a, b)
        x = unicodedata.normalize("NFKD", x)
        x = "".join(c for c in x if not unicodedata.combining(c))
        x = _RX_PULIZIA.sub(" ", x)
        x = " ".join(w for w in x.split() if w not in _SIGLE_SQ)
        return _ALIAS.get(x, x)

    # I DUE STEMMI E IL PAESE viaggiano dentro la chiave, in coda:
    # stessa_partita() li usa quando i nomi non si somigliano abbastanza.
    # Le chiavi restano tuple, quindi valgono ancora come chiavi di
    # dizionario in scelta_schedina().
    return (pulisci(casa), pulisci(ospite), stemma(casa), stemma(ospite),
            _paese(campionato))


def _paese(campionato):
    """La prima parola del campionato: il paese. AsianOdds scrive "Spain:
    Copa Del Rey", FootyMetrics "Spain Copa Del Rey": tutti e due 'spain'.
    Vuoto se il campionato non c'e', e allora le abbreviazioni non si
    provano."""
    p = _RX_PULIZIA.sub(" ", str(campionato or "").lower()).split()
    return p[0] if p else ""


# ---------------------------------------------------------------- stemmi
# IL SECONDO MODO DI RICONOSCERE LA STESSA PARTITA: lo stemma.
#
# Il prefisso comune non arriva dove le due grafie non condividono
# nulla. Il 27 ottobre restavano doppie, con due prezzi e due righe nella
# schedina, tre partite:
#     "M'Gladbach - Karlsruher SC"   e  "B. Monchengladbach - Karlsruher SC"
#     "Milton Keynes Dons - Bromley" e  "MK Dons - Bromley"
#     "Vicenza - Modena"             e  "L.R. Vicenza - Modena"
# La mappa dei nomi dell'addon (loghi.json, 11.420 grafie) le conosce
# gia' tutte: due squadre che portano allo stesso file di stemma sono la
# stessa squadra. E' lo stesso criterio con cui l'addon aggancia le Sure
# Bets ad AsianOdds (_chiave_incontro in default.py).
#
# SOLO I PASSAGGI ESATTI DI logo_per_squadra() DELL'ADDON, nello stesso
# ordine: il nome com'e'; con la punteggiatura come spazi; senza accenti;
# senza i marcatori di variante in coda ("Man United W" -> "man united").
# La somiglianza (difflib 0.88), che l'addon usa fra un passaggio esatto
# e l'altro, qui NO: per mostrare uno stemma uno sbaglio costa poco, per
# fondere due partite costa una riga di prezzo sparita. Un nome che sta
# fra le omonimie ("ambigue": Leon, Barcelona, Rangers...) non prende
# stemma affatto. Quando un nome vero non si trova, la strada e' un
# alias in loghi.json, che serve anche all'addon - non la somiglianza.
#
# NORMALIZZAZIONE IDENTICA A normalizza_squadra() dell'addon e di
# prepara_loghi.py: le chiavi di loghi.json sono fatte cosi'. Se le due
# divergono gli stemmi smettono di essere trovati - senza errori, solo
# meno unioni.
#
# Se loghi.json non si legge si va avanti senza: l'unione per stemma
# AGGIUNGE unioni a quelle del prefisso, non ne toglie, quindi senza
# mappa il risultato e' quello di prima.
URL_LOGHI = "https://raw.githubusercontent.com/Caltiki/pronostici-repo/main/loghi.json"
_LOGHI = None
_LOGHI_FILE = None          # --loghi: una copia locale, se c'e'

_SUFFISSI_SQ = ["fc", "ac", "as", "ss", "us", "cf", "sc", "afc", "fk", "sk",
                "bk", "if"]
_RE_SUFF_SQ = [re.compile(r"\b" + w + r"\b") for w in _SUFFISSI_SQ]
_RE_SPAZI = re.compile(r"\s+")
_RE_PUNTEGGIATURA = re.compile(r"[-‐-―_.,'’/]+")
_TRADUZIONE_LETTERE = {
    "ø": "o", "æ": "ae", "å": "a", "ð": "d", "þ": "th",
    "ß": "ss", "đ": "d", "ł": "l", "ı": "i", "œ": "oe",
    "ħ": "h", "ż": "z", "ġ": "g",
}


def _normalizza_squadra(nome, separa_punteggiatura=False):
    """Copia di normalizza_squadra() dell'addon: non cambiarla da sola."""
    n = str(nome).lower().strip()
    if separa_punteggiatura:
        n = _RE_PUNTEGGIATURA.sub(" ", n)
    n = _RE_SPAZI.sub(" ", n)
    for rx in _RE_SUFF_SQ:
        n = rx.sub("", n)
    return _RE_SPAZI.sub(" ", n).strip()


def _senza_accenti(testo):
    """Copia di _senza_accenti() dell'addon."""
    import unicodedata
    testo = "".join(_TRADUZIONE_LETTERE.get(c, c) for c in str(testo))
    scomposto = unicodedata.normalize("NFKD", testo)
    return "".join(c for c in scomposto if not unicodedata.combining(c))


def _carica_loghi():
    """La mappa dei nomi, letta una volta per giro. {} se non si legge."""
    global _LOGHI
    if _LOGHI is not None:
        return _LOGHI
    dati = None
    if _LOGHI_FILE and os.path.exists(_LOGHI_FILE):
        try:
            with open(_LOGHI_FILE, encoding="utf-8") as f:
                dati = json.load(f)
        except Exception as e:
            print("  loghi.json locale illeggibile: {}".format(e))
    if dati is None:
        try:
            import urllib.request
            with urllib.request.urlopen(URL_LOGHI, timeout=30) as r:
                dati = json.loads(r.read().decode("utf-8-sig"))
        except Exception as e:
            print("  loghi.json non raggiungibile ({}): unione per stemma "
                  "disattivata, resta solo quella per nome".format(e))
    if not isinstance(dati, dict) or not isinstance(dati.get("mappa"), dict):
        _LOGHI = {"mappa": {}, "ambigue": {}}
    else:
        _LOGHI = {"mappa": dati["mappa"], "ambigue": dati.get("ambigue") or {}}
    return _LOGHI


# Le parole che in CODA al nome dicono quale squadra del club e'. Stessa
# lista di _MARCATORI_VARIANTE dell'addon.
_MARCATORI_CODA = {
    "w", "wom", "women", "womens", "ladies", "femminile", "femenino",
    "femenil", "feminino", "feminin", "feminina", "frauen", "damen",
    "dames", "kvinner", "naiset", "kobiet", "zeny",
    "b", "c", "ii", "iii", "2", "u21", "u23", "u19", "reserve", "reserves",
    "riserve", "youth", "primavera", "u16", "u17", "u18", "u20", "u22",
}
_RE_PARENTESI = re.compile(r"[()\[\]{}]+")
_INDICI = {}


def _indice_loghi(nome, trasforma):
    """Le chiavi della mappa passate per la stessa trasformazione della
    domanda: il confronto deve essere simmetrico, come nell'addon."""
    if nome not in _INDICI:
        d = {}
        for k in _carica_loghi()["mappa"]:
            d.setdefault(trasforma(k), k)
        _INDICI[nome] = d
    return _INDICI[nome]


def _senza_marcatori_coda(chiave):
    parole = _RE_PARENTESI.sub(" ", chiave).split()
    tolto = False
    while parole and parole[-1] in _MARCATORI_CODA:
        parole.pop()
        tolto = True
    return " ".join(parole) if tolto and parole else None


def stemma(nome):
    """Il file di stemma della squadra, o None. Solo passaggi esatti."""
    if not nome:
        return None
    loghi = _carica_loghi()
    mappa, ambigue = loghi["mappa"], loghi["ambigue"]
    if not mappa:
        return None
    chiave = _normalizza_squadra(nome)
    if chiave in ambigue:
        return None
    if chiave in mappa:
        return mappa[chiave]
    punt = _normalizza_squadra(nome, separa_punteggiatura=True)
    k = _indice_loghi("punt", lambda x: _normalizza_squadra(x, True)).get(punt)
    if k:
        return mappa[k]
    acc = _indice_loghi("acc", lambda x: _senza_accenti(_normalizza_squadra(x, True)))
    k = acc.get(_senza_accenti(punt))
    if k:
        return mappa[k]
    base = _senza_marcatori_coda(punt)
    if base:
        if base in ambigue:
            return None
        k = acc.get(_senza_accenti(base))
        if k:
            return mappa[k]
    return None


# I NOMI CHE NESSUNA REGOLA SUI CARATTERI PUO' UNIRE, perche' le due
# grafie non condividono niente: non e' un accento o una sigla, e' il
# nome tradotto. Qui ci va SOLO quello che si e' visto davvero nei file
# e si e' verificato, mai quello che sembra ovvio.
#
# Perche' mai per intuito: il 26 ottobre nella lista c'era
# "Montpellier - Red Star", che e' il Red Star di Parigi. L'alias
# "red star = crvena zvezda", che a chiunque sembrerebbe scontato,
# avrebbe unito due club di due paesi diversi.
#
# COME SI AGGIUNGE UNA VOCE: si parte da due righe della stessa
# giornata che sono palesemente la stessa partita, si guarda la chiave
# che chiave_partita() produce per entrambe, e si mappa quella della
# fonte meno diffusa su quella dell'altra. Il confronto avviene DOPO la
# pulizia, quindi la chiave e' minuscola, senza accenti e senza sigle.
#
# - kobenhavn: AsianOdds scrive "FC Copenhagen", FootyMetrics
#   "FC København". Visto il 26 ottobre, stessa partita contro Odense,
#   ed erano due righe distinte dentro la schedina.
# - koln: la stessa cosa fra "FC Koln" e "FC Cologne".
_ALIAS = {
    "kobenhavn": "copenhagen",
    "koln": "cologne",
}


_RX_ETA = re.compile(r"^u\d{2}$")

# Le parole che non sono il nome della squadra ma QUALE squadra del club:
# femminile, primavera, riserve. Due nomi si possono confrontare solo se
# portano gli stessi marcatori, altrimenti "Milan" e "Milan U19" o
# "Celtic" e "Celtic W" passano per la stessa cosa.
_MARCATORI = {"w", "women", "ladies", "femminile", "femenino", "feminino",
              "frauen", "dames", "b", "ii", "res", "reserves", "youth",
              "academy", "juniors", "primavera", "jugend"}


def _marcatori(nome):
    return frozenset(p for p in str(nome or "").split()
                     if p in _MARCATORI or _RX_ETA.match(p))


def _nome_simile(a, b):
    """Due grafie sono la stessa squadra? Confronto per prefisso comune.

    chiave_partita() pulisce accenti, punteggiatura e sigle, e tanto
    basta per "Club Brugge KV" contro "Club Brugge". Non basta quando le
    due fonti usano DUE LINGUE: "Bayern Munich" e "Bayern Munchen",
    "Sparta Praha" e "Sparta Prague" restano stringhe diverse dopo
    qualunque normalizzazione, perche' la differenza non e' un segno
    grafico - e' la traduzione del nome.

    Una tabella di alias a mano morirebbe alla prima squadra nuova. Il
    prefisso invece regge da solo: le due grafie di uno stesso nome
    divergono sempre in fondo e mai in testa. Serve prefisso di almeno
    tre lettere e almeno meta' del nome piu' corto.

    PERCHE' SI PUO' USARE ANCHE SUL CARICAMENTO. La regola sbaglia per
    eccesso - "Manchester City" e "Manchester United" condividono undici
    caratteri - ma per cancellare una partita vera servirebbero DUE
    partite nello stesso giorno con entrambi i lati somiglianti, cioe'
    una squadra che gioca due volte in giornata. Non capita, tranne
    maschile/femminile e primavera, che _marcatori() tiene separati.
    Verificata sui dati del 25 ottobre: 31 coppie unite su 647 grafie,
    tutte controllate a mano, nessun falso positivo. Il conto e'
    comunque a senso unico: perdere una riga da una lista di prezzi non
    costa niente, tenere due volte la stessa partita in una multipla
    costa la giocata intera, perche' i due eventi cadono insieme e la
    quota che si vede e' una bugia.

    QUELLO CHE NON PRENDE. Due nomi senza nulla in comune - "Koln" e
    "Cologne", "Crvena Zvezda" e "Red Star" - restano due partite.
    Nessuna regola sui caratteri ci arriva: serve una tabella di nomi,
    e va costruita sui casi visti davvero, non indovinata. Quella tabella
    c'e' gia', ed e' loghi.json: vedi _stesso_stemma(), che stessa_partita()
    prova quando questa funzione dice di no.

    [storico] La prima versione confrontava la stringa grezza, e la
    stessa partita entrava due volte nella schedina.
    """
    if not a or not b:
        return False
    # PRIMA I MARCATORI, perche' il prefisso da solo sbaglia proprio qui:
    # "Hibernian" e "Hibernian W" si somigliano per costruzione - la
    # seconda e' la prima piu' una lettera - ma sono due squadre diverse
    # che giocano lo stesso giorno. Visto sui dati del 25 ottobre.
    if _marcatori(a) != _marcatori(b):
        return False
    if _prefisso_regge(a, b):
        return True
    # SENZA LA PRIMA PAROLA. Le due grafie di un club differiscono spesso
    # per una parola in testa che una fonte mette e l'altra no, e quella
    # parola manda a zero il prefisso comune: "Royale Union SG" e "Union
    # St.Gilloise" non condividono nemmeno la prima lettera, ma sono la
    # stessa squadra. Togliendo la testa a uno dei due il confronto
    # torna a funzionare.
    # Il resto deve essere un nome, non un'iniziale: su "Victoriano A."
    # contro "Club A. Guemes" il resto era la lettera "a", che combacia
    # con qualunque nome cominci per a, e due partite argentine diverse
    # venivano unite. Quattro caratteri bastano a chiudere il buco senza
    # perdere nessuno dei casi veri (il piu' corto e' "betis").
    ta, tb = a.split(), b.split()
    ra, rb = " ".join(ta[1:]), " ".join(tb[1:])
    if len(ra) >= 4 and _prefisso_regge(ra, b):
        return True
    if len(rb) >= 4 and _prefisso_regge(a, rb):
        return True
    return False


def _prefisso_regge(a, b):
    if a == b or a.startswith(b) or b.startswith(a):
        return True
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n >= 3 and n * 2 >= min(len(a), len(b))


def stessa_partita(ka, kb):
    """ka, kb sono chiavi di chiave_partita(): (casa, ospite) ripuliti,
    piu' i due stemmi."""
    if _nome_simile(ka[0], kb[0]) and _nome_simile(ka[1], kb[1]):
        return True
    if _stesso_stemma(ka, kb):
        return True
    return _stessa_per_abbreviazione(ka, kb)


# ----------------------------------------------------------- abbreviazioni
# IL TERZO MODO, per le squadre che non hanno stemma o che la mappa non
# conosce in quella grafia. Il 27 ottobre, dopo prefisso e stemma,
# restavano doppie cinque partite, tutte per la stessa ragione: una fonte
# abbrevia, l'altra scrive per esteso.
#     "Atl. Paso"  /  "Atletico Paso"        prima parola accorciata
#     "Din. Zagreb"  /  "Dinamo Zagreb"      idem
#     "RW Essen"  /  "Rot-Weiss Essen"       iniziali di due parole
#     "G.A. Eagles"  /  "Go Ahead Eagles"    iniziali puntate
#     "Argentinos Jrs"  /  "Argentinos Juniors"   parola contratta
#     "HFC"  /  "Koninklijke HFC"            parola in piu' in testa
# e il giorno prima, sugli stessi dati, "Lok. Plovdiv", "Dep. Riestra",
# "H. Beer Sheva" contro "Hapoel Be'er Sheva", "Univ. Craiova".
# Nessuna e' un prefisso comune - "atl paso" e "atletico paso" hanno in
# comune tre lettere su otto - e nessuna e' una traduzione.
#
# LA REGOLA, parola per parola: due parole stanno insieme se sono uguali,
# se una e' l'inizio dell'altra, se una e' la contrazione dell'altra
# (stessa iniziale, lettere nello stesso ordine: "jrs" in "juniors"), o
# se una e' fatta delle iniziali delle due o tre parole successive
# dell'altra ("rw" = rot weiss). Una parola in piu' in TESTA a uno dei
# due nomi si salta, come fa gia' _nome_simile().
#
# I PALETTI, perche' accorciare avvicina tutto a tutto:
#   - tutte e due le squadre devono stare insieme, non una;
#   - almeno una parola di quattro lettere o piu' deve essere IDENTICA
#     nell'uno o nell'altro lato: "paso", "zagreb", "eagles", "essen";
#   - stesso PAESE del campionato, oltre allo stesso giorno, che vale
#     gia' per tutto;
#   - stessa categoria (femminile, giovanili, riserve), come per lo
#     stemma.
# Misurato su sei fotografie vere fra il 25 e il 27 ottobre: 9 partite
# unite in piu', tutte controllate una per una e tutte giuste, nessuna
# partita vera persa. Le unioni nuove finiscono nel log, marcate
# [abbreviazione], proprio per poterle ricontrollare.
#
# QUELLO CHE NON CAMBIA. "Hapoel Tel Aviv - Maccabi Haifa" e "Hapoel
# Haifa - Maccabi Tel Aviv" lo stesso giorno verrebbero unite: ma lo fa
# gia' _nome_simile(), per il prefisso "hapoel ", ed e' il prezzo
# dichiarato di quella regola (vedi la sua nota). Questa non lo peggiora.
def _contrazione(corta, lunga):
    """'jrs' sta in 'juniors': stessa iniziale, poi le lettere in ordine."""
    if not corta or not lunga or corta[0] != lunga[0] or len(corta) >= len(lunga):
        return False
    i = 0
    for c in lunga:
        if i < len(corta) and c == corta[i]:
            i += 1
    return i == len(corta)


def _parole_compatibili(ta, tb):
    """Allinea due liste di parole. Vero se si consumano tutte e due."""
    i = j = 0
    while i < len(ta) and j < len(tb):
        a, b = ta[i], tb[j]
        if a == b:
            i += 1
            j += 1
            continue
        # due parole che sono una: "be er" contro "beer". L'apostrofo di
        # "Be'er Sheva" diventa uno spazio nella pulizia. PRIMA del
        # prefisso, altrimenti "be" passa per l'inizio di "beer" e "er"
        # resta senza compagna.
        if i + 1 < len(ta) and ta[i] + ta[i + 1] == b:
            i += 2
            j += 1
            continue
        if j + 1 < len(tb) and tb[j] + tb[j + 1] == a:
            i += 1
            j += 2
            continue
        if a.startswith(b) or b.startswith(a):
            i += 1
            j += 1
            continue
        if _contrazione(a, b) or _contrazione(b, a):
            i += 1
            j += 1
            continue
        # iniziali: "rw" contro "rot weiss", "ga" contro "go ahead"
        presa = False
        for corta, lunga, quale in ((a, tb[j:], "a"), (b, ta[i:], "b")):
            n = len(corta)
            if 2 <= n <= 3 and len(lunga) >= n and \
                    all(lunga[x][:1] == corta[x] for x in range(n)):
                if quale == "a":
                    i += 1
                    j += n
                else:
                    j += 1
                    i += n
                presa = True
                break
        if not presa:
            return False
    return i == len(ta) and j == len(tb)


def _squadra_abbreviata(a, b):
    ta, tb = a.split(), b.split()
    if not ta or not tb:
        return False
    if _parole_compatibili(ta, tb):
        return True
    # una parola in piu' in testa, da una parte sola
    return ((len(ta) > 1 and _parole_compatibili(ta[1:], tb)) or
            (len(tb) > 1 and _parole_compatibili(ta, tb[1:])))


def _stessa_per_abbreviazione(ka, kb):
    if len(ka) < 5 or len(kb) < 5 or not ka[4] or ka[4] != kb[4]:
        return False
    # Ogni squadra puo' stare insieme per l'una o per l'altra strada:
    # "Sepsi" / "Sepsi Sf. Gheorghe" per prefisso, "Universitatea Craiova"
    # / "Univ. Craiova" per abbreviazione, nella stessa partita.
    for x, y in ((ka[0], kb[0]), (ka[1], kb[1])):
        if _categoria(x) != _categoria(y):
            return False
        if not (_squadra_abbreviata(x, y) or _nome_simile(x, y)):
            return False
    lunghe = lambda s: {w for w in s.split() if len(w) >= 4}
    return bool((lunghe(ka[0]) & lunghe(kb[0])) | (lunghe(ka[1]) & lunghe(kb[1])))


# Che squadra del club e': femminile, giovanile, riserve, o la prima.
# SERVE PERCHE' loghi.json manda di proposito femminili e seconde squadre
# sullo stemma del club - uno stemma loro non ce l'hanno - quindi "Celtic"
# e "Celtic W" arrivano allo stesso file. Lo stemma da solo li unirebbe.
# Qui si confronta la CATEGORIA e non la parola: "Chelsea W" e "Chelsea
# Women" sono la stessa squadra scritta in due modi.
_FEMMINILE = {"w", "wom", "women", "womens", "ladies", "femminile",
              "femenino", "femenil", "feminino", "feminin", "feminina",
              "frauen", "damen", "dames", "kvinner", "naiset", "kobiet",
              "zeny"}
# Niente "juniors": Boca Juniors e Argentinos Juniors sono prime squadre,
# e la parola sta proprio in fondo al nome. Nemmeno l'addon la conta.
_RISERVE = {"b", "c", "ii", "iii", "2", "res", "reserve", "reserves",
            "riserve", "youth", "academy", "primavera", "jugend"}


def _categoria(nome_pulito):
    """Le riserve si riconoscono SOLO dall'ultima parola: "Celta Vigo B"
    e' la seconda squadra, "B. Monchengladbach" e' il Borussia. Contare la
    "b" ovunque - come fa _variante() dell'addon - faceva del Gladbach di
    AsianOdds una squadra riserve, e la partita non si univa mai."""
    parole = str(nome_pulito or "").split()
    if set(parole) & _FEMMINILE:
        return "f"
    eta = sorted(p for p in parole if _RX_ETA.match(p))
    if eta:
        return eta[0]
    if parole and parole[-1] in _RISERVE:
        return "r"
    return ""


def _stesso_stemma(ka, kb):
    """Stessa partita se le due squadre portano agli stessi due stemmi.

    Tutti e quattro gli stemmi devono esserci: due squadre SENZA stemma
    non sono la stessa squadra. E la categoria deve combaciare su
    entrambi i lati, per il motivo scritto sopra _categoria()."""
    if len(ka) < 4 or len(kb) < 4:
        return False
    if not (ka[2] and ka[3] and kb[2] and kb[3]):
        return False
    if ka[2] != kb[2] or ka[3] != kb[3]:
        return False
    return (_categoria(ka[0]) == _categoria(kb[0])
            and _categoria(ka[1]) == _categoria(kb[1]))


def _chiave_lega(nome):
    return " ".join(_RX_PULIZIA.sub(" ", str(nome or "").lower()).split())


# I nomi di Pinnacle (API-Football) sono scritti diversi da AsianOdds:
# "Italy Serie A" e non "Italy: Serie A", "Spain La Liga" e non "Spain:
# Laliga". Il confronto si fa sulla forma pulita, piu' questi sinonimi.
_PRINCIPALI_ALTRI = {"spain la liga", "spain segunda division", "portugal primeira liga",
                     "belgium jupiler pro league", "usa major league soccer",
                     "world uefa champions league", "world uefa europa league",
                     "world uefa europa conference league"}


def principale(lega):
    k = _chiave_lega(lega)
    return k in {_chiave_lega(x) for x in PRINCIPALI} or k in _PRINCIPALI_ALTRI


def _senza_accenti(t):
    import unicodedata
    t = unicodedata.normalize("NFKD", str(t or ""))
    return "".join(c for c in t if not unicodedata.combining(c))


def su_schedina(lega):
    # (14/11) anche senza accenti: "Spain Segunda División" diventava
    # "spain segunda divisi n" e non si riconosceva
    k, k2 = _chiave_lega(lega), _chiave_lega(_senza_accenti(lega))
    if k in SISAL or k2 in SISAL:
        return True
    if any(x in k2 for x in ("women", "femenil", "femenina", "feminine", "frauen", "u17", "u19", "u20", "u21", "u23")):
        return False
    return any(all(w in k2 for w in parole) for parole in _NAZIONALI)


# (14/11) GIOVANILI, FEMMINILI E RISERVE si riconoscono anche dal nome della
# squadra: in una competizione ammessa (una coppa, le nazionali) possono
# comparire "Brazil U20" o "Arsenal W", che non si giocano come le maggiori.
_RX_GIOVANILI = re.compile(r"(\bu ?(17|18|19|20|21|23)\b|\bwomen\b|\bw\b|\bii\b|\breserves?\b|\bb team\b)", re.I)


def giovanili(casa, ospite):
    return bool(_RX_GIOVANILI.search(str(casa or "")) or _RX_GIOVANILI.search(str(ospite or "")))


# Quante righe al massimo, per giornata, possono avere la STESSA
# SELEZIONE. "Over 1.5" su una linea 3.0 vale 84.5% ovunque, quindi senza
# tetto la lista diventa la stessa scommessa ripetuta su venti partite
# diverse. Il tetto e' sulla selezione nuda e non su (mercato, selezione):
# "Over" su OU 1.5, 2.5 e 3.5 conta come tre mercati, e il tetto non
# morderebbe.
SCHEDINA_PER_GIOCATA = 4

# Quante righe al massimo per giornata, in tutto.
SCHEDINA_MAX = 24

# I quattro gruppi, nell'ordine con cui si pescano a turno.
GRUPPI_SCHEDINA = ("Combo", "1X2", "Gol NoGol", "Under Over")


# (14/11) LE NUOVE REGOLE DELLA SCHEDINA, decise con Max:
#   - margine al massimo 5% (era 8%): con margini larghi la probabilita'
#     senza margine e' meno affidabile;
#   - fuori le giocate su cui il mercato si e' spostato CONTRO di almeno
#     MOV_CONTRO punti dalla prima lettura (soldi informati, notizie);
#   - a parita' di probabilita' (al punto) vince il prezzo affidabile
#     (margine stretto), poi il movimento a favore, poi il margine piu' basso.
MARGINE_SCHEDINA = 5.0
# Margine massimo delle righe nei JSON (Kodi, sito): vedi scrivi_kodi.
MARGINE_MAX_PINNACLE = 10.0     # (18/11) era 8 come per tutti
MARGINE_MAX_ALTRI = 8.0
MOV_CONTRO = 2.0


def stretto(r):
    m = r.get("Margine")
    if m is None:
        return False
    return m < (2.0 if r.get("Fonte") == "FootyMetrics" else 3.0)


def scelta_schedina(righe, margine_max=MARGINE_SCHEDINA, banda=(60.0, 80.0)):
    """Le righe della scheda Schedina, in ordine.

    LA REGOLA STA QUI E SOLO QUI. Prima viveva in due posti - il foglio
    Excel e la vista Kodi - e due copie della stessa regola divergono
    alla prima modifica: si sarebbe finiti con Telegram e Kodi che
    mostrano schedine diverse senza che nessuno capisca perche'.

    LA FASCIA, corretta il 25 ottobre. Era 55-85% e la schedina usciva
    con quota equa MEDIANA 1.21 - cinque eventi cosi' pagano 2.59 eque.
    Adesso 50-75%, cioe' quote fra 1.33 e 2.00.

    (14/11) 60-80%, CIOE' QUOTE EQUE FRA 1.25 E 1.67. Il test sui dieci
    anni di football-data.co.uk (mercato_storico.py, 101.469 partite con la
    chiusura Pinnacle, 22 campionati) dice per fasce di probabilita' alla
    chiusura, giocando alla quota MEDIA del mercato: 50-55% -4.7%, 55-60%
    -3.8%, 60-65% -1.1%, 65-70% -0.7%, 70-75% 0.0%, 75-80% -0.5%, 80-85%
    -3.3%. Nella 70-75% l'esito esce il 3.0% in piu' di quanto promette
    (t 3.8, nello stesso verso fino al 2018-19 e dopo): il favourite-
    longshot bias, lo stesso del test del 7/11 qui sotto. Si toglie la
    fascia 50-60, la piu' cara, e si prende la 75-80; sopra l'80% si torna
    a perdere. Il prezzo: quote piu' basse, quindi piu' eventi per la
    stessa multipla.

    LA MOTIVAZIONE DI ALLORA NON REGGE, misurato il 7/11. Si era scritto
    che il margine del bookmaker si compone a ogni evento e che quindi
    conviene il minor numero possibile di eventi a quota piu' alta. Vale
    solo se il margine pesa uguale su ogni quota, e non e' cosi': il test
    sulle quote di chiusura di 101.467 partite (test_favorite.py, 22
    campionati, 2012-2026) mostra il favourite-longshot bias. Alla quota
    MEDIA di chiusura il segno 1 in casa sotto 1.35 perde l'1.5%, fra
    1.60 e 2.40 il 5.0% (differenza +3.5 punti, t +3.0); su tutte le
    selezioni il ROI scende con regolarita' al salire della quota. Per
    arrivare a una multipla da circa 5.0, sei-sette eventi intorno a 1.28
    costano in media circa il 3%, due-tre eventi fra 1.70 e 2.20 circa il
    9%. Sotto 1.20 invece si torna a perdere (-2.7%, t -2.2).
    La fascia resta 50-75% per scelta (7/11), ma il motivo vero per non
    scendere sotto 1.33 non e' il margine: e' il numero di eventi. Piu'
    eventi vuol dire piu' schedine perse per una sola partita, a parita'
    di perdita media. Se un giorno la fascia si allarga verso il basso,
    il margine non e' un argomento contro.

    SI PESCA A TURNO DAI QUATTRO GRUPPI, e questa e' la correzione che
    conta. Prima si ordinava tutto per probabilita' e si teneva una riga
    per partita: siccome la riga piu' probabile di una partita e' quasi
    sempre un Under/Over, le combo venivano eliminate PRIMA che il tetto
    per selezione entrasse in gioco. Il 25 ottobre la schedina usciva
    con 16 Under/Over, 3 Gol/NoGol, 2 esiti e ZERO combo, pur avendo 91
    combo disponibili in fascia. Pescando a turno ogni gruppo entra.

    DOPO LA CORREZIONE, sugli stessi dati: 115 righe su cinque giornate
    invece di 21, quota equa mediana 1.48 invece di 1.21, e i quattro
    gruppi a 32-30-28-25 invece di 16-3-2-0.

    UNA PARTITA SOLA PER GIORNATA, e il confronto e' stessa_partita() e
    non la stringa: "Sparta Praha - Slovácko" e "Sparta Prague -
    Slovacko" sono la stessa partita scritta dalle due fonti, e in una
    multipla sarebbero due eventi che cadono insieme.
    """
    # Prima si sceglie la riga migliore di ogni partita DENTRO ogni
    # gruppo: cosi' una partita puo' candidarsi con la sua combo anche
    # se ha un Under/Over piu' probabile.
    per_gruppo = {g: {} for g in GRUPPI_SCHEDINA}
    for r in righe:
        if r.get("Schedina") != "si":
            continue
        if r.get("Margine") is not None and r["Margine"] > margine_max:
            continue
        if not (banda[0] <= r["Prob"] <= banda[1]):
            continue
        if r.get("Mov_Sel") is not None and r["Mov_Sel"] <= -MOV_CONTRO:
            continue
        g = r.get("Gruppo")
        if g not in per_gruppo:
            continue
        k = (r["Data"],) + chiave_partita(r["Casa"], r["Ospite"], r.get("Campionato"))
        d = per_gruppo[g]
        if k not in d or r["Prob"] > d[k]["Prob"]:
            d[k] = r

    # A PARITA' DI PROBABILITA' VINCE IL MARGINE PIU' BASSO: a 1.4% quel
    # 70% regge, a 8% e' un numero gonfio.
    code = {}
    for g, d in per_gruppo.items():
        code[g] = sorted(d.values(),
                         key=lambda x: (-round(x["Prob"]),
                                        0 if stretto(x) else 1,
                                        -(x.get("Mov_Sel") or 0.0),
                                        x["Margine"] if x["Margine"] is not None else 99,
                                        x["Data"], x["Ora"]))

    # Giro di pesca: un gruppo alla volta, finche' c'e' roba o finche'
    # non si riempie la giornata. Una partita entra una volta sola, in
    # qualunque gruppo sia finita.
    # 'presi' tiene le chiavi per giornata e non in un insieme unico,
    # perche' il confronto morbido non e' una chiave hashabile: va fatto
    # a scorrimento contro le partite gia' entrate in quel giorno. Sono
    # ventiquattro per giornata, il costo non si vede.
    quante, presi, fuori = {}, {}, []
    indice = {g: 0 for g in GRUPPI_SCHEDINA}
    per_giorno = {}
    while True:
        mosso = False
        for g in GRUPPI_SCHEDINA:
            lista = code[g]
            i = indice[g]
            while i < len(lista):
                r = lista[i]
                i += 1
                k = chiave_partita(r["Casa"], r["Ospite"], r.get("Campionato"))
                gia = presi.setdefault(r["Data"], [])
                if any(stessa_partita(k, x) for x in gia):
                    continue
                if per_giorno.get(r["Data"], 0) >= SCHEDINA_MAX:
                    continue
                ks = (r["Data"], r["Sel"])
                if quante.get(ks, 0) >= SCHEDINA_PER_GIOCATA:
                    continue
                gia.append(k)
                quante[ks] = quante.get(ks, 0) + 1
                per_giorno[r["Data"]] = per_giorno.get(r["Data"], 0) + 1
                fuori.append(r)
                mosso = True
                break
            indice[g] = i
        if not mosso:
            break
    fuori.sort(key=lambda x: (x["Data"], -x["Prob"], x["Ora"]))
    return fuori


_RX_DATA = re.compile(r"_(\d{4}-\d{2}-\d{2})\.json$")


def num(v):
    try:
        return float(str(v).replace(",", "."))
    except Exception:
        return None


def pois(k, l):
    if l <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-l) * l ** k / math.factorial(k)


def lam_da_linea(L):
    """Gol totali attesi a cui la linea O/U L e' equa.

    Sulle linee intere il pareggio esatto e' rimborsato, sui quarti si
    gioca meta' e meta': i tre casi hanno formule diverse e usarne una
    sola sbaglierebbe di un decimo di gol buono.
    """
    def val(Lx, l):
        so = sum(pois(k, l) for k in range(0, 41) if k > Lx)
        su = sum(pois(k, l) for k in range(0, 41) if k < Lx)
        return so - su if abs(Lx - round(Lx)) < 1e-9 else so - 0.5

    def f(l):
        if abs(L * 4 - round(L * 4)) < 1e-9 and abs(L * 2 - round(L * 2)) > 1e-9:
            return (val(L - 0.25, l) + val(L + 0.25, l)) / 2
        return val(L, l)

    lo, hi = 0.2, 8.0
    for _ in range(200):
        m = (lo + hi) / 2
        if f(m) < 0:
            lo = m
        else:
            hi = m
    return (lo + hi) / 2


def lam_da_prob(p_over, soglia):
    """Gol totali attesi che danno quella probabilita' di Over.

    La strada di FootyMetrics: li' non c'e' una linea asiatica, c'e' la
    quota dell'Over 2.5. De-viggata diventa una probabilita', e da quella
    si torna indietro ai gol attesi - l'inverso di lam_da_linea().
    """
    lo, hi = 0.2, 8.0
    for _ in range(200):
        m = (lo + hi) / 2
        v = 1 - sum(pois(k, m) for k in range(0, int(soglia) + 1))
        if v < p_over:
            lo = m
        else:
            hi = m
    return (lo + hi) / 2


def dividi(lt, p1):
    """Divide i gol attesi fra casa e ospite perche' torni P(1) = p1."""
    lo, hi = 0.01, 0.99
    for _ in range(200):
        q = (lo + hi) / 2
        lc, lv = lt * q, lt * (1 - q)
        v = sum(pois(a, lc) * pois(b, lv)
                for a in range(0, 13) for b in range(0, 13) if a > b)
        if v < p1:
            lo = q
        else:
            hi = q
    q = (lo + hi) / 2
    return lt * q, lt * (1 - q)


def movimento_sel(sel, mov):
    """Quanto si e' mosso il mercato sulla parte 1X2 della selezione: "1",
    "1X", "1 + Over 2.5", "X2 + NoGol"... None per i mercati solo di gol."""
    if not mov:
        return None
    segno = str(sel or "").split("+")[0].strip()
    if segno in ("1", "X", "2"):
        return mov[segno]
    if segno in ("1X", "X2", "12"):
        return mov[segno[0]] + mov[segno[1]]
    return None


def righe_partita(r, fonte="AsianOdds", data_file=None):
    """Tutte le selezioni di una partita, o [] se manca il necessario.

    DUE STRADE PER ARRIVARE AGLI STESSI NUMERI.

    AsianOdds da' la linea asiatica e il de-vig di Shin, ed e' la fonte
    migliore - ma copre solo le prossime 24 ore. Oltre quelle, il
    calendario resterebbe vuoto.

    FootyMetrics pubblica le quote 1X2, Over/Under 2.5 e Gol/NoGol fino a
    cinque giorni avanti. Non e' un ripiego: su 68 partite che il 25
    ottobre avevano tutte e due le fonti, la sua probabilita' de-viggata
    si discostava da quella di AsianOdds di 0.7 punti in mediana e di
    -0.1 in segno. Leggono lo stesso mercato. L'overround mediano e'
    anzi piu' basso, 103.9% contro il 110.7% di AsianOdds, perche'
    sembra essere il meglio fra piu' bookmaker: il 4% delle righe somma
    sotto il 100%, cosa che un libro solo non puo' fare. Quelle si
    scartano, perche' il meglio-di-tanti non e' una distribuzione
    coerente e de-viggarlo darebbe numeri storti.

    Dove ci sono tutte e due vince AsianOdds, che e' un prezzo solo e
    con la linea asiatica vera.

    PINNACLE (da API-Football, pinnacle_<data>.json scritto da
    apifootball.py) sta in mezzo: dopo AsianOdds, prima di FootyMetrics.
    E' un libro solo, come AsianOdds, mentre FootyMetrics e' il meglio di
    tanti. Prova del 3/11: delle 10 partite lette 5 AsianOdds non le
    aveva; dove c'erano tutte e due, probabilita' senza margine a 1-2
    punti, margine medio 4.2% contro 6.4%. Il file porta gia' le
    probabilita' Shin, la linea Under/Over a mezzo gol piu' equilibrata
    con la sua probabilita' di Over, e il Gol/NoGol senza margine.
    """
    ou_diretto = {}
    if fonte in ("Pinnacle", "OddsAPI"):
        # ODDSAPI (11/11, oddsapi_<data>.json scritto da oddsapi_ci.py): la
        # stessa forma di Pinnacle - probabilita' Shin del book di
        # riferimento (Betfair Exchange sul piano gratuito), linea
        # Under/Over e probabilita' dell'Over - con P1/PX/P2 al posto di
        # "P1 Shin". Niente Gol/NoGol quotato: lo fa la griglia.
        k1, kx, k2 = (("P1 Shin", "PX Shin", "P2 Shin") if fonte == "Pinnacle"
                      else ("P1", "PX", "P2"))
        p1, px, p2 = num(r.get(k1)), num(r.get(kx)), num(r.get(k2))
        L, po = num(r.get("OU")), num(r.get("P Over"))
        if p1 is None or L is None or po is None or not (0 < p1 < 100) \
                or not (0 < po < 100):
            return []
        # A mezzo gol (2.5) la probabilita' dell'Over porta dritta ai gol
        # attesi; sulle linee asiatiche (2.25, 2.75, 3) si fa come con
        # AsianOdds: la linea piu' equilibrata e' quella equa.
        if abs(L * 2 - round(L * 2)) < 1e-9 and round(L * 2) % 2 == 1:
            lt = lam_da_prob(po / 100.0, L)
        else:
            lt = lam_da_linea(L)
        margine = num(r.get("Margine"))
        pgol = num(r.get("P Gol"))
        p_btts = pgol / 100.0 if pgol is not None and 0 < pgol < 100 else None
        # LE LINEE QUOTATE (12/11): pinnacle_<data>.json porta le quote
        # Over/Under di tutte le linee ("Linee OU": {"2.5": [over, under]}).
        # Dove Pinnacle quota l'1.5, il 2.5 o il 3.5, la probabilita' e'
        # quella del mercato senza margine, non la stima di Poisson.
        ou_diretto = {}
        for k, v in ((r.get("Linee OU") or {}) if fonte == "Pinnacle" else {}).items():
            try:
                qo, qu = float(v[0]), float(v[1])
                if qo > 1.0 and qu > 1.0:
                    ou_diretto[round(float(k), 2)] = (1 / qo) / (1 / qo + 1 / qu)
            except (TypeError, ValueError, IndexError):
                pass
        casa, osp = r.get("Casa", ""), r.get("Ospite", "")
        lega = r.get("Lega", "")
        data = str(r.get("Data") or data_file or "")[:10]
    elif fonte == "AsianOdds":
        p1, px, p2 = (num(r.get("P1 Shin")), num(r.get("PX Shin")),
                      num(r.get("P2 Shin")))
        L = num(r.get("OU"))
        if p1 is None or L is None or not (0 < p1 < 100):
            return []
        lt = lam_da_linea(L)
        margine = num(r.get("Margine"))
        p_btts = None
        casa, osp = r.get("Casa", ""), r.get("Ospite", "")
        lega = r.get("Lega", "")
        data = str(r.get("Data", ""))[:10]
    else:
        q1, qx, q2 = num(r.get("Q1")), num(r.get("QX")), num(r.get("Q2"))
        qo, qu = num(r.get("Q_O25")), num(r.get("Q_U25"))
        if not all(v and v > 1.01 for v in (q1, qx, q2, qo, qu)):
            return []
        inv = [1/q1, 1/qx, 1/q2]
        tot = sum(inv)
        if tot < 1.0:        # meglio-di-tanti: non e' un libro, si salta
            return []
        p1, px, p2 = [100*x/tot for x in inv]
        margine = round((tot - 1) * 100, 1)
        po = (1/qo) / (1/qo + 1/qu)
        lt = lam_da_prob(po, 2.5)
        L = None
        # IL GOL/NOGOL QUI E' UN PREZZO, NON UN MODELLO: FootyMetrics
        # quota Gol e NoGol, quindi si de-vigga e si usa quello invece
        # della griglia di Poisson. Una stima in meno fra il dato e il
        # numero mostrato.
        qg, qn = num(r.get("Q_GG")), num(r.get("Q_NG"))
        p_btts = ((1/qg) / (1/qg + 1/qn)
                  if qg and qn and qg > 1.01 and qn > 1.01 else None)
        casa, osp = r.get("Casa", ""), r.get("Ospite", "")
        lega = "{} {}".format(r.get("Paese", "") or "", r.get("Lega", "") or "").strip()
        data = str(r.get("Data") or data_file or "")[:10]
    # (14/11) IL MOVIMENTO DAL PRIMO PREZZO, per segno, in punti di
    # probabilita' (positivo = il mercato ci crede di piu'). Solo Pinnacle,
    # che tiene la prima lettura del giorno nel campo "Prima".
    mov1x2 = None
    pr = r.get("Prima") if fonte == "Pinnacle" else None
    if isinstance(pr, list) and len(pr) >= 3 and None not in (num(pr[0]), num(pr[1]), num(pr[2])):
        mov1x2 = {"1": p1 - num(pr[0]), "X": px - num(pr[1]), "2": p2 - num(pr[2])}
    lc, lv = dividi(lt, p1 / 100.0)
    P = lambda cond: sum(pois(a, lc) * pois(b, lv)
                         for a in range(0, 13) for b in range(0, 13) if cond(a, b))

    base = {
        "Data": data,
        "Ora": str(r.get("Ora", ""))[:5],
        "Campionato": lega,
        "Partita": "{} - {}".format(casa, osp),
        "Casa": casa,
        "Ospite": osp,
        "Linea_OU": L if L is not None else "",
        "Gol_attesi": round(lt, 2),
        "Margine": margine,
        "Movimento": num(r.get("Mov")),
        "Principale": "si" if principale(lega) else "",
        "Schedina": "si" if su_schedina(lega) and not giovanili(casa, osp) else "",
        "Fonte": fonte,
    }
    fuori = []

    def agg(gruppo, mercato, sel, p):
        if p is None or not (0.01 < p < 0.995):
            return
        v = dict(base)
        v.update({"Gruppo": gruppo, "Mercato": mercato, "Sel": sel,
                  "Prob": round(p * 100, 1), "Equa": round(1 / p, 2)})
        m = movimento_sel(sel, mov1x2)
        if m is not None:
            v["Mov_Sel"] = round(m, 1)
        fuori.append(v)

    # 1X2: il de-vig di AsianOdds, non il mio modello. E' gia' una
    # probabilita', ripassarla per la griglia aggiungerebbe solo l'errore
    # dell'approssimazione di Poisson.
    for sel, v in (("1", p1), ("X", px), ("2", p2)):
        if v is not None:
            agg("1X2", "1X2", sel, v / 100.0)

    for soglia in (1.5, 2.5, 3.5):
        po = ou_diretto.get(soglia)
        if po is None:
            po = P(lambda a, b, s=soglia: a + b > s)
        agg("Under Over", "OU {}".format(soglia), "Over", po)
        agg("Under Over", "OU {}".format(soglia), "Under", 1 - po)

    pg = p_btts if p_btts is not None else P(lambda a, b: a >= 1 and b >= 1)
    agg("Gol NoGol", "BTTS", "Gol", pg)
    agg("Gol NoGol", "BTTS", "NoGol", 1 - pg)

    for nome, cond in COMBO:
        p = P(cond)
        if BANDA[0] <= p <= BANDA[1]:
            agg("Combo", "Esito + Gol", nome, p)
    return fuori


def _file_del_giorno(cartella, prefisso, da_oggi):
    for p in sorted(glob.glob(os.path.join(cartella, prefisso + "_*.json"))):
        m = _RX_DATA.search(os.path.basename(p))
        if not m or (da_oggi and m.group(1) < date.today().isoformat()):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                dati = json.load(f)
        except Exception as e:
            print("  {} illeggibile: {}".format(os.path.basename(p), e))
            continue
        if isinstance(dati, list):
            yield m.group(1), dati


# PINNACLE SOLO SE FRESCA (11/11). Col piano PRO apifootball.py riscrive i
# file Pinnacle ogni ora, fino a tre giorni avanti. Se il piano finisce (o
# i giri orari saltano) quei file restano com'erano: senza questo controllo
# i prezzi equi userebbero per giorni un prezzo Pinnacle fermo, PRIMA di
# AsianOdds fresco. Una riga letta da piu' di PIN_MAX_ORE ore si salta e la
# partita prende il prezzo dalla fonte dopo.
#   - "Letto": quando l'abbiamo letta noi (ora di Roma), dal 11/11;
#   - "Aggiornato": l'ora di API-Football (UTC), per le righe di prima.
# Una riga senza nessuno dei due si tiene: non c'e' modo di dirla vecchia.
#
# PERCHE' 24 ORE E NON MENO. Col piano gratuito apifootball.py legge
# Pinnacle una volta sola, nel primo giro della notte (verso le 02-03 in
# Italia), per le partite di oggi: una partita delle 23:00 viene letta
# circa 20 ore prima e il prezzo deve valere ancora. Col PRO le righe si
# rileggono ogni ora (oggi) o ogni sei (i giorni dopo): 24 ore vuol dire
# che qualcosa si e' fermato.
PIN_MAX_ORE = 24


def pinnacle_fresca(r, adesso=None):
    adesso = adesso or datetime.now(timezone.utc)
    for campo, tz in (("Letto", _ROMA), ("Aggiornato", timezone.utc)):
        v = str(r.get(campo) or "").strip().replace("T", " ")[:16]
        if not v:
            continue
        try:
            t = datetime.strptime(v, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        except ValueError:
            continue
        return (adesso - t) <= timedelta(hours=PIN_MAX_ORE)
    return True


def carica(cartella, da_oggi):
    """Pinnacle dove c'e', poi AsianOdds, poi OddsAPI, poi FootyMetrics.

    PINNACLE PRIMA DI TUTTI (11/11, piano PRO di API-Football): e' il book
    di riferimento dei professionisti, margine 2-3% sui campionati
    principali, e apifootball.py pinnaclepro lo legge ogni ora per tutte le
    partite del sito. Prima veniva dopo AsianOdds e copriva solo i buchi.
    Quando il piano finisce i file pinnacle_<data>.json non si rifanno e
    l'ordine torna da solo quello di prima (vedi pinnacle_fresca(): una
    riga letta da piu' di PIN_MAX_ORE non si usa). Le righe Pinnacle portano
    ID_API, il numero della partita: il registro delle giocate lo usa per
    trovare la chiusura in pinnacle_archivio.csv.

    ODDSAPI (11/11) viene subito dopo AsianOdds: riempie le partite che
    AsianOdds non ha (quelle oltre le 24 ore dei campionati letti) e, dove
    ci sono tutte e due, AsianOdds resta il prezzo ma le sue righe 1X2
    prendono accanto il prezzo del book di riferimento di OddsAPI
    (Rif_Book, Rif_Prob, Rif_Equa): e' il confronto con un prezzo sharp,
    e serve anche a misurare quale dei due e' piu' giusto.

    L'ordine conta: si riempie prima con AsianOdds e poi si aggiunge
    FootyMetrics SOLO sulle partite che mancano. Dove ci sono tutte e
    due vince il prezzo asiatico, che e' un libro solo e porta la linea
    vera invece di una ricavata dalla quota dell'Over.
    """
    righe, viste, agg, doppie = [], {}, 0, 0
    per_stemma = []
    # IL SECONDO CONTROLLO, SOLO PER TOGLIERE. Il riconoscimento qui sopra
    # non sa che "ADT" e' "AD Tarma" (prova Pinnacle del 3/11: la stessa
    # partita usciva due volte, da AsianOdds e da Pinnacle). Per Pinnacle e
    # FootyMetrics, che riempiono solo i buchi, si chiede anche ad
    # apifootball.trova(): se dice che la partita c'e' gia', la riga nuova
    # si scarta. Puo' solo togliere un doppione, mai unire due partite.
    try:
        import apifootball as _af
    except Exception:
        _af = None
    gia_per_data = {}
    sigle = []

    def gia_presente(nuove):
        if _af is None:
            return None
        n0 = nuove[0]
        x = _af.trova(n0["Casa"], n0["Ospite"], n0["Ora"], gia_per_data.get(n0["Data"], []))
        if x is None:
            x = stessa_per_ora_e_parole(n0, gia_per_data.get(n0["Data"], []))
        return x

    # (13/11) ULTIMA STRADA: STESSA ORA (ENTRO 30 MINUTI) E PAROLE IN COMUNE.
    # "Montevideo City Torque - Racing" alle 20:30 (FootyMetrics) e
    # "Atletico Torque - Racing Montevideo" alle 21:00 (Pinnacle) sono la
    # stessa partita, ma i nomi sono troppo diversi e la mezz'ora di
    # differenza fermava anche la regola dell'ora: la partita usciva due
    # volte, e la card di FootyMetrics diceva che Pinnacle non la quotava.
    # Qui: per tutte e due le squadre una parola in comune (o un nome tutto
    # dentro l'altro: "Racing" e "Racing Montevideo"), per almeno una delle
    # due una parola vera (non "Racing", "Atletico", "Real"...), gli stessi
    # segni (U20, W, riserve) e UNA sola candidata nel giorno.
    def _sig(n):
        return {w for w in _af.base(n).split() if len(w) >= 4 and w not in _af.GENERICHE}

    def _dentro(a, b):
        pa, pb = _af.base(a).split(), _af.base(b).split()
        if not pa or not pb:
            return False
        corto, lungo = (pa, pb) if len(pa) <= len(pb) else (pb, pa)
        return all(w in lungo for w in corto)

    def stessa_per_ora_e_parole(n0, lista):
        mo = _af.minuti(n0["Ora"])
        if mo is None:
            return None
        cand = []
        for g in lista:
            mg = _af.minuti(g["ora"])
            if mg is None or abs(mo - mg) > 30:
                continue
            if _af.segni(n0["Casa"]) != _af.segni(g["casa"]) or _af.segni(n0["Ospite"]) != _af.segni(g["ospite"]):
                continue
            fc, fo = bool(_sig(n0["Casa"]) & _sig(g["casa"])), bool(_sig(n0["Ospite"]) & _sig(g["ospite"]))
            lc = fc or _dentro(n0["Casa"], g["casa"])
            lo = fo or _dentro(n0["Ospite"], g["ospite"])
            if lc and lo and (fc or fo):
                cand.append(g)
        if len(cand) != 1:
            return None
        per_stemma.append("{} {}  =  {}   [ora e parole]".format(n0["Data"], n0["Partita"], cand[0]["partita"]))
        return cand[0]

    def segna(nuove):
        n0 = nuove[0]
        gia_per_data.setdefault(n0["Data"], []).append(
            {"casa": n0["Casa"], "ospite": n0["Ospite"], "ora": n0["Ora"], "partita": n0["Partita"]})

    def nuova(nuove):
        """Vero se la partita non c'e' ancora, e in quel caso la segna."""
        k = chiave_partita(nuove[0]["Casa"], nuove[0]["Ospite"],
                           nuove[0].get("Campionato"))
        gia = viste.setdefault(nuove[0]["Data"], [])
        for x in gia:
            if stessa_partita(k, x):
                # Le unioni fatte SOLO da stemma o abbreviazione si
                # scrivono nel log, una per una: sono quelle nuove, e chi
                # guarda il giro deve poterle controllare a occhio.
                if not (_nome_simile(k[0], x[0]) and _nome_simile(k[1], x[1])):
                    per_stemma.append("{} {}  =  {}   [{}]".format(
                        nuove[0]["Data"], nuove[0]["Partita"],
                        "{} - {}".format(*x[5]),
                        "stemma" if _stesso_stemma(k, x) else "abbreviazione"))
                return False
        gia.append(k + ((nuove[0]["Casa"], nuove[0]["Ospite"]),))
        return True

    righe_di = {}                 # (data, casa, ospite) -> righe della fonte principale
    pinn = vecchie_pin = 0
    for data, dati in _file_del_giorno(cartella, "pinnacle", da_oggi):
        for r in dati:
            if not isinstance(r, dict):
                continue
            if not pinnacle_fresca(r):
                vecchie_pin += 1
                continue
            nuove = righe_partita(r, "Pinnacle", data)
            if not nuove:
                continue
            if not nuova(nuove):
                doppie += 1
                continue
            for x in nuove:
                x["ID_API"] = r.get("ID_API")
            segna(nuove)
            righe.extend(nuove)
            righe_di[(nuove[0]["Data"], nuove[0]["Casa"], nuove[0]["Ospite"])] = nuove
            pinn += 1
    # GLI ALTRI BOOK (12/11), dove Pinnacle manca: altribook_<data>.json,
    # scritto da apifootball.py col book piu' sharp presente (SBO, Betfair,
    # Marathonbet, Dafabet, 188Bet, 1xBet) o con la media dei book. Stessa
    # forma delle righe Pinnacle, stessa regola di freschezza; la fonte e'
    # il nome del book. Dopo Pinnacle, prima di quello che resta di AsianOdds.
    altri = 0
    for data, dati in _file_del_giorno(cartella, "altribook", da_oggi):
        for r in dati:
            if not isinstance(r, dict) or not pinnacle_fresca(r):
                continue
            nuove = righe_partita(r, "Pinnacle", data)
            if not nuove:
                continue
            if pinn:
                x = gia_presente(nuove)
                if x is not None:
                    doppie += 1
                    continue
            if not nuova(nuove):
                doppie += 1
                continue
            for x in nuove:
                x["Fonte"] = r.get("Book") or "altri book"
                x["ID_API"] = r.get("ID_API")
            segna(nuove)
            righe.extend(nuove)
            righe_di[(nuove[0]["Data"], nuove[0]["Casa"], nuove[0]["Ospite"])] = nuove
            altri += 1
    if altri:
        print("  Altri book: {} partite col prezzo dove Pinnacle manca".format(altri))
    for data, dati in _file_del_giorno(cartella, "asianodds", da_oggi):
        for r in dati:
            if not isinstance(r, dict):
                continue
            nuove = righe_partita(r, "AsianOdds", data)
            if not nuove:
                continue
            # Dopo Pinnacle serve anche il secondo controllo: i nomi di
            # API-Football e di AsianOdds a volte si scrivono diversi.
            if pinn:
                x = gia_presente(nuove)
                if x is not None:
                    doppie += 1
                    continue
            if not nuova(nuove):
                doppie += 1
                continue
            segna(nuove)
            righe.extend(nuove)
            righe_di[(nuove[0]["Data"], nuove[0]["Casa"], nuove[0]["Ospite"])] = nuove
    if pinn:
        print("  Pinnacle: {} partite col prezzo, prima di AsianOdds".format(pinn))
    if vecchie_pin:
        print("  Pinnacle: {} righe lette da piu' di {} ore, saltate (prezzo dalla fonte dopo)".format(
            vecchie_pin, PIN_MAX_ORE))

    def gia_vista(nuove):
        """La partita gia' presente a cui corrisponde, (data, casa, ospite)."""
        k = chiave_partita(nuove[0]["Casa"], nuove[0]["Ospite"], nuove[0].get("Campionato"))
        for x in viste.get(nuove[0]["Data"], []):
            if stessa_partita(k, x):
                return (nuove[0]["Data"],) + tuple(x[5])
        y = gia_presente(nuove)
        if y is not None:
            return (nuove[0]["Data"], y["casa"], y["ospite"])
        return None

    oapi = rif = 0
    for data, dati in _file_del_giorno(cartella, "oddsapi", da_oggi):
        for r in dati:
            if not isinstance(r, dict):
                continue
            nuove = righe_partita(r, "OddsAPI", data)
            if not nuove:
                continue
            g = gia_vista(nuove)
            if g is not None:
                doppie += 1
                # IL RIFERIMENTO: sulle righe 1X2 di AsianOdds della stessa
                # partita, la probabilita' e l'Equa del book di OddsAPI.
                quella = righe_di.get(g)
                if quella:
                    pr = {"1": num(r.get("P1")), "X": num(r.get("PX")), "2": num(r.get("P2"))}
                    for x in quella:
                        if x["Mercato"] == "1X2" and pr.get(x["Sel"]):
                            x["Rif_Book"] = r.get("Book") or "OddsAPI"
                            x["Rif_Prob"] = round(pr[x["Sel"]], 1)
                            x["Rif_Equa"] = round(100.0 / pr[x["Sel"]], 2)
                    rif += 1
                continue
            nuova(nuove)
            segna(nuove)
            for x in nuove:
                x["Rif_Book"] = r.get("Book") or "OddsAPI"
            righe.extend(nuove)
            oapi += 1
    if oapi or rif:
        print("  OddsAPI: {} partite aggiunte, {} col riferimento accanto ad AsianOdds".format(oapi, rif))
    for data, dati in _file_del_giorno(cartella, "footymetrics", da_oggi):
        for r in dati:
            if not isinstance(r, dict):
                continue
            nuove = righe_partita(r, "FootyMetrics", data)
            if not nuove:
                continue
            x = gia_presente(nuove)
            if x is not None:
                doppie += 1
                sigle.append("{} {}  =  {}   [FootyMetrics]".format(nuove[0]["Data"], nuove[0]["Partita"], x["partita"]))
                continue
            if not nuova(nuove):
                doppie += 1
                continue
            segna(nuove)
            righe.extend(nuove)
            agg += 1
    if doppie:
        print("  partite scartate perche' gia' presenti con altra "
              "grafia: {}".format(doppie))
    if per_stemma:
        print("  di queste, riconosciute solo da stemma o abbreviazione: {}".format(
            len(per_stemma)))
        for t in per_stemma:
            print("    " + t)
    if sigle:
        print("  riconosciute dal secondo controllo (apifootball.trova): {}".format(len(sigle)))
        for t in sigle:
            print("    " + t)
    if pinn:
        pass   # gia' scritto sopra
    if agg:
        print("  partite aggiunte da FootyMetrics oltre le 24h: {}".format(agg))
    return righe


COLONNE = [("Data", 11), ("Ora", 7), ("Campionato", 26), ("Partita", 34),
           ("Mercato", 13), ("Sel", 14), ("Prob", 8), ("Equa", 8),
           ("Margine", 9), ("Fonte", 13), ("Schedina", 10),
           ("Linea_OU", 9), ("Gol_attesi", 11),
           ("Movimento", 11), ("Principale", 11)]

LEGENDA = [
    "COME SI LEGGE",
    "",
    "Equa e' il prezzo giusto della selezione, calcolato dal mercato.",
    "Se il bookmaker paga PIU' di quel numero stai prendendo valore.",
    "Se paga MENO stai pagando margine. Non e' un pronostico: e' un metro.",
    "",
    "Prob e' la probabilita' che la selezione esca, in percentuale.",
    "L'ordine e' per probabilita', che NON e' l'ordine di convenienza:",
    "il valore atteso e' identico su tutte le righe, perche' vengono",
    "tutte dallo stesso prezzo. Quello che cambia e' quanto ti fidi.",
    "",
    "Margine e' quanto AsianOdds carica su quella partita. Sotto il 2%",
    "il prezzo e' stretto e la probabilita' solida; sopra il 5% lo",
    "stesso numero balla. E' la colonna da guardare per prima.",
    "",
    "Movimento: di quanto si e' mosso il mercato 1X2 della partita",
    "dall'apertura, in punti di probabilita'. NON e' la quota della riga",
    "(su un Over parla dell'1X2) e non dice in che verso si e' mosso.",
    "",
    "Fonte: da dove viene il prezzo. AsianOdds copre solo le prossime 24",
    "ore; oltre quelle subentrano le quote di FootyMetrics, che arriva a",
    "cinque giorni. Non e' un ripiego: sulle 68 partite che il 25 ottobre",
    "avevano tutte e due, le probabilita' de-viggate differivano di 0.7",
    "punti in mediana. Dove ci sono entrambe vince AsianOdds, che porta la",
    "linea asiatica vera. Sul Gol/NoGol la strada FootyMetrics e' anzi",
    "migliore: li' il mercato e' QUOTATO, quindi si de-vigga il prezzo",
    "invece di ricavarlo dal modello.",
    "",
    "QUANTO CI SI PUO' FIDARE, misurato su 563 partite finite",
    "",
    "Gol/NoGol: tarato. Fascia 55-65%, 238 partite, previsto 60.0% e",
    "uscito 60.1%. E' il foglio piu' affidabile.",
    "",
    "Over/Under: il modello li sottostima. Previsto 56.6% di Over 2.5 e",
    "ne sono usciti il 61.3%, scarto +4.6 con errore standard 2.1.",
    "I gol veri hanno superato quelli attesi di +0.18 a partita, a tutte",
    "le linee. Non si sa ancora perche'. La direzione pero' e' chiara:",
    "le probabilita' di Over qui sono semmai basse, non alte.",
    "",
    "Combo con 1X o X2: le meno affidabili. Poisson indipendente",
    "sottostima i pareggi, quindi la loro quota equa e' un po' troppo",
    "generosa. Considerale un gradino sotto a quello che leggi.",
    "",
    "QUANTO COSTA ALLUNGARE LA SCHEDINA",
    "Il margine si compone a ogni evento. Con selezioni al 60% e un 5%",
    "di margine per evento: a 2 eventi lasci il 9%, a 3 il 14%, a 4 il",
    "18%, a 5 il 22%. Tre e' il punto dove la quota diventa interessante",
    "senza che il margine mangi piu' di un settimo.",
]


def scrivi_xlsx(righe, cartella):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        print("openpyxl non installato: niente Excel.")
        return None
    if not os.path.isdir(cartella):
        os.makedirs(cartella)
    nome = os.path.join(cartella, "PrezziEqui_{}.xlsx".format(
        datetime.now().strftime("%Y-%m-%d_%H%M")))

    wb = Workbook()
    wb.remove(wb.active)
    testa = Font(bold=True, color="FFFFFF")
    sfondo = PatternFill("solid", fgColor="1F3864")
    # IL FOGLIO SCHEDINA PER PRIMO perche' e' quello che si apre.
    #
    # TRE FILTRI, E IL TERZO E' QUELLO CHE CONTA. Campionati che i
    # concessionari italiani offrono davvero; margine non largo, perche'
    # una probabilita' alta che viene da una quota larga e' alta proprio
    # per quello; e soprattutto la FASCIA GIOCABILE.
    #
    # Senza la fascia, "ordinato per probabilita'" riempie la testa della
    # lista di Over 1.5 al 95%, che pagano 1.05: per farci una schedina
    # ne servirebbero venti, e venti eventi di margine composto mangiano
    # piu' di quanto la quota renda. Sotto il 55% invece e' testa o
    # croce. In mezzo, quote fra 1.18 e 1.82, che e' il materiale con cui
    # una multipla da tre o quattro ha senso.
    for gruppo in ("Schedina", "Combo", "Gol NoGol", "Under Over", "1X2"):
        if gruppo == "Schedina":
            sub = scelta_schedina(righe)
        else:
            sub = [r for r in righe if r["Gruppo"] == gruppo]
        # A PARITA' DI PROBABILITA' VINCE IL MARGINE PIU' BASSO, e non e'
        # un dettaglio estetico: "Over 1.5" su una linea 3.0 vale 84.5%
        # ovunque, quindi decine di righe arrivano appaiate e l'ordine
        # per sola probabilita' le lascia in un mucchio dove sembrano
        # equivalenti. Non lo sono: a 1.4% di margine quel 84.5% regge, a
        # 8% e' un numero gonfio. Il secondo criterio trasforma un
        # mucchio in una classifica vera.
        sub.sort(key=lambda x: (-x["Prob"],
                                x["Margine"] if x["Margine"] is not None else 99,
                                x["Data"], x["Ora"]))
        ws = wb.create_sheet(gruppo[:31])
        ws.append([c for c, _ in COLONNE])
        for c in ws[1]:
            c.font = testa
            c.fill = sfondo
            c.alignment = Alignment(horizontal="center")
        for r in sub:
            ws.append([r.get(c) for c, _ in COLONNE])
        for i, (c, w) in enumerate(COLONNE, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        # NIENTE RIQUADRI BLOCCATI: su telefono il file non si apre.
        ws.auto_filter.ref = "A1:{}{}".format(
            get_column_letter(len(COLONNE)), max(ws.max_row, 1))
    ws = wb.create_sheet("Legenda")
    for r in LEGENDA:
        ws.append([r])
    ws.column_dimensions["A"].width = 76
    ws["A1"].font = Font(bold=True, size=13)
    wb.save(nome)
    print("Excel: {}  ({} righe)".format(nome, len(righe)))
    return nome


def per_kodi(righe):
    """Le righe che vale la pena sfogliare col telecomando.

    L'Excel tiene tutto - si filtra con le colonne - ma in Kodi il file
    si scarica intero a ogni apertura, e 5.800 selezioni fanno due
    megabyte per una giornata sola. Si tiene quindi solo il lato utile
    di ogni mercato: il segno favorito, non tutti e tre; il lato sopra
    il 50% di Under/Over e Gol/NoGol, non tutti e due, perche' l'altro
    e' il complemento e si ricava a mente. L'Over 3.5 esce del tutto:
    e' il piu' sensibile all'errore sul totale e il meno giocato.

    Via anche le righe con margine oltre l'8%: li' la probabilita'
    balla, e una lista ordinata per probabilita' le metterebbe in cima
    proprio perche' sono gonfie. La soglia e' tarata sui dati veri: il
    margine mediano di AsianOdds e' il 10.7%, sotto l'8% ci sta meno di
    un terzo delle partite. I prezzi di cui fidarsi sono pochi, e questa
    e' l'informazione, non un difetto del filtro.

    (18/11) PER PINNACLE LA SOGLIA E' IL 10%. L'8% era tarato su AsianOdds;
    Pinnacle sulle leghe minori sta quasi sempre fra l'8 e il 10% e la
    soglia vecchia ne tagliava il 70% (il 18/11: 17 partite su 57). Misurato
    sul 14-17/11, 958 partite Pinnacle col risultato: sopra l'8% le
    probabilita' non sono meno tarate (favorito 8-10%: previsto 50.8%,
    uscito 53.2%; fino al 6%: 54.0 e 62.9). Pochi giorni, scarti dentro il
    caso: da rimisurare con piu' settimane. Oltre il 10% resta fuori
    (mercati a puntata bassa). Le altre fonti restano all'8%.
    """
    # LA VISTA SCHEDINA: la stessa funzione del foglio Excel, cosi'
    # Telegram e Kodi non possono mostrare schedine diverse.
    for r in scelta_schedina(righe):
        r["InSchedina"] = "si"

    migliore = {}
    fuori = []
    for r in righe:
        m = r.get("Margine")
        if m is not None and m > (MARGINE_MAX_PINNACLE if r.get("Fonte") == "Pinnacle" else MARGINE_MAX_ALTRI):
            continue
        # UNA RIGA DELLA SCHEDINA NON SI PERDE MAI QUI SOTTO. Le regole
        # che seguono tengono il "lato migliore" di ogni mercato, quindi
        # un Under scelto per la schedina sarebbe stato buttato via
        # perche' il suo Over e' piu' probabile: in Kodi la vista
        # Schedina risultava piu' corta del foglio Excel, 9 righe contro
        # 14, senza che si capisse perche'.
        if r.get("InSchedina") == "si" or r["Gruppo"] == "Combo":
            fuori.append(r)
            continue
        if r["Mercato"] == "OU 3.5":
            continue
        k = (r["Data"], r["Partita"], r["Mercato"])
        if k not in migliore or r["Prob"] > migliore[k]["Prob"]:
            migliore[k] = r
    fuori.extend(migliore.values())
    return fuori


def scrivi_kodi(righe, cartella):
    if not os.path.isdir(cartella):
        os.makedirs(cartella)
    righe = per_kodi(righe)
    per_data = {}
    for r in righe:
        # Casa e Ospite stanno gia' dentro Partita: in Kodi servono solo
        # per agganciare gli stemmi, quindi restano, ma il resto no.
        per_data.setdefault(r["Data"], []).append(r)
    # L'ora italiana, non quella del computer: sul runner di GitHub e' UTC
    # e il sito scriveva "aggiornato alle 01:04" per un giro delle 02:04.
    try:
        from zoneinfo import ZoneInfo
        agg = datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        agg = datetime.now().strftime("%Y-%m-%d %H:%M")
    for d, v in per_data.items():
        v.sort(key=lambda x: (-x["Prob"],
                              x["Margine"] if x["Margine"] is not None else 99,
                              x["Ora"]))
        p = os.path.join(cartella, "prezzi_{}.json".format(d))
        with open(p, "w", encoding="utf-8") as f:
            json.dump(v, f, ensure_ascii=False, indent=1)
    with open(os.path.join(cartella, "prezzi_index.json"), "w",
              encoding="utf-8") as f:
        json.dump({"updated": agg, "date": sorted(per_data)}, f,
                  ensure_ascii=False, indent=1)
    for d in sorted(per_data):
        print("  {}  {:>5} selezioni".format(d, len(per_data[d])))
    print("Kodi: prezzi_*.json in {}".format(cartella))


def main():
    ap = argparse.ArgumentParser(description="Prezzi equi dal mercato")
    ap.add_argument("--dati", default="ghdata")
    ap.add_argument("--xlsx", default=None, help="cartella per l'Excel")
    ap.add_argument("--kodi", default=None, help="cartella per i JSON")
    ap.add_argument("--tutte", action="store_true",
                    help="anche le giornate passate (default: da oggi)")
    ap.add_argument("--loghi", default=None,
                    help="copia locale di loghi.json (default: la scarica "
                         "da Caltiki/pronostici-repo, ramo main)")
    a = ap.parse_args()

    global _LOGHI_FILE
    _LOGHI_FILE = a.loghi

    righe = carica(a.dati, not a.tutte)
    if not righe:
        print("Nessuna partita con linea O/U e de-vig: niente da calcolare.")
        return 0
    partite = len({(r["Data"], r["Partita"]) for r in righe})
    print("partite con prezzo: {}   selezioni: {}".format(partite, len(righe)))
    for g in ("Combo", "Gol NoGol", "Under Over", "1X2"):
        print("  {:<12} {:>6}".format(g, sum(1 for r in righe if r["Gruppo"] == g)))
    if a.xlsx:
        scrivi_xlsx(righe, a.xlsx)
    if a.kodi:
        scrivi_kodi(righe, a.kodi)
    return 0


if __name__ == "__main__":
    sys.exit(main())
