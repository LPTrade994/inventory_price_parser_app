# inventory_price_parser_app.py
# ---------------------------------------------------------
# Streamlit web-app per caricare un file inventario (ITALIA.xlsx
# o inventario.txt) e un file prezzi d'acquisto (PREZZI ACQUISTO.xlsx
# o acquisti.txt) e abbinarli tramite SKU. Il prezzo di riferimento usato è
# "Prezzo medio". Integra anche l'export Automate Pricing (flat-file Amazon)
# per compilare rule-name e minimum-seller-allowed-price.
# Esecuzione locale:
#   streamlit run inventory_price_parser_app.py
# ---------------------------------------------------------

import io
import re

import openpyxl
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
DST_PCT = 3.0  # Italia (DST default)

COUNTRY_CODES = {
    "AE","AU","BE","BR","CA","CN","DE","ES","FR","GB","IT","JP",
    "MX","NL","PL","SE","TR","UK","US","IE"
}

# ---------------------------------------------------------
# Sidebar – caricamento file e opzioni
# ---------------------------------------------------------
st.set_page_config(page_title="Inventory & Price Parser", layout="wide")
st.title("📦 Inventory & Price Parser")

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

    export_file = st.file_uploader(
        "📥 Flat-file Amazon (export) • Non Automated SKUs",
        type=["xlsx", "xls", "csv", "tsv"],
        key="export_uploader",
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

    name = (getattr(uploaded_file, "name", "") or "").lower()
    if name.endswith(".txt"):
        uploaded_file.seek(0)
        content = uploaded_file.read().decode("latin-1", errors="ignore")
        # prova TSV poi CSV
        try:
            df = pd.read_csv(io.StringIO(content), sep="\t", decimal=",")
        except Exception:
            df = pd.read_csv(io.StringIO(content), sep=";", decimal=",")
        return df

    # Excel
    try:
        uploaded_file.seek(0)
        df = pd.read_excel(uploaded_file, engine="openpyxl")
    except Exception:
        uploaded_file.seek(0)
        df = pd.read_excel(uploaded_file)
    return df


def _to_kebab(s: str) -> str:
    s = "" if s is None else str(s)
    s = s.strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    return s


def load_amazon_template(uploaded_file):
    """Parser robusto per template/export Automate Pricing (anche CSV/TSV).

    - Cerca il foglio giusto ("Modello assegnazione prezzo" / "Esempio") se Excel,
      altrimenti legge CSV/TSV senza header.
    - Individua dinamicamente la riga di header con il campo 'sku' (case-insensitive).
    - Converte l'header in *kebab-case* e rinomina 'sku' -> 'SKU'; 'current-selling-price' -> 'Prezzo'.
    - Esegue coercizioni numeriche sui campi prezzo/rank; valida rule-action/country-code.
    """
    name = (getattr(uploaded_file, "name", "") or "").lower()

    # --- Caricamento grezzo in DataFrame senza header ---
    df_raw = None
    if name.endswith((".csv", ".tsv")):
        uploaded_file.seek(0)
        sample = uploaded_file.read(4096).decode("utf-8", errors="ignore")
        uploaded_file.seek(0)
        sep = "\t" if ("\t" in sample and "," not in sample) else ","
        df_raw = pd.read_csv(uploaded_file, header=None, sep=sep, dtype=str, keep_default_na=False)
    else:
        # Excel
        uploaded_file.seek(0)
        wb = openpyxl.load_workbook(uploaded_file, read_only=True, data_only=True)
        # Prova nomi noti, altrimenti prima sheet utile
        candidate_sheets = [
            s for s in ["Modello assegnazione prezzo", "Esempio"] if s in wb.sheetnames
        ] or wb.sheetnames
        ws = wb[candidate_sheets[0]]
        data = list(ws.values)
        if not data:
            return pd.DataFrame()
        df_raw = pd.DataFrame(data)

    # --- Ricerca della riga header che contiene 'sku' ---
    header_idx = None
    max_scan = min(25, len(df_raw))
    for i in range(max_scan):
        row = (
            df_raw.iloc[i]
            .astype(str)
            .str.strip()
            .str.lower()
            .replace({"nan": ""})
            .tolist()
        )
        if any(cell == "sku" for cell in row):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Header con 'sku' non trovato nel flat-file export.")

    # --- Costruzione DataFrame con intestazioni pulite ---
    headers = [_to_kebab(x) for x in df_raw.iloc[header_idx].tolist()]
    df = df_raw.iloc[header_idx + 1 :].reset_index(drop=True).copy()
    # taglia/estende colonne per allinearle all'header
    df = df.iloc[:, : len(headers)]
    df.columns = headers

    # drop righe completamente vuote
    df = df.dropna(how="all")

    # normalizza stringhe
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].astype(str).str.strip()

    # rinomina chiavi principali
    rename_map = {"sku": "SKU", "current-selling-price": "Prezzo"}
    df = df.rename(columns=rename_map)

    # coercizioni numeriche
    price_like = [c for c in df.columns if re.search(r"price|points|amount", c)]
    for c in price_like:
        df[c] = (
            df[c]
            .astype(str)
            .str.replace(".", "", regex=False)  # rimuovi separatore migliaia se presente
            .str.replace(",", ".", regex=False)
        )
        df[c] = pd.to_numeric(df[c], errors="coerce")
        df.loc[df[c] < 0, c] = np.nan

    if "sales-rank" in df.columns:
        df["sales-rank"] = (
            df["sales-rank"].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
        )
        df["sales-rank"] = pd.to_numeric(df["sales-rank"], errors="coerce").astype("Int64")

    if "rule-action" in df.columns:
        df["rule-action"] = df["rule-action"].astype(str).str.strip().str.upper()
        valid_actions = {"START", "STOP"}
        invalid = df.loc[
            ~df["rule-action"].isin(valid_actions) & df["rule-action"].notna(), "rule-action"
        ].unique()
        if len(invalid) > 0:
            raise ValueError(f"Valori 'rule-action' non validi: {', '.join(invalid)}")

    if "country-code" in df.columns:
        df["country-code"] = df["country-code"].astype(str).str.strip().str.upper()
        invalid_cc = df.loc[
            ~df["country-code"].isin(COUNTRY_CODES) & df["country-code"].notna(), "country-code"
        ].unique()
        if len(invalid_cc) > 0:
            # Non blocco: avviso in UI; qui mi limito a normalizzare
            pass

    # pulizia SKU
    if "SKU" in df.columns:
        df = df[df["SKU"].astype(str).str.strip() != ""]

    return df.reset_index(drop=True)


