import os
from datetime import date, timedelta

import altair as alt
import oracledb
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "1521")
DB_SERVICE = os.getenv("DB_SERVICE")

# Orden fijo de categorias y su color asociado (paleta categorica validada
# contra daltonismo del skill dataviz) - nunca se reordenan ni se reciclan.
CATEGORIES = ["GARGANTUA", "ATRIOX", "TEXTRACT", "MANUAL", "INSERCION_DIRECTA"]
COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
LABELS = {
    "GARGANTUA": "Gargantua",
    "ATRIOX": "Atriox",
    "TEXTRACT": "Textract",
    "MANUAL": "Manual",
    "INSERCION_DIRECTA": "Insercion Directa (SAMAI)",
}

# Misma logica que la consulta original, solo con :desde/:hasta parametrizados
# en vez de fechas fijas (TO_DATE hardcodeado).
QUERY = """
WITH TEXTRACT_BASE AS (
    SELECT REGEXP_SUBSTR(FILE_CSV, '[^_]+', 1, 7) AS ORACLE_ID,
           FECHA_CREA
    FROM TEXTRACT_PDF
    WHERE FECHA_CREA >= :desde AND FECHA_CREA < :hasta

    UNION ALL

    SELECT REGEXP_SUBSTR(FILE_CSV, '[^_]+', 1, 7) AS ORACLE_ID,
           FECHA_CREA
    FROM TEXTRACT_PDF_BKP
    WHERE FECHA_CREA >= :desde AND FECHA_CREA < :hasta
),
TEXTRACT_ORACLES AS (
    SELECT DISTINCT ORACLE_ID, FECHA_CREA
    FROM TEXTRACT_BASE
    WHERE ORACLE_ID IS NOT NULL AND REGEXP_LIKE(ORACLE_ID, '^[0-9]+$')
),
BASE AS (
    SELECT T.ORACLE_ID,
           T.FECHA_CREACION,
           CASE
               WHEN T.ESTADO_PRUEBA = 'GARGANTUA'
                   AND T.USUARIO_VERIFICADOR IS NULL
                   THEN 'GARGANTUA'
               WHEN T.ESTADO = 'REVISADO_ATRIOX'
                   AND T.USUARIO_VERIFICADOR IS NULL
                   THEN 'ATRIOX'
               WHEN T.ESTADO = 'REVISADO_ATRIOX_GENERALES'
                   AND T.USUARIO_VERIFICADOR IS NULL
                   THEN 'ATRIOX_GENERALES'
               WHEN T.ESTADO = 'REVISADO_ATRIOX_CORTE_TIERRAS'
                   AND T.USUARIO_VERIFICADOR IS NULL
                   THEN 'ATRIOX_CORTE_TIERRAS'
               WHEN T.USUARIO_VERIFICADOR IS NOT NULL
                   THEN 'MANUAL'
               WHEN T.FECHA_BUSCA IS NOT NULL
                   AND TX.ORACLE_ID IS NOT NULL
                   THEN 'TEXTRACT'
               ELSE 'INSERCION_DIRECTA (SAMAI)'
           END AS TIPO
    FROM TORRE_ARCHIVOS_AWS T
    LEFT JOIN TEXTRACT_ORACLES TX ON TX.ORACLE_ID = TO_CHAR(T.ORACLE_ID)
    WHERE T.FECHA_CREACION >= :desde AND T.FECHA_CREACION < :hasta
)
SELECT TO_CHAR(FECHA_CREACION, 'DD/MM/YYYY') AS DIA,
       COUNT(DISTINCT ORACLE_ID)                                                      AS TOTAL_GENERAL,
       COUNT(DISTINCT CASE WHEN TIPO = 'TEXTRACT' THEN ORACLE_ID END)                  AS TEXTRACT,
       COUNT(DISTINCT CASE WHEN TIPO = 'GARGANTUA' THEN ORACLE_ID END)                 AS GARGANTUA,
       COUNT(DISTINCT CASE WHEN TIPO IN ('ATRIOX', 'ATRIOX_GENERALES', 'ATRIOX_CORTE_TIERRAS')
                               THEN ORACLE_ID END)                                     AS ATRIOX,
       COUNT(DISTINCT CASE WHEN TIPO = 'MANUAL' THEN ORACLE_ID END)                    AS MANUAL,
       COUNT(DISTINCT CASE WHEN TIPO = 'INSERCION_DIRECTA (SAMAI)' THEN ORACLE_ID END) AS INSERCION_DIRECTA
FROM BASE
GROUP BY TO_CHAR(FECHA_CREACION, 'DD/MM/YYYY')
ORDER BY TO_DATE(TO_CHAR(FECHA_CREACION, 'DD/MM/YYYY'), 'DD/MM/YYYY')
"""


