"""
AsturCamín · Generador del dataset sintético (paso 2)

Crea avisos ciudadanos etiquetados, coherentes con contrato.json:
  - cada aviso lleva su estado completo (texto + contexto) y sus 5 etiquetas
  - las etiquetas salen de reglas explícitas (tipo de incidencia + agravantes de contexto)
  - incluye duplicados (varios avisos de la misma incidencia) y pares con contextos opuestos
  - separa train/test por incidencia (los duplicados nunca quedan a ambos lados)

Uso:
    python generar_dataset.py --n 850 --semilla 42 --contrato contrato.json --salida datos/

Solo usa la biblioteca estándar de Python.
"""

import argparse
import csv
import json
import math
import os
import random
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta

NIVELES = ["Baja", "Media", "Alta", "Crítica"]
CONS = "Conservación de carreteras"
EMER = "Emergencias"
AYTO = "Ayuntamiento"
TRAF = "Tráfico"
MAMB = "Medio ambiente"

# --------------------------------------------------------------------------
# Vías reales de Asturias (coordenadas aproximadas de los extremos del tramo)
# prob_montana = probabilidad de que un punto del tramo sea zona de montaña
# --------------------------------------------------------------------------
VIAS = [
    # via,     titular,      tipo_via,   inicio,             fin,               prob_montana, pueblos
    ("AS-114", "Principado", "regional", (43.350, -5.120), (43.330, -4.590), 0.5, ["Arenas de Cabrales", "Carreña", "Panes"]),
    ("AS-117", "Principado", "regional", (43.300, -5.690), (43.110, -5.230), 0.6, ["El Entrego", "Sotrondio", "Pola de Laviana", "Tarna"]),
    ("AS-15", "Principado", "regional", (43.380, -6.180), (43.180, -6.550), 0.5, ["Cornellana", "Cangas del Narcea"]),
    ("AS-227", "Principado", "regional", (43.250, -6.120), (43.020, -6.250), 0.9, ["Pola de Somiedo", "el puerto de Somiedo"]),
    ("AS-263", "Principado", "regional", (43.450, -5.050), (43.410, -4.760), 0.1, ["Ribadesella", "Llanes"]),
    ("AS-II", "Principado", "autovia", (43.360, -5.840), (43.520, -5.690), 0.0, ["Oviedo", "Gijón"]),
    ("AS-17", "Principado", "regional", (43.550, -5.920), (43.300, -5.690), 0.1, ["Avilés", "Riaño"]),
    ("AS-12", "Principado", "regional", (43.540, -6.940), (43.200, -7.000), 0.6, ["Navia", "Grandas de Salime"]),
    ("LN-8", "Principado", "local", (43.130, -5.830), (43.050, -5.870), 0.9, ["Tuiza", "La Cubilla"]),
    ("N-634", "Estado", "nacional", (43.460, -5.060), (43.410, -6.260), 0.2, ["Ribadesella", "Salas", "Grado"]),
    ("N-632", "Estado", "nacional", (43.480, -5.430), (43.530, -5.600), 0.0, ["Villaviciosa", "Gijón"]),
    ("N-630", "Estado", "nacional", (43.190, -5.780), (42.990, -5.760), 0.9, ["Campomanes", "Pajares"]),
    ("A-66", "Estado", "autovia", (43.360, -5.840), (43.150, -5.800), 0.3, ["Mieres", "Campomanes"]),
    ("A-8", "Estado", "autovia", (43.520, -5.620), (43.430, -4.600), 0.1, ["Gijón", "Llanes"]),
]

CIUDADES = [
    ("Oviedo", (43.3614, -5.8494), ["calle Uría", "calle Fray Ceferino", "avenida de Galicia", "calle General Elorza"]),
    ("Gijón", (43.5322, -5.6611), ["avenida de la Costa", "calle Corrida", "avenida Pablo Iglesias", "avenida de Schulz"]),
    ("Avilés", (43.5547, -5.9248), ["calle La Cámara", "avenida de Lugo", "calle Rivero"]),
    ("Mieres", (43.2503, -5.7770), ["calle Manuel Llaneza", "calle La Vega"]),
    ("Langreo", (43.3000, -5.6900), ["calle Dorado", "avenida de la Constitución"]),
]

# Salida y puesta de sol aproximadas en Oviedo, hora local (decimal), por mes.
# En producción se calcula con la librería astral a partir de lat/lon.
SOL = {1: (8.9, 18.25), 2: (8.5, 18.8), 3: (8.1, 19.6), 4: (8.2, 20.8), 5: (7.6, 21.4), 6: (7.25, 21.8),
       7: (7.5, 21.8), 8: (8.0, 21.25), 9: (8.6, 20.4), 10: (8.9, 19.4), 11: (8.1, 17.9), 12: (8.7, 17.8)}

ESTACION_MESES = {"invierno": [12, 1, 2], "primavera": [3, 4, 5], "verano": [6, 7, 8], "otono": [9, 10, 11]}