def normalize_sku(series: pd.Series, parse_suffix: bool) -> pd.Series:
    series = series.astype(str).str.strip()
    if parse_suffix:
        return series.str.rsplit("-", n=1).str[0]
    return series


def get_merged_inventory(inventory_df: pd.DataFrame, purchase_df: pd.DataFrame, parse_suffix: bool):
    inv_key_candidates = [c for c in inventory_df.columns if c.upper() in {"SKU", "CODICE(ASIN)", "CODICE", "ASIN"}]
    price_key_candidates = [c for c in purchase_df.columns if c.upper() in {"CODICE", "SKU", "CODICE(ASIN)"}]
    if not inv_key_candidates or not price_key_candidates:
        st.error(
            "Assicurati che entrambi i file contengano almeno una colonna chiave tra: SKU, CODICE(ASIN), CODICE, ASIN."
        )
        st.stop()

    inv_key = st.selectbox("Colonna SKU nell'inventario", inv_key_candidates, index=0)
    price_key = st.selectbox("Colonna SKU nel file acquisti", price_key_candidates, index=0)

    inventory_df["_SKU_KEY_"] = normalize_sku(inventory_df[inv_key], parse_suffix)
    purchase_df["_SKU_KEY_"] = normalize_sku(purchase_df[price_key], parse_suffix)

    # trova colonna prezzo più adatta
    candidate_price_cols = [
        c
        for c in purchase_df.columns
        if ("prezzo" in c.lower() and "medio" in c.lower()) or c.lower().strip() in {"prezzo", "prezzo medio", "prezzo medio (€)"}
    ]
    if not candidate_price_cols:
        st.error("Colonna del prezzo d'acquisto non trovata (es. 'Prezzo medio', 'Prezzo').")
        st.stop()
    price_col = candidate_price_cols[0]

    # mantiene "Categoria" se presente
    cat_cols = [c for c in purchase_df.columns if c.lower().strip() == "categoria"]

    optional_cols = [
        c
        for c in purchase_df.columns
        if c.lower().strip()
        in {
            "quantita'",
            "quantità",
            "prezzo",
            "prezzo medio",
            "codice(asin)",
            "asin",
            "sku",
            "country-code",
            "currency-code",
            "rule-name",
            "rule-action",
            "business-rule-name",
            "business-rule-action",
        }
    ]
    subset_cols = ["_SKU_KEY_", price_col] + optional_cols
    if cat_cols:
        purchase_df = purchase_df.rename(columns={cat_cols[0]: "Categoria"})
        subset_cols.append("Categoria")

    merged = inventory_df.merge(
        purchase_df[subset_cols], on="_SKU_KEY_", how="left", suffixes=("", "_acquisto")
    )
    merged = merged.rename(columns={price_col: "Prezzo medio acquisto (€)"})
    return merged, inv_key


