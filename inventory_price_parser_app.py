# inventory_price_parser_app.py
# ---------------------------------------------------------
# Streamlit web‑app per caricare un file inventario (ITALIA.xlsx)
# e un file prezzi d'acquisto (PREZZI ACQUISTO.xlsx) e abbinarli
# tramite SKU. Il prezzo di riferimento usato è "Prezzo medio".
# Esecuzione locale:  
#   streamlit run inventory_price_parser_app.py
# ---------------------------------------------------------

import io

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


def get_merged_inventory(inventory_df: pd.DataFrame, purchase_df: pd.DataFrame, parse_suffix: bool) -> pd.DataFrame:
    """Esegue il merge mantenendo la colonna Categoria se presente."""
    inv_key_candidates = [c for c in inventory_df.columns if c.upper() in {"SKU", "CODICE(ASIN)", "CODICE", "ASIN"}]
    price_key_candidates = [c for c in purchase_df.columns if c.upper() in {"CODICE", "SKU", "CODICE(ASIN)"}]

    if not inv_key_candidates:
        st.error("⚠️ Il file inventario non contiene una colonna riconoscibile come SKU.")
        st.stop()
    if not price_key_candidates:
        st.error("⚠️ Il file prezzi non contiene una colonna riconoscibile come Codice.")
        st.stop()

    inv_key = st.selectbox("Colonna SKU in inventario", inv_key_candidates, index=0)
    price_key = st.selectbox("Colonna SKU in prezzi", price_key_candidates, index=0)

    inventory_df["_SKU_KEY_"] = normalize_sku(inventory_df[inv_key], parse_suffix)
    purchase_df["_SKU_KEY_"] = normalize_sku(purchase_df[price_key], parse_suffix)

    price_cols = [c for c in purchase_df.columns if c.lower().startswith("prezzo medio") or c.lower().startswith("prezzo_medio")]
    if not price_cols:
        st.error("⚠️ La colonna 'Prezzo medio' non è stata trovata nel file prezzi.")
        st.stop()
    price_col = price_cols[0]

    cat_cols = [c for c in purchase_df.columns if "categoria" in c.lower()]
    if cat_cols:
        purchase_df = purchase_df.rename(columns={cat_cols[0]: "Categoria"})
        subset_cols = ["_SKU_KEY_", price_col, "Categoria"]
    else:
        subset_cols = ["_SKU_KEY_", price_col]

    merged = inventory_df.merge(
        purchase_df[subset_cols], on="_SKU_KEY_", how="left", suffixes=("", "_acquisto")
    )
    merged = merged.rename(columns={price_col: "Prezzo medio acquisto (€)"})
    return merged


def calc_min_price(row, fee_pct: float, margin_pct: float, fba: float):
    cost = row["Prezzo medio acquisto (€)"]
    if pd.isna(cost):
        return None
    return round((cost + fba) / (1 - (fee_pct + margin_pct) / 100), 2)


def build_flatfile(df: pd.DataFrame) -> pd.DataFrame:
    field_names = [
        "sku",
        "minimum-seller-allowed-price",
        "maximum-seller-allowed-price",
        "country-code",
        "currency-code",
        "rule-name",
        "rule-action",
        "business-rule-name",
        "business-rule-action",
    ]

    header_desc = [
        "SKU",
        "Min price",
        "Max price",
        "Country code",
        "Currency code",
        "Rule name",
        "Rule action",
        "Business rule name",
        "Business rule action",
    ]

    data = {
        "sku": df["_SKU_KEY_"],
        "minimum-seller-allowed-price": df["Prezzo minimo suggerito (€)"],
        "maximum-seller-allowed-price": "",
        "country-code": "IT",
        "currency-code": "EUR",
        "rule-name": "Rule1",
        "rule-action": "start",
        "business-rule-name": "",
        "business-rule-action": "",
    }

    df_data = pd.DataFrame(data)
    df_full = pd.concat(
        [pd.DataFrame([header_desc], columns=field_names), pd.DataFrame([field_names], columns=field_names), df_data],
        ignore_index=True,
    )
    return df_full


def make_flatfile_bytes(df: pd.DataFrame) -> io.BytesIO:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, header=False)
    output.seek(0)
    return output


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
merged_df = get_merged_inventory(inventory_df, purchase_df, parse_option)

with st.sidebar:
    st.subheader("⚙️ Parametri di calcolo")
    amazon_fee_pct = st.number_input("📉 Fee Amazon %", value=15.0, min_value=0.0)
    margin_pct = st.number_input("💰 Margine desiderato %", value=20.0, min_value=0.0)
    fba_cost = st.number_input("📦 Costo FBA / spedizione €", value=0.0, min_value=0.0)

    if "Categoria" in merged_df.columns:
        cats = merged_df["Categoria"].dropna().unique().tolist()
        selected_cats = st.multiselect("🔍 Filtra per categoria", cats, default=cats)
        merged_df = merged_df[merged_df["Categoria"].isin(selected_cats)]

merged_df["Prezzo minimo suggerito (€)"] = merged_df.apply(
    calc_min_price,
    axis=1,
    fee_pct=amazon_fee_pct,
    margin_pct=margin_pct,
    fba=fba_cost,
)

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
    totale_vendita = round((merged_df["Prezzo"] * merged_df["Quantita'"]).sum(), 2)
    st.metric(
        "Totale inventario (valore vendita)",
        f"€ {totale_vendita:,.2f}",
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

st.download_button(
    "💾 Scarica Flat‑File (min price)",
    data=make_flatfile_bytes(build_flatfile(merged_df)),
    file_name="AutomatePricing_MinOnly.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

# ---------------------------------------------------------
# Credits
# ---------------------------------------------------------
st.caption("Made with Streamlit · Ultimo aggiornamento: 19 lug 2025")
