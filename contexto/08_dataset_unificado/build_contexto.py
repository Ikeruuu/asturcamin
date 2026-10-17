"""Construye el dataset unificado de contexto por tramo (carretera + km) para Asturias.
Uso: python3 build_contexto.py <carpeta hackathon_datasets> <etapa>
Etapas: tramos | riesgos | accidentes | clima | final
"""
import sys, os, re, json, glob, warnings
import numpy as np, pandas as pd, geopandas as gpd
from shapely.geometry import Point
warnings.filterwarnings("ignore")
D = sys.argv[1]; STAGE = sys.argv[2]
W = os.path.expanduser("~/work"); CRS = 25830
CAT = {0: "autopista_autovia", 1: "nacional", 2: "regional", 3: "comarcal", 4: "local_1_orden", 5: "local_2_orden"}
ROADF = ["carreteras_0_autopistas_autovias", "carreteras_1_nacionales", "carreteras_2_regionales_AS",
         "carreteras_3_comarcales_AS", "carreteras_4_locales_1orden", "carreteras_5_locales_2orden"]

def norm_road(s):
    s = str(s).strip().upper().replace(" ", "")
    return s.split("_")[0]

def load_roads():
    out = []
    for k, f in enumerate(ROADF):
        g = gpd.read_file(f"{D}/02_carreteras/{f}.geojson").to_crs(CRS)
        g["categoria_via"] = CAT[k]; g["road"] = g.codigoc.map(norm_road)
        out.append(g[["road", "nombre", "titul_0027", "categoria_via", "geometry"]])
    return pd.concat(out, ignore_index=True)

def save(df, name): df.to_pickle(f"{W}/{name}.pkl")
def load(name): return pd.read_pickle(f"{W}/{name}.pkl")

