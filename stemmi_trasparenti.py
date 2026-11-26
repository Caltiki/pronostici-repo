#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stemmi_trasparenti.py - Toglie lo sfondo chiaro pieno dagli stemmi (26/11).

Molti stemmi (API-Football per le squadre minori, alcuni del pacchetto
Kodi) sono un quadrato bianco o grigio chiaro con lo stemma dentro: sul
sito, in tema scuro e sulle card colorate, si vede il riquadro. Qui:

  - si salta l'immagine che ha gia' la trasparenza sul bordo (gia' fatta,
    o fatta bene in origine): lo script si puo' rilanciare quante volte si
    vuole, tocca solo quelle da fare;
  - il bordo dell'immagine deve essere di UN colore solo e CHIARO (quasi
    tutto entro DIST_BORDO dal colore mediano, luminosita' >= LUCE_MIN):
    uno stemma quadrato colorato che riempie il riquadro non si tocca;
  - lo sfondo e' quello che si raggiunge dal bordo restando vicini al suo
    colore (riempimento dal bordo, non "tutto il bianco": il bianco DENTRO
    lo stemma resta);
  - sul contorno dello stemma la trasparenza sfuma (TOLL_PIENO-TOLL_BORDO),
    col colore ripulito dal bianco, cosi' non resta l'alone;
  - se lo sfondo tolto e' meno del 3% o piu' dell'85% dell'immagine non si
    tocca niente: o non c'era sfondo, o lo stemma stesso e' chiaro e
    toccava il bordo, e il riempimento ci sarebbe entrato dentro.

Uso:  python stemmi_trasparenti.py CARTELLA [CARTELLA ...] [--prova]
      --prova: non scrive, dice solo cosa farebbe.
Serve: pip install pillow numpy scipy.
"""
import os
import sys
import numpy as np
from PIL import Image
from scipy import ndimage

DIST_BORDO = 28      # differenza di colore ammessa lungo il bordo
QUOTA_BORDO = 0.55   # quanta parte del bordo deve stare in quel colore (il resto e' stemma che tocca il bordo)
LUCE_MIN = 205       # il colore del bordo dev'essere chiaro
TOLL_PIENO = 34      # fino a qui e' sfondo pieno (trasparente)
TOLL_BORDO = 90      # da TOLL_PIENO a qui sfuma, sul contorno
MIN_TOLTO, MAX_TOLTO = 0.03, 0.85


def tratta(percorso, prova=False):
    """'fatto', o il motivo per cui non si tocca."""
    try:
        im = Image.open(percorso)
        im.load()
    except Exception:
        return "illeggibile"
    im = im.convert("RGBA")
    a = np.asarray(im).astype(np.int16)
    h, w = a.shape[:2]
    if w < 8 or h < 8:
        return "troppo piccola"
    rgb, alfa = a[:, :, :3], a[:, :, 3]
    giro = np.zeros((h, w), bool)
    giro[0, :] = giro[-1, :] = giro[:, 0] = giro[:, -1] = True
    if (alfa[giro] < 250).mean() > 0.05:
        return "gia' trasparente"
    med = np.median(rgb[giro], axis=0)
    if 0.299 * med[0] + 0.587 * med[1] + 0.114 * med[2] < LUCE_MIN:
        return "bordo scuro o colorato"
    dist = np.abs(rgb - med).max(axis=2)
    if (dist[giro] <= DIST_BORDO).mean() < QUOTA_BORDO:
        return "bordo non uniforme"
    # riempimento dal bordo: le zone vicine al colore del bordo che lo toccano
    vicino = dist <= TOLL_PIENO
    etich, _ = ndimage.label(vicino)
    dal_bordo = np.unique(etich[giro & vicino])
    fondo = np.isin(etich, dal_bordo[dal_bordo > 0])
    tolti = fondo.mean()
    if tolti < MIN_TOLTO:
        return "sfondo quasi assente"
    if tolti > MAX_TOLTO:
        return "stemma chiaro sul bordo"
    if prova:
        return "fatto"
    out = a.astype(np.float64)
    nuova_alfa = alfa.astype(np.float64)
    nuova_alfa[fondo] = 0
    # contorno: i pixel accanto allo sfondo sfumano, col colore ripulito
    contorno = ndimage.binary_dilation(fondo) & ~fondo & (dist < TOLL_BORDO)
    al = np.clip((dist - TOLL_PIENO) / float(TOLL_BORDO - TOLL_PIENO), 0, 1)
    al_c = al[contorno]
    sicuro = np.maximum(al_c, 0.02)[:, None]
    pulito = np.clip((rgb[contorno] - (1 - sicuro) * med) / sicuro, 0, 255)
    out[:, :, :3][contorno] = pulito
    nuova_alfa[contorno] = alfa[contorno] * np.where(al_c <= 0.02, 0, al_c)
    out[:, :, 3] = nuova_alfa
    out[:, :, :3][fondo] = med
    risultato = Image.fromarray(np.round(out).astype(np.uint8), "RGBA")
    tmp = percorso + ".tmp.png"
    risultato.save(tmp, "PNG", optimize=True)
    os.replace(tmp, percorso)
    return "fatto"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    prova = "--prova" in sys.argv
    if not args:
        print(__doc__)
        return 2
    conta = {}
    for cart in args:
        for nome in sorted(os.listdir(cart)):
            if not nome.lower().endswith(".png"):
                continue
            esito = tratta(os.path.join(cart, nome), prova)
            conta[esito] = conta.get(esito, 0) + 1
    print("stemmi_trasparenti{}: {}".format(" (prova)" if prova else "",
          ", ".join("{} {}".format(v, k) for k, v in sorted(conta.items(), key=lambda x: -x[1]))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
