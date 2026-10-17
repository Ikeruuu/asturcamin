"""
AsturCamín · Evaluación del modelo de decisión (paso 3)

Mide aciertos sobre N avisos y permite comparar configuraciones:

  --checkpoint    multilingual | english | typed-decisions   (modelos base de Laya)
  --modelo-local  carpeta de un modelo afinado (por ejemplo, el que sale de Kaggle)
  --estado        json | texto   (texto = plantilla fija de modelo/formato.py)
  --etiquetas     nombres | neutras
  --datos         train (por defecto) | val (la validación del afinado) | test (medida final, con --final)

Uso (desde la raíz, con el .venv activado):
    python modelo/evaluar.py --n 100 --estado texto --etiquetas nombres
    python modelo/evaluar.py --modelo-local modelo/laya_asturcamin --datos val
    python modelo/evaluar.py --modelo-local modelo/laya_asturcamin --datos test --final   (UNA sola vez)

Con val y test se usan todos los avisos (no se muestrea). datos/avisos_test.jsonl es solo para la
medida final: se pasa una vez, con el modelo definitivo, y no se vuelve a ajustar nada después.
Guarda las predicciones en modelo/resultados/<configuración>.csv para analizar fallos.
Además de los aciertos, muestra dónde falla la severidad: matriz de confusión, si se queda
corto o se pasa, cómo queda tras la regla del backend (Alta + Crítica >= umbral -> al menos Alta)
y qué tipos de incidencia y agravantes acumulan los errores.
"""

import argparse
import csv
import json
import random
import time
from collections import Counter, defaultdict
from pathlib import Path

import laya

from formato import CONTRATO, NIVELES, NOULS, RAIZ, construir_preguntas, estado_como_texto

UMBRAL_ALTA = CONTRATO["umbrales"]["severidad_minima_alta_si_alta_mas_critica_desde"]


def cargar_modelo(checkpoint, modelo_local):
    if modelo_local:
        return laya.Agent(modelo_local, device="cpu")
    if checkpoint == "english":
        return laya.load("convaiinnovations/laya")
    return laya.load("convaiinnovations/laya", subfolder=checkpoint)


def cargar_avisos(datos):
    if datos == "test":
        ruta = RAIZ / "datos" / "avisos_test.jsonl"
        return [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines()]
    if datos == "val":
        ruta = RAIZ / "datos" / "finetune" / "finetune_val.jsonl"
        filas = [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines()]
        ids = {f["id"] for f in filas}
        train = [json.loads(l) for l in (RAIZ / "datos" / "avisos_train.jsonl").read_text(encoding="utf-8").splitlines()]
        return [a for a in train if a["id"] in ids]
    return [json.loads(l) for l in (RAIZ / "datos" / "avisos_train.jsonl").read_text(encoding="utf-8").splitlines()]