# ---------------------------------------------------------------- 1. TRAMOS
if STAGE == "tramos":
    pk = gpd.read_file(f"{D}/02_carreteras/asturias_pk_carreteras.geojson").to_crs(CRS)
    pk["road"] = pk.nombre.map(norm_road); pk["km"] = pk.pkini.astype(int)
    pk["x"] = pk.geometry.x; pk["y"] = pk.geometry.y
    a = pk.groupby(["road", "km"]).agg(x=("x", "mean"), y=("y", "mean"), denominacion=("denominaci", "first"),
                                       jerarquia=("jerarquia", "first")).reset_index()
    a["fuente_pk"] = "SITPA Principado"
    est = pd.read_csv(glob.glob(f"{D}/02_carreteras/pk_carreteras_estado_asturias_cartociudad.csv")[0])
    est["road"] = est.carretera.map(norm_road)
    ge = gpd.GeoDataFrame(est, geometry=gpd.points_from_xy(est.lon, est.lat), crs=4326).to_crs(CRS)
    b = pd.DataFrame({"road": est.road, "km": est.km.astype(int), "x": ge.geometry.x, "y": ge.geometry.y,
                      "denominacion": None, "jerarquia": None, "fuente_pk": "CartoCiudad (IGN)"})
    b = b[~b.set_index(["road", "km"]).index.isin(a.set_index(["road", "km"]).index)]
    t = pd.concat([a, b], ignore_index=True)
    t = gpd.GeoDataFrame(t, geometry=gpd.points_from_xy(t.x, t.y), crs=CRS)
    roads = load_roads()
    # atributos de la vía: tramo de carretera más cercano con el mismo código
    t["categoria_via"] = None; t["titularidad_cod"] = None; t["dist_eje_m"] = np.nan; t["sinuosidad"] = np.nan
    for r, idx in t.groupby("road").groups.items():
        rl = roads[roads.road == r]
        if rl.empty: continue
        geom = rl.geometry.union_all()
        cat = rl.categoria_via.mode()[0]; tit = rl.titul_0027.mode()[0]
        for i in idx:
            p = t.geometry[i]
            t.at[i, "categoria_via"] = cat; t.at[i, "titularidad_cod"] = tit
            t.at[i, "dist_eje_m"] = p.distance(geom)
            clip = geom.intersection(p.buffer(500))
            parts = list(getattr(clip, "geoms", [clip]))
            parts = [q for q in parts if q.length > 0]
            if parts:
                q = max(parts, key=lambda z: z.length)
                cs = list(q.coords); chord = Point(cs[0]).distance(Point(cs[-1]))
                if chord > 200: t.at[i, "sinuosidad"] = min(q.length / chord, 3.0)
    # vías sin geometría en las capas 0-5 (enlaces, ramales…): categoría por jerarquía o código
    jer = {"Autopista / Autovía": "autopista_autovia"}
    t["categoria_via"] = t.categoria_via.fillna(t.jerarquia.map(jer)).fillna("otra_enlace")
    t["titularidad_cod"] = t.titularidad_cod.fillna(pd.Series(np.where(t.fuente_pk.str.startswith("Carto"), "EST", "CAU"), index=t.index))
    # concejo
    con = gpd.read_file(f"{D}/02_carreteras/asturias_concejos.geojson").to_crs(CRS); con["geometry"] = con.make_valid()
    t = gpd.sjoin_nearest(t, con[["concejo", "codigo_ine", "geometry"]], how="left", max_distance=2000).drop(columns="index_right")
    t = t[~t.index.duplicated()]
    # altitud y pendiente longitudinal (MDT 25 m)
    import rasterio
    with rasterio.open(f"{D}/04_topografia/asturias_mdt25_elevacion.tif") as src:
        z = np.array([v[0] for v in src.sample(list(zip(t.x, t.y)))], dtype=float)
    z[z < -9000] = np.nan; t["altitud_m"] = np.round(z, 1)
    t = t.sort_values(["road", "km"]).reset_index(drop=True)
    alt = t.set_index(["road", "km"]).altitud_m
    def slope(r, k):
        v = [abs(alt.get((r, k + d), np.nan) - alt[(r, k)]) for d in (-1, 1)]
        v = [s for s in v if not np.isnan(s)]
        return round(np.mean(v) / 10, 2) if v else np.nan   # m por 1000 m -> %
    t["pendiente_via_pct"] = [slope(r, k) for r, k in zip(t.road, t.km)]
    t = t[t.dist_eje_m.isna() | (t.dist_eje_m < 300)]   # descarta PK mal geocodificados lejos de su carretera
    save(t, "tramos"); print("tramos", len(t), t.fuente_pk.value_counts().to_dict(), t.categoria_via.value_counts().to_dict())

# ---------------------------------------------------------------- 2. RIESGOS (una capa por llamada; 'riesgos_merge' une)
RIESGOS = {"susc_desprendimientos": ("03_riesgos_naturales/riesgo_desprendimientos_rocas", 100, "max"),
           "susc_argayos": ("03_riesgos_naturales/riesgo_deslizamientos_argayos", 100, "max"),
           "susc_grandes_movimientos": ("03_riesgos_naturales/riesgo_grandes_movimientos_masa", 100, "max"),
           "susc_aludes": ("03_riesgos_naturales/riesgo_aludes_nieve", 100, "max"),
           "susc_incendios": ("03_riesgos_naturales/riesgo_incendios_forestales", 100, "max"),
           "proceso_activo_ladera": ("03_riesgos_naturales/procesos_activos_movimientos_ladera", 200, "any"),
           "proceso_activo_inundacion": ("03_riesgos_naturales/procesos_activos_inundacion", 200, "any"),
           "terreno_cinegetico": ("06_fauna/fauna_terrenos_cinegeticos", 0, "any"),
           "area_de_caza": ("06_fauna/fauna_areas_de_caza", 0, "any")}
