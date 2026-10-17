# Datasets del proyecto – Hackathon TalentUO 2026 (Reto 3 NTT DATA)
Descargados el 15/10/2026. Ámbito: Principado de Asturias.

## 01_accidentes_DGT – Registro histórico de accidentes
- originales_toda_espana/dgt_accidentes_2019..2024.xlsx – Ficheros originales de la DGT (toda España), solo como respaldo. Fuente: DGT, "DGT en cifras".
- dgt_accidentes_asturias_2019_2024.csv – **Filtrado a Asturias (COD_PROVINCIA = 33): 10.913 accidentes**, 4.922 en vías interurbanas con CARRETERA + KM.
  Campos clave: ANYO, MES, DIA_SEMANA, HORA, CARRETERA, KM, TITULARIDAD_VIA, TIPO_ACCIDENTE (8 = atropello a animales, 162 casos),
  CONDICION_METEO (3/4 = lluvia, 1.746 casos), CONDICION_ILUMINACION (día / amanecer-atardecer / noche), CONDICION_FIRME, TRAZADO_PLANTA (curvas), víctimas.
- dgt_diccionario_tabla_accidentes.xlsx – Significado de cada código.

## 02_carreteras – Red viaria (SITPA-IDEAS, Gobierno del Principado de Asturias)
- carreteras_0..6_*.geojson – Autopistas/autovías, nacionales, regionales AS, comarcales AS, locales 1º y 2º orden, municipales.
  Campos: codigoc (p.ej. AS-118), nombre del tramo, titul_0027 (titularidad), tipo.
- asturias_pk_carreteras.geojson – 3.829 puntos kilométricos de 446 carreteras (clave para unir accidentes "carretera + km" con el mapa).
- asturias_concejos.geojson – 78 concejos (para análisis "por zonas").

## 03_riesgos_naturales – Susceptibilidad (SITPA-IDEAS, servicio RiesgosNaturales)
grid_code: 1 = baja, 2 = media, 3 = alta, 4 = muy alta.
- riesgo_desprendimientos_rocas.geojson (rocas en calzada)
- riesgo_deslizamientos_argayos.geojson (argayos)
- riesgo_grandes_movimientos_masa.geojson
- riesgo_aludes_nieve.geojson
- riesgo_incendios_forestales.geojson
- procesos_activos_movimientos_ladera.geojson y procesos_activos_inundacion.geojson (Cartografía de Procesos Activos 1:25.000)
Nota: geometrías simplificadas a ~10 m; ~2 % de polígonos necesitan `make_valid()` antes de cruzarlos.

## 04_topografia
- asturias_mdt25_elevacion.tif – Modelo digital de elevaciones 25 m (EPSG:25830), mosaico de 4 teselas. Altitud y pendiente por tramo.

## 05_meteorologia
- meteo_diaria_concejos_asturias_2019_2024.csv – Precipitación, nieve, Tmin/Tmax y racha máxima diaria en el centro de cada concejo.
  Fuente: Open-Meteo Historical Weather API (reanálisis ERA5/ECMWF), mientras llega la clave de AEMET OpenData.

## 06_fauna
- fauna_estudio_accidentes_animales_asturias_MITECO.pdf – Estudio de accidentes con fauna silvestre en Asturias (6.377 registros, tramos de concentración).
- fauna_problematica_jabalies_asturias_MITECO.pdf – Población de jabalí y distribución horaria/mensual de accidentes.
- fauna_terrenos_cinegeticos.geojson y fauna_areas_de_caza.geojson – Registro de terrenos cinegéticos (proxy de densidad de fauna).

## 07_tiempo_real
- dgt_incidencias_asturias_snapshot_2026-10-15.csv – 30 incidencias activas en Asturias el 15/10/2026 (filtradas del feed).
- dgt_incidencias_tiempo_real_snapshot_2026-10-15.xml – Feed DATEX2 v3.7 original (toda España).
  Endpoint en vivo para la app: https://nap.dgt.es/datex2/v3/dgt/SituationPublication/datex2_v37.xml

## 08_dataset_unificado – Dataset de contexto (todo junto)
- contexto_tramos_asturias.csv/.geojson: 3.735 tramos (carretera + km) con todos los factores y un nivel de riesgo base explicable.
- enriquecer_aviso.py: añade el contexto real a cualquier aviso (sintético o real) antes de clasificarlo.
- Ver DATA_CARD.md para metodología, columnas y cómo convive con los avisos sintéticos.

## Ámbito
Todas las capas están recortadas al límite del Principado de Asturias (se eliminaron ~46 elementos de borde y el MDT queda sin datos fuera de Asturias). Incluyen todas las titularidades (Estado, Principado y concejos), porque el triaje debe derivar cada aviso al organismo competente.

## Avisos meteorológicos en vivo (para la app, sin clave)
- MeteoAlarm (avisos oficiales de AEMET en formato CAP): https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-spain
  Filtrar por las zonas de Asturias (campo cap:areaDesc / código EMMA_ID). Probado el 15/10/2026: funciona; ese día no había avisos en Asturias.

## Fuentes
- DGT microdatos: https://www.dgt.es/menusecundario/dgt-en-cifras/
- SITPA-IDEAS (ArcGIS REST): https://sig.asturias.es/servicios/rest/services
- MDE 25 m: https://sig.asturias.es/servicios/rest/services/Fondos/MDE/ImageServer
- Open-Meteo: https://open-meteo.com/en/docs/historical-weather-api
- DGT NAP: https://nap.dgt.es/dataset/incidencias-dgt-datex2-v3-7
- MITECO fauna: https://www.miteco.gob.es/content/dam/miteco/es/biodiversidad/temas/ecosistemas-y-conectividad/posters_tcm30-505795.pdf

## Pendiente
- AEMET OpenData (estaciones oficiales + avisos en vivo): necesita API key.
- Zonas inundables SNCZI (MITECO): las descargas oficiales devuelven 404; sustituidas por procesos_activos_inundacion.
- IMD (tráfico por tramo): no publicada como dato abierto para la red autonómica; se usará aproximación por tipo de vía.
