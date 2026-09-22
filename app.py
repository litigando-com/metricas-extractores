import os
from datetime import date, timedelta

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


st.set_page_config(page_title="Cobertura por pipeline", layout="wide")
st.title("Cobertura diaria por pipeline")
st.caption(
    "TORRE_ARCHIVOS_AWS: cuantos registros procesa cada pipeline por dia "
    "(Gargantua, Atriox, Textract, revision manual, insercion directa/SAMAI). "
    "Es una foto del ESTADO actual, no un historico de quien proceso cada registro."
)

col1, col2 = st.columns(2)
desde = col1.date_input("Desde", value=date.today() - timedelta(days=60))
hasta = col2.date_input("Hasta", value=date.today())

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
cols[0].metric("Total general", total_general)
for c, cat in zip(cols[1:], CATEGORIES):
    total_cat = int(df[cat].sum())
    pct = (total_cat / total_general * 100) if total_general else 0
    c.metric(cat.replace("_", " ").title(), total_cat, f"{pct:.0f}%")

st.area_chart(df, x="DIA", y=CATEGORIES, color=COLORS)

with st.expander("Ver datos crudos"):
    st.dataframe(df, use_container_width=True)
    st.download_button(
        "Descargar CSV",
        df.to_csv(index=False).encode("utf-8"),
        file_name=f"cobertura_{desde}_{hasta}.csv",
        mime="text/csv",
    )