if STAGE == "riesgo":
    col = sys.argv[3]; f, radio, modo = RIESGOS[col]
    t = load("tramos")
    zona = gpd.GeoDataFrame(geometry=(t.geometry.buffer(radio) if radio else t.geometry), crs=CRS)
    g = gpd.read_file(f"{D}/{f}.geojson").to_crs(CRS)
    cand = np.unique(g.sindex.query(zona.geometry, predicate=None)[1])
    g = g.iloc[cand].copy(); g["geometry"] = g.make_valid()
    j = gpd.sjoin(zona, g, how="left", predicate="intersects")
    if modo == "max":
        LBL = {"MUY BAJA": 0, "BAJA": 1, "MEDIA": 2, "ALTA": 3, "MUY ALTA": 4}
        j["nivel"] = j.suscept.astype(str).str.strip().str.upper().map(LBL)
        v = j.groupby(level=0).nivel.max().reindex(t.index).fillna(0).astype(int)
    else: v = j.groupby(level=0).index_right.count().reindex(t.index).fillna(0).gt(0).astype(int)
    v.name = col; save(v.to_frame(), "r_" + col)
    print(col, v.value_counts().sort_index().to_dict(), flush=True)
if STAGE == "riesgos_merge":
    res = pd.concat([load("r_" + c) for c in RIESGOS], axis=1); save(res, "riesgos"); print(res.describe().loc[["mean","max"]].round(2).to_string())

# ---------------------------------------------------------------- 3. ACCIDENTES
if STAGE == "accidentes":
    t = load("tramos")
    a = pd.read_csv(f"{D}/01_accidentes_DGT/dgt_accidentes_asturias_2019_2024.csv", low_memory=False)
    a["road"] = a.CARRETERA.map(norm_road); a["km"] = np.floor(pd.to_numeric(a.KM, errors="coerce"))
    inter = a[a.ZONA_AGRUPADA == 1].copy()
    key = t.set_index(["road", "km"])
    f = {
        "n_accidentes": lambda d: len(d),
        "n_fallecidos_30d": lambda d: d.TOTAL_MU30DF.sum(),
        "n_heridos_graves_30d": lambda d: d.TOTAL_HG30DF.sum(),
        "n_acc_graves": lambda d: ((d.TOTAL_MU30DF + d.TOTAL_HG30DF) > 0).sum(),
        "n_noche": lambda d: d.CONDICION_ILUMINACION.isin([4, 5, 6]).sum(),
        "n_amanecer_atardecer": lambda d: d.CONDICION_ILUMINACION.isin([2, 3]).sum(),
        "n_lluvia": lambda d: d.CONDICION_METEO.isin([3, 4]).sum(),
        "n_niebla": lambda d: d.CONDICION_NIEBLA.isin([1, 2]).sum(),
        "n_firme_mojado": lambda d: d.CONDICION_FIRME.isin([3, 4]).sum(),
        "n_hielo_nieve": lambda d: (d.CONDICION_FIRME.isin([5, 6]) | d.CONDICION_METEO.isin([6])).sum(),
        "n_animales": lambda d: (d.TIPO_ACCIDENTE == 8).sum(),
        "n_obstaculo": lambda d: (d.TIPO_ACCIDENTE == 6).sum(),
        "n_salida_via": lambda d: d.TIPO_ACCIDENTE.between(11, 19).sum(),
        "n_en_curva": lambda d: d.TRAZADO_PLANTA.isin([2, 3]).sum(),
    }
    g = inter.groupby(["road", "km"])
    agg = pd.DataFrame({k: g.apply(fn) for k, fn in f.items()})
    acc = key.join(agg, how="left")[list(f)].fillna(0).astype(int).reset_index()
    matched = inter.set_index(["road", "km"]).index.isin(key.index)
    print("accidentes interurbanos", len(inter), "ubicados en un tramo", int(matched.sum()))
    # accidentes geolocalizados (para mapas / web)
    loc = gpd.GeoDataFrame(t[["road", "km"]], geometry=t.geometry, crs=CRS).to_crs(4326)
    loc["lat"] = loc.geometry.y.round(6); loc["lon"] = loc.geometry.x.round(6)
    geo = inter.merge(loc[["road", "km", "lat", "lon"]], on=["road", "km"], how="left")
    geo.drop(columns=["road"]).to_csv(f"{W}/accidentes_geo.csv", index=False)
    save(acc, "accidentes"); print(acc[list(f)].sum().to_dict())

