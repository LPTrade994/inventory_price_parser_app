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
    "TR",
    "UK",
    "US",
    "IE",
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

    name = uploaded_file.name.lower()
    if name.endswith(".txt"):
        uploaded_file.seek(0)
        content = uploaded_file.read().decode("latin-1", errors="ignore")
        # prova TSV e CSV
        try:
            df = pd.read_csv(io.StringIO(content), sep="\t", decimal=",")
        except Exception:
            df = pd.read_csv(io.StringIO(content), sep=";", decimal=",")
        return df

    # Excel
    try:
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
    """Carica un file *template* Automate Pricing (doppio header fisso)."""
    uploaded_file.seek(0)
    wb = openpyxl.load_workbook(uploaded_file, read_only=True, data_only=True)
    sheet_name = next((n for n in [
        "Modello assegnazione prezzo",
        "Modello di assegnazione prezzo",
        "Modello di assegnazione del prezzo",
        "Esempio",
    ] if n in wb.sheetnames), wb.sheetnames[0])

    ws = wb[sheet_name]
    data = list(ws.values)
    if len(data) < 2:
        return pd.DataFrame()

    # riga 2 (index 1) = nomi macchina nella maggior parte dei template
    header_raw = data[1]
    columns = [_to_kebab(h) for h in header_raw]
    rows = [list(row) for row in data[2:]]
    df = pd.DataFrame(rows, columns=columns)

    df = df.dropna(how="all")
    if "sku" in df.columns:
        df["sku"] = df["sku"].astype(str).str.strip()
        df = df[df["sku"] != ""]

    # normalizza stringhe
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].astype(str).str.strip()

    # alias e rinomina
    rename_map = {"sku": "SKU", "seller-sku": "SKU", "current-selling-price": "Prezzo"}
    df = df.rename(columns=rename_map)

    # coercizioni numeriche
    price_cols = [c for c in df.columns if re.search(r"price|points", c)]
    for col in price_cols:
        df[col] = pd.to_numeric(
            pd.Series(df[col]).astype(str).str.replace(",", "."), errors="coerce"
        )
        df.loc[df[col] < 0, col] = np.nan

    if "sales-rank" in df.columns:
        df["sales-rank"] = pd.to_numeric(
            pd.Series(df["sales-rank"]).astype(str).str.replace(",", "."), errors="coerce"
        ).astype("Int64")

    if "rule-action" in df.columns:
        df["rule-action"] = df["rule-action"].astype(str).str.strip().str.upper()
        valid_actions = {"START", "STOP"}
        bad = df.loc[~df["rule-action"].isin(valid_actions) & df["rule-action"].notna(), "rule-action"].unique()
        if len(bad) > 0:
            raise ValueError(f"Valori 'rule-action' non validi: {', '.join(bad)}")

    if "country-code" in df.columns:
        df["country-code"] = df["country-code"].astype(str).str.strip().str.upper()
        bad_cc = df.loc[~df["country-code"].isin(COUNTRY_CODES) & df["country-code"].notna(), "country-code"].unique()
        if len(bad_cc) > 0:
            raise ValueError(f"Codici paese non validi: {', '.join(bad_cc)}")

    return df.reset_index(drop=True)


