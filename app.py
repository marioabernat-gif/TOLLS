import streamlit as st
import re
import pandas as pd
import io
import math
from fpdf import FPDF


def parse_gcode_for_time_and_tools(file_content):
    """
    Analiza el contenido de un archivo G-code para extraer herramientas, grupos
    y calcular un tiempo, distancia, avances y RPMs estimados para cada grupo,
    respetando la naturaleza modal de los comandos G y F.
    """
    results = []
    current_group_info = None

    # --- Estado de la Máquina Virtual (persiste a lo largo del archivo) ---
    pos = {'X': 0.0, 'Y': 0.0, 'Z': 0.0}
    rpm = 1.0
    # El avance (feed) es modal. Se mantiene activo hasta que se comanda uno nuevo.
    feed = 0.0
    g95_active = False  # Modo de avance (G95: por revolución)
    # El modo de movimiento (G0/G1) es modal.
    motion_mode = 'G0'

    # --- Expresiones Regulares ---
    group_begin_re = re.compile(r'GROUP_BEGIN\([^,]*,\s*"([^"]*)"')
    group_end_re = re.compile(r'GROUP_END')
    tool_re = re.compile(r'T="([^"]*)"')
    coord_re = re.compile(r'([XYZ])([-\d.]+)')
    feed_re = re.compile(r'F([-\d.]+)')
    rpm_re = re.compile(r'S(\d+)')
    g_code_re = re.compile(r'G0?([01])')  # Busca G0, G1, G00, G01

    for line in file_content.splitlines():
        # --- Manejo de Grupos ---
        match = group_begin_re.search(line)
        if match:
            if current_group_info:
                results.append(current_group_info)
            current_group_info = {
                "Herramienta": "N/A",
                "Grupo": match.group(1),
                "Tiempo Corte Est. (seg)": 0.0,
                "Distancia Corte (mm)": 0.0,
                "Avances": set(),
                "RPMs": set()
            }

        if group_end_re.search(line) and current_group_info:
            results.append(current_group_info)
            current_group_info = None

        if not current_group_info:
            continue

        # --- Actualización del Estado Modal de la Máquina ---
        g_match = g_code_re.search(line)
        if g_match:
            motion_mode = f"G{g_match.group(1)}"

        if "G95" in line:
            g95_active = True
        if "G94" in line:
            g95_active = False

        rpm_match = rpm_re.search(line)
        if rpm_match:
            rpm = float(rpm_match.group(1))
            current_group_info["RPMs"].add(int(rpm))

        feed_match = feed_re.search(line)
        if feed_match:
            # Se actualiza el valor modal del avance.
            feed = float(feed_match.group(1))
            current_group_info["Avances"].add(feed)

        tool_match = tool_re.search(line)
        if tool_match:
            current_group_info["Herramienta"] = tool_match.group(1)

        # --- Procesamiento de Movimiento ---
        coords_found = coord_re.findall(line)
        if coords_found:
            target_pos = pos.copy()
            for axis, value in coords_found:
                target_pos[axis] = float(value)

            distance = math.sqrt(
                (target_pos['X'] - pos['X'])**2 +
                (target_pos['Y'] - pos['Y'])**2 +
                (target_pos['Z'] - pos['Z'])**2
            )

            # Se calcula el tiempo SOLO si el modo de movimiento es G1 (corte).
            # Se utiliza el valor de avance (feed) que esté activo en ese momento.
            if motion_mode == 'G1' and distance > 0:
                time_for_move = 0.0
                if g95_active and rpm > 0 and feed > 0:
                    time_for_move = (distance / (feed * rpm)) * 60
                elif not g95_active and feed > 0:
                    time_for_move = (distance / feed) * 60

                current_group_info["Tiempo Corte Est. (seg)"] += time_for_move
                current_group_info["Distancia Corte (mm)"] += distance

            # La posición de la herramienta se actualiza siempre, sin importar el modo.
            pos = target_pos

    if current_group_info:
        results.append(current_group_info)

    return results


