# inventory_price_parser_app.py
# ---------------------------------------------------------
# Streamlit web‑app per caricare un file inventario (ITALIA.xlsx
# o inventario.txt) e un file prezzi d'acquisto (PREZZI ACQUISTO.xlsx
# o acquisti.txt) e abbinarli
# tramite SKU. Il prezzo di riferimento usato è "Prezzo medio".
# Esecuzione locale:  
#   streamlit run inventory_price_parser_app.py
# ---------------------------------------------------------

import io

import pandas as pd
import numpy as np
import streamlit as st

CATEGORY_MAP = {
    "Videogiochi - Giochi e accessori": {"referral": 15.0, "closing": 0.81},
    "Videogiochi - Console": {"referral": 8.0, "closing": 0.81},
    "Libri": {"referral": 15.0, "closing": 1.01},
    "Musica": {"referral": 15.0, "closing": 0.81},
    "Video e DVD": {"referral": 15.0, "closing": 0.81},
    "Software": {"referral": 15.0, "closing": 0.81},
    "_default": {"referral": 15.0, "closing": 0.00},
}
DST_PCT = 3.0  # Italia

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
        "Ignora suffisso dopo l'ultimo '-' nello SKU (es. 4556415-2-XY → 4556415-2)",
        value=True,
    )

    inv_file = st.file_uploader(
        "📥 File inventario (es. ITALIA.xlsx o inventario.txt)",
        type=["xlsx", "xls", "txt"],
        key="inv_uploader",
    )

    price_file = st.file_uploader(
        "📥 File acquisti (es. PREZZI ACQUISTO.xlsx o acquisti.txt)",
        type=["xlsx", "xls", "txt"],
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

    filename = getattr(uploaded_file, "name", "").lower()
    if filename.endswith(".txt"):
        try:
            return pd.read_csv(uploaded_file, sep="\t", encoding="utf-8")
        except UnicodeDecodeError:
            uploaded_file.seek(0)
            # alcuni file .txt esportati da Excel non sono UTF-8
            return pd.read_csv(uploaded_file, sep="\t", encoding="latin-1")

    return pd.read_excel(uploaded_file)


def normalize_sku(series: pd.Series, parse_suffix: bool) -> pd.Series:
    """Restituisce SKU come stringa.

    Se ``parse_suffix`` è ``True`` rimuove solo il suffisso dopo l'ultimo
    trattino (``-``), ad esempio ``ABC-1-2`` diventa ``ABC-1``.
    """
    series = series.astype(str).str.strip()
    if parse_suffix:
        return series.str.rsplit("-", n=1).str[0]
    return series


def get_merged_inventory(
    inventory_df: pd.DataFrame,
    purchase_df: pd.DataFrame,
    parse_suffix: bool,
) -> tuple[pd.DataFrame, str]:
    """Esegue il merge mantenendo la colonna Categoria se presente.

    Restituisce anche il nome della colonna usata come SKU originale
    nell'inventario.
    """
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

    # Assicura che gli SKU siano gestiti come testo, evitando problemi di
    # conversione numerica durante l'editing nella griglia dei risultati.
    inventory_df[inv_key] = inventory_df[inv_key].astype(str)
    purchase_df[price_key] = purchase_df[price_key].astype(str)

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
    return merged, inv_key


def calc_min_price(
    row,
    referral_pct: float,
    closing_fee: float,
    dst_pct: float,
    ship_cost: float,
    vat_pct: float,
    margin_pct: float,
):
    """Restituisce il prezzo minimo (IVA inclusa) affinché il margine netto
    desiderato (profitto/prezzo) sia raggiunto.

    Formula ricavata da:
        P/k - (1+d)*(r*P + C) - S - CoG >= m*P

        dove:
          P   = Prezzo IVATO da calcolare
          k   = 1 + v            (v = IVA /100)
          r   = referral_pct /100
          d   = dst_pct      /100
          C   = closing_fee
          S   = costo spedizione / fulfilment (€)
          CoG = costo medio acquisto (€)   [colonna 'Prezzo medio acquisto (€)']
          m   = margin_pct   /100

        Risolto per P:
          P = k*(CoG + S + (1+d)*C)  /  ( 1 - k*((1+d)*r + m) )
    """

    cost = row["Prezzo medio acquisto (€)"]
    if pd.isna(cost) or cost <= 0:
        return None

    r = referral_pct / 100.0
    d = dst_pct      / 100.0
    v = vat_pct      / 100.0
    m = margin_pct   / 100.0
    k = 1 + v

    numerator = k * (cost + ship_cost + (1 + d) * closing_fee)
    denom     = 1 - k * ((1 + d) * r + m)

    if denom <= 0:
        return None  # parametri impossibili (fee+margine troppo alti)

    return round(numerator / denom, 2)


def build_flatfile(df: pd.DataFrame, sku_col: str) -> pd.DataFrame:
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
        "sku": df[sku_col],
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


def highlight_below(row):
    min_val = pd.to_numeric(row.get("Prezzo minimo suggerito (€)"), errors="coerce")
    price_val = pd.to_numeric(row.get("Prezzo"), errors="coerce")
    if np.isfinite(min_val) and np.isfinite(price_val) and min_val < price_val:
        return ["background-color: lightcoral"] * len(row)
    return [""] * len(row)


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
merged_df, inv_key = get_merged_inventory(inventory_df, purchase_df, parse_option)

with st.sidebar:
    st.subheader("⚙️ Parametri commissioni")

    cats = [c for c in CATEGORY_MAP.keys() if c != "_default"]
    selected_cat = st.selectbox("Categoria", cats)
    # Applica la stessa categoria a tutte le righe
    merged_df["Categoria"] = selected_cat

    defaults = CATEGORY_MAP.get(selected_cat, CATEGORY_MAP["_default"])
    referral_fee_pct = st.number_input(
        "% Commissione Amazon", value=defaults["referral"], min_value=0.0
    )
    shipping_cost = st.number_input("Costo spedizione €", value=0.0, min_value=0.0)
    vat_pct = st.number_input("IVA %", value=22.0, min_value=0.0, step=0.1)
    margin_pct = st.number_input("Margine desiderato %", value=20.0, min_value=0.0)

# verifica parametri di costo/margine
denom_check = 1 - (1 + vat_pct/100) * (
    (1 + DST_PCT/100) * (referral_fee_pct/100) + margin_pct/100
)
if denom_check <= 0:
    st.warning(
        "Parametri non validi: commissioni + margine troppo alti rispetto al prezzo."
    )

closing_fee = CATEGORY_MAP.get(selected_cat, CATEGORY_MAP["_default"])["closing"]

merged_df["Prezzo minimo suggerito (€)"] = merged_df.apply(
    calc_min_price,
    axis=1,
    referral_pct=referral_fee_pct,
    closing_fee=closing_fee,
    dst_pct=DST_PCT,
    ship_cost=shipping_cost,
    vat_pct=vat_pct,
    margin_pct=margin_pct,
)

invalid_mask = (
    merged_df["Prezzo minimo suggerito (€)"].isna()
    & merged_df["Prezzo medio acquisto (€)"].notna()
    & (merged_df["Prezzo medio acquisto (€)"] > 0)
)
if invalid_mask.any():
    st.warning(
        "Parametri non validi: commissioni + margine troppo alti rispetto al prezzo."
    )

# ---------------------------------------------------------
# Dashboard dei risultati
# ---------------------------------------------------------
st.subheader("Anteprima dataset unificato")

edited_df = st.data_editor(
    merged_df,
    key="merged_df_editor",
    use_container_width=True,
    hide_index=True,
    column_config={
        inv_key: st.column_config.TextColumn(disabled=False)
    },
)

edited_df["_SKU_KEY_"] = normalize_sku(edited_df[inv_key], parse_option)

if st.button("🔄 Ricalcola prezzi minimi"):
    edited_df["Prezzo minimo suggerito (€)"] = edited_df.apply(
        calc_min_price,
        axis=1,
        referral_pct=referral_fee_pct,
        closing_fee=closing_fee,
        dst_pct=DST_PCT,
        ship_cost=shipping_cost,
        vat_pct=vat_pct,
        margin_pct=margin_pct,
    )

styled_df = edited_df.style.apply(highlight_below, axis=1)
st.dataframe(styled_df, use_container_width=True, hide_index=True)

# KPI
st.subheader("📈 KPI principali")
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("SKU totali", len(edited_df))
with col2:
    st.metric(
        "Prezzo medio (€/art.)",
        round(edited_df["Prezzo medio acquisto (€)"].mean(skipna=True), 2),
    )
with col3:
    totale_vendita = round(
        (edited_df["Prezzo"] * edited_df["Quantita'"]).sum(),
        2,
    )
    st.metric(
        "Totale inventario (valore vendita)",
        f"€ {totale_vendita:,.2f}",
    )

# ---------------------------------------------------------
# Download
# ---------------------------------------------------------
output = io.BytesIO()
with pd.ExcelWriter(output, engine="openpyxl") as writer:
    excel_df = edited_df.drop(columns=["_SKU_KEY_"], errors="ignore")
    excel_df.to_excel(writer, index=False, sheet_name="inventario_match")
output.seek(0)

st.download_button(
    label="💾 Scarica Excel unificato",
    data=output,
    file_name=download_name or "inventario_match.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

st.download_button(
    "💾 Scarica Flat‑File (min price)",
    data=make_flatfile_bytes(build_flatfile(edited_df, inv_key)),
    file_name="AutomatePricing_MinOnly.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

# ---------------------------------------------------------
# Credits
# ---------------------------------------------------------
st.caption("Made with Streamlit · Ultimo aggiornamento: 19 lug 2025")