# --------------------------------------------------------------------------
# Catálogo de incidencias
#   org_i / org_u : organismo en vía interurbana / urbana (None = no ocurre ahí)
#   base          : severidad base (0 Baja … 3 Crítica)
#   agr           : agravantes que suben un nivel (noche, lluvia, alerta, montana, trafico)
#   calzada       : afecta a la calzada (cuenta para riesgo vial)
#   insp          : necesita inspección presencial
#   urg           : siempre urgente
#   forzar        : condiciones de contexto obligatorias para que el caso sea realista
#   dif           : dificultad (normal / dificil)
# Marcadores de texto: {via} {ref} (lugar), {ctx} (frase de contexto coherente)
# --------------------------------------------------------------------------
I = []


def inc(tipo, org_i, org_u, base, agr, calzada, insp, textos, urg=False, forzar=None, dif="normal", cortada=False,
        riesgo=None, planif=False):
    # riesgo: si el tipo pone en riesgo a quien circula (por defecto, si afecta a la calzada)
    # planif: aunque sea grave, la reparación se puede programar en horas o días (no es urgente)
    I.append(dict(tipo=tipo, org_i=org_i, org_u=org_u, base=base, agr=agr, calzada=calzada, insp=insp,
                  textos=textos, urg=urg, forzar=forzar or {}, dif=dif, cortada=cortada,
                  riesgo=calzada if riesgo is None else riesgo, planif=planif))


inc("roca_carril", CONS, None, 3, ["noche", "lluvia", "montana"], True, True, [
    "Hay una roca ocupando parcialmente un carril en la {via}{ref}{ctx}",
    "piedra grande en mitad del carril en la {via}{ref}{ctx}",
    "Ojo que ha caído una roca a la carretera{ref}, ocupa medio carril{ctx}",
    "Rocas en la calzada de la {via}{ref}, hay que esquivarlas invadiendo el otro carril{ctx}",
    "acaba de caer un pedrusco a la {via}{ref}{ctx}"])
inc("argayo_proximo", CONS, None, 2, ["lluvia", "alerta", "montana"], False, True, [
    "Hay un desprendimiento próximo a la {via}{ref}{ctx}",
    "se está viniendo abajo el talud al lado de la carretera{ref}{ctx}",
    "argayo en la ladera junto a la {via}{ref}, de momento no llega a la calzada{ctx}",
    "Veo tierra y piedras cayendo del monte al borde de la {via}{ref}{ctx}"], forzar={"lluvia_min": 10}, riesgo=True)
inc("argayo_corta", CONS, None, 3, [], True, True, [
    "Un argayo ha cortado la {via}{ref}, no se puede pasar{ctx}",
    "carretera cortada por un desprendimiento{ref}, tierra y árboles en los dos carriles{ctx}",
    "Se ha venido medio monte abajo y la {via} está cortada{ref}{ctx}"], forzar={"lluvia_min": 25}, cortada=True)
inc("arbol_calzada", CONS, AYTO, 3, ["noche", "trafico"], True, True, [
    "Árbol caído atravesado en la {via}{ref}{ctx}",
    "se ha caído un árbol y ocupa toda la calzada{ref}{ctx}",
    "hay un árbol tirado en medio de la carretera{ref} tras el temporal{ctx}"], forzar={"alerta_min": "amarilla"})
inc("ramas_arcen", CONS, AYTO, 0, ["noche", "alerta"], False, False, [
    "Ramas en el arcén de la {via}{ref}{ctx}",
    "hay unas ramas caídas en la cuneta{ref}, no molestan mucho{ctx}",
    "restos de poda en el borde de la carretera{ref}{ctx}"])
inc("bache_pequeno", CONS, AYTO, 0, ["trafico"], False, False, [
    "Bache pequeño en el arcén de la {via}{ref}{ctx}",
    "hay un bachecillo en el borde{ref}, nada grave{ctx}",
    "El asfalto está algo levantado en el arcén{ref}{ctx}"])
inc("bache_grande", CONS, AYTO, 1, ["noche", "lluvia", "trafico"], True, True, [
    "Bache enorme en el carril de la {via}{ref}{ctx}",
    "hay un agujero grande en la calzada{ref}, casi reviento una rueda{ctx}",
    "Bache muy profundo en mitad del carril{ref}{ctx}"], planif=True)
inc("socavon", CONS, AYTO, 2, ["lluvia", "trafico", "noche"], True, True, [
    "Se ha abierto un socavón en la {via}{ref}{ctx}",
    "la calzada se está hundiendo{ref}, hay un socavón en el carril derecho{ctx}",
    "Socavón junto al borde del carril{ref}, el asfalto está cediendo{ctx}"])
inc("balsa_agua", CONS, AYTO, 2, ["noche", "alerta", "trafico"], True, False, [
    "Se ha detectado una balsa de agua peligrosa en la {via}{ref}{ctx}",
    "hay una balsa de agua enorme que ocupa el carril{ref}{ctx}",
    "Charca grande en la calzada{ref}, los coches pierden el control al pasar{ctx}",
    "balsa de agua en una curva{ref}, peligro de aquaplaning{ctx}"], forzar={"lluvia_min": 15})
