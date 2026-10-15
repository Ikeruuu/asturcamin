"""
AsturCamín · Formato común de entrada al modelo de decisión

Un único sitio que define CÓMO ve el modelo cada aviso: el estado convertido
en texto y las cinco preguntas del contrato. Lo usan el entrenamiento
(exportar_finetune.py), la evaluación (evaluar.py) y, más adelante, el backend.
Si se cambia algo aquí, hay que volver a entrenar: el modelo afinado solo
entiende el formato con el que se entrenó.

No importa laya, así que se puede usar en cualquier sitio.
"""

import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CONTRATO = json.loads((RAIZ / "contrato.json").read_text(encoding="utf-8"))
NIVELES = [n["nombre"] for n in CONTRATO["preguntas"]["severidad"]["niveles"]]
OPCIONES = CONTRATO["preguntas"]["organismo"]["opciones"]
NOULS = ("riesgo_vial", "urgente", "inspeccion")

PREGUNTAS_NOUL = {
    "riesgo_vial": "¿Supone esta incidencia un riesgo inmediato para quienes circulan por la vía?",
    "urgente": "¿Hay que intervenir de inmediato, en lugar de programar la reparación para otro día?",
    "inspeccion": "¿Hace falta que un técnico vaya al lugar a inspeccionar antes de actuar?",
}

LUZ = {"dia": "de día", "crepusculo": "al anochecer o amanecer", "noche": "de noche"}


def estado_como_texto(e):
    """Convierte el estado en frases fijas. Es una plantilla determinista, no texto generado."""
    def si_no(v):
        return "sí" if v else ("no" if v is False else "desconocido")
    lluvia = "desconocida" if e["lluvia_24h_mm"] is None else f'{e["lluvia_24h_mm"]} mm'
    temperatura = "desconocida" if e["temperatura_c"] is None else f'{e["temperatura_c"]} °C'
    via = e["via"] if e["via"] else "vía desconocida"
    lineas = [
        f'Aviso ciudadano: {e["texto"]}',
        f'Lugar: {via}, vía {e["tipo_via"]} de titularidad {e["titular"]}'
        + (", zona de montaña" if e["zona_montana"] else ""),
        f'Momento: {LUZ.get(e["luz"], e["luz"])}, {e["estacion"]}',
        f'Tiempo: lluvia en 24 h {lluvia}, aviso meteorológico {e["alerta_aemet"]}, '
        f'temperatura {temperatura}',
        f'Circulación en la vía: intensidad {e["intensidad_trafico"]}',
        f'Historial de la zona: {e["historial_zona"].replace("_", " ")}; riesgo de fauna {e["riesgo_fauna"]}',
        f'Vía cortada: {si_no(e["via_cortada"])}; cadenas obligatorias: {si_no(e["cadenas"])}; '
        f'incidencia oficial cerca: {si_no(e["incidencia_oficial_cerca"])}; '
        f'avisos parecidos cerca: {e["avisos_similares_cerca"]}',
    ]
    return "\n".join(lineas)


def construir_preguntas(etiquetas="nombres"):
    """Las cinco preguntas en formato Laya. Devuelve (preguntas, mapa clave->organismo)."""
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