def create_pdf_report(files_data, comparison_df):
    """
    Genera un reporte en PDF con los datos de las herramientas y tiempos.
    """
    pdf = FPDF(orientation='L')
    pdf.add_page()

    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Reporte de Herramientas y Tiempos CNC", 0, 1, "C")
    pdf.ln(10)

    for file_data in files_data:
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 10, f"Herramientas en: {file_data['name']}", 0, 1)

        pdf.set_font("Arial", "B", 10)
        pdf.cell(15, 10, "ID", 1)
        pdf.cell(45, 10, "Herramienta", 1)
        pdf.cell(70, 10, "Grupo", 1)
        pdf.cell(25, 10, "RPM (S)", 1)
        pdf.cell(25, 10, "Avance (F)", 1)
        pdf.cell(30, 10, "Distancia (mm)", 1)
        pdf.cell(40, 10, "Tiempo Est.", 1)
        pdf.ln()

        pdf.set_font("Arial", "", 9)
        if file_data['data']:
            for item in file_data['data']:
                herramienta_safe = item["Herramienta"][:25].encode(
                    'latin-1', 'replace').decode('latin-1')
                grupo_safe = item["Grupo"][:40].encode(
                    'latin-1', 'replace').decode('latin-1')

                segundos = item.get('Tiempo Corte Est. (seg)', 0.0)
                minutos = segundos / 60
                tiempo_str = f"{segundos:.0f}s ({minutos:.2f}m)"
                distancia_str = f"{item.get('Distancia Corte (mm)', 0.0):.2f}"
                avances_str = ", ".join(
                    map(str, sorted(list(item.get('Avances', set())))))
                rpms_str = ", ".join(
                    map(str, sorted(list(item.get('RPMs', set())))))

                pdf.cell(15, 10, item["ID"], 1)
                pdf.cell(45, 10, herramienta_safe, 1)
                pdf.cell(70, 10, grupo_safe, 1)
                pdf.cell(25, 10, rpms_str, 1)
                pdf.cell(25, 10, avances_str, 1)
                pdf.cell(30, 10, distancia_str, 1)
                pdf.cell(40, 10, tiempo_str, 1)
                pdf.ln()

            # --- Fila de Totales en PDF ---
            total_distancia = sum(item.get('Distancia Corte (mm)', 0.0)
                                  for item in file_data['data'])
            total_tiempo_seg = sum(
                item.get('Tiempo Corte Est. (seg)', 0.0) for item in file_data['data'])
            total_tiempo_min = total_tiempo_seg / 60

            pdf.set_font("Arial", "B", 10)
            pdf.cell(15 + 45 + 70 + 25 + 25, 10, "TOTALES", 1, 0, 'R')
            pdf.cell(30, 10, f"{total_distancia:.2f}", 1)
            pdf.cell(
                40, 10, f"{total_tiempo_seg:.0f}s ({total_tiempo_min:.2f}m)", 1)
            pdf.ln()

        else:
            pdf.cell(0, 10, "No se encontraron herramientas.", 1, 1)
        pdf.ln(5)

    if not comparison_df.empty:
        pdf.add_page()
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 10, "Tabla Comparativa de Herramientas", 0, 1)

        pdf.set_font("Arial", "B", 10)
        col_width = 270 / len(comparison_df.columns)
        for col in comparison_df.columns:
            col_safe = col.encode('latin-1', 'replace').decode('latin-1')
            pdf.cell(col_width, 10, col_safe, 1)
        pdf.ln()

        pdf.set_font("Arial", "", 10)
        for index, row in comparison_df.iterrows():
            for item in row:
                item_safe = str(item).encode(
                    'latin-1', 'replace').decode('latin-1')
                pdf.cell(col_width, 10, item_safe, 1)
            pdf.ln()

    return bytes(pdf.output(dest='S'))


