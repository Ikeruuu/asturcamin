# Dataset unificado de contexto – Carreteras de Asturias
Hackathon TalentUO 2026 · Reto 3 (NTT DATA) · generado el 15/10/2026

## 1. Qué es (y qué NO es)
- **Es** una tabla de **3.735 tramos** (carretera + kilómetro) de la red interurbana de Asturias con todo lo que sabemos
  **de verdad** sobre cada punto: accidentes 2019-2024, riesgos geológicos, altitud y pendiente, curvas, fauna, clima y
  quién es el organismo competente.
- **No es** el dataset de entrenamiento del clasificador. Los **avisos** con los que se entrena el modelo son
  **sintéticos (ficticios)**. Este dataset es el **contexto real** que se añade a cada aviso —sintético o real—
  justo antes de clasificarlo.

```
aviso (texto + lat/lon + hora)  ──►  enriquecer_aviso.py  ──►  JSON {aviso + contexto real}  ──►  Laya (organismo, severidad, sí/no)
         ▲ sintético en entrenamiento                                   │
         └ real en producción                                           └► prior explicable: veracidad × y severidad +/-
```

## 2. Archivos
| Archivo | Contenido |
|---|---|
| contexto_tramos_asturias.csv / .geojson | 1 fila por tramo, 72 columnas (el .geojson es para el mapa web) |
| accidentes_asturias_geolocalizados.csv | 4.922 accidentes interurbanos; 3.400 con coordenadas de su tramo |
| clima_concejos_resumen.csv | Clima 2019-2024 por concejo |
| parametros_globales.json | Proporciones globales, factores de fauna por mes y hora, umbrales y pesos del score |
| enriquecer_aviso.py | `Contexto(carpeta).enriquecer(lat, lon, fecha_hora, categoria, es_sintetico)` |
| ejemplos_reto_enriquecidos.json | Los 5 ejemplos del enunciado con su contexto (ubicaciones ilustrativas) |
| build_contexto.py | Reproduce todo: etapas `tramos, riesgo <capa>, riesgos_merge, accidentes, clima, final, params` |

## 3. Columnas principales
- **Identificación:** id_tramo, carretera, km, lat, lon, denominacion, categoria_via, concejo, codigo_ine, fuente_pk.
- **Competencias:** titularidad (Estado / Principado), organismo_conservacion, autoridad_trafico.
- **Terreno:** altitud_m, pendiente_via_pct (desnivel con los km vecinos), sinuosidad (1 = recta; ≥1,3 = curvas).
- **Riesgos naturales (0 = muy baja/nada … 4 = muy alta, buffer 100 m):** susc_desprendimientos, susc_argayos,
  susc_grandes_movimientos, susc_aludes, susc_incendios; proceso_activo_ladera / proceso_activo_inundacion (≤200 m).
- **Accidentes 2019-2024 (DGT, solo con víctimas):** n_accidentes, n_acc_graves, n_fallecidos_30d, n_noche,
  n_amanecer_atardecer, n_lluvia, n_niebla, n_firme_mojado, n_hielo_nieve, n_animales, n_obstaculo, n_salida_via, n_en_curva.
- **Índices:** indice_peligrosidad (accidentes suavizados / media de Asturias), indice_vs_su_categoria,
  rr_noche, rr_lluvia, rr_hielo_nieve, rr_curva (riesgo relativo vs. la proporción global), indice_fauna (ventana ±3 km).
- **Clima del concejo:** precip_media_anual_mm, pct_dias_lluvia_fuerte (≥20 mm), pct_dias_helada, pct_dias_nieve, racha_p95_kmh.
- **Resultado:** comp_* (5 componentes 0-1), score_riesgo_base, **nivel_riesgo_base** (baja/media/alta/critica), **factores_riesgo** (texto explicable).
- **Puente con lo sintético:** w_sint_* (pesos de muestreo), particion (train/test por carretera), origen_datos.

## 4. Metodología (para defenderla ante el jurado)
1. **Ubicación de accidentes:** la DGT da "carretera + km" sin coordenadas. Se cruzan con los puntos kilométricos del
   Principado (SITPA, 2.909 tramos) y, para las carreteras del Estado, con PK geocodificados en CartoCiudad-IGN (826 tramos).
2. **Suavizado empírico-bayesiano (Poisson-Gamma) por tipo de vía:** un tramo con 1 accidente en 6 años no sale "rojo";
   se acerca a la media de su categoría hasta que los datos justifican lo contrario.
3. **Riesgos relativos con contracción beta-binomial (k = 10):** "este tramo tiene X veces más accidentes nocturnos de lo normal".
4. **Score base = 0,40 siniestralidad + 0,25 geológico + 0,15 fauna + 0,10 meteo/altitud + 0,10 trazado** (pesos expertos
   iniciales). Niveles por percentiles: baja <P50, media P50-80, alta P80-95, crítica >P95.
5. **Validación cualitativa:** los tramos críticos incluyen la N-630 y la AP-66 en el puerto de Pajares (Lena), tramos de
   montaña con alta siniestralidad conocidos.

## 5. Cómo convive con los avisos SINTÉTICOS (importante)
1. **Todo aviso lleva `es_sintetico`.** El contexto siempre lleva `origen_contexto = "real"`.
2. **Generar avisos ficticios anclados a lugares reales:** para cada tipo de aviso se elige el tramo con su peso
   `w_sint_<tipo>` (p. ej. los desprendimientos caen más donde la susceptibilidad es alta, la fauna donde hay más atropellos).
   La hora y el mes se muestrean con `fauna_factor_mes` / `fauna_factor_hora` y las proporciones reales de noche y lluvia.
   Así la distribución de los avisos ficticios imita la real, aunque el texto sea inventado.
3. **Evitar atajos:** si la etiqueta sintética se decide solo con el contexto, el modelo aprende la regla y no el aviso.
   Por eso: la etiqueta la decide sobre todo el **texto**; el contexto solo la ajusta ±1 nivel; y se añade un 15-20 %
   de **contraejemplos** (avisos graves en tramos de riesgo bajo y viceversa).
4. **Evaluación honesta:** `particion` separa carreteras completas (742 tramos de 80 carreteras en test) y, además,
   hay que escribir a mano un set de avisos de prueba. Las métricas deben decir "evaluado sobre avisos sintéticos".
5. **El contexto es un prior, nunca un veto:** un aviso en un tramo "tranquilo" se tramita igual; el contexto mueve la
   veracidad (×0,8-1,6) y la severidad sugerida (hasta +2), siempre con motivos legibles.
6. **Cuando haya avisos reales**, recalibrar pesos y umbrales con ellos (el diseño ya lo permite).

## 6. Limitaciones conocidas
- Sin intensidad de tráfico (IMD) abierta para la red autonómica: el índice mezcla peligrosidad y volumen de tráfico
  (el suavizado por categoría de vía lo atenúa).
- La DGT solo registra accidentes **con víctimas**: los atropellos de fauna con daños materiales no aparecen
  (el estudio del Principado los cifra en miles; ver 06_fauna).
- 1.522 accidentes interurbanos no se pudieron ubicar (caminos sin denominación, vías municipales, PK inexistentes).
- Vías urbanas y municipales no tienen tramo: el enriquecedor devuelve el concejo y deriva al Ayuntamiento.
- Clima de reanálisis (Open-Meteo/ERA5), no de estaciones AEMET.
- `terreno_cinegetico` cubre ~100 % de los tramos: se conserva pero no se usa en el score.
- Capas geológicas simplificadas a ~10 m.