inc("senal_caida_fuera", CONS, AYTO, 1, [], False, False, [
    "Existe una señal de tráfico caída tras un temporal{ref}{ctx}",
    "hay una señal tirada en la cuneta de la {via}{ref}{ctx}",
    "Señal de stop caída fuera de la calzada{ref}{ctx}"])
inc("senal_caida_calzada", CONS, AYTO, 2, ["noche", "trafico"], True, False, [
    "Una señal se ha caído y está en medio del carril{ref}{ctx}",
    "el viento ha tirado una señal a la calzada de la {via}{ref}{ctx}",
    "Panel informativo caído sobre la carretera{ref}{ctx}"])
inc("senal_sucia", CONS, AYTO, 0, [], False, False, [
    "La señal de la curva está muy sucia y casi no se lee{ref}{ctx}",
    "señal descolorida en la {via}{ref}{ctx}",
    "hay una señal tapada por la vegetación{ref}{ctx}"])
inc("semaforo_apagado", None, AYTO, 1, ["trafico", "noche"], True, False, [
    "Un semáforo lleva apagado varias horas en la {via}{ref}{ctx}",
    "semáforo apagado en el cruce de la {via}{ref}{ctx}",
    "No funciona el semáforo{ref}, los coches pasan como pueden{ctx}"])
inc("farola_apagada", None, AYTO, 0, ["noche"], False, False, [
    "Varias farolas apagadas en la {via}{ref}{ctx}",
    "la calle está a oscuras, no funciona el alumbrado{ref}{ctx}",
    "farola fundida en la {via}{ref}{ctx}"])
inc("tapa_alcantarilla", None, AYTO, 2, ["noche", "trafico"], True, True, [
    "Falta la tapa de una alcantarilla en la calzada de la {via}{ref}{ctx}",
    "hay una alcantarilla abierta en mitad de la calle{ref}{ctx}",
    "tapa de registro levantada en el carril{ref}{ctx}"])
inc("acera_rota", None, AYTO, 0, [], False, False, [
    "Baldosas levantadas en la acera de la {via}{ref}{ctx}",
    "la acera está rota{ref}{ctx}",
    "acera hundida junto al paso de peatones{ref}{ctx}"])
inc("barrera_danada", CONS, None, 1, ["montana", "noche"], False, True, [
    "Quitamiedos roto en la {via}{ref}{ctx}",
    "la bionda de la curva está arrancada{ref}{ctx}",
    "barrera de seguridad doblada tras un golpe{ref}{ctx}"], riesgo=True, planif=True)
inc("hielo", CONS, None, 2, ["noche", "montana"], True, False, [
    "Placas de hielo en la {via}{ref}{ctx}",
    "la carretera está helada{ref}, patinan los coches{ctx}",
    "Hielo en la calzada en la zona de sombra{ref}{ctx}"], forzar={"estacion": "invierno", "temp_max": 1})
inc("nieve_puerto", CONS, None, 2, ["alerta", "noche"], True, False, [
    "Está nevando fuerte y la {via} empieza a cubrirse{ref}{ctx}",
    "nieve en la calzada{ref}, sin cadenas no se sube{ctx}",
    "La quitanieves no ha pasado y hay nieve acumulada{ref}{ctx}"],
    forzar={"estacion": "invierno", "temp_max": 1, "montana": True})
inc("animal_muerto", CONS, None, 2, ["noche", "trafico"], True, False, [
    "Hay un jabalí muerto en mitad del carril de la {via}{ref}{ctx}",
    "animal atropellado en la calzada{ref}{ctx}",
    "un corzo muerto en la carretera{ref}, ocupa el carril{ctx}"])
inc("ganado_suelto", TRAF, None, 2, ["noche"], True, False, [
    "Vacas sueltas por la {via}{ref}{ctx}",
    "hay caballos en la carretera{ref}{ctx}",
    "rebaño de ovejas cruzando la {via}{ref}, sin nadie{ctx}"])
inc("fauna_herida", MAMB, None, 1, [], False, False, [
    "Hay un corzo herido en la cuneta de la {via}{ref}{ctx}",
    "un zorro atropellado pero vivo en el arcén{ref}{ctx}",
    "ave rapaz herida al borde de la carretera{ref}{ctx}"])
inc("averiado_calzada", TRAF, TRAF, 2, ["noche", "trafico", "lluvia"], True, False, [
    "Coche averiado parado en el carril de la {via}{ref}{ctx}",
    "hay un camión averiado ocupando un carril{ref}{ctx}",
    "furgoneta parada en la calzada sin triángulos{ref}{ctx}"])
inc("accidente_sin_heridos", TRAF, TRAF, 2, ["noche", "trafico"], True, False, [
    "Choque entre dos coches en la {via}{ref}, parece que no hay heridos{ctx}",
    "alcance sin heridos{ref}, los coches están en el carril{ctx}",
    "un coche se ha salido un poco de la carretera{ref}, la conductora está bien{ctx}"])
