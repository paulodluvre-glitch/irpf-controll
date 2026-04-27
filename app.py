from __future__ import annotations

import io
import re
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st


CLIENT_COLUMNS = [
    "CPF",
    "NOME",
    "Grupo",
    "Reunião",
    "Nivel de Complexidade",
    "Status Preenchimento",
    "Responsável pelo Preenchimento",
    "Status Pós-Envio",
    "Telefone",
    "Senha Gov",
    "Cadastro de Procuração",
]

DOCUMENT_COLUMNS = [
    "Nome Pessoa",
    "Tipo Documento",
    "Instituição",
    "Status",
    "Última Atualização",
    "chave_controle",
]

LOCAL_CLIENT_SAMPLE = Path(r"C:\Users\user\Downloads\INFORMAÇÕES DE CLIENTES(Relatório).csv")
LOCAL_DOCUMENT_SAMPLE = Path(r"C:\Users\user\Downloads\controle_documento(Controle Documentos).csv")
DATA_DIR = Path(__file__).resolve().parent / "data"
SNAPSHOT_PATH = DATA_DIR / "historico_snapshots.csv"


def normalize_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\n", " ")).strip()


def normalize_key(value: object) -> str:
    text = normalize_text(value).upper()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^A-Z0-9]+", " ", text).strip()


def normalize_column(value: object) -> str:
    return normalize_key(value).lower()