def calc_min_price(
    row: pd.Series,
    referral_pct: float,
    closing_fee: float,
    dst_pct: float,
    ship_cost: float,
    vat_pct: float,
    margin_pct: float,
):
    """Calcola il prezzo minimo ivato che soddisfi margine/fee/costi.

    Formula:
        P = k * (C + S + (1 + d)*F) / (1 - k*((1 + d)*R + M))

    dove:
        k = 1 + IVA
        C = costo acquisto
        S = costo spedizione
        F = fee di chiusura
        R = referral fee
        d = DST
        M = margine target
    """
    cost = pd.to_numeric(row.get("Prezzo medio acquisto (€)"), errors="coerce")
    if not np.isfinite(cost) or cost <= 0:
        return None

    r = referral_pct / 100.0
    d = dst_pct / 100.0
    m = margin_pct / 100.0
    v = vat_pct / 100.0

    ship_cost = float(ship_cost or 0.0)
    closing_fee = float(closing_fee or 0.0)

    k = 1 + v
    numerator = k * (cost + ship_cost + (1 + d) * closing_fee)
    denom = 1 - k * ((1 + d) * r + m)

    if denom <= 0:
        return None

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
        "sku": df.get(sku_col, df.get("SKU")),
        "minimum-seller-allowed-price": df.get("minimum-seller-allowed-price", df.get("Prezzo minimo suggerito (€)")),
        "maximum-seller-allowed-price": df.get("maximum-seller-allowed-price", ""),
        "country-code": df.get("country-code", "IT"),
        "currency-code": df.get("currency-code", "EUR"),
        "rule-name": df.get("rule-name", "Rule1"),
        "rule-action": df.get("rule-action", "START"),  # default in UPPERCASE
        "business-rule-name": df.get("business-rule-name", ""),
        "business-rule-action": df.get("business-rule-action", ""),
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
        df.to_excel(writer, index=False, header=False, sheet_name="Modello assegnazione prezzo")
    output.seek(0)
    return output


def highlight_below(row):
    min_val = pd.to_numeric(row.get("Prezzo minimo suggerito (€)"), errors="coerce")
    price_val = pd.to_numeric(row.get("Prezzo"), errors="coerce")
    if np.isfinite(min_val) and np.isfinite(price_val) and min_val > price_val:
        return ["background-color: lightcoral"] * len(row)
    return [""] * len(row)