inc("accidente_heridos", EMER, EMER, 3, [], True, False, [
    "Accidente con heridos en la {via}{ref}{ctx}",
    "choque fuerte{ref}, hay una persona sangrando{ctx}",
    "Moto accidentada{ref}, el motorista no se levanta{ctx}"], urg=True)
inc("vehiculo_despenado", EMER, None, 3, [], False, False, [
    "Un coche se ha caído por el terraplén de la {via}{ref}{ctx}",
    "vehículo volcado fuera de la carretera{ref}, puede haber alguien dentro{ctx}"], urg=True, riesgo=False)
inc("incendio_cuneta", EMER, None, 3, [], False, False, [
    "Fuego en la cuneta de la {via}{ref}{ctx}",
    "se está quemando el monte al lado de la carretera{ref}{ctx}",
    "hay humo y llamas junto a la {via}{ref}{ctx}"], urg=True, forzar={"lluvia_max": 0}, riesgo=True)
inc("vertido_peligroso", EMER, EMER, 3, [], True, True, [
    "Un camión cisterna pierde líquido en la {via}{ref}{ctx}",
    "vertido de producto químico en la calzada{ref}, huele muy fuerte{ctx}"], urg=True)
inc("mancha_aceite", CONS, AYTO, 2, ["lluvia", "noche"], True, False, [
    "Mancha de aceite grande en la calzada de la {via}{ref}{ctx}",
    "gasoil derramado en la curva{ref}, las motos resbalan{ctx}",
    "hay aceite en la carretera{ref}{ctx}"])
inc("objeto_caido", TRAF, TRAF, 2, ["noche", "trafico"], True, False, [
    "Se le ha caído la carga a un camión en la {via}{ref}{ctx}",
    "hay un neumático en mitad del carril{ref}{ctx}",
    "objetos caídos de una furgoneta en la calzada{ref}{ctx}"])
inc("retencion", TRAF, TRAF, 1, [], False, False, [
    "Retención larga en la {via}{ref}{ctx}",
    "atasco parado{ref}, no se mueve nada{ctx}"])
inc("residuos", MAMB, None, 0, [], False, False, [
    "Alguien ha tirado escombros en la cuneta de la {via}{ref}{ctx}",
    "bolsas de basura abandonadas junto a la carretera{ref}{ctx}"])
inc("vertido_rio", MAMB, None, 1, [], False, True, [
    "El río junto a la {via} baja con espuma y color raro{ref}{ctx}",
    "vertido al regato al lado de la carretera{ref}{ctx}"])
inc("aviso_vago", CONS, None, 2, ["noche", "lluvia"], True, True, [
    "Hay algo raro en la curva de la {via}{ref}, casi me la pego{ctx}",
    "cuidado{ref}, algo en la carretera{ctx}",
    "no sé qué es pero hay algo en el carril{ref}{ctx}"], dif="dificil")

# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------


