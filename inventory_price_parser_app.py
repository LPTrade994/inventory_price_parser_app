# inventory_price_parser_app.py
# ---------------------------------------------------------
# Inventory & Price Parser
# - Merge inventario + acquisti, calcola "Prezzo minimo suggerito (€)"
# - Legge export Automate Pricing (flat-file) e prepara il file da reimportare
# - Mantiene i country-code dell’export riga per riga (niente forzature a IT)
# - Compila currency-code solo se mancante (Paese→Valuta)
# - Rule-name identico per tutti gli SKU (nessun suffisso)
# ---------------------------------------------------------

import io, re
import openpyxl
import numpy as np
import pandas as pd
import streamlit as st

# ---------------------------- Costanti ----------------------------
CATEGORY_MAP = {
    "Videogiochi - Giochi e accessori": {"referral": 15.0, "closing": 0.81},
    "Videogiochi - Console":            {"referral": 8.0,  "closing": 0.81},
    "Libri":                            {"referral": 15.0, "closing": 1.01},
    "Musica":                           {"referral": 15.0, "closing": 0.81},
    "Video e DVD":                      {"referral": 15.0, "closing": 0.81},
    "Software":                         {"referral": 15.0, "closing": 0.81},
    "_default":                         {"referral": 15.0, "closing": 0.00},
}
DST_PCT = 3.0  # Italia

# mappa Paese → Valuta (usata SOLO per riempire i vuoti; mai per sovrascrivere)
CURRENCY_BY_COUNTRY = {
    "IT":"EUR","DE":"EUR","FR":"EUR","ES":"EUR","NL":"EUR","BE":"EUR","IE":"EUR",
    "PL":"PLN","SE":"SEK","GB":"GBP","UK":"GBP",
    "US":"USD","CA":"CAD","MX":"MXN","JP":"JPY","CN":"CNY",
    "AE":"AED","AU":"AUD","BR":"BRL","TR":"TRY",
}

# ---------------------------- UI base ----------------------------
st.set_page_config(page_title="Inventory & Price Parser", layout="wide")
st.title("📦 Inventory & Price Parser")

with st.sidebar:
    st.header("⚙️ Opzioni")
    parse_suffix = st.checkbox("Ignora suffisso dopo l'ultimo '-' nello SKU", value=True)

    inv_file = st.file_uploader("📥 Inventario (xlsx/xls/txt)", type=["xlsx","xls","txt"], key="inv")
    price_file = st.file_uploader("📥 Prezzi d'acquisto (xlsx/xls/txt)", type=["xlsx","xls","txt"], key="buy")
    export_file = st.file_uploader("📥 Export Automate Pricing (xlsx/csv/tsv)", type=["xlsx","xls","csv","tsv"], key="exp")

    download_name = st.text_input("Nome file Excel da scaricare", value="inventario_con_prezzi.xlsx")

# ------------------------- Utilità I/O -------------------------
@st.cache_data(show_spinner=False)
def load_excel(uploaded_file):
    if uploaded_file is None:
        return None
    name = uploaded_file.name.lower()
    if name.endswith(".txt"):
        uploaded_file.seek(0)
        content = uploaded_file.read().decode("latin-1", errors="ignore")
        # prova TSV poi CSV
        try:
            return pd.read_csv(io.StringIO(content), sep="\t", decimal=",")
        except Exception:
            return pd.read_csv(io.StringIO(content), sep=";", decimal=",")
    try:
        return pd.read_excel(uploaded_file, engine="openpyxl")
    except Exception:
        uploaded_file.seek(0)
        return pd.read_excel(uploaded_file)

def _to_kebab(s: str) -> str:
    s = "" if s is None else str(s)
    s = s.strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    return s

