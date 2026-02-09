import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
from ortools.sat.python import cp_model
import io

# Sayfa Genişliği ve Başlık
st.set_page_config(page_title="Adil Gözetmen Dağıtımı", layout="wide")

st.title("⚖️ Adil Sınav Gözetmen Dağıtım Sistemi")
st.markdown("XML dosyanızı yükleyin ve adil gözetmen dağıtımını anında hesaplayın.")

# --- FONKSİYONLAR ---
def parse_xml(xml_content):
    tree = ET.ElementTree(ET.fromstring(xml_content))
    root = tree.getroot()
    tasks = []
    for gun in root.findall('gun'):
        gun_adi = gun.get('isim')
        sinavlar = gun.findall('sınav') + gun.findall('sinav')
        for sinav in sinavlar:
            ad = sinav.get('ad')
            bas = sinav.get('baslangic')
            bit = sinav.get('bitis')
            sure = int(sinav.get('sure'))
            etiket = sinav.get('etiket', 'normal')
            siniflar = sinav.find('siniflar').text.split(',')
            for s in siniflar:
                tasks.append({
                    'gun': gun_adi, 'sinav': ad, 'saat': f"{bas}-{bit}",
                    'sinif': s.strip(), 'sure': sure, 'etiket': etiket,
                    'is_big': 1 if s.strip() in ['301', '309'] else 0,
                    'slot_id': f"{gun_adi}_{bas}"
                })
    return tasks

# --- KENAR ÇUBUĞU ---
st.sidebar.header("📁 Ayarlar")
uploaded_file = st.sidebar.file_uploader("XML Dosyası Yükleyin", type=["xml"])
staff_count = st.sidebar.slider("Gözetmen Sayısı", 4, 15, 6)

if uploaded_file:
    tasks = parse_xml(uploaded_file.read())
    invigilators = list(range(1, staff_count + 1))
    
    if st.sidebar.button("Hesaplamayı Başlat"):
        model = cp_model.CpModel()
        x = {(i, t): model.NewBoolVar(f'x_{i}_{t}') for i in invigilators for t in range(len(tasks))}

        # Kısıtlar
        for t in range(len(tasks)):
            model.Add(sum(x[i, t] for i in invigilators) == 1)
        
        slots = set(t['slot_id'] for t in tasks)
        for i in invigilators:
            for slot in slots:
                overlap = [idx for idx, t in enumerate(tasks) if t['slot_id'] == slot]
                model.Add(sum(x[i, idx] for idx in overlap) <= 1)

        # Adalet Değişkenleri
        total_mins, big_mins, morn_cnt, eve_cnt = {}, {}, {}, {}
        for i in invigilators:
            total_mins[i] = model.NewIntVar(0, 5000, f'tm_{i}')
            big_mins[i] = model.NewIntVar(0, 5000, f'bm_{i}')
            morn_cnt[i] = model.NewIntVar(0, 50, f'mc_{i}')
            eve_cnt[i] = model.NewIntVar(0, 50, f'ec_{i}')
            
            model.Add(total_mins[i] == sum(x[i, t] * tasks[t]['sure'] for t in range(len(tasks))))
            model.Add(big_mins[i] == sum(x[i, t] * tasks[t]['sure'] for t in range(len(tasks)) if tasks[t]['is_big']))
            model.Add(morn_cnt[i] == sum(x[i, t] for t in range(len(tasks)) if tasks[t]['etiket'] == 'sabah'))
            model.Add(eve_cnt[i] == sum(x[i, t] for t in range(len(tasks)) if tasks[t]['etiket'] == 'aksam'))

        # Minimize Max-Min Diff
        def get_diff(vars):
            mi, ma = model.NewIntVar(0, 5000, 'min'), model.NewIntVar(0, 5000, 'max')
            model.AddMinEquality(mi, vars.values())
            model.AddMaxEquality(ma, vars.values())
            return ma - mi

        model.Minimize(get_diff(total_mins)*10 + get_diff(big_mins)*5 + get_diff(morn_cnt)*2 + get_diff(eve_cnt)*2)
        
        solver = cp_model.CpSolver()
        if solver.Solve(model) in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            st.success("✅ Dağıtım Başarıyla Tamamlandı!")
            
            # Veriyi Hazırla
            final_list = []
            for t in range(len(tasks)):
                for i in invigilators:
                    if solver.Value(x[i, t]):
                        row = tasks[t].copy()
                        row['Gözetmen'] = f"Gözetmen {i}"
                        final_list.append(row)
            
            df_final = pd.DataFrame(final_list)
            
            # Excel Çıktısı Hazırlama
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_final[['gun', 'sinav', 'saat', 'sinif', 'Gözetmen']].to_excel(writer, index=False, sheet_name='Atama Listesi')
            excel_data = output.getvalue()

            # Arayüz Tabları
            t1, t2 = st.tabs(["📋 Görev Listesi", "📊 Adalet Raporu"])
            
            with t1:
                st.download_button(label="📥 Sonuçları Excel Olarak İndir", data=excel_data, file_name="sinav_atama_listesi.xlsx")
                st.dataframe(df_final[['gun', 'sinav', 'saat', 'sinif', 'Gözetmen']], use_container_width=True)
                
            with t2:
                report = []
                for i in invigilators:
                    report.append({
                        "Gözetmen": f"Gözetmen {i}",
                        "Top. Süre": solver.Value(total_mins[i]),
                        "Büyük Sınıf": solver.Value(big_mins[i]),
                        "Sabah": solver.Value(morn_cnt[i]),
                        "Akşam": solver.Value(eve_cnt[i])
                    })
                st.table(pd.DataFrame(report))
else:
    st.info("Lütfen soldaki menüden bir XML dosyası yükleyerek başlayın.")