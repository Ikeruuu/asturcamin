"""
AsturCamín · Primera prueba del modelo de decisión (paso 3)

Carga contrato.json, convierte las 5 preguntas al formato de Laya y clasifica
los 5 ejemplos de la diapositiva (datos/semillas_reto.jsonl), comparando con
las etiquetas puestas a mano.

Uso (desde la raíz del repositorio, con el .venv activado):
    python modelo/probar_laya.py

La primera ejecución descarga el modelo multilingüe desde Hugging Face.
"""

import json
import time
from pathlib import Path

import laya

RAIZ = Path(__file__).resolve().parent.parent
CONTRATO = json.loads((RAIZ / "contrato.json").read_text(encoding="utf-8"))
NIVELES = [n["nombre"] for n in CONTRATO["preguntas"]["severidad"]["niveles"]]


def preguntas_laya(contrato):
    """Traduce las preguntas del contrato al formato de Laya."""
    p = contrato["preguntas"]
    q = {
        "organismo": {
            "type": "choice",
            "instructions": p["organismo"]["instrucciones"],
            "criteria": p["organismo"]["opciones"],
        },
        "severidad": {
            "type": "score",
            "instructions": p["severidad"]["instrucciones"],
            # Laya espera los niveles ordenados de menor a mayor
            "criteria": [f'{n["nombre"]}: {n["criterio"]}' for n in p["severidad"]["niveles"]],
        },
    }
    for clave in ("riesgo_vial", "urgente", "inspeccion"):
        q[clave] = {"type": "noul", "instructions": p[clave]["instrucciones"]}
    return q


def nivel_desde_score(score):
    """Convierte el score continuo (0 = Baja … 3 = Crítica) al nivel más cercano."""
    i = min(max(round(score), 0), len(NIVELES) - 1)
    return NIVELES[i]


def main():
    print("Cargando modelo multilingüe (la primera vez descarga desde Hugging Face)…")
    t0 = time.perf_counter()
    agente = laya.load("convaiinnovations/laya", subfolder="multilingual")
    print(f"Modelo cargado en {time.perf_counter() - t0:.1f} s\n")

    preguntas = preguntas_laya(CONTRATO)
    semillas = [json.loads(l) for l in (RAIZ / "datos" / "semillas_reto.jsonl").read_text(encoding="utf-8").splitlines()]

    aciertos = {"organismo": 0, "severidad": 0}
    for i, s in enumerate(semillas):
        t0 = time.perf_counter()
        res = agente.predict(s["estado"], preguntas)
        ms = (time.perf_counter() - t0) * 1000
        a = res["answers"]
        if i == 0:
            print("Respuesta completa del primer aviso (para ver el formato exacto):")
            print(json.dumps(res, ensure_ascii=False, indent=2, default=str)[:2500], "\n")

        org = a["organismo"].get("choice")
        conf = a["organismo"].get("confidence")
        score = a["severidad"].get("score")
        sev = nivel_desde_score(score) if score is not None else None
        esperado = s["etiquetas"]
        aciertos["organismo"] += org == esperado["organismo"]
        aciertos["severidad"] += sev == esperado["severidad"]

        print(f'· {s["estado"]["texto"]}')
        print(f'  organismo  {org} ({conf:.0%})  | esperado: {esperado["organismo"]}')
        print(f'  severidad  {sev} (score {score:.2f})  | esperado: {esperado["severidad"]}')
        print(f'  riesgo_vial {a["riesgo_vial"].get("noul"):.2f}  urgente {a["urgente"].get("noul"):.2f}  '
              f'inspeccion {a["inspeccion"].get("noul"):.2f}  | esperado: '
              f'{esperado["riesgo_vial"]}, {esperado["urgente"]}, {esperado["inspeccion"]}')
        print(f"  {ms:.0f} ms\n")

    n = len(semillas)
    print(f'Aciertos: organismo {aciertos["organismo"]}/{n} · severidad {aciertos["severidad"]}/{n}')


if __name__ == "__main__":
    main()
