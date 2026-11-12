"""Anomalie degli import come righe di testo, per la console e per il log delle esecuzioni."""

from collections import defaultdict


def righe_anomalie(anomalie, massimo=None):
    """`anomalie` è una lista di (tipo, riferimento, dettaglio); per ogni tipo si mostrano al massimo `massimo` righe."""
    gruppi = defaultdict(list)
    for tipo, riferimento, dettaglio in anomalie:
        gruppi[tipo].append(f"{riferimento}: {dettaglio}" if dettaglio else riferimento)
    righe = [f"Anomalie: {len(anomalie)}"]
    for tipo, elenco in gruppi.items():
        righe.append(f"  {tipo} ({len(elenco)})")
        righe += [f"    {riga}" for riga in elenco[:massimo]]
        if massimo and len(elenco) > massimo:
            righe.append(f"    ... e altre {len(elenco) - massimo}")
    return righe