def load_amazon_export(uploaded_file):
    """Parser robusto dell'export Automate Pricing (doppio header variabile, CSV/XLSX/TSV)."""
    name = uploaded_file.name.lower()

    def _build_df(matrix):
        header_idx, header_row = None, None
        for i in range(min(30, len(matrix))):
            row_vals = ["" if v is None else str(v) for v in matrix[i]]
            keb = [_to_kebab(v) for v in row_vals]
            if "sku" in keb or "seller-sku" in keb:
                header_idx, header_row = i, keb
                break
        if header_idx is None:
            header_idx = 1 if len(matrix) > 1 else 0
            header_row = [_to_kebab(v) for v in (matrix[header_idx] if matrix else [])]
        data_rows = [list(r) for r in matrix[header_idx+1:]]
        df = pd.DataFrame(data_rows, columns=header_row)
        for col in df.select_dtypes(include="object").columns:
            df[col] = df[col].astype(str).str.strip()
        return df.rename(columns={"sku":"SKU","seller-sku":"SKU","current-selling-price":"Prezzo"})

    if name.endswith((".xlsx",".xls")):
        uploaded_file.seek(0)
        wb = openpyxl.load_workbook(uploaded_file, read_only=True, data_only=True)
        sheet_name = next((n for n in [
            "Modello assegnazione prezzo","Modello di assegnazione prezzo",
            "Modello di assegnazione del prezzo","Esempio",
        ] if n in wb.sheetnames), wb.sheetnames[0])
        ws = wb[sheet_name]
        df = _build_df(list(ws.values))
    else:
        uploaded_file.seek(0)
        content = uploaded_file.read().decode("utf-8", errors="ignore")
        frames = []
        for sep in [",","\t",";","|"]:
            try: frames.append(pd.read_csv(io.StringIO(content), sep=sep, header=None, dtype=str))
            except Exception: pass
        if not frames:
            raise ValueError("Impossibile leggere il flat-file export (CSV/TSV).")
        df_raw = max(frames, key=lambda d: d.shape[1])
        df = _build_df(df_raw.fillna("").values.tolist())

    # numeriche comuni
    for c in ["minimum-seller-allowed-price","maximum-seller-allowed-price","current-selling-price","buybox-landed-price","lowest-landed-price","sales-rank"]:
        if c in df.columns:
            df[c] = pd.to_numeric(pd.Series(df[c]).astype(str).str.replace(",", "."), errors="coerce")

    # normalizza etichette
    if "rule-action" in df.columns:
        df["rule-action"] = df["rule-action"].astype(str).str.upper().replace({"":"START","nan":"START"})
    if "country-code" in df.columns:
        df["country-code"] = df["country-code"].astype(str).str.strip().str.upper()
    if "currency-code" in df.columns:
        df["currency-code"] = df["currency-code"].astype(str).str.strip().str.upper()

    if "SKU" in df.columns:
        df = df[df["SKU"].astype(str).str.strip()!=""]

    return df.reset_index(drop=True)

def ensure_country_currency(df: pd.DataFrame) -> pd.DataFrame:
    """NON tocca i country-code esistenti; riempie currency-code se vuota."""
    if "country-code" not in df.columns:
        df["country-code"] = pd.Series([None]*len(df))
    else:
        df["country-code"] = df["country-code"].astype(str).str.strip().str.upper().replace({"NAN":""})

    if "currency-code" not in df.columns:
        df["currency-code"] = df["country-code"].map(CURRENCY_BY_COUNTRY)
    else:
        df["currency-code"] = df["currency-code"].astype(str).str.strip().str.upper().replace({"NAN":""})
        mask = (df["currency-code"]=="") | df["currency-code"].isna()
        df.loc[mask, "currency-code"] = df.loc[mask,"country-code"].map(CURRENCY_BY_COUNTRY)

    return df

def normalize_sku(s: pd.Series, parse_suffix: bool) -> pd.Series:
    s = s.astype(str).str.strip()
    return s.str.rsplit("-", n=1).str[0] if parse_suffix else s

