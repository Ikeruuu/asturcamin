"""
AsturCamín · Evaluación del modelo de decisión sobre avisos de entrenamiento (paso 3)

Mide aciertos sobre N avisos de datos/avisos_train.jsonl (NUNCA usa el test) y
permite comparar configuraciones:

  --checkpoint  multilingual | english | typed-decisions
  --estado      json  (el estado tal cual)
                texto (el estado convertido en frases fijas con plantilla; no es texto generado)
  --etiquetas   nombres (Conservación de carreteras, Tráfico…)
                neutras  (A, B, C… con la descripción como criterio)

Uso (desde la raíz, con el .venv activado):
    python modelo/evaluar.py --n 100 --checkpoint multilingual --estado texto --etiquetas neutras

Guarda las predicciones en modelo/resultados/<configuración>.csv para analizar fallos.
"""

import argparse
import csv
import json
import random
import time
from collections import Counter, defaultdict
from pathlib import Path

import laya

RAIZ = Path(__file__).resolve().parent.parent
CONTRATO = json.loads((RAIZ / "contrato.json").read_text(encoding="utf-8"))
NIVELES = [n["nombre"] for n in CONTRATO["preguntas"]["severidad"]["niveles"]]
OPCIONES = CONTRATO["preguntas"]["organismo"]["opciones"]
NOULS = ("riesgo_vial", "urgente", "inspeccion")

# Las preguntas sí/no formuladas como preguntas (Laya las espera así en sus ejemplos)
PREGUNTAS_NOUL = {
    "riesgo_vial": "¿Supone esta incidencia un riesgo inmediato para quienes circulan por la vía?",
    "urgente": "¿Hay que intervenir de inmediato, en lugar de programar la reparación para otro día?",
    "inspeccion": "¿Hace falta que un técnico vaya al lugar a inspeccionar antes de actuar?",
}

LUZ = {"dia": "de día", "crepusculo": "al anochecer o amanecer", "noche": "de noche"}


def cargar_modelo(checkpoint):
    if checkpoint == "english":
        return laya.load("convaiinnovations/laya")
    return laya.load("convaiinnovations/laya", subfolder=checkpoint)


def estado_como_texto(e):
    """Convierte el estado en frases fijas. Es una plantilla determinista, no texto generado."""
    def si_no(v):
        return "sí" if v else ("no" if v is False else "desconocido")
    lluvia = "desconocida" if e["lluvia_24h_mm"] is None else f'{e["lluvia_24h_mm"]} mm'
    lineas = [
        f'Aviso ciudadano: {e["texto"]}',
        f'Lugar: {e["via"]}, vía {e["tipo_via"]} de titularidad {e["titular"]}'
        + (", zona de montaña" if e["zona_montana"] else ""),
        f'Momento: {LUZ.get(e["luz"], e["luz"])}, {e["estacion"]}',
        f'Tiempo: lluvia en 24 h {lluvia}, aviso meteorológico {e["alerta_aemet"]}, '
        f'temperatura {e["temperatura_c"]} °C',
        f'Tráfico en la vía: intensidad {e["intensidad_trafico"]}',
        f'Historial de la zona: {e["historial_zona"].replace("_", " ")}; riesgo de fauna {e["riesgo_fauna"]}',
        f'Vía cortada: {si_no(e["via_cortada"])}; cadenas obligatorias: {si_no(e["cadenas"])}; '
        f'incidencia oficial cerca: {si_no(e["incidencia_oficial_cerca"])}; '
        f'avisos parecidos cerca: {e["avisos_similares_cerca"]}',
    ]
    return "\n".join(lineas)


def construir_preguntas(etiquetas):
    p = CONTRATO["preguntas"]
    if etiquetas == "neutras":
        claves = [chr(ord("A") + i) for i in range(len(OPCIONES))]
        criterio = {k: f"{nombre}. {desc}" for k, (nombre, desc) in zip(claves, OPCIONES.items())}
        mapa = dict(zip(claves, OPCIONES))
    else:
        criterio = dict(OPCIONES)
        mapa = {k: k for k in OPCIONES}
    q = {
        "organismo": {"type": "choice", "instructions": p["organismo"]["instrucciones"], "criteria": criterio},
        "severidad": {"type": "score", "instructions": p["severidad"]["instrucciones"],
                      "criteria": [f'{n["nombre"]}: {n["criterio"]}' for n in p["severidad"]["niveles"]]},
    }
    for k in NOULS:
        q[k] = {"type": "noul", "instructions": PREGUNTAS_NOUL[k]}
    return q, mapa


def muestra_estratificada(n, semilla):
    filas = [json.loads(l) for l in (RAIZ / "datos" / "avisos_train.jsonl").read_text(encoding="utf-8").splitlines()]
    grupos = defaultdict(list)
    for f in filas:
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
    ap.add_argument("--estado", default="texto", choices=["json", "texto"])
    ap.add_argument("--etiquetas", default="neutras", choices=["nombres", "neutras"])
    ap.add_argument("--semilla", type=int, default=7)
    args = ap.parse_args()
    nombre = f"{args.checkpoint}_{args.estado}_{args.etiquetas}"

    print(f"Configuración: {nombre} · {args.n} avisos de entrenamiento")
    t0 = time.perf_counter()
    agente = cargar_modelo(args.checkpoint)
    print(f"Modelo cargado en {time.perf_counter() - t0:.1f} s")

    preguntas, mapa = construir_preguntas(args.etiquetas)
    avisos = muestra_estratificada(args.n, args.semilla)

    filas, latencias = [], []
    ok_org = ok_sev = ok_sev1 = 0
    ok_noul = Counter()
    conf_org = Counter()
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
        fila = {"id": a["id"], "texto": a["estado"]["texto"], "organismo_real": et["organismo"], "organismo_pred": org,
                "organismo_prob": round(r["organismo"].get("answer_confidence", 0), 3),
                "severidad_real": et["severidad"], "severidad_pred": sev,
                "severidad_score": round(r["severidad"]["score"], 2)}
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