def haversine_km(a, b):
    r = 6371
    la1, lo1, la2, lo2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def sin_tildes(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def ensuciar(texto, rng):
    """Hace el texto más parecido a como escribe la gente."""
    if rng.random() < 0.25:
        texto = texto.lower()
    if rng.random() < 0.20:
        texto = sin_tildes(texto)
    if rng.random() < 0.15:
        texto = texto.replace("hay ", "ay ", 1)
    if rng.random() < 0.10:
        texto += " porfa"
    if rng.random() < 0.5 and not texto.endswith((".", "!", "?")):
        texto += rng.choice([".", "!", "..."])
    return texto


def luz_de(dt):
    sale, pone = SOL[dt.month]
    h = dt.hour + dt.minute / 60
    if sale - 0.75 <= h < sale + 0.25 or pone - 0.25 <= h < pone + 0.75:
        return "crepusculo"
    return "dia" if sale + 0.25 <= h < pone - 0.25 else "noche"


def estacion_de(mes):
    for est, meses in ESTACION_MESES.items():
        if mes in meses:
            return est


# --------------------------------------------------------------------------
# Generación de contexto
# --------------------------------------------------------------------------


def generar_fecha(rng, forzar):
    if "estacion" in forzar:
        mes = rng.choice(ESTACION_MESES[forzar["estacion"]])
    else:
        mes = rng.randint(1, 12)
    anio = 2025 if mes >= 10 else 2026
    dia = rng.randint(1, 28)
    # más avisos de día que de madrugada
    hora = rng.choices(range(24), weights=[1, 1, 1, 1, 1, 2, 3, 5, 7, 7, 6, 6, 6, 6, 6, 6, 7, 8, 8, 7, 6, 4, 3, 2])[0]
    return datetime(anio, mes, dia, hora, rng.randint(0, 59))


def generar_meteo(rng, estacion, montana, forzar):
    p_lluvia = {"invierno": 0.6, "otono": 0.55, "primavera": 0.45, "verano": 0.25}[estacion]
    lluvia = 0.0
    if rng.random() < p_lluvia:
        lluvia = round(rng.gammavariate(1.2, 12), 1)
    if "lluvia_min" in forzar:
        lluvia = max(lluvia, round(forzar["lluvia_min"] + rng.gammavariate(1.5, 10), 1))
    if "lluvia_max" in forzar:
        lluvia = min(lluvia, forzar["lluvia_max"])

    t_media = {"invierno": 7, "otono": 12, "primavera": 13, "verano": 20}[estacion]
    temp = rng.gauss(t_media, 4) - (5 if montana else 0)
    if "temp_max" in forzar:
        temp = min(temp, forzar["temp_max"] - abs(rng.gauss(0, 2)))
    temp = round(temp, 1)

    if lluvia >= 80:
        alerta = "roja"
    elif lluvia >= 40:
        alerta = rng.choice(["naranja", "naranja", "amarilla"])
    elif lluvia >= 20:
        alerta = rng.choice(["amarilla", "amarilla", "ninguna"])
    else:
        alerta = "ninguna"
    if estacion == "invierno" and montana and temp < 0 and alerta == "ninguna" and rng.random() < 0.5:
        alerta = "amarilla"  # aviso por nieve
    orden = ["ninguna", "amarilla", "naranja", "roja"]
    if "alerta_min" in forzar and orden.index(alerta) < orden.index(forzar["alerta_min"]):
        alerta = forzar["alerta_min"]
    if rng.random() < 0.03:
        alerta = "desconocido"  # simula caída de la API de AEMET
    return lluvia, temp, alerta


def intensidad(rng, tipo_via, hora):
    punta = hora in (7, 8, 9, 13, 14, 18, 19, 20)
    if tipo_via in ("autovia", "urbana"):
        return "alta" if punta or rng.random() < 0.3 else "media"
    if tipo_via == "nacional":
        return rng.choice(["alta", "media"]) if punta else rng.choice(["media", "baja"])
    if tipo_via == "local":
        return "baja"
    return rng.choice(["media", "baja"]) if not punta else rng.choice(["alta", "media"])


def riesgo_fauna(rng, tipo_via, montana, luz):
    if tipo_via == "urbana":
        return "bajo"
    base = 1 if montana else 0
    if luz != "dia":
        base += 1
    if rng.random() < 0.05:
        return "desconocido"
    return ["bajo", "medio", "alto"][min(base, 2)]


# --------------------------------------------------------------------------
# Construcción de un aviso
# --------------------------------------------------------------------------


def elegir_lugar(rng, inc_def):
    opciones = []
    if inc_def["org_i"]:
        opciones.append("interurbano")
    if inc_def["org_u"]:
        opciones.append("urbano")
    ambito = rng.choice(opciones)
    forzar = inc_def["forzar"]
    if ambito == "interurbano":
        candidatas = VIAS
        if forzar.get("montana"):
            candidatas = [v for v in VIAS if v[5] >= 0.5]
        via, titular, tipo_via, a, b, p_mont, pueblos = rng.choice(candidatas)
        t = rng.random()
        lat = a[0] + (b[0] - a[0]) * t + rng.gauss(0, 0.002)
        lon = a[1] + (b[1] - a[1]) * t + rng.gauss(0, 0.002)
        pk = round(haversine_km(a, b) * t, 1)
        montana = True if forzar.get("montana") else rng.random() < p_mont
        ref = rng.choice([f" a la altura del km {int(pk)}", f" cerca de {rng.choice(pueblos)}",
                          f" pasado {rng.choice(pueblos)}", f" en el km {int(pk)}", ""])
        return dict(ambito=ambito, via=via, titular=titular, tipo_via=tipo_via, lat=lat, lon=lon,
                    montana=montana, ref=ref, organismo=inc_def["org_i"])
    ciudad, centro, calles = rng.choice(CIUDADES)
    calle = rng.choice(calles)
    lat = centro[0] + rng.uniform(-0.008, 0.008)
    lon = centro[1] + rng.uniform(-0.008, 0.008)
    ref = rng.choice([f" en {ciudad}", f" de {ciudad}, a la altura del número {rng.randint(2, 90)}",
                      f" en {ciudad}, junto al cruce", ""])
    return dict(ambito=ambito, via=calle, titular="Ayuntamiento", tipo_via="urbana", lat=lat, lon=lon,
                montana=False, ref=ref, organismo=inc_def["org_u"])


def agravantes_activos(inc_def, estado):
    activos = []
    for a in inc_def["agr"]:
        if a == "noche" and estado["luz"] in ("noche", "crepusculo"):
            activos.append("noche")
        elif a == "lluvia" and (estado["lluvia_24h_mm"] or 0) >= 20:
            activos.append("lluvia")
        elif a == "alerta" and estado["alerta_aemet"] in ("naranja", "roja"):
            activos.append("alerta")
        elif a == "montana" and estado["zona_montana"]:
            activos.append("montana")
        elif a == "trafico" and estado["intensidad_trafico"] == "alta":
            activos.append("trafico")
    if estado["historial_zona"] == "tramo_peligroso" and inc_def["calzada"]:
        activos.append("tramo_peligroso")
    return activos


def etiquetar(inc_def, estado, organismo):
    activos = agravantes_activos(inc_def, estado)
    nivel = min(inc_def["base"] + (1 if activos else 0), 3)
    # riesgo vial: el tipo pone en peligro a quien circula y el nivel es Alta o Crítica
    riesgo = inc_def["riesgo"] and nivel >= 2
    # urgente: Crítica siempre; Alta salvo que la reparación se pueda programar
    urgente = inc_def["urg"] or nivel == 3 or (nivel == 2 and not inc_def["planif"])
    return {
        "organismo": organismo,
        "severidad": NIVELES[nivel],
        "riesgo_vial": bool(riesgo),
        "urgente": bool(urgente),
        "inspeccion": bool(inc_def["insp"]),
    }, activos


def frase_contexto(rng, estado, activos):
    """Frase opcional que el ciudadano añade, siempre coherente con el contexto."""
    candidatas = []
    if "noche" in activos or estado["luz"] == "noche":
        candidatas.append(", y es de noche, no se ve nada")
    if (estado["lluvia_24h_mm"] or 0) >= 20:
        candidatas.append(" con la que está cayendo")
    if estado["intensidad_trafico"] == "alta":
        candidatas.append(", y pasan muchos coches")
    if not candidatas or rng.random() < 0.6:
        return ""
    return rng.choice(candidatas)


def construir(rng, inc_def, id_aviso, id_inc, fijo=None):
    """fijo: dict con lugar/fecha ya decididos (para duplicados y pares)."""
    forzar = inc_def["forzar"]
    lugar = fijo["lugar"] if fijo else elegir_lugar(rng, inc_def)
    dt = fijo["dt"] if fijo and "dt" in fijo else generar_fecha(rng, forzar)
    est = estacion_de(dt.month)
    lluvia, temp, alerta = generar_meteo(rng, est, lugar["montana"], forzar)
    luz = luz_de(dt)
    estado = {
        "texto": "",
        "lat": round(lugar["lat"], 5),
        "lon": round(lugar["lon"], 5),
        "fecha_hora": dt.strftime("%Y-%m-%dT%H:%M"),
        "via": lugar["via"] if lugar["ambito"] == "interurbano" else lugar["via"],
        "titular": lugar["titular"],
        "tipo_via": lugar["tipo_via"],
        "luz": luz,
        "estacion": est,
        "lluvia_24h_mm": lluvia if rng.random() > 0.03 else None,
        "alerta_aemet": alerta,
        "temperatura_c": temp,
        "zona_montana": lugar["montana"],
        "riesgo_fauna": riesgo_fauna(rng, lugar["tipo_via"], lugar["montana"], luz),
        "historial_zona": rng.choices(["sin_historial", "algun_accidente", "tramo_peligroso", "desconocido"],
                                      weights=[55, 30, 10, 5])[0],
        "incidencia_oficial_cerca": rng.random() < 0.08,
        "via_cortada": bool(inc_def["cortada"]),
        "cadenas": bool(est == "invierno" and lugar["montana"] and temp <= 0 and rng.random() < 0.7),
        "intensidad_trafico": intensidad(rng, lugar["tipo_via"], dt.hour),
        "avisos_similares_cerca": 0,
    }
    if fijo and "override" in fijo:
        estado.update(fijo["override"])
    if lugar["titular"] == "Ayuntamiento":
        estado["riesgo_fauna"] = "bajo"
    etiquetas, activos = etiquetar(inc_def, estado, lugar["organismo"])
    plantilla = rng.choice(inc_def["textos"])
    texto = plantilla.format(via=lugar["via"], ref=lugar["ref"], ctx=frase_contexto(rng, estado, activos))
    if lugar["ambito"] == "urbano":
        for a, b in (("la carretera", "la calle"), ("cuneta", "acera"), ("arcén", "borde de la calzada")):
            texto = texto.replace(a, b)
    texto = texto[0].upper() + texto[1:]
    estado["texto"] = ensuciar(texto, rng)
    return {
        "id": id_aviso,
        "id_incidencia": id_inc,
        "estado": estado,
        "etiquetas": etiquetas,
        "meta": {"tipo": inc_def["tipo"], "ambito": lugar["ambito"], "dificultad": inc_def["dif"],
                 "agravantes": activos, "origen": "sintetico"},
    }, lugar, dt


# --------------------------------------------------------------------------
# Semillas: los 5 ejemplos de la diapositiva del reto, etiquetados a mano
# --------------------------------------------------------------------------

def semillas():
    base = {"tipo_via": "regional", "estacion": "otono", "lluvia_24h_mm": 0.0, "alerta_aemet": "ninguna",
            "temperatura_c": 14.0, "riesgo_fauna": "medio", "historial_zona": "sin_historial",
            "incidencia_oficial_cerca": False, "via_cortada": False, "cadenas": False,
            "intensidad_trafico": "media", "avisos_similares_cerca": 0}
    casos = [
        ("Hay una roca ocupando parcialmente un carril en la AS-114.", 43.335, -4.80, "2026-10-15T09:30", "AS-114",
         "Principado", "dia", {"zona_montana": True},
         (CONS, "Crítica", True, True, True)),
        ("Un semáforo lleva apagado varias horas en una zona urbana.", 43.3614, -5.8494, "2026-10-15T11:00",
         "calle Uría", "Ayuntamiento", "dia", {"zona_montana": False, "tipo_via": "urbana", "riesgo_fauna": "bajo",
                                               "intensidad_trafico": "alta"},
         (AYTO, "Alta", True, True, False)),
        ("Existe una señal de tráfico caída tras un temporal.", 43.40, -6.10, "2026-10-15T10:15", "N-634",
         "Estado", "dia", {"zona_montana": False, "tipo_via": "nacional", "lluvia_24h_mm": 35.0,
                           "alerta_aemet": "amarilla"},
         (CONS, "Media", False, False, False)),
        ("Se ha detectado una balsa de agua peligrosa en una carretera secundaria.", 43.29, -5.66,
         "2026-10-15T08:45", "AS-117", "Principado", "dia", {"zona_montana": False, "lluvia_24h_mm": 30.0,
                                                             "alerta_aemet": "amarilla"},
         (CONS, "Alta", True, True, False)),
        ("Hay un desprendimiento próximo a una vía de montaña.", 43.10, -6.24, "2026-10-15T17:30", "AS-227",
         "Principado", "dia", {"zona_montana": True, "lluvia_24h_mm": 25.0, "alerta_aemet": "amarilla",
                               "intensidad_trafico": "baja"},
         (CONS, "Crítica", True, True, True)),
    ]
    filas = []
    for i, (texto, lat, lon, fh, via, titular, luz, extra, et) in enumerate(casos, 1):
        estado = dict(base, texto=texto, lat=lat, lon=lon, fecha_hora=fh, via=via, titular=titular, luz=luz)
        estado.update(extra)
        filas.append({
            "id": f"SEM-{i}", "id_incidencia": f"SEM-{i}", "estado": estado,
            "etiquetas": dict(zip(["organismo", "severidad", "riesgo_vial", "urgente", "inspeccion"], et)),
            "meta": {"tipo": "semilla_reto", "ambito": "urbano" if titular == "Ayuntamiento" else "interurbano",
                     "dificultad": "normal", "agravantes": [], "origen": "diapositiva_reto"},
        })
    return filas


# --------------------------------------------------------------------------
# Validación contra el contrato
# --------------------------------------------------------------------------

def validar(filas, contrato):
    campos = contrato["estado"]["campos"]
    opciones_org = set(contrato["preguntas"]["organismo"]["opciones"])
    niveles = [n["nombre"] for n in contrato["preguntas"]["severidad"]["niveles"]]
    errores = []
    for f in filas:
        e = f["estado"]
        faltan = set(campos) - set(e)
        sobran = set(e) - set(campos)
        if faltan or sobran:
            errores.append(f"{f['id']}: campos faltan={faltan} sobran={sobran}")
        for k, d in campos.items():
            if d["tipo"] == "enum" and e.get(k) not in d["valores"]:
                errores.append(f"{f['id']}: {k}={e.get(k)!r} fuera del enum")
        et = f["etiquetas"]
        if et["organismo"] not in opciones_org:
            errores.append(f"{f['id']}: organismo {et['organismo']!r} no está en el contrato")
        if et["severidad"] not in niveles:
            errores.append(f"{f['id']}: severidad {et['severidad']!r} no está en el contrato")
        for k in ("riesgo_vial", "urgente", "inspeccion"):
            if not isinstance(et[k], bool):
                errores.append(f"{f['id']}: {k} no es booleano")
    return errores


# --------------------------------------------------------------------------
# Principal
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=850, help="incidencias base (los duplicados y pares se suman)")
    ap.add_argument("--semilla", type=int, default=42)
    ap.add_argument("--contrato", default="contrato.json")
    ap.add_argument("--salida", default="datos")
    ap.add_argument("--prop_test", type=float, default=0.2)
    args = ap.parse_args()

    rng = random.Random(args.semilla)
    with open(args.contrato, encoding="utf-8") as fh:
        contrato = json.load(fh)
    os.makedirs(args.salida, exist_ok=True)

    # Peso de cada tipo para que las severidades finales queden equilibradas
    # (los agravantes suben un nivel, así que se generan más casos de base baja)
    objetivo_base = {0: 0.27, 1: 0.30, 2: 0.28, 3: 0.15}
    n_por_base = Counter(t["base"] for t in I)
    pesos = [objetivo_base[t["base"]] / n_por_base[t["base"]] for t in I]

    filas = []
    n_aviso = 0
    for k in range(1, args.n + 1):
        inc_def = rng.choices(I, weights=pesos)[0]
        id_inc = f"INC-{k:04d}"
        n_aviso += 1
        fila, lugar, dt = construir(rng, inc_def, f"AV-{n_aviso:05d}", id_inc)
        filas.append(fila)

        r = rng.random()
        if r < 0.08:
            # Duplicados: otras personas avisan de lo mismo poco después y cerca
            for j in range(rng.randint(1, 3)):
                n_aviso += 1
                dt2 = dt + timedelta(minutes=rng.randint(3, 90))
                lugar2 = dict(lugar, lat=lugar["lat"] + rng.gauss(0, 0.0008), lon=lugar["lon"] + rng.gauss(0, 0.0008))
                dup, _, _ = construir(rng, inc_def, f"AV-{n_aviso:05d}", id_inc,
                                      fijo={"lugar": lugar2, "dt": dt2, "override": {"avisos_similares_cerca": j + 1}})
                dup["meta"]["duplicado_de"] = fila["id"]
                filas.append(dup)
        elif r < 0.14 and inc_def["agr"]:
            # Par de contraste: mismo hecho, contexto opuesto (agravado vs tranquilo)
            n_aviso += 1
            agravado = fila["meta"]["agravantes"] == []
            if agravado:
                override = {"luz": "noche", "lluvia_24h_mm": 45.0, "alerta_aemet": "naranja",
                            "intensidad_trafico": "alta"}
                dt2 = dt.replace(hour=23)
            else:
                override = {"luz": "dia", "lluvia_24h_mm": 0.0, "alerta_aemet": "ninguna",
                            "intensidad_trafico": "baja", "historial_zona": "sin_historial"}
                dt2 = dt.replace(hour=12)
            if inc_def["forzar"].get("lluvia_min"):
                override.pop("lluvia_24h_mm", None)
            par, _, _ = construir(rng, inc_def, f"AV-{n_aviso:05d}", id_inc + "-P",
                                  fijo={"lugar": dict(lugar, montana=False if not agravado else lugar["montana"]),
                                        "dt": dt2, "override": override})
            par["meta"]["par_de"] = fila["id"]
            par["id_incidencia"] = id_inc  # mismo hecho: va al mismo lado del split
            filas.append(par)

    errores = validar(filas, contrato) + validar(semillas(), contrato)
    if errores:
        print("ERRORES DE VALIDACIÓN:")
        for e in errores[:30]:
            print("  ", e)
        raise SystemExit(1)

    # Split por incidencia, estratificado por organismo x severidad
    grupos = defaultdict(list)
    for f in filas:
        grupos[f["id_incidencia"]].append(f)
    por_estrato = defaultdict(list)
    for gid, fs in grupos.items():
        et = fs[0]["etiquetas"]
        por_estrato[(et["organismo"], et["severidad"])].append(gid)
    test_ids = set()
    rng_split = random.Random(args.semilla)
    for estrato, gids in sorted(por_estrato.items()):
        gids = sorted(gids)
        rng_split.shuffle(gids)
        n_test = round(len(gids) * args.prop_test)
        test_ids.update(gids[:n_test])
    train = [f for f in filas if f["id_incidencia"] not in test_ids]
    test = [f for f in filas if f["id_incidencia"] in test_ids]

    def volcar(nombre, datos):
        with open(os.path.join(args.salida, nombre), "w", encoding="utf-8") as fh:
            for d in datos:
                fh.write(json.dumps(d, ensure_ascii=False) + "\n")

    volcar("avisos_train.jsonl", train)
    volcar("avisos_test.jsonl", test)
    volcar("semillas_reto.jsonl", semillas())

    # Muestra para revisión humana (solo de train: el test no se mira)
    muestra = random.Random(args.semilla + 1).sample(train, min(100, len(train)))
    with open(os.path.join(args.salida, "revision_muestra.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["id", "texto", "via", "titular", "luz", "lluvia_24h_mm", "alerta_aemet", "intensidad_trafico",
                    "zona_montana", "organismo", "severidad", "riesgo_vial", "urgente", "inspeccion",
                    "correcta_s_n", "correccion"])
        for f in muestra:
            e, et = f["estado"], f["etiquetas"]
            w.writerow([f["id"], e["texto"], e["via"], e["titular"], e["luz"], e["lluvia_24h_mm"], e["alerta_aemet"],
                        e["intensidad_trafico"], e["zona_montana"], et["organismo"], et["severidad"],
                        et["riesgo_vial"], et["urgente"], et["inspeccion"], "", ""])

    # Resumen
    def resumen(nombre, datos):
        print(f"\n{nombre}: {len(datos)} avisos, {len({d['id_incidencia'] for d in datos})} incidencias")
        print("  organismo:", dict(Counter(d["etiquetas"]["organismo"] for d in datos).most_common()))
        print("  severidad:", {n: Counter(d["etiquetas"]["severidad"] for d in datos)[n] for n in NIVELES})
        print("  riesgo_vial=True:", sum(d["etiquetas"]["riesgo_vial"] for d in datos),
              "| urgente=True:", sum(d["etiquetas"]["urgente"] for d in datos),
              "| inspeccion=True:", sum(d["etiquetas"]["inspeccion"] for d in datos))

    print(f"Validación contra {args.contrato}: OK ({len(filas)} avisos + 5 semillas)")
    resumen("TRAIN", train)
    resumen("TEST", test)
    solapadas = {f["id_incidencia"] for f in train} & {f["id_incidencia"] for f in test}
    print(f"\nIncidencias a ambos lados del split: {len(solapadas)}")


if __name__ == "__main__":
    main()
