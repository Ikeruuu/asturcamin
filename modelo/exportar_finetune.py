"""
AsturCamín · Exporta el dataset de entrenamiento al formato del notebook de Laya

Convierte datos/avisos_train.jsonl en dos ficheros con el formato que espera el
notebook oficial de afinado de Laya (laya_finetune_typed_decisions_2xT4_kaggle):

  datos/finetune/finetune_train.jsonl  (90 % de las incidencias de train)
  datos/finetune/finetune_val.jsonl    (10 %, para medir el modelo afinado)

Cada línea: {"id", "workflow", "state", "questions", "gold"}, con state, questions
y gold como cadenas JSON. La separación es por incidencia, para que los avisos
duplicados de un mismo hecho no queden a ambos lados.

El conjunto de test (datos/avisos_test.jsonl) NO se toca.

Uso (desde la raíz):
    python modelo/exportar_finetune.py
"""

import json
import random
from collections import Counter, defaultdict

from formato import NIVELES, NOULS, OPCIONES, RAIZ, construir_preguntas, estado_como_texto

SEMILLA = 11
PROP_VAL = 0.10


def gold_de(etiquetas):
    org = etiquetas["organismo"]
    nivel = NIVELES.index(etiquetas["severidad"])
    gold = {
        "organismo": {"label": org,
                      "probabilities": {o: (1.0 if o == org else 0.0) for o in OPCIONES}},
        "severidad": {"label": nivel, "score": float(nivel),
                      "probabilities": {str(i): (1.0 if i == nivel else 0.0) for i in range(len(NIVELES))}},
    }
    for k in NOULS:
        v = bool(etiquetas[k])
        gold[k] = {"label": "true" if v else "false", "noul": 1.0 if v else 0.0,
                   "probabilities": {"true": 1.0 if v else 0.0, "false": 0.0 if v else 1.0}}
    return gold


def main():
    avisos = [json.loads(l) for l in (RAIZ / "datos" / "avisos_train.jsonl").read_text(encoding="utf-8").splitlines()]
    preguntas, _ = construir_preguntas("nombres")
    preguntas_json = json.dumps(preguntas, ensure_ascii=False)

    grupos = defaultdict(list)
    for a in avisos:
        grupos[a["id_incidencia"]].append(a)
    ids = sorted(grupos)
    random.Random(SEMILLA).shuffle(ids)
    n_val = round(len(ids) * PROP_VAL)
    val_ids = set(ids[:n_val])

    destino = RAIZ / "datos" / "finetune"
    destino.mkdir(parents=True, exist_ok=True)
    salidas = {"train": [], "val": []}
    for gid, filas in grupos.items():
        parte = "val" if gid in val_ids else "train"
        for a in filas:
            salidas[parte].append({
                "id": a["id"],
                "workflow": "asturcamin",
                "state": json.dumps(estado_como_texto(a["estado"]), ensure_ascii=False),
                "questions": preguntas_json,
                "gold": json.dumps(gold_de(a["etiquetas"]), ensure_ascii=False),
            })

    for parte, filas in salidas.items():
        with open(destino / f"finetune_{parte}.jsonl", "w", encoding="utf-8") as fh:
            for f in filas:
                fh.write(json.dumps(f, ensure_ascii=False) + "\n")
        orgs = Counter(json.loads(f["gold"])["organismo"]["label"] for f in filas)
        print(f"finetune_{parte}.jsonl: {len(filas)} avisos ({len(filas) * 5} preguntas) · {dict(orgs)}")


if __name__ == "__main__":
    main()