@st.cache_data(ttl=300, show_spinner="Consultando Oracle...")
def load_data(desde: date, hasta_exclusiva: date) -> pd.DataFrame:
    dsn = f"{DB_HOST}:{DB_PORT}/{DB_SERVICE}"
    with oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=dsn) as conn:
        return pd.read_sql(QUERY, conn, params={"desde": desde, "hasta": hasta_exclusiva})


# Totales acumulados (sin filtro de fecha, igual que la consulta original de despachos).
DESPACHOS_QUERY = """
SELECT
    (SELECT COUNT(*) FROM DESPACHOS_GARGANTUA) AS GARGANTUA,
    (SELECT COUNT(*) FROM DESPACHOS_ATRIOX) AS ATRIOX,
    (SELECT COUNT(*) FROM DESPACHOS_ATRIOX_GENERALES) AS ATRIOX_GENERALES,
    (SELECT COUNT(*) FROM DESPACHOS_ATRIOX_CORTE_TIERRAS) AS ATRIOX_CORTE_TIERRAS,
    (
        SELECT COUNT(*)
        FROM TORRE_DESPACHO TD, TAREAS TAR, DISPOSITIVOS DI, FUNCIONARIOS FU, DESPACHOS D, LOCALIDADES L
        WHERE TD.DESPACHO_ID = TAR.DESPACHO_ID (+)
          AND TD.DESPACHO_ID = D.DESPACHO_ID
          AND D.LOCALIDAD_ID = L.LOCALIDAD_ID
          AND TAR.DISPOSITIVO_ID_NOTIF = DI.DISPOSITIVO_ID (+)
          AND DI.FUNCIONARIO_ID = FU.FUNCIONARIO_ID (+)
          AND TD.GESTOR_RAMA = 'SI'
          AND (FU.FUNCIONARIO_NOMBRE NOT IN ('DESPACHOS', 'SISTEMA') OR FU.FUNCIONARIO_NOMBRE IS NULL)
    ) AS GESTOR_RAMA_TOTALES
FROM DUAL
"""


@st.cache_data(ttl=300, show_spinner="Consultando despachos...")
def load_despachos_coverage() -> pd.Series:
    dsn = f"{DB_HOST}:{DB_PORT}/{DB_SERVICE}"
    with oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=dsn) as conn:
        return pd.read_sql(DESPACHOS_QUERY, conn).iloc[0].astype(int)


st.set_page_config(page_title="Cobertura por pipeline", page_icon="assets/icon.png", layout="wide")

st.image("assets/logo.png", width=220)
st.title("Cobertura diaria por pipeline")
st.caption(
    "TORRE_ARCHIVOS_AWS: cuantos registros procesa cada pipeline por dia "
    "(Gargantua, Atriox, Textract, revision manual, insercion directa/SAMAI)."
)

PRESET_DIAS = {"7 dias": 7, "30 dias": 30, "60 dias": 60, "90 dias": 90}
preset = st.segmented_control(
    "Rango de fechas",
    options=[*PRESET_DIAS, "Personalizado"],
    default="60 dias",
    required=True,
)

if preset == "Personalizado":
    col1, col2 = st.columns(2)
    desde = col1.date_input("Desde", value=date.today() - timedelta(days=60))
    hasta = col2.date_input("Hasta", value=date.today())
else:
    hasta = date.today()
    desde = hasta - timedelta(days=PRESET_DIAS[preset])

if desde > hasta:
    st.error("La fecha 'Desde' debe ser anterior o igual a 'Hasta'.")
    st.stop()

try:
    df = load_data(desde, hasta + timedelta(days=1))
except Exception as e:
    st.error(f"No se pudo conectar a Oracle ({DB_HOST}:{DB_PORT}/{DB_SERVICE}): {e}")
    st.stop()

if df.empty:
    st.info("No hay registros en ese rango de fechas.")
    st.stop()

total_general = int(df["TOTAL_GENERAL"].sum())
cols = st.columns(len(CATEGORIES) + 1)
cols[0].metric("Notificaciones en TORRE_ARCHIVOS_AWS", total_general)
for c, cat in zip(cols[1:], CATEGORIES):
    total_cat = int(df[cat].sum())
    pct = (total_cat / total_general * 100) if total_general else 0
    c.metric(LABELS[cat], total_cat, f"{pct:.0f}%")

df_long = df.melt(id_vars="DIA", value_vars=CATEGORIES, var_name="PIPELINE", value_name="CANTIDAD")
df_long["FECHA"] = pd.to_datetime(df_long["DIA"], format="%d/%m/%Y")