def load_amazon_export(uploaded_file):
    """Parser *robusto* del flat-file **export** (header variabile, CSV/XLSX/TSV).

    - Scansiona le prime ~30 righe per trovare la riga che contiene 'sku' (o 'seller-sku').
    - Usa quella riga come intestazione effettiva (in kebab-case) e legge i record successivi.
    - Supporta Excel (qualsiasi nome foglio) e CSV/TSV (senza header).
    """
    name = uploaded_file.name.lower()

    def _build_df_from_matrix(matrix):
        # trova riga header
        header_idx = None
        header_row = None
        for i in range(min(30, len(matrix))):
            row_vals = ["" if v is None else str(v) for v in matrix[i]]
            keb = [_to_kebab(v) for v in row_vals]
            if "sku" in keb or "seller-sku" in keb:
                header_idx = i
                header_row = keb
                break
        if header_idx is None:
            # fallback: prova seconda riga come nei template
            header_idx = 1 if len(matrix) > 1 else 0
            header_row = [_to_kebab(v) for v in (matrix[header_idx] if matrix else [])]

        data_rows = [list(r) for r in matrix[header_idx + 1 :]]
        df = pd.DataFrame(data_rows, columns=header_row)
        # pulizie
        for col in df.select_dtypes(include="object").columns:
            df[col] = df[col].astype(str).str.strip()
        # alias sku
        ren = {"sku": "SKU", "seller-sku": "SKU", "current-selling-price": "Prezzo"}
        df = df.rename(columns=ren)
        return df

    if name.endswith((".xlsx", ".xls")):
        uploaded_file.seek(0)
        wb = openpyxl.load_workbook(uploaded_file, read_only=True, data_only=True)
        # scegli foglio preferito se presente, altrimenti il primo
        sheet_name = next((n for n in [
            "Modello assegnazione prezzo",
            "Modello di assegnazione prezzo",
            "Modello di assegnazione del prezzo",
            "Esempio",
        ] if n in wb.sheetnames), wb.sheetnames[0])
        ws = wb[sheet_name]
        matrix = list(ws.values)
        df = _build_df_from_matrix(matrix)
    else:
        # CSV/TSV senza header deterministico
        uploaded_file.seek(0)
        content = uploaded_file.read().decode("utf-8", errors="ignore")
        # tenta separatori comuni
        frames = []
        for sep in [",", "\t", ";", "|"]:
            try:
                tmp = pd.read_csv(io.StringIO(content), sep=sep, header=None, dtype=str)
                frames.append(tmp)
            except Exception:
                pass
        if not frames:
            raise ValueError("Impossibile leggere il flat-file export (CSV/TSV)")
        # scegli quello con più colonne (euristica)
        df_raw = max(frames, key=lambda d: d.shape[1])
        matrix = df_raw.fillna("").values.tolist()
        df = _build_df_from_matrix(matrix)

    # coercizioni numeriche standard
    num_cols = [
        "minimum-seller-allowed-price",
        "maximum-seller-allowed-price",
        "current-selling-price",
        "buybox-landed-price",
        "lowest-landed-price",
        "sales-rank",
    ]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(pd.Series(df[c]).astype(str).str.replace(",", "."), errors="coerce")

    # normalizza campi chiave
    if "rule-action" in df.columns:
        df["rule-action"] = df["rule-action"].astype(str).str.upper().replace({"": "START", "nan": "START"})
    if "country-code" in df.columns:
        df["country-code"] = df["country-code"].astype(str).str.upper()
    if "currency-code" in df.columns:
        df["currency-code"] = df["currency-code"].astype(str).str.upper()

    # filtra righe vuote sku
    if "SKU" in df.columns:
        df = df[df["SKU"].astype(str).str.strip() != ""]

    return df.reset_index(drop=True)


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
    inv_key_candidates = [
        c for c in inventory_df.columns if c.upper() in {"SKU", "CODICE(ASIN)", "CODICE", "ASIN"}
    ]
    price_key_candidates = [
        c for c in purchase_df.columns if c.upper() in {"CODICE", "SKU", "CODICE(ASIN)"}
    ]
    if not inv_key_candidates or not price_key_candidates:
        st.error(
            "Assicurati che entrambi i file contengano almeno una colonna chiave tra: "
            "SKU, CODICE(ASIN), CODICE, ASIN."
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
        if "prezzo" in c.lower() and "medio" in c.lower()
        or c.lower().strip() in {"prezzo", "prezzo medio", "prezzo medio (€)"}
    ]
    if not candidate_price_cols:
        st.error(
            "Colonna del prezzo d'acquisto non trovata (es. 'Prezzo medio', 'Prezzo')."
        )
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
    vat_sale_pct: float,
    margin_pct: float,
    shipping_credit: float = 0.0,
    subtract_sale_vat: bool = True,
    vat_on_closing: bool = True,
    margin_basis: str = "gross",  # "gross" → (prezzo + credito sped.) ; "exvat" → (prezzo/1+IVA + credito)
):
    """Calcola il **prezzo minimo** che realizza il margine target replicando la logica del Revenue Calculator.

    Assunzioni Amazon MFN EU:
    - *Referral fee* = r% del **prezzo di vendita totale** (prezzo articolo + *shipping credit* mostrato al cliente).
    - *Closing fee* = importo fisso per categoria (es. 0,81€ in Videogiochi).
    - *DST* (Digital Services Tax) = d% delle **Selling on Amazon fees** (referral + closing), **senza IVA**.
    - *IVA sulle fee* = IVA% applicata alle fee Amazon. Per coerenza con i casi reali è attiva anche sulla closing fee,
      ma puoi disattivarla con `vat_on_closing=False` se il tuo report Amazon mostra diversamente.
    - Opzionale: sottrarre l'IVA sulla vendita dal profitto (`subtract_sale_vat=True`) per un allineamento più stretto
      al Revenue Calculator EU.

    Target di margine:
    - `margin_basis="gross"`  → margine % calcolato su (prezzo + shipping credit)
    - `margin_basis="exvat"`  → margine % calcolato su (prezzo/1+IVA + shipping credit)

    Restituisce il **prezzo minimo** (arrotondato a 2 decimali) oppure ``None`` se i parametri rendono impossibile il calcolo.
    """
    cost = pd.to_numeric(row.get("Prezzo medio acquisto (€)"), errors="coerce")
    if not np.isfinite(cost) or cost <= 0:
        return None

    # Parametri
    A = float(referral_pct) / 100.0        # referral rate
    B = float(closing_fee or 0.0)          # closing fee
    d = float(dst_pct) / 100.0             # DST rate
    v_fee = float(vat_sale_pct) / 100.0    # IVA sulle fee Amazon (stessa % dell'IVA vendita, in EU
    v_sale = float(vat_sale_pct) / 100.0   # IVA sulla vendita
    m = float(margin_pct) / 100.0          # margine target

    Cb = float(shipping_credit or 0.0)     # shipping addebitata al cliente (ricavo)
    S  = float(ship_cost or 0.0)           # costo di spedizione del venditore

    # Coefficienti delle fee nel modello lineare
    # AmazonCharges = Referral*(1+v_fee+d) + Closing*(1+(v_fee if vat_on_closing else 0)+d)
    alpha = 1.0 - (1.0 + v_fee + d) * A
    v_fee_closing = (v_fee if vat_on_closing else 0.0)
    D = (1.0 + v_fee_closing + d) * B + cost + S

    # IVA su vendita da sottrarre al profitto
    t = (v_sale / (1.0 + v_sale)) if subtract_sale_vat else 0.0

    # Denominatore del margine (base)
    if margin_basis == "exvat":
        u = 1.0 / (1.0 + v_sale)  # coeff per prezzo al netto IVA
        denom_coef = alpha - t - m * u
        rhs = D - (alpha - m) * Cb
    else:  # "gross"
        denom_coef = alpha - t - m
        rhs = D - (alpha - m) * Cb

    if abs(denom_coef) < 1e-12:
        return None

    P = rhs / denom_coef
    if not np.isfinite(P) or P <= 0:
        return None

    return round(P, 2)
 vendita dal profitto (`subtract_sale_vat=True`) per un allineamento più stretto
      al Revenue Calculator EU.

    Target di margine:
    - `margin_basis="gross"`  → margine % calcolato su (prezzo + shipping credit)
    - `margin_basis="exvat"`  → margine % calcolato su (prezzo/1+IVA + shipping credit)

    Restituisce il **prezzo minimo** (arrotondato a 2 decimali) oppure ``None`` se i parametri rendono impossibile il calcolo.
    """
    cost = pd.to_numeric(row.get("Prezzo medio acquisto (€)"), errors="coerce")
    if not np.isfinite(cost) or cost <= 0:
        return None

    # Parametri
    A = float(referral_pct) / 100.0        # referral rate
    B = float(closing_fee or 0.0)          # closing fee
    d = float(dst_pct) / 100.0             # DST rate
    v_fee = float(vat_sale_pct) / 100.0    # IVA sulle fee Amazon (stessa % dell'IVA vendita, in EU
    v_sale = float(vat_sale_pct) / 100.0   # IVA sulla vendita
    m = float(margin_pct) / 100.0          # margine target

    Cb = float(shipping_credit or 0.0)     # shipping addebitata al cliente (ricavo)
    S  = float(ship_cost or 0.0)           # costo di spedizione del venditore

    # Coefficienti delle fee nel modello lineare
    # AmazonCharges = Referral*(1+v_fee+d) + Closing*(1+(v_fee if vat_on_closing else 0)+d)
    alpha = 1.0 - (1.0 + v_fee + d) * A
    v_fee_closing = (v_fee if vat_on_closing else 0.0)
    D = (1.0 + v_fee_closing + d) * B + cost + S

    # IVA su vendita da sottrarre al profitto
    t = (v_sale / (1.0 + v_sale)) if subtract_sale_vat else 0.0

    # Denominatore del margine (base)
    if margin_basis == "exvat":
        u = 1.0 / (1.0 + v_sale)  # coeff per prezzo al netto IVA
        denom_coef = alpha - t - m * u
        rhs = D - (alpha - m) * Cb
    else:  # "gross"
        denom_coef = alpha - t - m
        rhs = D - (alpha - m) * Cb

    if abs(denom_coef) < 1e-12:
        return None

    P = rhs / denom_coef
    if not np.isfinite(P) or P <= 0:
        return None

    return round(P, 2)
 vendita dal profitto (`subtract_sale_vat=True`) per un allineamento più stretto
      al Revenue Calculator EU.

    Target di margine:
    - `margin_basis="gross"`  → margine % calcolato su (prezzo + shipping credit)
    - `margin_basis="exvat"`  → margine % calcolato su (prezzo/1+IVA + shipping credit)

    Restituisce il **prezzo minimo** (arrotondato a 2 decimali) oppure ``None`` se i parametri rendono impossibile il calcolo.
    """
    cost = pd.to_numeric(row.get("Prezzo medio acquisto (€)"), errors="coerce")
    if not np.isfinite(cost) or cost <= 0:
        return None

    # Parametri
    A = float(referral_pct) / 100.0        # referral rate
    B = float(closing_fee or 0.0)          # closing fee
    d = float(dst_pct) / 100.0             # DST rate
    v_fee = float(vat_sale_pct) / 100.0    # IVA sulle fee Amazon (stessa % dell'IVA vendita, in EU
    v_sale = float(vat_sale_pct) / 100.0   # IVA sulla vendita
    m = float(margin_pct) / 100.0          # margine target

    Cb = float(shipping_credit or 0.0)     # shipping addebitata al cliente (ricavo)
    S  = float(ship_cost or 0.0)           # costo di spedizione del venditore

    # Coefficienti delle fee nel modello lineare
    # AmazonCharges = Referral*(1+v_fee+d) + Closing*(1+(v_fee if vat_on_closing else 0)+d)
    alpha = 1.0 - (1.0 + v_fee + d) * A
    v_fee_closing = (v_fee if vat_on_closing else 0.0)
    D = (1.0 + v_fee_closing + d) * B + cost + S

    # IVA su vendita da sottrarre al profitto
    t = (v_sale / (1.0 + v_sale)) if subtract_sale_vat else 0.0

    # Denominatore del margine (base)
    if margin_basis == "exvat":
        u = 1.0 / (1.0 + v_sale)  # coeff per prezzo al netto IVA
        denom_coef = alpha - t - m * u
        rhs = D - (alpha - m) * Cb
    else:  # "gross"
        denom_coef = alpha - t - m
        rhs = D - (alpha - m) * Cb

    if abs(denom_coef) < 1e-12:
        return None

    P = rhs / denom_coef
    if not np.isfinite(P) or P <= 0:
        return None

    return round(P, 2)
 vendita dal profitto (`subtract_sale_vat=True`) per un allineamento più stretto
      al Revenue Calculator EU.

    Target di margine:
    - `margin_basis="gross"`  → margine % calcolato su (prezzo + shipping credit)
    - `margin_basis="exvat"`  → margine % calcolato su (prezzo/1+IVA + shipping credit)

    Restituisce il **prezzo minimo** (arrotondato a 2 decimali) oppure ``None`` se i parametri rendono impossibile il calcolo.
    """
    cost = pd.to_numeric(row.get("Prezzo medio acquisto (€)"), errors="coerce")
    if not np.isfinite(cost) or cost <= 0:
        return None

    # Parametri
    A = float(referral_pct) / 100.0        # referral rate
    B = float(closing_fee or 0.0)          # closing fee
    d = float(dst_pct) / 100.0             # DST rate
    v_fee = float(vat_sale_pct) / 100.0    # IVA sulle fee Amazon (stessa % dell'IVA vendita, in EU
    v_sale = float(vat_sale_pct) / 100.0   # IVA sulla vendita
    m = float(margin_pct) / 100.0          # margine target

    Cb = float(shipping_credit or 0.0)     # shipping addebitata al cliente (ricavo)
    S  = float(ship_cost or 0.0)           # costo di spedizione del venditore

    # Coefficienti delle fee nel modello lineare
    # AmazonCharges = Referral*(1+v_fee+d) + Closing*(1+(v_fee if vat_on_closing else 0)+d)
    alpha = 1.0 - (1.0 + v_fee + d) * A
    v_fee_closing = (v_fee if vat_on_closing else 0.0)
    D = (1.0 + v_fee_closing + d) * B + cost + S

    # IVA su vendita da sottrarre al profitto
    t = (v_sale / (1.0 + v_sale)) if subtract_sale_vat else 0.0

    # Denominatore del margine (base)
    if margin_basis == "exvat":
        u = 1.0 / (1.0 + v_sale)  # coeff per prezzo al netto IVA
        denom_coef = alpha - t - m * u
        rhs = D - (alpha - m) * Cb
    else:  # "gross"
        denom_coef = alpha - t - m
        rhs = D - (alpha - m) * Cb

    if abs(denom_coef) < 1e-12:
        return None

    P = rhs / denom_coef
    if not np.isfinite(P

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
rename_map = {"sku": "SKU", "seller-sku": "SKU", "current-selling-price": "Prezzo"}
if inventory_df is not None:
    inventory_df = inventory_df.rename(columns=rename_map)
if purchase_df is not None:
    purchase_df = purchase_df.rename(columns=rename_map)

# Early-return rivisto:
# - Se NON c'è l'export Amazon: servono inventario + acquisti.
# - Se c'è l'export Amazon: puoi lavorare anche solo con **inventario** (costi dall'inventario)
#   oppure solo con **acquisti** (costi dal file acquisti). Qui attiviamo i rami dedicati.
if export_file is None and (inventory_df is None or purchase_df is None):
    st.info("👈 Carica inventario + acquisti, oppure carica flat-file Amazon + inventario (o + acquisti).")
    st.stop()

# -------------------------------------------------------------------
# SOLO EXPORT + INVENTARIO: flat-file Amazon + file inventario (senza acquisti)
# -------------------------------------------------------------------
if export_file is not None and inventory_df is not None and purchase_df is None:
    st.subheader("Non Automated SKUs – da export Amazon (solo export + inventario)")

    # Leggo l'export con header dinamico
    try:
        export_df = load_amazon_export(export_file)
    except Exception as e:
        st.error(f"Errore nel parsing del flat-file export: {e}")
        st.stop()

    if "SKU" not in export_df.columns:
        st.error("Il flat-file export non contiene 'SKU'.")
        st.stop()

    export_df["_SKU_KEY_"] = normalize_sku(export_df["SKU"], parse_option)

    # Individuo chiave SKU nell'inventario
    inv_key_candidates = [c for c in inventory_df.columns if c.upper() in {"SKU","CODICE(ASIN)","CODICE","ASIN"}]
    if not inv_key_candidates:
        st.error("Nel file inventario manca la colonna SKU/CODICE/CODICE(ASIN)/ASIN.")
        st.stop()
    inv_key_exp = st.selectbox("Colonna SKU nell'inventario", inv_key_candidates, index=0, key="expinv_invkey")
    inventory_df["_SKU_KEY_"] = normalize_sku(inventory_df[inv_key_exp], parse_option)

    # Scelta della colonna COSTO nell'inventario
    priority = [
        "Prezzo medio acquisto (€)",
        "Prezzo medio acquisto",
        "Prezzo acquisto",
        "Prezzo d'acquisto",
        "Costo acquisto",
        "Costo",
        "Costo unitario",
        "Costo medio",
        "Prezzo medio",
    ]
    # Candidati per nome e per contenuto (acquisto/costo)
    name_hits = [c for c in inventory_df.columns if any(p.lower() in c.lower() for p in ["acquisto","costo","cost","purchase"]) ]
    candidates = [c for c in priority if c in inventory_df.columns]
    for c in name_hits:
        if c not in candidates:
            candidates.append(c)
    if not candidates:
        # ultima spiaggia: tutte le colonne numeriche
        num_cols = [c for c in inventory_df.columns if pd.api.types.is_numeric_dtype(inventory_df[c])]
        if not num_cols:
            st.error("Non trovo una colonna costo nell'inventario. Aggiungi una colonna costo o carica anche il file acquisti.")
            st.stop()
        candidates = num_cols

    cost_col = st.selectbox("Colonna costo d'acquisto nell'inventario", candidates, index=0, key="expinv_costcol")

    # Categoria opzionale
    cat_col = None
    for c in inventory_df.columns:
        if c.lower().strip() == "categoria":
            cat_col = c
            break

    inv_subset_cols = ["_SKU_KEY_", cost_col] + ([cat_col] if cat_col else [])
    inv_subset = inventory_df[inv_subset_cols].copy()
    inv_subset = inv_subset.rename(columns={cost_col: "Prezzo medio acquisto (€)", cat_col: "Categoria"} if cat_col else {cost_col: "Prezzo medio acquisto (€)"})

    # Merge export ⟷ inventario (per avere il costo)
    merged_export = export_df.merge(
        inv_subset,
        on="_SKU_KEY_", how="left", suffixes=("", "_inv")
    )

    # Parametri (indipendenti dal file acquisti)
st.markdown("### Parametri costi e margini (solo export + inventario)")
cats = list(CATEGORY_MAP.keys())
selected_cat_exp = st.selectbox("Categoria predefinita", cats, index=0, key="expinv_cat")
defaults_exp = CATEGORY_MAP.get(selected_cat_exp, CATEGORY_MAP["_default"])
referral_fee_pct_exp = st.number_input("% Commissione Amazon (referral)", value=defaults_exp["referral"], min_value=0.0, key="expinv_ref")
closing_fee_exp       = st.number_input("Commissione di chiusura €", value=float(defaults_exp.get("closing", 0.0)), min_value=0.0, key="expinv_close")
shipping_cost_exp     = st.number_input("Costo spedizione del venditore €", value=0.0, min_value=0.0, key="expinv_ship")
shipping_credit_exp   = st.number_input("Spedizione addebitata al cliente (credito) €", value=0.0, min_value=0.0, key="expinv_shipcredit")
vat_pct_exp           = st.number_input("IVA %", value=22.0, min_value=0.0, step=0.1, key="expinv_vat")
margin_pct_exp        = st.number_input("Margine desiderato %", value=20.0, min_value=0.0, key="expinv_margin")
col_exp1, col_exp2 = st.columns(2)
with col_exp1:
    subtract_sale_vat_exp = st.checkbox("Sottrai IVA sulla vendita dal profitto", value=True, key="expinv_subvat")
with col_exp2:
    vat_on_closing_exp = st.checkbox("IVA anche sulla commissione di chiusura", value=True, key="expinv_vatclosing")
margin_basis_exp = st.radio(
    "Base margine",
    options=["gross","exvat"], index=0, key="expinv_marginbasis",
    format_func=lambda x: "Ricavo lordo (prezzo + shipping credit)" if x=="gross" else "Ricavo netto IVA (prezzo/1+IVA + shipping credit)"
)

    # Calcolo minimo
merged_export["Prezzo medio acquisto (€)"] = pd.to_numeric(merged_export["Prezzo medio acquisto (€)"], errors="coerce")
# Fallback variabili se non impostate (compatibilità)
if 'shipping_credit_exp' not in locals():
    shipping_credit_exp = 0.0
if 'subtract_sale_vat_exp' not in locals():
    subtract_sale_vat_exp = True
if 'vat_on_closing_exp' not in locals():
    vat_on_closing_exp = True
if 'margin_basis_exp' not in locals():
    margin_basis_exp = "gross"

merged_export["Prezzo minimo suggerito (€)"] = merged_export.apply(
    calc_min_price,
    axis=1,
    referral_pct=referral_fee_pct_exp,
    closing_fee=closing_fee_exp,
    dst_pct=DST_PCT,
    ship_cost=shipping_cost_exp,
    vat_sale_pct=vat_pct_exp,
    margin_pct=margin_pct_exp,
    shipping_credit=shipping_credit_exp,
    subtract_sale_vat=subtract_sale_vat_exp,
    vat_on_closing=vat_on_closing_exp,
    margin_basis=margin_basis_exp,
)

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
