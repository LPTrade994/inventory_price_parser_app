# inventory_price_parser_app.py
# ---------------------------------------------------------
# Streamlit web‑app per caricare un file inventario (ITALIA.xlsx)
# e un file prezzi d'acquisto (PREZZI ACQUISTO.xlsx) e abbinarli
# tramite SKU. Il prezzo di riferimento usato è "Prezzo medio".
# Esecuzione locale:  
#   streamlit run inventory_price_parser_app.py
# ---------------------------------------------------------

import io
from pathlib import Path

import pandas as pd
import streamlit as st

# ---------------------------------------------------------
# Configurazione pagina
# ---------------------------------------------------------
st.set_page_config(
    page_title="Matcher prezzi d'acquisto ↔ inventario",
    page_icon="📊",
    layout="wide",
)

st.title("📦 Matcher Prezzi d'acquisto → Inventario")
st.markdown(
    """
    Carica **due** file Excel:
    1. **Inventario** – deve contenere una colonna **SKU** (o `Codice(ASIN)` se preferisci)
    2. **Prezzi d'acquisto** – deve contenere una colonna **Codice** e **Prezzo medio**
    
    L'app esegue il *parsing* dello SKU (opzionale), esegue il join
    e fornisce un dataset unificato con il prezzo medio di acquisto affiancato.
    """
)

# ---------------------------------------------------------
# Sidebar – caricamento file e opzioni
# ---------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Opzioni")
    parse_option = st.checkbox(
        "Ignora suffisso dopo '-' nello SKU (es. 4556415-2 → 4556415)",
        value=True,
    )

    inv_file = st.file_uploader(
        "📥 File inventario (es. ITALIA.xlsx)",
        type=["xlsx", "xls"],
        key="inv_uploader",
    )

    price_file = st.file_uploader(
        "📥 File acquisti (es. PREZZI ACQUISTO.xlsx)",
        type=["xlsx", "xls"],
        key="price_uploader",
    )

    download_name = st.text_input(
        "Nome file Excel da scaricare", value="inventario_con_prezzi.xlsx"
    )

# ---------------------------------------------------------
# Funzioni di utilità
# ---------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_excel(uploaded_file):
    if uploaded_file is None:
        return None
    return pd.read_excel(uploaded_file)


def normalize_sku(series: pd.Series, parse_suffix: bool) -> pd.Series:
    """Restituisce SKU come stringa; se parse_suffix=True rimuove tutto dopo '-'."""
    if parse_suffix:
        return series.astype(str).str.split("-").str[0].str.strip()
    return series.astype(str).str.strip()


# ---------------------------------------------------------
# Caricamento dati
# ---------------------------------------------------------
inventory_df = load_excel(inv_file)
purchase_df = load_excel(price_file)

# Early‑return UI se mancano file
if inventory_df is None or purchase_df is None:
    st.info("👈 Carica entrambi i file nella barra laterale per continuare.")
    st.stop()

# ---------------------------------------------------------
# Preparazione e merge
# ---------------------------------------------------------
# Identifica colonne chiave presunte (fall‑back)
inv_key_candidates = [col for col in inventory_df.columns if col.upper() in {"SKU", "CODICE(ASIN)", "CODICE", "ASIN"}]
price_key_candidates = [col for col in purchase_df.columns if col.upper() in {"CODICE", "SKU", "CODICE(ASIN)"}]

if not inv_key_candidates:
    st.error("⚠️ Il file inventario non contiene una colonna riconoscibile come SKU.")
    st.stop()
if not price_key_candidates:
    st.error("⚠️ Il file prezzi non contiene una colonna riconoscibile come Codice.")
    st.stop()

inv_key = st.selectbox("Colonna SKU in inventario", inv_key_candidates, index=0)
price_key = st.selectbox("Colonna SKU in prezzi", price_key_candidates, index=0)

# Normalizza chiavi
inventory_df["_SKU_KEY_"] = normalize_sku(inventory_df[inv_key], parse_option)
purchase_df["_SKU_KEY_"] = normalize_sku(purchase_df[price_key], parse_option)

# Prezzo medio – gestisci nomi alternativi
price_cols = [c for c in purchase_df.columns if c.lower().startswith("prezzo medio") or c.lower().startswith("prezzo_medio")]
if not price_cols:
    st.error("⚠️ La colonna 'Prezzo medio' non è stata trovata nel file prezzi.")
    st.stop()
price_col = price_cols[0]

# Merge left join (tutti gli articoli inventario)
merged_df = inventory_df.merge(
    purchase_df[["_SKU_KEY_", price_col]], on="_SKU_KEY_", how="left", suffixes=("", "_acquisto")
)

# Rename per chiarezza
merged_df = merged_df.rename(columns={price_col: "Prezzo medio acquisto (€)"})

# ---------------------------------------------------------
# Dashboard dei risultati
# ---------------------------------------------------------
st.subheader("Anteprima dataset unificato")
st.dataframe(merged_df, use_container_width=True, hide_index=True)

# KPI
st.subheader("📈 KPI principali")
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("SKU totali", len(merged_df))
with col2:
    st.metric("Prezzo medio (€/art.)", round(merged_df["Prezzo medio acquisto (€)"].mean(skipna=True), 2))
with col3:
    st.metric(
        "Totale inventario (valore vendita)",
        f"€ {round((merged_df['Prezzo'] * merged_df["Quantita'"]).sum(), 2):,.2f}",
    )

# ---------------------------------------------------------
# Download
# ---------------------------------------------------------
output = io.BytesIO()
with pd.ExcelWriter(output, engine="openpyxl") as writer:
    merged_df.to_excel(writer, index=False, sheet_name="inventario_match")
output.seek(0)

st.download_button(
    label="💾 Scarica Excel unificato",
    data=output,
    file_name=download_name or "inventario_match.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

# ---------------------------------------------------------
# Credits
# ---------------------------------------------------------
st.caption("Made with Streamlit · Ultimo aggiornamento: 19 lug 2025")