# ---------------------------------------------------------
# Caricamento dati
# ---------------------------------------------------------
inventory_df = load_excel(inv_file)
purchase_df = load_excel(price_file)

# rinomina colonne chiave se presenti
rename_map = {"sku": "SKU", "current-selling-price": "Prezzo"}
if inventory_df is not None:
    inventory_df = inventory_df.rename(columns=rename_map)
if purchase_df is not None:
    purchase_df = purchase_df.rename(columns=rename_map)

# Early-return UI se mancano file
if inventory_df is None or purchase_df is None:
    st.info("👈 Carica entrambi i file nella barra laterale per continuare.")
    st.stop()

# ---------------------------------------------------------
# Preparazione e merge
# ---------------------------------------------------------
merged_df, inv_key = get_merged_inventory(inventory_df, purchase_df, parse_option)

# Parametri costi e margini
st.subheader("Parametri costi e margini")

cats = list(CATEGORY_MAP.keys())
selected_cat = st.selectbox("Categoria", cats)

defaults = CATEGORY_MAP.get(selected_cat, CATEGORY_MAP["_default"])
referral_fee_pct = st.number_input("% Commissione Amazon", value=defaults["referral"], min_value=0.0)
shipping_cost = st.number_input("Costo spedizione €", value=0.0, min_value=0.0)
vat_pct = st.number_input("IVA %", value=22.0, min_value=0.0, step=0.1)
margin_pct = st.number_input("Margine desiderato %", value=20.0, min_value=0.0)

show_only_matches = st.checkbox("Mostra solo articoli presenti nel file acquisti", value=False)

# verifica parametri di costo/margine
denom_check = 1 - (1 + vat_pct / 100) * (((1 + DST_PCT / 100) * (referral_fee_pct / 100)) + (margin_pct / 100))
if denom_check <= 0:
    st.warning("Parametri non validi: commissioni + margine troppo alti rispetto al prezzo.")

closing_fee = CATEGORY_MAP.get(selected_cat, CATEGORY_MAP["_default"]) ["closing"]

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
    st.warning("Parametri non validi: commissioni + margine troppo alti rispetto al prezzo.")

# Applica filtro opzionale per mostrare solo gli articoli presenti nel file acquisti
display_df = merged_df
if show_only_matches:
    display_df = merged_df[merged_df["Prezzo medio acquisto (€)"].notna()]

