# inventory_price_parser_app.py
# ---------------------------------------------------------
# Streamlit web-app per caricare un file inventario (ITALIA.xlsx
# o inventario.txt) e un file prezzi d'acquisto (PREZZI ACQUISTO.xlsx
# o acquisti.txt) e abbinarli
# tramite SKU. Il prezzo di riferimento usato è "Prezzo medio".
# Esecuzione locale:  
#   streamlit run inventory_price_parser_app.py
# ---------------------------------------------------------

import io
import re

import openpyxl
import pandas as pd
import numpy as np
import streamlit as st

st.set_page_config(page_title="Matcher Inventario ↔ Prezzi d'acquisto", layout="wide")

# ---------------------------------------------------------
# Costanti e mappe di supporto
# ---------------------------------------------------------
CATEGORY_MAP = {
    "Videogiochi": {"referral": 8.14, "closing": 0.00},
    "Elettronica": {"referral": 7.21, "closing": 0.00},
    "Informatica": {"referral": 7.21, "closing": 0.00},
    "Cuffie e audio": {"referral": 7.21, "closing": 0.00},
    "Console e accessori": {"referral": 8.14, "closing": 0.00},
    "Accessori gaming": {"referral": 8.14, "closing": 0.00},
    "Film e TV": {"referral": 15.0, "closing": 0.81},
    "Musica": {"referral": 15.0, "closing": 0.81},
    "Libri": {"referral": 15.0, "closing": 0.81},
    "Videogiochi (media fisico)": {"referral": 15.0, "closing": 0.81},
    "Video e DVD": {"referral": 15.0, "closing": 0.81},
    "Software": {"referral": 15.0, "closing": 0.81},
    "_default": {"referral": 15.0, "closing": 0.00},
}
DST_PCT = 3.0  # Italia
COUNTRY_CODES = {
    "AE",
    "AU",
    "BE",
    "BR",
    "CA",
    "CN",
    "DE",
    "ES",
    "FR",
    "GB",
    "IT",
    "JP",
    "MX",
    "NL",
    "PL",
    "SE",
    "SG",
    "TR",
    "US",
    "IN",
    "IE",
}