chart = (
    alt.Chart(df_long)
    .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=80, filled=True))
    .encode(
        x=alt.X("FECHA:T", title="Fecha", axis=alt.Axis(format="%d/%m", labelFontSize=13, titleFontSize=14)),
        y=alt.Y(
            "CANTIDAD:Q",
            title="Cantidad de PDFs",
            axis=alt.Axis(labelFontSize=13, titleFontSize=14, tickCount=5),
        ),
        color=alt.Color(
            "PIPELINE:N",
            title="Pipeline",
            scale=alt.Scale(domain=CATEGORIES, range=COLORS),
            legend=alt.Legend(labelFontSize=13, titleFontSize=14, symbolSize=120),
        ),
        tooltip=[
            alt.Tooltip("DIA:N", title="Fecha"),
            alt.Tooltip("PIPELINE:N", title="Pipeline"),
            alt.Tooltip("CANTIDAD:Q", title="PDFs", format=","),
        ],
    )
    .properties(height=550)
    .configure_axis(grid=True, gridColor="#eeeeee")
    .configure_view(strokeWidth=0)
)
st.altair_chart(chart, width="stretch")

with st.expander("Ver datos crudos"):
    st.dataframe(df, width="stretch")
    st.download_button(
        "Descargar CSV",
        df.to_csv(index=False).encode("utf-8"),
        file_name=f"cobertura_{desde}_{hasta}.csv",
        mime="text/csv",
    )

st.divider()
st.subheader("Cobertura de despachos - Gargantua")
st.caption(
    "GARGANTUA vs. despachos totales del gestor de rama (DESPACHOS_GESTOR_RAMA_TOTALES). "
    "La diferencia son los despachos que aun dependen de Textract."
)

try:
    despachos = load_despachos_coverage()
except Exception as e:
    st.error(f"No se pudo consultar despachos: {e}")
    st.stop()

gargantua = despachos["GARGANTUA"]
gestor_rama_totales = despachos["GESTOR_RAMA_TOTALES"]
pct_cobertura = (gargantua / gestor_rama_totales * 100) if gestor_rama_totales else 0
pendiente_textract = gestor_rama_totales - gargantua

gauge_df = pd.DataFrame({
    "estado": ["Cobertura", "Restante"],
    "valor": [pct_cobertura, 100 - pct_cobertura],
})
gauge = (
    alt.Chart(gauge_df)
    .mark_arc(innerRadius=70, outerRadius=100, cornerRadius=6)
    .encode(
        theta=alt.Theta("valor:Q", sort=None, stack=True),
        color=alt.Color(
            "estado:N",
            scale=alt.Scale(domain=["Cobertura", "Restante"], range=["#2a78d6", "#d6e6fa"]),
            legend=None,
        ),
        order=alt.Order("estado:N", sort="descending"),
        tooltip=[alt.Tooltip("estado:N", title="Estado"), alt.Tooltip("valor:Q", title="Porcentaje", format=".1f")],
    )
    .properties(height=260, width=260)
)
gauge_label = (
    alt.Chart(pd.DataFrame({"texto": [f"{pct_cobertura:.0f}%"]}))
    .mark_text(fontSize=40, fontWeight="bold", color="#2392fb")
    .encode(text="texto:N")
)

_, centro, _ = st.columns([1, 2, 1])
with centro:
    gcol1, gcol2 = st.columns([1, 1])
    with gcol1:
        st.altair_chart(alt.layer(gauge, gauge_label), width="content")
    with gcol2:
        st.metric("Despachos gestor rama (total)", gestor_rama_totales)
        st.metric("Cubiertos por Gargantua", gargantua, f"{pct_cobertura:.0f}%")
        st.metric("Pendientes (Textract)", pendiente_textract)

st.subheader("Volumen de despachos por metrica")
resumen_df = pd.DataFrame({
    "metrica": ["Gestor rama (total)", "Gargantua", "Textract (pendientes)", "Atriox", "Atriox Generales", "Atriox Corte Tierras"],
    "cantidad": [
        gestor_rama_totales,
        gargantua,
        pendiente_textract,
        despachos["ATRIOX"],
        despachos["ATRIOX_GENERALES"],
        despachos["ATRIOX_CORTE_TIERRAS"],
    ],
})
barras = (
    alt.Chart(resumen_df)
    .mark_bar(size=24, cornerRadiusEnd=4, color="#2a78d6")
    .encode(
        y=alt.Y("metrica:N", sort="-x", title=None, axis=alt.Axis(labelFontSize=13)),
        x=alt.X("cantidad:Q", title="Cantidad de despachos", axis=alt.Axis(labelFontSize=13, titleFontSize=14)),
        tooltip=[alt.Tooltip("metrica:N", title="Metrica"), alt.Tooltip("cantidad:Q", title="Cantidad", format=",")],
    )
)
etiquetas = barras.mark_text(align="left", dx=6, fontSize=13).encode(text=alt.Text("cantidad:Q", format=","))
st.altair_chart((barras + etiquetas).properties(height=270), width="stretch")

st.divider()
st.caption("Metricas de extractores de PDFs en Litigando", text_alignment="center")