def get_merged_inventory(inventory_df: pd.DataFrame, purchase_df: pd.DataFrame, parse_suffix: bool):
    inv_key_candidates   = [c for c in inventory_df.columns if c.upper() in {"SKU","CODICE(ASIN)","CODICE","ASIN"}]
    price_key_candidates = [c for c in purchase_df.columns  if c.upper() in {"CODICE","SKU","CODICE(ASIN)"}]
    if not inv_key_candidates or not price_key_candidates:
        st.error("Servono colonne SKU/CODICE(ASIN)/CODICE/ASIN in entrambi i file."); st.stop()
    inv_key   = st.selectbox("Colonna SKU nell'inventario", inv_key_candidates, index=0)
    price_key = st.selectbox("Colonna SKU nel file acquisti", price_key_candidates, index=0)
    inventory_df["_SKU_KEY_"] = normalize_sku(inventory_df[inv_key], parse_suffix)
    purchase_df["_SKU_KEY_"]  = normalize_sku(purchase_df[price_key], parse_suffix)

    candidate_price_cols = [c for c in purchase_df.columns if ("prezzo" in c.lower() and "medio" in c.lower())
                            or c.lower().strip() in {"prezzo","prezzo medio","prezzo medio (€)"}]
    if not candidate_price_cols:
        st.error("Colonna del prezzo d'acquisto non trovata (es. 'Prezzo medio')."); st.stop()
    price_col = candidate_price_cols[0]

    cat_cols = [c for c in purchase_df.columns if c.lower().strip()=="categoria"]
    optional_cols = [c for c in purchase_df.columns if c.lower().strip() in {
        "quantita'","quantità","prezzo","prezzo medio","codice(asin)","asin","sku",
        "country-code","currency-code","rule-name","rule-action","business-rule-name","business-rule-action",
    }]
    subset_cols = ["_SKU_KEY_", price_col] + optional_cols
    if cat_cols:
        purchase_df = purchase_df.rename(columns={cat_cols[0]:"Categoria"})
        subset_cols.append("Categoria")

    merged = inventory_df.merge(purchase_df[subset_cols], on="_SKU_KEY_", how="left", suffixes=("", "_acquisto"))
    merged = merged.rename(columns={price_col: "Prezzo medio acquisto (€)"})
    return merged, inv_key

# --------------------- Calcolo prezzo minimo (LEGACY) -----------------------
def calc_min_price(row: pd.Series, referral_pct: float, closing_fee: float, dst_pct: float,
                   ship_cost: float, vat_pct: float, margin_pct: float):
    """
    Formula legacy (parità con Revenue Calculator):
        P = k*(CoG + S + (1+d)*C) / ( 1 - k*((1+d)*r + m) )
    """
    cost = pd.to_numeric(row.get("Prezzo medio acquisto (€)"), errors="coerce")
    if not np.isfinite(cost) or cost <= 0:
        return None
    r = referral_pct/100.0
    d = dst_pct/100.0
    v = vat_pct/100.0
    m = margin_pct/100.0
    k = 1.0 + v
    num   = k * (cost + ship_cost + (1.0 + d) * (closing_fee or 0.0))
    denom = 1.0 - k * ((1.0 + d) * r + m)
    if denom <= 1e-12: return None
    P = num / denom
    return round(P, 2) if np.isfinite(P) and P > 0 else None

# ---------------------- Export template Amazon ---------------------
def build_flatfile(df: pd.DataFrame, sku_col: str) -> pd.DataFrame:
    """
    Costruisce un file Excel con doppio header **senza forzare country/currency**.
    """
    field_names = ["sku","minimum-seller-allowed-price","maximum-seller-allowed-price",
                   "country-code","currency-code","rule-name","rule-action",
                   "business-rule-name","business-rule-action"]
    header_desc = ["SKU","Min price","Max price","Country code","Currency code",
                   "Rule name","Rule action","Business rule name","Business rule action"]

    data = {
        "sku": df.get(sku_col, df.get("SKU")),
        "minimum-seller-allowed-price": df.get("minimum-seller-allowed-price", df.get("Prezzo minimo suggerito (€)")),
        "maximum-seller-allowed-price": df.get("maximum-seller-allowed-price", ""),
        # ⬇️ niente default "IT"/"EUR": prendiamo ciò che c’è nel DataFrame
        "country-code": df.get("country-code"),
        "currency-code": df.get("currency-code"),
        "rule-name": df.get("rule-name", "AUTO"),
        "rule-action": df.get("rule-action", "START"),
        "business-rule-name": df.get("business-rule-name", ""),
        "business-rule-action": df.get("business-rule-action", ""),
    }
    df_data = pd.DataFrame(data)
    return pd.concat(
        [pd.DataFrame([header_desc], columns=field_names),
         pd.DataFrame([field_names], columns=field_names),
         df_data],
        ignore_index=True,
    )

def make_flatfile_bytes(df: pd.DataFrame) -> io.BytesIO:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, header=False, sheet_name="Modello assegnazione prezzo")
    output.seek(0)
    return output

def highlight_below(row):
    min_v = pd.to_numeric(row.get("Prezzo minimo suggerito (€)"), errors="coerce")
    pr_v  = pd.to_numeric(row.get("Prezzo"), errors="coerce")
    if np.isfinite(min_v) and np.isfinite(pr_v) and min_v > pr_v:
        return ["background-color: lightcoral"] * len(row)
    return [""] * len(row)