# ---------------------------------------------------------
# Utility UI
# ---------------------------------------------------------
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

    export_file = st.file_uploader(
        "📥 Flat-file Amazon (export) • Non Automated SKUs",
        type=["xlsx", "xls", "csv", "tsv"]
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
            return pd.read_csv(
                uploaded_file,
                sep="\t",
                encoding="utf-8",
                decimal=",",
            )
        except UnicodeDecodeError:
            uploaded_file.seek(0)
            # alcuni file .txt esportati da Excel non sono UTF-8
            return pd.read_csv(
                uploaded_file,
                sep="\t",
                encoding="latin-1",
                decimal=",",
            )

    return pd.read_excel(uploaded_file)


def to_kebab(s: str) -> str:
    s = (s or "").strip()
    s = s.replace(" ", " ")  # NBSP
    s = re.sub(r"\s+", " ", s)
    s = s.lower()
    s = s.replace(" – ", " ").replace(" — ", " ").replace("–", " ").replace("—", " ")
    s = s.replace("’", "'")
    s = s.replace(" ", "-")
    s = re.sub(r"[^a-z0-9\-]", "", s)
    s = re.sub(r"-{2,}", "-", s)
    return s


def normalize_sku(series: pd.Series, ignore_suffix: bool) -> pd.Series:
    """Se ignore_suffix=True, elimina tutto dopo l'ultimo '-'."""
    if not ignore_suffix:
        return series.astype(str).str.strip()
    s = series.astype(str).str.strip()
    # es: ABC-1-2 → ABC-1
    return s.str.replace(r"-[^-]+$", "", regex=True)


def header_row_index(values_2d, probe="sku", max_rows=20):
    for i in range(min(max_rows, len(values_2d))):
        row = [str(x or "").strip().lower() for x in values_2d[i][:20]]
        if row and row[0] == probe:
            return i
    return None


def load_amazon_template(uploaded_file):
    """Carica un file modello prezzi Amazon e restituisce un DataFrame pulito.

    - Individua il primo foglio disponibile tra "Modello assegnazione prezzo"
      ed "Esempio".
    - Usa la seconda riga come intestazioni (convertite in *kebab-case*).
    - Scarta le prime due righe del foglio.
    - Rimuove le righe vuote o con SKU mancante e normalizza le stringhe.

    Parameters
    ----------
    uploaded_file: file-like
        File caricato tramite Streamlit ``file_uploader``.

    Returns
    -------
    pd.DataFrame
        DataFrame con colonne in kebab-case (es. `sku`, `country-code`, ...)
    """
    if uploaded_file is None:
        return None

    uploaded_file.seek(0)
    wb = openpyxl.load_workbook(uploaded_file, read_only=True, data_only=True)
    sheet_name = next((n for n in ["Modello assegnazione prezzo", "Esempio"] if n in wb.sheetnames), None)
    if sheet_name is None:
        raise ValueError("Foglio 'Modello assegnazione prezzo' o 'Esempio' non trovato")

    ws = wb[sheet_name]
    data = list(ws.values)
    if len(data) < 2:
        return pd.DataFrame()

    # Usa la RIGA 2 (indice 1) come header descrittivo, la RIGA 3 (indice 2) come header machine (quando presente)
    # In export Amazon spesso la riga 3 contiene i field names (sku, country-code, ...)
    idx = header_row_index(data, probe="sku", max_rows=15)
    if idx is None:
        # fallback: usa riga 2 come intestazione
        header = [to_kebab(x) for x in (data[1] or [])]
        df = pd.DataFrame(data[2:], columns=header)
    else:
        header = [str(x or "").strip() for x in (data[idx] or [])]
        # Normalizza header
        header = [to_kebab(h) for h in header]
        df = pd.DataFrame(data[idx + 1 :], columns=header)

    # Drop righe completamente vuote e SKU mancanti
    if "sku" not in df.columns:
        # prova alias
        rename_map = {"seller-sku": "sku", "sku-seller": "sku"}
        df = df.rename(columns=rename_map)
    if "sku" not in df.columns:
        return pd.DataFrame()

    # Normalizza stringhe base
    for col in df.columns:
        if df[col].dtype == "object":
            df[col] = df[col].astype(str).str.strip().replace({"nan": "", "None": ""})

    # Rinomina campi noti (qualora export localizzato)
    rename_map = {
        "codice-paese": "country-code",
        "valuta": "currency-code",
        "prezzo-di-vendita-corrente": "current-selling-price",
        "prezzo-minimo-consentito-al-venditore": "minimum-seller-allowed-price",
        "prezzo-massimo-consentito-al-venditore": "maximum-seller-allowed-price",
        "nome-regola": "rule-name",
        "azione-regola": "rule-action",
        "nome-regola-aziendale": "business-rule-name",
        "azione-regola-aziendale": "business-rule-action",
        "canale-di-evasione": "fulfillment-channel",
        "condizione": "condition",
        "classifica-vendite": "sales-rank",
    }
    df = df.rename(columns=rename_map)

    # conversioni numeriche e validazioni
    price_cols = [c for c in df.columns if re.search(r"price|points", c)]
    for col in price_cols:
        df[col] = df[col].astype(str).str.replace(",", ".")
        df[col] = pd.to_numeric(df[col], errors="coerce")
        df.loc[df[col] < 0, col] = np.nan

    if "sales-rank" in df.columns:
        df["sales-rank"] = df["sales-rank"].astype(str).str.replace(",", ".")
        df["sales-rank"] = pd.to_numeric(df["sales-rank"], errors="coerce").astype("Int64")
        df.loc[df["sales-rank"] < 0, "sales-rank"] = pd.NA

    if "customer-views-share" in df.columns:
        df["customer-views-share"] = (
            df["customer-views-share"].astype(str).str.replace(",", ".").str.replace("%", "")
        )
        df["customer-views-share"] = pd.to_numeric(df["customer-views-share"], errors="coerce")

    # Assicura country-code/currency-code plausibili
    if "country-code" in df.columns:
        df["country-code"] = df["country-code"].str.upper()
        df.loc[~df["country-code"].isin(COUNTRY_CODES), "country-code"] = pd.NA

    return df


def get_merged_inventory(
    inventory_df: pd.DataFrame,
    purchase_df: pd.DataFrame,
    parse_suffix: bool,
) -> tuple[pd.DataFrame, str]:
    """Esegue il merge mantenendo la colonna Categoria se presente.

    Restituisce anche il nome della colonna usata come SKU originale
    nell'inventario.
    """
    inv_key_candidates = [
        c for c in inventory_df.columns if c.upper() in {"SKU", "CODICE(ASIN)", "CODICE", "ASIN"}
    ]
    price_key_candidates = [
        c for c in purchase_df.columns if c.upper() in {"CODICE", "SKU", "CODICE(ASIN)"}
    ]

    if not inv_key_candidates:
        st.error("Nel file inventario non è stata trovata una colonna SKU/Codice/ASIN.")
        st.stop()
    if not price_key_candidates:
        st.error("Nel file acquisti non è stata trovata una colonna Codice/SKU.")
        st.stop()

    inv_key = inv_key_candidates[0]
    price_key = price_key_candidates[0]

    inventory_df = inventory_df.copy()
    purchase_df = purchase_df.copy()

    inventory_df["_SKU_KEY_"] = normalize_sku(inventory_df[inv_key], parse_suffix)
    purchase_df["_SKU_KEY_"] = normalize_sku(purchase_df[price_key], parse_suffix)

    # Individua la colonna prezzo d'acquisto più adatta
    price_candidates = [
        c
        for c in purchase_df.columns
        if str(c).strip().lower() in {"prezzo medio", "prezzo-medio", "prezzo_medio", "prezzo"}
    ]
    if not price_candidates:
        st.error("Nel file acquisti non è stata trovata una colonna 'Prezzo medio' o 'Prezzo'.")
        st.stop()
    price_col = price_candidates[0]

    # Tieni la Categoria se presente, più eventuali colonne utili del flat-file
    cat_cols = [c for c in purchase_df.columns if str(c).strip().lower() == "categoria"]
    optional_cols = [
        c
        for c in purchase_df.columns
        if str(c).strip().lower()
        in {
            "quantita'",
            "quantità",
            "quantita",
            "current-selling-price",
            "buybox-landed-price",
            "lowest-landed-price",
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
            k = (1 + IVA)
            d = DST (Digital Services Tax)
            r = referral fee %
            C = closing fee
            S = shipping cost
            CoG = purchase cost (Prezzo medio acquisto)
            m = target margin %
    """
    cost = row.get("Prezzo medio acquisto (€)")
    try:
        cost = float(cost)
    except Exception:
        return None
    if cost is None or cost <= 0:
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
        "Country",
        "Currency",
        "Rule name",
        "Action",
        "Business rule name",
        "Business rule action",
    ]

    # Allinea colonne e default
    df = df.copy()
    if sku_col not in df.columns:
        # prova alias
        if "SKU" in df.columns:
            sku_col = "SKU"
        else:
            raise ValueError("Colonna SKU non trovata per il flat-file.")

    # Normalizza nomi colonne attese
    rename_map = {
        sku_col: "sku",
        "SKU": "sku",
        "Country": "country-code",
        "Currency": "currency-code",
        "Rule name": "rule-name",
        "Action": "rule-action",
        "Business rule name": "business-rule-name",
        "Business rule action": "business-rule-action",
    }
    df = df.rename(columns=rename_map)

    data = {
        "sku": df["sku"].astype(str),
        "minimum-seller-allowed-price": df.get("minimum-seller-allowed-price", ""),
        "maximum-seller-allowed-price": df.get("maximum-seller-allowed-price", ""),
        "country-code": df.get("country-code", "IT"),
        "currency-code": df.get("currency-code", "EUR"),
        "rule-name": df.get("rule-name", "Rule1"),
        "rule-action": df.get("rule-action", "START"),
        "business-rule-name": df.get("business-rule-name", ""),
        "business-rule-action": df.get("business-rule-action", ""),
    }

    df_data = pd.DataFrame(data)
    df_full = pd.concat(
        [pd.DataFrame([header_desc], columns=field_names), pd.DataFrame([field_names], columns=field_names), df_data],
        ignore_index=True,
    )
    return df_full


def make_flatfile_bytes(df_full: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_full.to_excel(writer, index=False, header=False, sheet_name="Modello assegnazione prezzo")
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

# Parse del flat-file export (se caricato)
export_df = None
if "export_file" in locals() and export_file is not None:
    try:
        export_df = load_amazon_template(export_file)  # riuso parser modello Amazon
        # Normalizza chiave SKU per l’allineamento con l’inventario
        if "SKU" in export_df.columns:
            export_df["_SKU_KEY_"] = normalize_sku(export_df["SKU"], parse_option)
        else:
            st.warning("Il flat-file export non contiene la colonna 'SKU'.")
            export_df = None
    except Exception as e:
        st.error(f"Errore nel parsing del flat-file export: {e}")
        export_df = None

# Early-return UI se mancano file
if inventory_df is None or purchase_df is None:
    st.info("Carica i due file (inventario + prezzi d'acquisto) dalla sidebar.")
    st.stop()

# ---------------------------------------------------------
# Preparazione e merge
# ---------------------------------------------------------
merged_df, inv_key = get_merged_inventory(inventory_df, purchase_df, parse_option)

with st.sidebar:
    st.subheader("⚙️ Parametri commissioni")

    cats = [c for c in CATEGORY_MAP.keys() if c != "_default"]
    selected_cat = st.selectbox("Categoria", cats)
    # il selettore serve solo per preimpostare le commissioni

    defaults = CATEGORY_MAP.get(selected_cat, CATEGORY_MAP["_default"])
    referral_fee_pct = st.number_input(
        "% Commissione Amazon", value=defaults["referral"], min_value=0.0
    )
    shipping_cost = st.number_input("Costo spedizione €", value=0.0, min_value=0.0)
    vat_pct = st.number_input("IVA %", value=22.0, min_value=0.0, step=0.1)
    margin_pct = st.number_input("Margine desiderato %", value=20.0, min_value=0.0)

    show_only_matches = st.checkbox(
        "Mostra solo articoli presenti nel file acquisti", value=False
    )

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

# Applica filtro opzionale
display_df = merged_df.copy()
if show_only_matches:
    display_df = display_df[display_df["Prezzo medio acquisto (€)"].notna()]

# ---------------------------------------------------------
# Non Automated SKUs – builder da export Amazon
# ---------------------------------------------------------
ff_df = None
if "export_df" in locals() and export_df is not None:
    # Merge dell’export con i costi calcolati sul merge principale
    cols_to_pull = ["_SKU_KEY_", "Prezzo medio acquisto (€)", "Categoria"]
    cols_to_pull = [c for c in cols_to_pull if c in merged_df.columns]
    ff_df = export_df.merge(
        merged_df[cols_to_pull],
        on="_SKU_KEY_",
        how="left",
        suffixes=("", "_inv")
    )

    # Calcolo del "Prezzo minimo suggerito (€)" riga-per-riga con le tue regole
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

    st.subheader("Non Automated SKUs – da export Amazon")

    # Opzioni pratiche
    only_missing_min = st.checkbox("Mostra solo righe senza 'minimum-seller-allowed-price' nel file", value=True)
    overwrite_min = st.checkbox("Sovrascrivi il valore esistente di 'minimum-seller-allowed-price' (se presente)", value=False)

    # Proposta di rule-name bulk, se mancante
    default_rule_base = st.text_input("Rule name di default", value="AUTO")
    today_str = pd.Timestamp.today().strftime("%Y%m%d")

    if "rule-name" not in ff_df.columns:
        ff_df["rule-name"] = ""

    # country-code fallback (coerente con i template EU)
    if "country-code" not in ff_df.columns:
        ff_df["country-code"] = "IT"

    # currency-code fallback
    if "currency-code" not in ff_df.columns:
        ff_df["currency-code"] = "EUR"

    # rule-action → uppercase START se mancante
    ff_df["rule-action"] = ff_df.get("rule-action", "START")
    ff_df["rule-action"] = ff_df["rule-action"].astype(str).str.upper().replace({"": "START"})

    # Imposto rule-name dove vuoto: AUTO-<CC>-<YYYYMMDD>
    def _mk_rule_name(row):
        rn = str(row.get("rule-name") or "").strip()
        if rn:
            return rn
        cc = str(row.get("country-code") or "IT").upper()
        return f"{default_rule_base}-{cc}-{today_str}"
    ff_df["rule-name"] = ff_df.apply(_mk_rule_name, axis=1)

    # Applico il filtro "solo senza min"
    if "minimum-seller-allowed-price" in ff_df.columns and only_missing_min:
        ff_df_view = ff_df[ff_df["minimum-seller-allowed-price"].isna()].copy()
    else:
        ff_df_view = ff_df.copy()

    # Compilazione del minimo da esportare
    if overwrite_min or "minimum-seller-allowed-price" not in ff_df_view.columns:
        ff_df_view["minimum-seller-allowed-price"] = ff_df_view["Prezzo minimo suggerito (€)"]
    else:
        ff_df_view["minimum-seller-allowed-price"] = ff_df_view["minimum-seller-allowed-price"].where(
            ff_df_view["minimum-seller-allowed-price"].notna(),
            ff_df_view["Prezzo minimo suggerito (€)"]
        )

    # Righe esportabili: devono avere SKU e un minimo calcolato
    ff_out = ff_df_view.dropna(subset=["SKU", "minimum-seller-allowed-price"])

    # Preview veloce
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

    # Download del flat-file identico al template (riuso builder esistente)
    st.download_button(
        "💾 Scarica Flat-File (compilato da export)",
        data=make_flatfile_bytes(build_flatfile(ff_out, "SKU"))),
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
    column_config={
        inv_key: st.column_config.TextColumn(disabled=False)
    },
)

edited_df["_SKU_KEY_"] = normalize_sku(edited_df[inv_key], parse_option)

st.markdown("**Righe evidenziate**: prezzo attuale sotto al minimo suggerito.")
st.dataframe(
    edited_df.style.apply(highlight_below, axis=1),
    use_container_width=True,
    hide_index=True,
)

# ---------------------------------------------------------
# KPI rapidi
# ---------------------------------------------------------
k1, k2, k3 = st.columns(3)
with k1:
    st.metric("SKU totali", f"{len(edited_df):,}".replace(",", "."))
with k2:
    mean_cost = pd.to_numeric(edited_df["Prezzo medio acquisto (€)"], errors="coerce").mean()
    st.metric("Prezzo medio acquisto (€)", f"{mean_cost:,.2f}".replace(",", "."))
with k3:
    if "Prezzo" in edited_df.columns and "Quantita'" in edited_df.columns:
        val = (
            pd.to_numeric(edited_df["Prezzo"], errors="coerce")
            * pd.to_numeric(edited_df["Quantita'"], errors="coerce")
        ).sum()
        st.metric("Valore inventario (€)", f"{val:,.2f}".replace(",", "."))
    else:
        st.metric("Valore inventario (€)", "—")

# ---------------------------------------------------------
# Download
# ---------------------------------------------------------
st.download_button(
    "💾 Scarica Excel unificato",
    data=lambda: io.BytesIO(
        edited_df.to_excel(index=False, sheet_name="Match inventario", engine="openpyxl")
    ),
    file_name="inventario_match.xlsx" if inv_key.lower() == "sku" else "inventario_match.xlsx",
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
st.caption("Made with Streamlit · Ultimo aggiornamento: 13 ago 2025")