def muestra_estratificada(avisos, n, semilla):
    grupos = defaultdict(list)
    for f in avisos:
        grupos[f["etiquetas"]["organismo"]].append(f)
    rng = random.Random(semilla)
    por_grupo = max(1, n // len(grupos))
    elegidas = []
    for g in sorted(grupos):
        rng.shuffle(grupos[g])
        elegidas += grupos[g][:por_grupo]
    rng.shuffle(elegidas)
    return elegidas[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--checkpoint", default="multilingual", choices=["multilingual", "english", "typed-decisions"])
    ap.add_argument("--modelo-local", default=None)
    ap.add_argument("--estado", default="texto", choices=["json", "texto"])
    ap.add_argument("--etiquetas", default="nombres", choices=["nombres", "neutras"])
    ap.add_argument("--datos", default="train", choices=["train", "val", "test"])
    ap.add_argument("--final", action="store_true", help="obligatorio con --datos test: confirma la medida final")
    ap.add_argument("--semilla", type=int, default=7)
    args = ap.parse_args()
    base = "afinado" if args.modelo_local else args.checkpoint
    nombre = f"{base}_{args.datos}_{args.estado}_{args.etiquetas}"
    if args.datos == "test":
        nombre = f"MEDIDA_FINAL_{nombre}_{time.strftime('%Y%m%d_%H%M')}"

    if args.datos == "test" and not args.final:
        raise SystemExit("El conjunto de test es para la medida final. Si es el modelo definitivo, añade --final.")
    todos = cargar_avisos(args.datos)
    avisos = todos if args.datos in ("val", "test") else muestra_estratificada(todos, args.n, args.semilla)
    print(f"Configuración: {nombre} · {len(avisos)} avisos")
    t0 = time.perf_counter()
    agente = cargar_modelo(args.checkpoint, args.modelo_local)
    print(f"Modelo cargado en {time.perf_counter() - t0:.1f} s")

    preguntas, mapa = construir_preguntas(args.etiquetas)

    filas, latencias = [], []
    ok_org = ok_sev = ok_sev1 = 0
    ok_noul = Counter()
    conf_org = Counter()
    conf_sev, conf_sev_reglas = Counter(), Counter()
    fallos_tipo, total_tipo, fallos_agr = Counter(), Counter(), Counter()
    for i, a in enumerate(avisos, 1):
        estado = estado_como_texto(a["estado"]) if args.estado == "texto" else a["estado"]
        t = time.perf_counter()
        r = agente.predict(estado, preguntas)["answers"]
        latencias.append((time.perf_counter() - t) * 1000)

        org = mapa.get(r["organismo"]["choice"], r["organismo"]["choice"])
        p_sev = r["severidad"].get("probabilities", {})
        sev_i = int(max(p_sev, key=p_sev.get)) if p_sev else min(max(round(r["severidad"]["score"]), 0), 3)
        sev = NIVELES[sev_i]
        et = a["etiquetas"]

        ok_org += org == et["organismo"]
        ok_sev += sev == et["severidad"]
        ok_sev1 += abs(sev_i - NIVELES.index(et["severidad"])) <= 1
        conf_org[(et["organismo"], org)] += 1
        # Misma regla que aplica el backend: si Alta + Crítica pesa lo suficiente, al menos Alta
        p_niv = {NIVELES[int(k)]: v for k, v in p_sev.items()} if p_sev else {}
        sev_reglas = sev
        if p_niv.get("Alta", 0) + p_niv.get("Crítica", 0) >= UMBRAL_ALTA and sev_i < 2:
            sev_reglas = "Alta"
        conf_sev[(et["severidad"], sev)] += 1
        conf_sev_reglas[(et["severidad"], sev_reglas)] += 1
        meta = a.get("meta", {})
        tipo = meta.get("tipo", "?")
        total_tipo[tipo] += 1
        if sev != et["severidad"]:
            fallos_tipo[tipo] += 1
            for ag in meta.get("agravantes", []) or ["(sin agravantes)"]:
                fallos_agr[ag] += 1
        fila = {"id": a["id"], "texto": a["estado"]["texto"], "organismo_real": et["organismo"], "organismo_pred": org,
                "organismo_prob": round(r["organismo"].get("answer_confidence", 0), 3),
                "severidad_real": et["severidad"], "severidad_pred": sev,
                "severidad_score": round(r["severidad"]["score"], 2),
                "severidad_con_reglas": sev_reglas,
                "tipo": tipo, "agravantes": ",".join(meta.get("agravantes", [])),
                **{f"p_{nv}": round(p_niv.get(nv, 0), 3) for nv in NIVELES}}
        for k in NOULS:
            p = r[k]["noul"]
            ok_noul[k] += (p >= 0.5) == et[k]
            fila[f"{k}_real"] = et[k]
            fila[f"{k}_prob"] = round(p, 3)
        filas.append(fila)
        if i % 10 == 0:
            print(f"  {i}/{len(avisos)}…")

    n = len(avisos)
    print(f"\n=== {nombre} ===")
    print(f"Organismo:  {ok_org}/{n} ({ok_org / n:.0%})")
    print(f"Severidad:  {ok_sev}/{n} exacta ({ok_sev / n:.0%}) · {ok_sev1}/{n} a un nivel o menos ({ok_sev1 / n:.0%})")
    for k in NOULS:
        probs = [f[f"{k}_prob"] for f in filas]
        print(f"{k:12s}{ok_noul[k]}/{n} ({ok_noul[k] / n:.0%}) · prob. media {sum(probs) / n:.2f}, "
              f"mín {min(probs):.2f}, máx {max(probs):.2f}")
    print(f"Latencia media: {sum(latencias) / n:.0f} ms")

    def matriz(conf, titulo):
        print(f"\n{titulo} (filas = real, columnas = predicho)")
        print(" " * 9 + "".join(f"{nv:>9s}" for nv in NIVELES))
        for real in NIVELES:
            print(f"{real:>9s}" + "".join(f"{conf[(real, pred)]:>9d}" for pred in NIVELES))
        corto = sum(c for (r_, p_), c in conf.items() if NIVELES.index(p_) < NIVELES.index(r_))
        pasa = sum(c for (r_, p_), c in conf.items() if NIVELES.index(p_) > NIVELES.index(r_))
        exactos = sum(c for (r_, p_), c in conf.items() if r_ == p_)
        crit = sum(c for (r_, _), c in conf.items() if r_ == "Crítica")
        crit_baja = sum(c for (r_, p_), c in conf.items() if r_ == "Crítica" and NIVELES.index(p_) < 2)
        print(f"Exacta {exactos}/{n} ({exactos / n:.0%}) · se queda corto {corto} · se pasa {pasa} · "
              f"Críticas que bajan a Media o Baja: {crit_baja}/{crit}")

    matriz(conf_sev, "Severidad del modelo")
    matriz(conf_sev_reglas, f"Severidad tras la regla del backend (Alta + Crítica >= {UMBRAL_ALTA})")

    print("\nFallos de severidad por tipo de incidencia:")
    for tipo, c in fallos_tipo.most_common(12):
        print(f"  {tipo:24s} {c}/{total_tipo[tipo]}")
    print("Agravantes presentes en los fallos:", dict(fallos_agr.most_common()))

    print("\nConfusión de organismo (real -> predicho):")
    for (real, pred), c in sorted(conf_org.items(), key=lambda x: -x[1]):
        if real != pred:
            print(f"  {real} -> {pred}: {c}")

    destino = RAIZ / "modelo" / "resultados"
    destino.mkdir(parents=True, exist_ok=True)
    with open(destino / f"{nombre}.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(filas[0]), delimiter=";")
        w.writeheader()
        w.writerows(filas)
    print(f"\nPredicciones guardadas en modelo/resultados/{nombre}.csv")


if __name__ == "__main__":
    main()