def country_summary(df: pd.DataFrame, title: str):
    if "country-code" in df.columns:
        vals = df["country-code"].dropna().astype(str).str.upper().unique().tolist()
        st.caption(f"{title} · country-code rilevati: {', '.join(sorted([v for v in vals if v])) or '—'}")

# --------------------------- Caricamento ---------------------------
inventory_df = load_excel(inv_file)
purchase_df  = load_excel(price_file)
rename_map = {"sku":"SKU","seller-sku":"SKU","current-selling-price":"Prezzo"}
if inventory_df is not None: inventory_df = inventory_df.rename(columns=rename_map)
if purchase_df  is not None: purchase_df  = purchase_df.rename(columns=rename_map)

# Se l’utente non carica export: richiedi inv+acquisti
if export_file is None and (inventory_df is None or purchase_df is None):
    st.info("👈 Carica inventario + acquisti, oppure carica l’export Automate Pricing.")
    st.stop()

# ---------------- Modalità A: EXPORT + ACQUISTI -------------------
if export_file is not None and purchase_df is not None and inventory_df is None:
    st.subheader("Non Automated SKUs – da export (solo export + acquisti)")
    try:
        export_df = load_amazon_export(export_file)
    except Exception as e:
        st.error(f"Errore nel parsing del flat-file export: {e}"); st.stop()
    if "SKU" not in export_df.columns:
        st.error("Il flat-file export non contiene 'SKU'."); st.stop()

    export_df["_SKU_KEY_"] = normalize_sku(export_df["SKU"], parse_suffix)
    export_df = ensure_country_currency(export_df)  # ⬅️ preserva / compila currency vuote
    country_summary(export_df, "Export caricato")

    price_key_candidates = [c for c in purchase_df.columns if c.upper() in {"CODICE","SKU","CODICE(ASIN)"}]
    if not price_key_candidates:
        st.error("Nel file acquisti manca la colonna SKU/CODICE/CODICE(ASIN)."); st.stop()
    price_key = st.selectbox("Colonna SKU nel file acquisti", price_key_candidates, index=0, key="exponly_pricekey")
    purchase_df["_SKU_KEY_"] = normalize_sku(purchase_df[price_key], parse_suffix)

    candidate_price_cols = [c for c in purchase_df.columns if ("prezzo" in c.lower() and "medio" in c.lower())
                            or c.lower().strip() in {"prezzo","prezzo medio","prezzo medio (€)"}]
    if not candidate_price_cols:
        st.error("Colonna prezzo d'acquisto non trovata (es. 'Prezzo medio')."); st.stop()
    price_col = candidate_price_cols[0]
    merged_export = export_df.merge(
        purchase_df[["_SKU_KEY_", price_col]].rename(columns={price_col:"Prezzo medio acquisto (€)"}),
        on="_SKU_KEY_", how="left"
    )

    st.markdown("### Parametri costi e margini")
    defaults = CATEGORY_MAP["_default"]
    referral_fee_pct = st.number_input("% Commissione Amazon (referral)", value=defaults["referral"], min_value=0.0, key="exponly_ref")
    closing_fee      = st.number_input("Commissione di chiusura variabile €", value=float(defaults["closing"]), min_value=0.0, key="exponly_close")
    shipping_cost    = st.number_input("Costo spedizione del venditore €", value=0.0, min_value=0.0, key="exponly_ship")
    vat_pct          = st.number_input("IVA %", value=22.0, min_value=0.0, step=0.1, key="exponly_vat")
    margin_pct       = st.number_input("Margine desiderato %", value=10.0, min_value=0.0, key="exponly_margin")

    merged_export["Prezzo medio acquisto (€)"] = pd.to_numeric(merged_export["Prezzo medio acquisto (€)"], errors="coerce")
    merged_export["Prezzo minimo suggerito (€)"] = merged_export.apply(
        calc_min_price, axis=1,
        referral_pct=referral_fee_pct, closing_fee=closing_fee, dst_pct=DST_PCT,
        ship_cost=shipping_cost, vat_pct=vat_pct, margin_pct=margin_pct,
    )

    # ---- Rule-name identico; preserva i country/currency letti dall'export ----
    rule_name_value = st.text_input("Rule name da applicare a tutti gli SKU", value="AUTO", key="exponly_rule_value")
    merged_export["rule-name"]   = rule_name_value
    merged_export["rule-action"] = "START"
    merged_export = ensure_country_currency(merged_export)
    country_summary(merged_export, "Dopo merge + parametri")

    only_missing_min = st.checkbox("Mostra solo righe senza 'minimum-seller-allowed-price'", value=True, key="exponly_missmin")
    overwrite_min    = st.checkbox("Sovrascrivi 'minimum-seller-allowed-price' se già presente", value=False, key="exponly_overwrite")

    view = merged_export.copy()
    if "minimum-seller-allowed-price" in view.columns and only_missing_min:
        view = view[view["minimum-seller-allowed-price"].isna()]
    if overwrite_min or "minimum-seller-allowed-price" not in view.columns:
        view["minimum-seller-allowed-price"] = view["Prezzo minimo suggerito (€)"]
    else:
        view["minimum-seller-allowed-price"] = view["minimum-seller-allowed-price"].where(
            view["minimum-seller-allowed-price"].notna(), view["Prezzo minimo suggerito (€)"]
        )

    ff_out = view.dropna(subset=["SKU", "minimum-seller-allowed-price"])
    st.dataframe(ff_out[["SKU","country-code","currency-code","rule-name","rule-action",
                         "minimum-seller-allowed-price","Prezzo medio acquisto (€)","Prezzo minimo suggerito (€)"]],
                 use_container_width=True, hide_index=True)
    st.download_button("💾 Scarica Flat-File (compilato da export)",
        data=make_flatfile_bytes(build_flatfile(ff_out, "SKU")),
        file_name="AutomatePricing_FromExport.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.stop()

# --------------- Modalità B: EXPORT + INVENTARIO (senza acquisti) ------------
if export_file is not None and inventory_df is not None and purchase_df is None:
    st.subheader("Non Automated SKUs – da export (solo export + inventario)")
    try:
        export_df = load_amazon_export(export_file)
    except Exception as e:
        st.error(f"Errore nel parsing del flat-file export: {e}"); st.stop()
    if "SKU" not in export_df.columns:
        st.error("Il flat-file export non contiene 'SKU'."); st.stop()

    export_df["_SKU_KEY_"] = normalize_sku(export_df["SKU"], parse_suffix)
    export_df = ensure_country_currency(export_df)
    country_summary(export_df, "Export caricato")

    inv_key_candidates = [c for c in inventory_df.columns if c.upper() in {"SKU","CODICE(ASIN)","CODICE","ASIN"}]
    if not inv_key_candidates:
        st.error("Nel file inventario manca la colonna SKU/CODICE(ASIN)/CODICE/ASIN."); st.stop()
    inv_key = st.selectbox("Colonna SKU nell'inventario", inv_key_candidates, index=0, key="expinv_invkey")
    inventory_df["_SKU_KEY_"] = normalize_sku(inventory_df[inv_key], parse_suffix)

    # colonna costo dall’inventario
    priority = ["Prezzo medio acquisto (€)","Prezzo medio acquisto","Prezzo acquisto","Prezzo d'acquisto",
                "Costo acquisto","Costo","Costo unitario","Costo medio","Prezzo medio"]
    name_hits = [c for c in inventory_df.columns if any(p in c.lower() for p in ["acquisto","costo","cost","purchase","prezzo"])]
    candidates = [c for c in priority if c in inventory_df.columns] + [c for c in name_hits if c not in priority]
    if not candidates:
        num_cols = [c for c in inventory_df.columns if pd.api.types.is_numeric_dtype(inventory_df[c])]
        if not num_cols:
            st.error("Non trovo una colonna costo nell'inventario."); st.stop()
        candidates = num_cols
    cost_col = st.selectbox("Colonna costo d'acquisto nell'inventario", candidates, index=0, key="expinv_costcol")

    inv_subset = inventory_df[["_SKU_KEY_", cost_col]].copy()
    inv_subset = inv_subset.rename(columns={cost_col: "Prezzo medio acquisto (€)"})

    merged_export = export_df.merge(inv_subset, on="_SKU_KEY_", how="left")
    country_summary(merged_export, "Dopo merge costi")

    st.markdown("### Parametri costi e margini")
    cats = list(CATEGORY_MAP.keys())
    selected_cat = st.selectbox("Categoria predefinita", cats, index=0, key="expinv_cat")
    defaults = CATEGORY_MAP.get(selected_cat, CATEGORY_MAP["_default"])
    referral_fee_pct = st.number_input("% Commissione Amazon (referral)", value=defaults["referral"], min_value=0.0, key="expinv_ref")
    closing_fee      = st.number_input("Commissione di chiusura variabile €", value=float(defaults["closing"]), min_value=0.0, key="expinv_close")
    shipping_cost    = st.number_input("Costo spedizione del venditore €", value=0.0, min_value=0.0, key="expinv_ship")
    vat_pct          = st.number_input("IVA %", value=22.0, min_value=0.0, step=0.1, key="expinv_vat")
    margin_pct       = st.number_input("Margine desiderato %", value=10.0, min_value=0.0, key="expinv_margin")

    merged_export["Prezzo medio acquisto (€)"] = pd.to_numeric(merged_export["Prezzo medio acquisto (€)"], errors="coerce")
    merged_export["Prezzo minimo suggerito (€)"] = merged_export.apply(
        calc_min_price, axis=1,
        referral_pct=referral_fee_pct, closing_fee=closing_fee, dst_pct=DST_PCT,
        ship_cost=shipping_cost, vat_pct=vat_pct, margin_pct=margin_pct,
    )

    rule_name_value = st.text_input("Rule name da applicare a tutti gli SKU", value="AUTO", key="expinv_rule_value")
    merged_export["rule-name"]   = rule_name_value
    merged_export["rule-action"] = "START"
    merged_export = ensure_country_currency(merged_export)
    country_summary(merged_export, "Pronto per export")

    only_missing_min = st.checkbox("Mostra solo righe senza 'minimum-seller-allowed-price'", value=True, key="expinv_missmin")
    overwrite_min    = st.checkbox("Sovrascrivi 'minimum-seller-allowed-price' se già presente", value=False, key="expinv_overwrite")

    view = merged_export.copy()
    if "minimum-seller-allowed-price" in view.columns and only_missing_min:
        view = view[view["minimum-seller-allowed-price"].isna()]
    if overwrite_min or "minimum-seller-allowed-price" not in view.columns:
        view["minimum-seller-allowed-price"] = view["Prezzo minimo suggerito (€)"]
    else:
        view["minimum-seller-allowed-price"] = view["minimum-seller-allowed-price"].where(
            view["minimum-seller-allowed-price"].notna(), view["Prezzo minimo suggerito (€)"]
        )

    ff_out = view.dropna(subset=["SKU", "minimum-seller-allowed-price"])
    st.dataframe(ff_out[["SKU","country-code","currency-code","rule-name","rule-action",
                         "minimum-seller-allowed-price","Prezzo medio acquisto (€)","Prezzo minimo suggerito (€)"]],
                 use_container_width=True, hide_index=True)
    st.download_button("💾 Scarica Flat-File (compilato da export)",
        data=make_flatfile_bytes(build_flatfile(ff_out, "SKU")),
        file_name="AutomatePricing_FromExport.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.stop()

# ---------------- Modalità C: COMPLETA (inventario + acquisti) ---------------
if inventory_df is None or purchase_df is None:
    st.info("👈 Carica inventario + acquisti, oppure una delle modalità export.")
    st.stop()

merged_df, inv_key = get_merged_inventory(inventory_df, purchase_df, parse_suffix)

st.subheader("Parametri costi e margini")
cats = list(CATEGORY_MAP.keys())
selected_cat = st.selectbox("Categoria", cats)
defaults = CATEGORY_MAP.get(selected_cat, CATEGORY_MAP["_default"])
referral_fee_pct = st.number_input("% Commissione Amazon (referral)", value=defaults["referral"], min_value=0.0)
closing_fee      = st.number_input("Commissione di chiusura variabile €", value=float(defaults["closing"]), min_value=0.0)
shipping_cost    = st.number_input("Costo spedizione del venditore €", value=0.0, min_value=0.0)
vat_pct          = st.number_input("IVA %", value=22.0, min_value=0.0, step=0.1)
margin_pct       = st.number_input("Margine desiderato %", value=10.0, min_value=0.0)

merged_df["Prezzo medio acquisto (€)"] = pd.to_numeric(merged_df["Prezzo medio acquisto (€)"], errors="coerce")
merged_df["Prezzo minimo suggerito (€)"] = merged_df.apply(
    calc_min_price, axis=1,
    referral_pct=referral_fee_pct, closing_fee=closing_fee, dst_pct=DST_PCT,
    ship_cost=shipping_cost, vat_pct=vat_pct, margin_pct=margin_pct,
)

st.subheader("Anteprima dataset unificato")
edited_df = st.data_editor(merged_df, key="merged_df_editor", use_container_width=True, hide_index=True,
                           column_config={inv_key: st.column_config.TextColumn(disabled=False)})
edited_df["_SKU_KEY_"] = normalize_sku(edited_df[inv_key], parse_suffix)

if st.button("🔄 Ricalcola prezzi minimi"):
    edited_df["Prezzo minimo suggerito (€)"] = edited_df.apply(
        calc_min_price, axis=1,
        referral_pct=referral_fee_pct, closing_fee=closing_fee, dst_pct=DST_PCT,
        ship_cost=shipping_cost, vat_pct=vat_pct, margin_pct=margin_pct,
    )

st.dataframe(edited_df.style.apply(highlight_below, axis=1), use_container_width=True, hide_index=True)

# Download Excel unificato
out = io.BytesIO()
with pd.ExcelWriter(out, engine="openpyxl") as writer:
    edited_df.drop(columns=["_SKU_KEY_"], errors="ignore").to_excel(writer, index=False, sheet_name="inventario_match")
out.seek(0)
st.download_button("💾 Scarica Excel unificato", data=out,
                   file_name=download_name or "inventario_match.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# Se hai caricato anche un export, permetti di generare il flat-file coerente coi country
if export_file is not None:
    st.subheader("Non Automated SKUs – builder da export Amazon (costi dal merge)")
    try:
        export_df2 = load_amazon_export(export_file)
    except Exception as e:
        st.error(f"Errore nel parsing del flat-file export: {e}")
        export_df2 = None
    if export_df2 is not None and "SKU" in export_df2.columns:
        export_df2["_SKU_KEY_"] = normalize_sku(export_df2["SKU"], parse_suffix)
        export_df2 = ensure_country_currency(export_df2)
        country_summary(export_df2, "Export per builder")

        ff_df = export_df2.merge(edited_df[["_SKU_KEY_", "Prezzo medio acquisto (€)"]], on="_SKU_KEY_", how="left")
        ff_df["Prezzo minimo suggerito (€)"] = ff_df.apply(
            calc_min_price, axis=1,
            referral_pct=referral_fee_pct, closing_fee=closing_fee, dst_pct=DST_PCT,
            ship_cost=shipping_cost, vat_pct=vat_pct, margin_pct=margin_pct,
        )

        rule_name_value_all = st.text_input("Rule name da applicare a tutti gli SKU (export)", value="AUTO", key="expfull_rule_value")
        ff_df["rule-name"]   = rule_name_value_all
        ff_df["rule-action"] = "START"
        ff_df = ensure_country_currency(ff_df)  # riempi SOLO valute vuote
        country_summary(ff_df, "Builder pronto")

        only_missing_min2 = st.checkbox("Mostra solo senza 'minimum-seller-allowed-price' (export)", value=True, key="exp_full_missmin")
        overwrite_min2    = st.checkbox("Sovrascrivi 'minimum-seller-allowed-price' (export)", value=False, key="exp_full_overwrite")
        view2 = ff_df.copy()
        if "minimum-seller-allowed-price" in view2.columns and only_missing_min2:
            view2 = view2[view2["minimum-seller-allowed-price"].isna()]
        if overwrite_min2 or "minimum-seller-allowed-price" not in view2.columns:
            view2["minimum-seller-allowed-price"] = view2["Prezzo minimo suggerito (€)"]
        else:
            view2["minimum-seller-allowed-price"] = view2["minimum-seller-allowed-price"].where(
                view2["minimum-seller-allowed-price"].notna(), view2["Prezzo minimo suggerito (€)"]
            )

        ff_out2 = view2.dropna(subset=["SKU", "minimum-seller-allowed-price"])
        st.dataframe(ff_out2[["SKU","country-code","currency-code","rule-name","rule-action",
                              "minimum-seller-allowed-price","Prezzo medio acquisto (€)","Prezzo minimo suggerito (€)"]],
                     use_container_width=True, hide_index=True)
        st.download_button("💾 Scarica Flat-File (compilato da export)",
            data=make_flatfile_bytes(build_flatfile(ff_out2, "SKU")),
            file_name="AutomatePricing_FromExport.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

st.caption("Made with Streamlit · Ultimo aggiornamento: fix country-code multi-paese")