# ---------------------------------------------------------------- 4. CLIMA por concejo
if STAGE == "clima":
    m = pd.read_csv(f"{D}/05_meteorologia/meteo_diaria_concejos_asturias_2019_2024.csv")
    extra = glob.glob(f"{D}/05_meteorologia/meteo_parte2*.csv")
    if extra: m = pd.concat([m] + [pd.read_csv(e) for e in extra], ignore_index=True).drop_duplicates(["codigo_ine", "fecha"])
    years = m.fecha.str[:4].nunique()
    c = m.groupby("codigo_ine").agg(
        precip_media_anual_mm=("precipitacion_mm", lambda s: round(s.sum() / years)),
        pct_dias_lluvia=("precipitacion_mm", lambda s: round((s >= 1).mean() * 100, 1)),
        pct_dias_lluvia_fuerte=("precipitacion_mm", lambda s: round((s >= 20).mean() * 100, 1)),
        pct_dias_helada=("tmin_c", lambda s: round((s < 0).mean() * 100, 1)),
        pct_dias_nieve=("nieve_cm", lambda s: round((s > 0).mean() * 100, 1)),
        racha_p95_kmh=("racha_max_kmh", lambda s: round(s.quantile(0.95), 1))).reset_index()
    save(c, "clima"); print("concejos con clima", len(c))