# ---------------------------------------------------------
# Non Automated SKUs – integrazione da export Amazon
# ---------------------------------------------------------
if export_file is not None:
    st.subheader("Non Automated SKUs – da export Amazon")

    try:
        export_df = load_amazon_template(export_file)
        if "SKU" not in export_df.columns:
            st.warning("Il flat-file export non contiene la colonna 'SKU'.")
            export_df = None
    except Exception as e:
        st.error(f"Errore nel parsing del flat-file export: {e}")
        export_df = None

    if export_df is not None:
        export_df["_SKU_KEY_"] = normalize_sku(export_df["SKU"], parse_option)

        # Merge con costi dall'inventario unificato
        cols_to_pull = ["_SKU_KEY_", "Prezzo medio acquisto (€)", "Categoria"]
        cols_to_pull = [c for c in cols_to_pull if c in merged_df.columns]
        ff_df = export_df.merge(merged_df[cols_to_pull], on="_SKU_KEY_", how="left", suffixes=("", "_inv"))

        # Calcolo del Prezzo minimo suggerito (€) per le righe del flat-file export
        def _min_price_row(row):
            return calc_min_price(
                row=row,
                referral_pct=referral_fee_pct,
                closing_fee=closing_fee,
                dst_pct=DST_PCT,
                ship_cost=shipping_cost,
                vat_pct=vat_pct,
                margin_pct=margin_pct,
            )

        ff_df["Prezzo minimo suggerito (€)"] = ff_df.apply(_min_price_row, axis=1)

        only_missing_min = st.checkbox("Mostra solo righe senza 'minimum-seller-allowed-price' nel file", value=True)
        overwrite_min = st.checkbox("Sovrascrivi il valore esistente di 'minimum-seller-allowed-price' (se presente)", value=False)

        default_rule_base = st.text_input("Rule name di default", value="AUTO")
        today_str = pd.Timestamp.today().strftime("%Y%m%d")

        if "rule-name" not in ff_df.columns:
            ff_df["rule-name"] = ""
        if "country-code" not in ff_df.columns:
            ff_df["country-code"] = "IT"
        if "currency-code" not in ff_df.columns:
            ff_df["currency-code"] = "EUR"

        ff_df["rule-action"] = ff_df.get("rule-action", "START")
        ff_df["rule-action"] = ff_df["rule-action"].astype(str).str.upper().replace({"": "START"})

        def _mk_rule_name(row):
            rn = str(row.get("rule-name") or "").strip()
            if rn:
                return rn
            cc = str(row.get("country-code") or "IT").upper()
            return f"{default_rule_base}-{cc}-{today_str}"

        ff_df["rule-name"] = ff_df.apply(_mk_rule_name, axis=1)

        if "minimum-seller-allowed-price" in ff_df.columns and only_missing_min:
            ff_df_view = ff_df[ff_df["minimum-seller-allowed-price"].isna()].copy()
        else:
            ff_df_view = ff_df.copy()

        if overwrite_min or "minimum-seller-allowed-price" not in ff_df_view.columns:
            ff_df_view["minimum-seller-allowed-price"] = ff_df_view["Prezzo minimo suggerito (€)"]
        else:
            ff_df_view["minimum-seller-allowed-price"] = ff_df_view["minimum-seller-allowed-price"].where(
                ff_df_view["minimum-seller-allowed-price"].notna(), ff_df_view["Prezzo minimo suggerito (€)"]
            )

        ff_out = ff_df_view.dropna(subset=["SKU", "minimum-seller-allowed-price"])  # componi dataset esportabile

        st.dataframe(
            ff_out[[
                "SKU",
                "country-code",
                "currency-code",
                "rule-name",
                "rule-action",
                "minimum-seller-allowed-price",
                "Prezzo medio acquisto (€)",
                "Prezzo minimo suggerito (€)",
            ]].head(100),
            use_container_width=True,
            hide_index=True,
        )

        st.download_button(
            "💾 Scarica Flat-File (compilato da export)",
            data=make_flatfile_bytes(build_flatfile(ff_out, "SKU")),
            file_name="AutomatePricing_FromExport.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

# ---------------------------------------------------------
# Dashboard dei risultati
# ---------------------------------------------------------
st.subheader("Anteprima dataset unificato")

edited_df = st.data_editor(
    display_df,
    key="merged_df_editor",
    use_container_width=True,
    hide_index=True,
    column_config={inv_key: st.column_config.TextColumn(disabled=False)},
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

# KPI sintetici
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("SKU totali", len(edited_df))
with col2:
    st.metric(
        "Prezzo medio acquisto",
        f"{pd.to_numeric(edited_df['Prezzo medio acquisto (€)'], errors='coerce').mean():.2f} €",
    )
with col3:
    if "Quantita'" in edited_df.columns:
        valore = (
            pd.to_numeric(edited_df["Prezzo"], errors="coerce").fillna(0)
            * pd.to_numeric(edited_df["Quantita'"], errors="coerce").fillna(0)
        ).sum()
        st.metric("Valore inventario (listino)", f"{valore:.2f} €")
    else:
        st.metric("Valore inventario (listino)", "—")

# ---------------------------------------------------------
# Download Excel unificato + Flat-file (min price)
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
    "💾 Scarica Flat-File (min price)",
    data=make_flatfile_bytes(build_flatfile(edited_df, inv_key)),
    file_name="AutomatePricing_MinOnly.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

# ---------------------------------------------------------
# Credits
# ---------------------------------------------------------
st.caption("Made with Streamlit · Patch header-detection flat-file export")