def safe_percent(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return round((numerator / denominator) * 100, 1)


def canonical_status(value: object) -> str:
    text = normalize_text(value).upper()
    normalized = normalize_key(text)
    if "TRANSMITID" in normalized:
        return "TRANSMITIDO"
    if "REVISAO" in normalized and "RENATO" in normalized:
        return "EM REVISÃO - RENATO"
    if "PREENCHIMENTO" in normalized:
        return "EM PREENCHIMENTO"
    if "PENDENTE" in normalized:
        return "PENDENTE"
    if "AJUSTE" in normalized:
        return text
    return text or "SEM STATUS"


def read_csv_bytes(file_bytes: bytes) -> pd.DataFrame:
    errors: list[str] = []
    for encoding in ["utf-8-sig", "utf-8", "latin1", "cp1252"]:
        try:
            return pd.read_csv(io.BytesIO(file_bytes), sep=None, engine="python", encoding=encoding)
        except Exception as exc:
            errors.append(f"{encoding}: {exc}")
    raise ValueError("Não foi possível ler o CSV. Tentativas: " + " | ".join(errors))


def read_table_file(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    suffix = Path(file_name).suffix.lower()
    if suffix in [".xlsx", ".xls"]:
        return pd.read_excel(io.BytesIO(file_bytes))
    return read_csv_bytes(file_bytes)


def select_columns(df: pd.DataFrame, expected_columns: list[str]) -> pd.DataFrame:
    normalized_to_original = {normalize_column(column): column for column in df.columns}
    selected: dict[str, pd.Series] = {}
    for expected in expected_columns:
        original = normalized_to_original.get(normalize_column(expected))
        if original is None:
            selected[expected] = pd.Series([""] * len(df))
        else:
            selected[expected] = df[original]
    return pd.DataFrame(selected)


def parse_clients(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    raw_df = read_table_file(file_bytes, file_name)
    df = select_columns(raw_df, CLIENT_COLUMNS)

    for column in CLIENT_COLUMNS:
        df[column] = df[column].map(normalize_text)

    df["CPF"] = df["CPF"].str.replace(r"\D", "", regex=True)
    df["NOME"] = df["NOME"].replace("", "Sem nome identificado")
    df["Grupo"] = df["Grupo"].replace("", "Sem grupo")
    df["Reunião"] = df["Reunião"].replace("", "Sem reunião informada")
    df["Nivel de Complexidade"] = (
        df["Nivel de Complexidade"].str.strip().str.title().replace("", "Não informado")
    )
    df["Status Preenchimento"] = df["Status Preenchimento"].map(canonical_status)
    df["Responsável pelo Preenchimento"] = (
        df["Responsável pelo Preenchimento"].str.upper().replace("", "Não atribuído")
    )
    df["Status Pós-Envio"] = df["Status Pós-Envio"].str.upper().replace("", "Não informado")
    df["Telefone"] = df["Telefone"].replace("", "Não informado")
    df["Cadastro de Procuração"] = df["Cadastro de Procuração"].replace("", "Não informado")
    df["chave_pessoa"] = df["NOME"].map(normalize_key)
    return df


def parse_documents(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    raw_df = read_table_file(file_bytes, file_name)
    df = select_columns(raw_df, DOCUMENT_COLUMNS)

    for column in DOCUMENT_COLUMNS:
        df[column] = df[column].map(normalize_text)

    df["Nome Pessoa"] = df["Nome Pessoa"].replace("", "Sem nome identificado")
    df["Tipo Documento"] = df["Tipo Documento"].replace("", "Não informado")
    df["Instituição"] = df["Instituição"].replace("", "Não informada")
    df["Status"] = df["Status"].str.upper().replace("", "SEM STATUS")
    df["Última Atualização"] = pd.to_datetime(
        df["Última Atualização"], format="%d/%m/%Y", errors="coerce"
    )
    df["documento_descricao"] = df["Tipo Documento"] + " - " + df["Instituição"]
    df["chave_pessoa"] = df["Nome Pessoa"].map(normalize_key)
    return df


def documentation_status(total: int, received: int) -> str:
    if total == 0 or received == 0:
        return "Sem documentação"
    if received == total:
        return "Recebido total"
    return "Recebido parcial"


def list_join(values: pd.Series) -> str:
    cleaned_values = [normalize_text(value) for value in values if normalize_text(value)]
    return "\n".join(dict.fromkeys(cleaned_values))


def build_people_summary(clients_df: pd.DataFrame, documents_df: pd.DataFrame) -> pd.DataFrame:
    docs_by_client = (
        documents_df.groupby("chave_pessoa", dropna=False)
        .agg(
            nome_documentos=("Nome Pessoa", "first"),
            total_documentos=("Status", "size"),
            documentos_recebidos=("Status", lambda values: int((values == "RECEBIDO").sum())),
            documentos_pendentes=("Status", lambda values: int((values != "RECEBIDO").sum())),
            documentos_enviados_lista=(
                "documento_descricao",
                lambda values: list_join(
                    documents_df.loc[values.index][
                        documents_df.loc[values.index, "Status"] == "RECEBIDO"
                    ]["documento_descricao"]
                ),
            ),
            documentos_faltantes_lista=(
                "documento_descricao",
                lambda values: list_join(
                    documents_df.loc[values.index][
                        documents_df.loc[values.index, "Status"] != "RECEBIDO"
                    ]["documento_descricao"]
                ),
            ),
            ultima_atualizacao=("Última Atualização", "max"),
        )
        .reset_index()
    )

    people_df = clients_df.merge(docs_by_client, on="chave_pessoa", how="outer")
    people_df["NOME"] = people_df["NOME"].replace("", pd.NA).fillna(people_df["nome_documentos"])
    people_df["Grupo"] = people_df["Grupo"].fillna("Sem grupo")
    people_df["Reunião"] = people_df["Reunião"].fillna("Sem reunião informada")
    people_df["Nivel de Complexidade"] = people_df["Nivel de Complexidade"].fillna("Não informado")
    people_df["Status Preenchimento"] = people_df["Status Preenchimento"].fillna("SEM STATUS")
    people_df["Responsável pelo Preenchimento"] = people_df[
        "Responsável pelo Preenchimento"
    ].fillna("Não atribuído")
    people_df["Telefone"] = people_df["Telefone"].fillna("Não informado")

    for column in ["total_documentos", "documentos_recebidos", "documentos_pendentes"]:
        people_df[column] = people_df[column].fillna(0).astype(int)

    people_df["Documentação"] = people_df.apply(
        lambda row: documentation_status(row["total_documentos"], row["documentos_recebidos"]),
        axis=1,
    )
    people_df["% documentação recebida"] = people_df.apply(
        lambda row: safe_percent(row["documentos_recebidos"], row["total_documentos"]),
        axis=1,
    )
    people_df["documentos_enviados_lista"] = people_df["documentos_enviados_lista"].fillna("")
    people_df["documentos_faltantes_lista"] = people_df["documentos_faltantes_lista"].fillna("")
    people_df["ultima_atualizacao"] = pd.to_datetime(people_df["ultima_atualizacao"], errors="coerce")
    people_df["dias_desde_ultima_atualizacao"] = (
        pd.Timestamp(date.today()) - people_df["ultima_atualizacao"]
    ).dt.days
    people_df["Precisa cobrar"] = (
        (people_df["Documentação"] != "Recebido total")
        & (
            people_df["ultima_atualizacao"].isna()
            | (people_df["dias_desde_ultima_atualizacao"] > 7)
        )
    )
    return people_df.sort_values(["Status Preenchimento", "Grupo", "NOME"])


def load_source(uploaded_file, fallback_path: Path) -> tuple[bytes | None, str]:
    if uploaded_file is not None:
        return uploaded_file.getvalue(), uploaded_file.name
    if fallback_path.exists():
        return fallback_path.read_bytes(), fallback_path.name
    return None, "Arquivo não carregado"


def save_snapshot(snapshot_df: pd.DataFrame) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if SNAPSHOT_PATH.exists():
        history_df = pd.read_csv(SNAPSHOT_PATH, parse_dates=["data_referencia"])
        history_df = history_df[
            history_df["data_referencia"].dt.date
            != snapshot_df.loc[0, "data_referencia"].date()
        ]
        snapshot_df = pd.concat([history_df, snapshot_df], ignore_index=True)
    snapshot_df.sort_values("data_referencia").to_csv(SNAPSHOT_PATH, index=False)


def load_history() -> pd.DataFrame:
    if not SNAPSHOT_PATH.exists():
        return pd.DataFrame()
    return pd.read_csv(SNAPSHOT_PATH, parse_dates=["data_referencia"]).sort_values(
        "data_referencia"
    )


def build_snapshot(snapshot_date: date, clients_df: pd.DataFrame, people_df: pd.DataFrame) -> pd.DataFrame:
    total_declarations = len(clients_df)
    transmitted = int((clients_df["Status Preenchimento"] == "TRANSMITIDO").sum())
    reviewing = int(clients_df["Status Preenchimento"].str.contains("REVISÃO", na=False).sum())
    docs_any = int((people_df["Documentação"] != "Sem documentação").sum())
    docs_complete = int((people_df["Documentação"] == "Recebido total").sum())
    docs_partial = int((people_df["Documentação"] == "Recebido parcial").sum())
    docs_missing = int((people_df["Documentação"] == "Sem documentação").sum())
    return pd.DataFrame(
        [
            {
                "data_referencia": pd.to_datetime(snapshot_date),
                "declaracoes": total_declarations,
                "transmitidas": transmitted,
                "em_revisao": reviewing,
                "clientes_com_alguma_documentacao": docs_any,
                "clientes_docs_completos": docs_complete,
                "clientes_docs_parciais": docs_partial,
                "clientes_sem_documentacao": docs_missing,
                "pct_transmitidas": safe_percent(transmitted, total_declarations),
                "pct_docs_completos": safe_percent(docs_complete, len(people_df)),
            }
        ]
    )


def display_metric(label: str, value: int, percent: float | None = None) -> None:
    delta = None if percent is None else f"{percent}%"
    st.metric(label, f"{value}", delta=delta)


def status_detail_table(people_df: pd.DataFrame, status_name: str) -> pd.DataFrame:
    return people_df[people_df["Status Preenchimento"] == status_name][
        [
            "NOME",
            "Grupo",
            "Responsável pelo Preenchimento",
            "Nivel de Complexidade",
            "Reunião",
            "Documentação",
        ]
    ].sort_values(["Grupo", "NOME"])


def render_status_sections(people_df: pd.DataFrame) -> None:
    st.subheader("Filas por status")
    status_order = [
        "PENDENTE",
        "EM PREENCHIMENTO",
        "EM REVISÃO - RENATO",
        "AGUARDANDO REUNIÃO",
        "TRANSMITIDO",
        "SEM STATUS",
    ]
    existing_statuses = list(people_df["Status Preenchimento"].dropna().unique())
    ordered_statuses = [status for status in status_order if status in existing_statuses]
    ordered_statuses += sorted(set(existing_statuses) - set(ordered_statuses))

    for status in ordered_statuses:
        details_df = status_detail_table(people_df, status)
        with st.expander(f"{status} ({len(details_df)})", expanded=status == "PENDENTE"):
            st.dataframe(details_df, use_container_width=True, hide_index=True)


def render_complexity_tables(people_df: pd.DataFrame) -> None:
    st.subheader("Peso da carteira por complexidade")
    complexity_order = ["Alto", "Média", "Media", "Baixo", "Não Informado", "Não informado"]
    existing_complexities = list(people_df["Nivel de Complexidade"].dropna().unique())
    ordered_complexities = [
        item for item in complexity_order if item in existing_complexities
    ] + sorted(set(existing_complexities) - set(complexity_order))

    for complexity in ordered_complexities:
        table_df = people_df[people_df["Nivel de Complexidade"] == complexity][
            [
                "NOME",
                "Grupo",
                "Status Preenchimento",
                "Responsável pelo Preenchimento",
                "Documentação",
            ]
        ].sort_values(["Status Preenchimento", "Grupo", "NOME"])
        st.markdown(f"**{complexity} ({len(table_df)})**")
        st.dataframe(table_df, use_container_width=True, hide_index=True)


def render_ready_to_fill(people_df: pd.DataFrame) -> None:
    st.subheader("Declarações prontas para preenchimento")
    ready_df = people_df[
        (people_df["Documentação"].isin(["Recebido total", "Recebido parcial"]))
        & (people_df["Responsável pelo Preenchimento"] == "Não atribuído")
    ][
        [
            "NOME",
            "Grupo",
            "Documentação",
            "Responsável pelo Preenchimento",
            "Status Preenchimento",
            "Nivel de Complexidade",
        ]
    ].sort_values(["Documentação", "Nivel de Complexidade", "Grupo", "NOME"])
    st.caption(
        "Clientes com documentação total ou parcial recebida e ainda sem responsável pelo preenchimento."
    )
    st.dataframe(ready_df, use_container_width=True, hide_index=True)
    st.download_button(
        "Exportar prontas para preencher",
        data=ready_df.to_csv(index=False).encode("utf-8-sig"),
        file_name="declaracoes_prontas_para_preencher.csv",
        mime="text/csv",
    )


def render_received_documentation(people_df: pd.DataFrame) -> None:
    st.subheader("Clientes com documentação recebida")
    received_df = people_df[
        people_df["Documentação"].isin(["Recebido total", "Recebido parcial"])
    ][
        [
            "NOME",
            "Grupo",
            "Documentação",
            "documentos_recebidos",
            "total_documentos",
            "% documentação recebida",
            "documentos_enviados_lista",
            "documentos_faltantes_lista",
            "Responsável pelo Preenchimento",
            "Status Preenchimento",
            "Nivel de Complexidade",
        ]
    ].sort_values(["Documentação", "Status Preenchimento", "Grupo", "NOME"])
    received_df = received_df.copy()
    received_df["Recebidos / Total"] = received_df.apply(
        lambda row: f"{int(row['documentos_recebidos'])} de {int(row['total_documentos'])}",
        axis=1,
    )
    received_df["% Recebido"] = received_df["% documentação recebida"].map(lambda value: f"{value:.1f}%")
    received_df = received_df[
        [
            "NOME",
            "Grupo",
            "Documentação",
            "Recebidos / Total",
            "% Recebido",
            "documentos_enviados_lista",
            "documentos_faltantes_lista",
            "Responsável pelo Preenchimento",
            "Status Preenchimento",
            "Nivel de Complexidade",
        ]
    ]
    received_df = received_df.rename(
        columns={
            "documentos_enviados_lista": "Documentos recebidos",
            "documentos_faltantes_lista": "Documentos faltantes",
        }
    )
    st.caption("Lista completa dos clientes que já enviaram documentação total ou parcial.")
    st.dataframe(received_df, use_container_width=True, hide_index=True)
    export_df = received_df.copy()
    export_df["Documentos recebidos"] = export_df["Documentos recebidos"].map(
        lambda value: normalize_text(str(value).replace("\n", " | "))
    )
    export_df["Documentos faltantes"] = export_df["Documentos faltantes"].map(
        lambda value: normalize_text(str(value).replace("\n", " | "))
    )
    st.download_button(
        "Exportar clientes com documentação recebida",
        data=export_df.to_csv(index=False, sep=";").encode("utf-8-sig"),
        file_name="clientes_com_documentacao_recebida.csv",
        mime="text/csv",
    )


def pending_message(row: pd.Series) -> str:
    pending_docs = normalize_text(row.get("documentos_faltantes_lista", ""))
    pending_docs_block = pending_docs if pending_docs else "Documentos pendentes não detalhados na base."
    last_contact = row.get("ultima_atualizacao")
    if pd.isna(last_contact):
        last_contact_text = "não temos uma data recente registrada de envio ou contato"
    else:
        last_contact_text = f"o último registro que temos é de {last_contact.strftime('%d/%m/%Y')}"

    return f"""Olá, {row['NOME']}, tudo bem?

Estamos entrando em contato sobre a sua Declaração de Imposto de Renda deste ano.

Para conseguirmos finalizar o seu processo e enviar tudo certinho para a Receita Federal, ainda precisamos que nos mande os seguintes documentos:

{pending_docs_block}

Identificamos que {last_contact_text}, então estamos reforçando esse pedido para conseguir organizar sua entrega com antecedência.

É muito importante lembrar que a entrega da sua declaração no prazo correto depende do envio desses documentos. Não poderemos nos responsabilizar por atrasos ou multas caso as informações não cheguem para nós em tempo hábil, tudo bem?

Se o(a) senhor(a) tiver alguma dúvida ou dificuldade para encontrar esses papéis, é só avisar.

Aguardamos o seu retorno para finalizarmos tudo com tranquilidade. Um abraço!"""


def render_document_collection(people_df: pd.DataFrame) -> None:
    st.subheader("Controle de cobrança de documentação")
    collection_df = people_df[people_df["Documentação"] != "Recebido total"][
        [
            "NOME",
            "Grupo",
            "Responsável pelo Preenchimento",
            "Documentação",
        ]
    ]
    ordering_df = people_df[people_df["Documentação"] != "Recebido total"][
        ["NOME", "documentos_faltantes_lista", "ultima_atualizacao", "Precisa cobrar"]
    ]
    collection_df = collection_df.merge(ordering_df, on="NOME", how="left").sort_values(
        ["Precisa cobrar", "ultima_atualizacao", "NOME"],
        ascending=[False, True, True],
    )

    st.dataframe(
        collection_df[
            ["NOME", "Grupo", "Responsável pelo Preenchimento", "Documentação"]
        ],
        use_container_width=True,
        hide_index=True,
    )

    chargeable_df = collection_df[collection_df["documentos_faltantes_lista"] != ""]
    if chargeable_df.empty:
        st.info("Não há clientes com documentos faltantes detalhados para gerar mensagem.")
        return

    selected_client = st.selectbox(
        "Selecionar cliente para gerar mensagem",
        options=chargeable_df["NOME"].tolist(),
    )
    selected_row = people_df[people_df["NOME"] == selected_client].iloc[0]
    if pd.isna(selected_row["ultima_atualizacao"]):
        st.caption("Último contato registrado: não informado. Cobrança recomendada.")
    else:
        days_since = selected_row["dias_desde_ultima_atualizacao"]
        if pd.notna(days_since) and days_since > 7:
            st.caption(
                f"Último contato registrado em {selected_row['ultima_atualizacao'].strftime('%d/%m/%Y')}."
                " Já passou de 7 dias, então vale cobrar de novo."
            )
        else:
            st.caption(
                f"Último contato registrado em {selected_row['ultima_atualizacao'].strftime('%d/%m/%Y')}."
            )
    st.text_area("Mensagem para copiar no WhatsApp", pending_message(selected_row), height=420)


def main() -> None:
    st.set_page_config(page_title="IRPF | Acompanhamento", layout="wide")
    st.title("Dashboard de acompanhamento IRPF")
    st.caption("Importe as planilhas diárias de clientes e documentos para atualizar o painel.")

    with st.sidebar:
        st.header("Importação diária")
        clients_upload = st.file_uploader(
            "Planilha de clientes/declaracões",
            type=["csv", "xlsx", "xls"],
        )
        documents_upload = st.file_uploader(
            "Planilha de controle de documentos",
            type=["csv", "xlsx", "xls"],
        )
        snapshot_date = st.date_input("Data de referência", value=date.today())

    client_bytes, client_label = load_source(clients_upload, LOCAL_CLIENT_SAMPLE)
    document_bytes, document_label = load_source(documents_upload, LOCAL_DOCUMENT_SAMPLE)
    if client_bytes is None or document_bytes is None:
        st.warning("Envie as duas planilhas para carregar o dashboard.")
        st.stop()

    clients_df = parse_clients(client_bytes, client_label)
    documents_df = parse_documents(document_bytes, document_label)
    people_df = build_people_summary(clients_df, documents_df)
    snapshot_df = build_snapshot(snapshot_date, clients_df, people_df)

    st.info(f"Arquivos carregados: `{client_label}` e `{document_label}`")

    metric_1, metric_2, metric_3, metric_4, metric_5 = st.columns(5)
    with metric_1:
        display_metric("Declarações", len(clients_df))
    with metric_2:
        display_metric(
            "Transmitidas",
            int(snapshot_df.loc[0, "transmitidas"]),
            snapshot_df.loc[0, "pct_transmitidas"],
        )
    with metric_3:
        display_metric("Em revisão", int(snapshot_df.loc[0, "em_revisao"]))
    with metric_4:
        display_metric(
            "Documentos recebidos",
            int(snapshot_df.loc[0, "clientes_com_alguma_documentacao"]),
            safe_percent(snapshot_df.loc[0, "clientes_com_alguma_documentacao"], len(people_df)),
        )
    with metric_5:
        display_metric(
            "Clientes com docs completos",
            int(snapshot_df.loc[0, "clientes_docs_completos"]),
            snapshot_df.loc[0, "pct_docs_completos"],
        )

    docs_status_df = (
        people_df["Documentação"].value_counts().rename_axis("Status documentação").reset_index(name="Clientes")
    )
    st.dataframe(docs_status_df, use_container_width=True, hide_index=True)

    tabs = st.tabs(
        [
            "Visão geral",
            "Complexidade",
            "Documentação recebida",
            "Prontas para preencher",
            "Cobrança de documentação",
            "Histórico",
        ]
    )

    with tabs[0]:
        st.subheader("Resumo executivo")
        status_df = (
            clients_df["Status Preenchimento"]
            .value_counts()
            .rename_axis("Status da declaração")
            .reset_index(name="Total")
        )
        st.dataframe(status_df, use_container_width=True, hide_index=True)
        render_status_sections(people_df)

    with tabs[1]:
        render_complexity_tables(people_df)

    with tabs[2]:
        render_received_documentation(people_df)

    with tabs[3]:
        render_ready_to_fill(people_df)

    with tabs[4]:
        render_document_collection(people_df)

    with tabs[5]:
        st.subheader("Histórico diário")
        if st.button("Salvar snapshot do dia"):
            save_snapshot(snapshot_df)
            st.success("Snapshot salvo em data/historico_snapshots.csv.")

        history_df = load_history()
        if history_df.empty:
            st.info("Ainda não há histórico salvo.")
        else:
            st.line_chart(
                history_df.set_index("data_referencia")[
                    [
                        "transmitidas",
                        "em_revisao",
                        "clientes_docs_completos",
                        "clientes_docs_parciais",
                        "clientes_sem_documentacao",
                    ]
                ]
            )
            st.dataframe(history_df, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