# ---------------------------------------------------------------- 5. FINAL
if STAGE == "final":
    t = load("tramos"); r = load("riesgos"); acc = load("accidentes"); c = load("clima")
    df = t.join(r).merge(acc, on=["road", "km"], how="left")
    df["codigo_ine"] = pd.to_numeric(df.codigo_ine, errors="coerce")
    df = df.merge(c, on="codigo_ine", how="left")
    df["clima_disponible"] = df.precip_media_anual_mm.notna().astype(int)
    YEARS = 6
    # --- Empírico-Bayes (Poisson-Gamma) por categoría de vía: accidentes/km/año suavizados
    def eb(n, grp):
        out = pd.Series(index=n.index, dtype=float)
        for _, ix in n.groupby(grp).groups.items():
            x = n[ix]; m, v = x.mean(), x.var()
            if len(x) < 5 or m == 0: out[ix] = (x + 0.5) / 2; continue
            if v <= m: v = m * 1.01
            alpha = m * m / (v - m); beta = m / (v - m)
            out[ix] = (x + alpha) / (1 + beta)
        return out
    df["acc_suav_6a"] = eb(df.n_accidentes, df.categoria_via)
    df["acc_km_anio_suav"] = (df.acc_suav_6a / YEARS).round(3)
    mean_all = df.acc_suav_6a.mean()
    df["indice_peligrosidad"] = (df.acc_suav_6a / mean_all).round(2)
    df["indice_vs_su_categoria"] = (df.acc_suav_6a / df.groupby("categoria_via").acc_suav_6a.transform("mean")).round(2)
    # --- riesgos relativos condicionales con contracción beta-binomial (k = 10 accidentes ficticios)
    K = 10; tot = df.n_accidentes.sum()
    for col, name in [("n_noche", "rr_noche"), ("n_lluvia", "rr_lluvia"), ("n_hielo_nieve", "rr_hielo_nieve"),
                      ("n_animales", "rr_fauna_condicional"), ("n_en_curva", "rr_curva")]:
        p = df[col].sum() / tot
        df[name] = (((df[col] + K * p) / (df.n_accidentes + K)) / p).round(2) if p > 0 else 1.0
    # --- índice de fauna: atropellos de animales suavizados + terreno cinegético
    # la fauna cruza por corredores de varios km: ventana móvil de ±3 km en la misma carretera
    df = df.sort_values(["road", "km"]).reset_index(drop=True)
    df["n_animales_ventana7km"] = df.groupby("road").n_animales.transform(lambda s: s.rolling(7, center=True, min_periods=1).sum()).astype(int)
    df["fauna_suav"] = eb(df.n_animales_ventana7km, pd.Series("all", index=df.index))
    df["indice_fauna"] = (df.fauna_suav / df.fauna_suav.mean()).round(2)   # terreno cinegético cubre ~100% de los tramos: no discrimina
    # --- componentes 0-1 y nivel de riesgo base
    pr = lambda s: s.rank(pct=True)
    s_acc = pr(df.indice_peligrosidad)
    s_geo = (df[["susc_desprendimientos", "susc_argayos", "susc_grandes_movimientos"]].max(axis=1) / 4 + 0.25 * df.proceso_activo_ladera).clip(0, 1)
    s_fau = pr(df.indice_fauna)
    s_met = np.clip((df.altitud_m.fillna(0) - 300) / 900, 0, 1).combine(df.susc_aludes / 4, max)
    s_met = np.maximum(s_met, 0.75 * df.proceso_activo_inundacion)
    s_via = (0.5 * pr(df.sinuosidad.fillna(1)) + 0.5 * pr(df.pendiente_via_pct.fillna(0)))
    df["comp_siniestralidad"] = s_acc.round(3); df["comp_geologico"] = s_geo.round(3); df["comp_fauna"] = s_fau.round(3)
    df["comp_meteo_altitud"] = s_met.round(3); df["comp_trazado"] = s_via.round(3)
    score = 0.40 * s_acc + 0.25 * s_geo + 0.15 * s_fau + 0.10 * s_met + 0.10 * s_via
    df["score_riesgo_base"] = score.round(3)
    q = score.quantile([0.5, 0.8, 0.95]).values
    df["nivel_riesgo_base"] = pd.cut(score, [-1, q[0], q[1], q[2], 9], labels=["baja", "media", "alta", "critica"]).astype(str)
    lv = {1: "baja", 2: "media", 3: "alta", 4: "muy alta"}
    def factores(row):
        f = []
        if row.indice_peligrosidad >= 2: f.append(f"siniestralidad {row.indice_peligrosidad:.1f}x la media de Asturias")
        if row.susc_desprendimientos >= 3: f.append(f"susceptibilidad a desprendimientos {lv[row.susc_desprendimientos]}")
        if row.susc_argayos >= 3: f.append(f"susceptibilidad a argayos {lv[row.susc_argayos]}")
        if row.proceso_activo_ladera: f.append("ladera con procesos activos a <200 m")
        if row.proceso_activo_inundacion: f.append("zona con procesos de inundación a <200 m")
        if row.indice_fauna >= 2 and row.n_animales_ventana7km >= 2: f.append(f"atropellos de fauna {row.indice_fauna:.1f}x la media ({row.n_animales_ventana7km} en ±3 km)")
        if (row.altitud_m or 0) >= 800: f.append(f"tramo de montaña ({row.altitud_m:.0f} m)")
        if row.susc_aludes >= 3: f.append("zona de aludes")
        if (row.sinuosidad or 1) >= 1.3: f.append("trazado sinuoso / curvas")
        if (row.pendiente_via_pct or 0) >= 6: f.append(f"pendiente {row.pendiente_via_pct:.0f} %")
        if row.rr_noche >= 1.5 and row.n_accidentes >= 3: f.append("más accidentes de noche de lo habitual")
        if row.rr_lluvia >= 1.5 and row.n_accidentes >= 3: f.append("más accidentes con lluvia de lo habitual")
        return "; ".join(f) if f else "sin factores destacados"
    df["factores_riesgo"] = df.apply(factores, axis=1)
    # --- competencias
    TIT = {"EST": "Estado", "CAU": "Principado de Asturias", "OTR": "Otra/Municipal"}
    df["titularidad"] = df.titularidad_cod.map(TIT).fillna("Desconocida")
    df["organismo_conservacion"] = np.select(
        [df.titularidad_cod == "EST", df.titularidad_cod == "CAU"],
        ["Demarcación de Carreteras del Estado en Asturias", "Servicio de Conservación de Carreteras del Principado"],
        default="Ayuntamiento de " + df.concejo.fillna("").str.title())
    df["autoridad_trafico"] = "Guardia Civil de Tráfico"
    # --- puente con los avisos sintéticos: pesos de muestreo por tipo de aviso (suman 1 cada uno)
    def w(x): x = np.asarray(x, float); x = np.clip(x, 0, None) + 1e-9; return x / x.sum()
    base = 0.05
    df["w_sint_desprendimiento"] = w(base + (df.susc_desprendimientos / 4) ** 2 + 0.5 * (df.susc_argayos / 4) ** 2 + 0.5 * df.proceso_activo_ladera)
    df["w_sint_fauna"] = w(base + df.indice_fauna / df.indice_fauna.max() + 0.3 * (df.susc_incendios >= 2))   # monte/arbolado como proxy de hábitat
    df["w_sint_agua_inundacion"] = w(base + df.proceso_activo_inundacion + df.precip_media_anual_mm.fillna(df.precip_media_anual_mm.median()) / 2500)
    df["w_sint_meteo_nieve_hielo"] = w(base + np.clip((df.altitud_m.fillna(0) - 400) / 800, 0, 1) + 0.5 * (df.susc_aludes / 4))
    df["w_sint_accidente_trafico"] = w(df.indice_peligrosidad)
    df["w_sint_infraestructura"] = w(np.where(df.categoria_via.isin(["autopista_autovia", "nacional", "regional"]), 1.0, 0.6))
    # partición por carretera (las carreteras de test no se ven en entrenamiento)
    import hashlib
    df["particion"] = df.road.map(lambda r: "test" if int(hashlib.md5(r.encode()).hexdigest(), 16) % 5 == 0 else "train")
    df["origen_datos"] = "real (DGT 2019-2024, SITPA, IGN/CartoCiudad, Open-Meteo)"
    g = gpd.GeoDataFrame(df, geometry="geometry", crs=CRS).to_crs(4326)
    g["lat"] = g.geometry.y.round(6); g["lon"] = g.geometry.x.round(6)
    g["id_tramo"] = g.road + "_km" + g.km.astype(int).astype(str).str.zfill(3)
    g = g.rename(columns={"road": "carretera"})
    cols = ["id_tramo", "carretera", "km", "lat", "lon", "denominacion", "categoria_via", "titularidad", "organismo_conservacion",
            "autoridad_trafico", "concejo", "codigo_ine", "fuente_pk", "altitud_m", "pendiente_via_pct", "sinuosidad",
            "susc_desprendimientos", "susc_argayos", "susc_grandes_movimientos", "susc_aludes", "susc_incendios",
            "proceso_activo_ladera", "proceso_activo_inundacion", "terreno_cinegetico", "area_de_caza",
            "n_accidentes", "n_acc_graves", "n_fallecidos_30d", "n_heridos_graves_30d", "n_noche", "n_amanecer_atardecer",
            "n_lluvia", "n_niebla", "n_firme_mojado", "n_hielo_nieve", "n_animales", "n_obstaculo", "n_salida_via", "n_en_curva",
            "acc_km_anio_suav", "indice_peligrosidad", "indice_vs_su_categoria", "rr_noche", "rr_lluvia", "rr_hielo_nieve",
            "rr_fauna_condicional", "rr_curva", "n_animales_ventana7km", "indice_fauna",
            "precip_media_anual_mm", "pct_dias_lluvia", "pct_dias_lluvia_fuerte", "pct_dias_helada", "pct_dias_nieve", "racha_p95_kmh", "clima_disponible",
            "comp_siniestralidad", "comp_geologico", "comp_fauna", "comp_meteo_altitud", "comp_trazado",
            "score_riesgo_base", "nivel_riesgo_base", "factores_riesgo",
            "w_sint_desprendimiento", "w_sint_fauna", "w_sint_agua_inundacion", "w_sint_meteo_nieve_hielo",
            "w_sint_accidente_trafico", "w_sint_infraestructura", "particion", "origen_datos"]
    O = f"{D}/08_dataset_unificado"; os.makedirs(O, exist_ok=True)
    out = g[cols]
    with open(f"{O}/contexto_tramos_asturias.csv", "w", encoding="utf-8") as fh: out.to_csv(fh, index=False)
    with open(f"{O}/contexto_tramos_asturias.geojson", "w", encoding="utf-8") as fh: fh.write(g[cols + ["geometry"]].to_json(drop_id=True))
    geo = pd.read_csv(f"{W}/accidentes_geo.csv", low_memory=False)
    with open(f"{O}/accidentes_asturias_geolocalizados.csv", "w", encoding="utf-8") as fh: geo.to_csv(fh, index=False)
    with open(f"{O}/clima_concejos_resumen.csv", "w", encoding="utf-8") as fh: c.to_csv(fh, index=False)
    print("TRAMOS", len(out)); print(out.nivel_riesgo_base.value_counts().to_dict()); print(out.particion.value_counts().to_dict())
    print(out.sort_values("score_riesgo_base", ascending=False)[["id_tramo", "concejo", "nivel_riesgo_base", "factores_riesgo"]].head(12).to_string())