def main():
    st.set_page_config(
        layout="wide", page_title="Extractor de Herramientas", page_icon="🛠️")

    st.markdown("""
    <style>
    .stApp { background-color: #1C1C1C; }
    body, .st-emotion-cache-1y4p8pa, .st-emotion-cache-1v0mbdj, .st-emotion-cache-16txtl3, .st-emotion-cache-1629p8f th, .st-emotion-cache-16txtl3 p { color: #FFFFFF; }
    .st-emotion-cache-1v0mbdj { border: 2px solid #28a745; border-radius: 10px; padding: 20px; background-color: #2A2A2A; }
    .stButton>button { background-color: #28a745; color: white; border-radius: 8px; padding: 10px 20px; border: none; cursor: pointer; font-size: 16px; }
    .stButton>button:hover { background-color: #218838; }
    .st-emotion-cache-16txtl3 h1, .st-emotion-cache-16txtl3 h2, .st-emotion-cache-16txtl3 h3 { color: #28a745; }
    .stDataFrame, .stDataFrame div, .stDataFrame th, .stDataFrame td { color: #262730; }
    </style>
    """, unsafe_allow_html=True)

    st.title("🛠️ Analizador de Tiempos y Herramientas CNC")
    st.markdown(
        "Sube, analiza y edita las listas de herramientas y calcula el tiempo de mecanizado estimado.")

    if 'original_data' not in st.session_state:
        st.session_state.original_data = None
    if 'edited_data' not in st.session_state:
        st.session_state.edited_data = None

    num_files = st.number_input(
        "Selecciona la cantidad de archivos a comparar", min_value=1, max_value=3, value=2, step=1)

    uploaded_files = []
    cols = st.columns(num_files)
    for i in range(num_files):
        with cols[i]:
            file = st.file_uploader(
                f"Elige el archivo {i+1}", type=['txt', 'mpf', 'spf'], key=f"file{i+1}")
            if file:
                uploaded_files.append(file)

    if len(uploaded_files) == num_files and st.session_state.original_data is None:
        try:
            all_files_data = []
            for i, file in enumerate(uploaded_files):
                stringio = io.TextIOWrapper(
                    file, encoding='utf-8', errors='replace')
                file_content = stringio.read()

                processed_groups = parse_gcode_for_time_and_tools(file_content)

                for j, item in enumerate(processed_groups):
                    item["ID"] = f"{i+1}.{j+1}"

                all_files_data.append(
                    {"name": file.name, "data": processed_groups})

            st.session_state.original_data = all_files_data
            st.session_state.edited_data = [d.copy() for d in all_files_data]
            st.success("¡Archivos procesados y tiempos calculados!")
        except Exception as e:
            st.error(f"Ocurrió un error al procesar los archivos: {e}")
            st.session_state.original_data = st.session_state.edited_data = None

    if st.session_state.edited_data:
        st.info("ℹ️ **Nota sobre la estimación de tiempo**:\n"
                "El tiempo y la distancia se calculan solo para movimientos de corte (`G1`). Los grupos que contienen únicamente ciclos (`CYCLE...`), macros (`F_...`) o movimientos rápidos (`G0`) mostrarán valores en 0.")

        st.header("Herramientas por Archivo (Editable)")

        if st.button("Restaurar Datos Originales"):
            st.session_state.edited_data = [
                d.copy() for d in st.session_state.original_data]
            st.toast("Datos restaurados.")

        res_cols = st.columns(num_files)
        for i, file_data in enumerate(st.session_state.edited_data):
            with res_cols[i]:
                st.subheader(f"Editando: `{file_data['name']}`")
                if file_data['data']:
                    df = pd.DataFrame(file_data['data'])
                    # Crear columnas de display formateadas
                    df['Tiempo Formateado'] = df['Tiempo Corte Est. (seg)'].apply(
                        lambda s: f"{s:.0f}s ({s/60:.2f}m)"
                    )
                    df['Distancia Formateada'] = df['Distancia Corte (mm)'].apply(
                        lambda d: f"{d:.2f} mm"
                    )
                    df['Avance Formateado'] = df['Avances'].apply(
                        lambda s: ", ".join(
                            map(str, sorted(list(s)))) if s else "N/A"
                    )
                    df['RPM Formateado'] = df['RPMs'].apply(
                        lambda s: ", ".join(
                            map(str, sorted(list(s)))) if s else "N/A"
                    )

                    edited_df = st.data_editor(df,
                                               column_order=("ID", "Herramienta", "Grupo", "RPM Formateado",
                                                             "Avance Formateado", "Distancia Formateada", "Tiempo Formateado"),
                                               column_config={
                                                   "Tiempo Corte Est. (seg)": None,
                                                   "Distancia Corte (mm)": None,
                                                   "Avances": None,
                                                   "RPMs": None,
                                                   "Tiempo Formateado": "Tiempo Est.",
                                                   "Distancia Formateada": "Distancia",
                                                   "Avance Formateado": "Avance (F)",
                                                   "RPM Formateado": "RPM (S)"
                                               },
                                               disabled=[
                                                   "ID", "Tiempo Formateado", "Distancia Formateada", "Avance Formateado", "RPM Formateado"],
                                               use_container_width=True, height=300, key=f"editor_{i}")

                    st.session_state.edited_data[i]['data'] = edited_df.drop(
                        columns=['Tiempo Formateado', 'Distancia Formateada', 'Avance Formateado', 'RPM Formateado']).to_dict('records')

                    # --- Fila de Totales en UI ---
                    st.markdown("---")
                    total_distancia = df['Distancia Corte (mm)'].sum()
                    total_tiempo_seg = df['Tiempo Corte Est. (seg)'].sum()
                    total_tiempo_min = total_tiempo_seg / 60

                    metric_cols = st.columns(2)
                    with metric_cols[0]:
                        st.metric(label="Distancia Total de Corte",
                                  value=f"{total_distancia:.2f} mm")
                    with metric_cols[1]:
                        st.metric(label="Tiempo Total de Corte",
                                  value=f"{total_tiempo_seg:.0f}s ({total_tiempo_min:.2f}m)")

                else:
                    st.warning("No se encontraron herramientas.")

        st.header("Tabla Comparativa de Herramientas")

        all_unique_tools_set = {item['Herramienta']
                                for file_data in st.session_state.edited_data for item in file_data['data']}

        if not all_unique_tools_set:
            st.warning("No hay herramientas para comparar.")
        else:
            df_comparison = pd.DataFrame(
                [{
                    "Herramienta": tool,
                    **{file_data["name"]: 'X' if tool in {d["Herramienta"] for d in file_data["data"]} else ''
                       for file_data in st.session_state.edited_data}
                } for tool in sorted(list(all_unique_tools_set))]
            )
            st.dataframe(df_comparison, use_container_width=True)

        st.markdown("---")
        pdf_bytes = create_pdf_report(
            st.session_state.edited_data, df_comparison)
        st.download_button(
            label="Descargar Reporte en PDF (con ediciones)",
            data=pdf_bytes,
            file_name="reporte_herramientas_tiempos.pdf",
            mime="application/pdf"
        )

    elif len(uploaded_files) != num_files:
        st.info(
            f"Por favor, sube {num_files} archivo(s) para comenzar el análisis.")


if __name__ == "__main__":
    main()