# ---------------------------------------------------------------- 6. PARÁMETROS GLOBALES (para el enriquecedor)
if STAGE == "params":
    O = f"{D}/08_dataset_unificado"
    a = pd.read_csv(f"{D}/01_accidentes_DGT/dgt_accidentes_asturias_2019_2024.csv", low_memory=False)
    i = a[a.ZONA_AGRUPADA == 1]; ani = i[i.TIPO_ACCIDENTE == 8]
    mes_f = (ani.MES.value_counts(normalize=True).sort_index() * 12).round(2)   # >1 = más que un mes medio
    hora_f = (ani.HORA.value_counts(normalize=True).sort_index() * 24).round(2)
    ctx = pd.read_csv(f"{O}/contexto_tramos_asturias.csv")
    p = {
        "periodo_accidentes": "2019-2024", "n_accidentes_interurbanos": int(len(i)),
        "prop_global": {k: round(float(v), 4) for k, v in {
            "noche": i.CONDICION_ILUMINACION.isin([4, 5, 6]).mean(), "lluvia": i.CONDICION_METEO.isin([3, 4]).mean(),
            "animales": (i.TIPO_ACCIDENTE == 8).mean(), "curva": i.TRAZADO_PLANTA.isin([2, 3]).mean()}.items()},
        "fauna_factor_mes": {int(k): float(v) for k, v in mes_f.items()},
        "fauna_factor_hora": {int(k): float(v) for k, v in hora_f.items()},
        "umbrales_score_nivel": {lvl: round(float(ctx.score_riesgo_base.quantile(q)), 3) for lvl, q in [("media", .5), ("alta", .8), ("critica", .95)]},
        "pesos_score": {"siniestralidad": .40, "geologico": .25, "fauna": .15, "meteo_altitud": .10, "trazado": .10},
    }
    with open(f"{O}/parametros_globales.json", "w", encoding="utf-8") as fh: json.dump(p, fh, ensure_ascii=False, indent=2)
    print(json.dumps(p, ensure_ascii=False)[:1500])
