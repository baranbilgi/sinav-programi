import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
from ortools.sat.python import cp_model
import io

# Sayfa Yapılandırması
st.set_page_config(page_title="Gözetmen Planlama Sistemi", layout="wide")

# Kurumsal Başlık
st.title("🏛️ Gözetmen Optimizasyon ve Görev Planlama Sistemi")

# --- FONKSİYONLAR ---
def parse_xml(xml_content):
    tree = ET.ElementTree(ET.fromstring(xml_content))
    root = tree.getroot()
    tasks = []
    all_rooms = set()
    days_order = []
    for gun in root.findall('gun'):
        gun_adi = gun.get('isim')
        if gun_adi not in days_order: days_order.append(gun_adi)
        sinavlar = gun.findall('sınav') + gun.findall('sinav')
        for sinav in sinavlar:
            siniflar_text = sinav.find('siniflar').text
            sinif_listesi = [s.strip() for s in siniflar_text.split(',') if s.strip()]
            for s in sinif_listesi:
                all_rooms.add(s)
                tasks.append({
                    'gun': gun_adi, 
                    'sinav': sinav.get('ad'), 
                    'saat': f"{sinav.get('baslangic')}-{sinav.get('bitis')}",
                    'baslangic': sinav.get('baslangic'), 
                    'sinif': s,
                    'sure': int(sinav.get('sure')), 
                    'etiket': sinav.get('etiket', 'normal'),
                    'slot_id': f"{gun_adi}_{sinav.get('baslangic')}"
                })
    return tasks, sorted(list(all_rooms)), days_order

# --- YAN MENÜ ---
st.sidebar.header("⚙️ Operasyonel Ayarlar")
uploaded_file = st.sidebar.file_uploader("Sınav Takvimi (XML)", type=["xml"])
staff_count = st.sidebar.number_input("Toplam Personel Sayısı", min_value=1, value=6)

if uploaded_file:
    tasks, rooms, days_list = parse_xml(uploaded_file.read().decode("utf-8"))
    
    big_rooms = st.sidebar.multiselect("Büyük Sınıf Kategorisindeki Odalar", rooms, default=[r for r in rooms if r in ['301', '309']])
    
    st.sidebar.subheader("🚫 Görev Muafiyetleri")
    unavailable_input = st.sidebar.text_area("İzinli Personel (Örn: Gözetmen 1:Pazartesi)")

    if st.sidebar.button("Planlamayı Optimize Et"):
        model = cp_model.CpModel()
        invs = list(range(1, staff_count + 1))
        num_t = len(tasks)

        x = {(i, t): model.NewBoolVar(f'x_{i}_{t}') for i in invs for t in range(num_t)}

        # Kısıtlar
        for t in range(num_t):
            model.Add(sum(x[i, t] for i in invs) == 1)
        
        for i in invs:
            for slot in set(t['slot_id'] for t in tasks):
                overlap = [idx for idx, t in enumerate(tasks) if t['slot_id'] == slot]
                model.Add(sum(x[i, idx] for idx in overlap) <= 1)

            # Günlük Max 3 Görev ve Gece-Sabah Yasağı
            for d_idx, d in enumerate(days_list):
                day_tasks = [idx for idx, t in enumerate(tasks) if t['gun'] == d]
                model.Add(sum(x[i, idx] for idx in day_tasks) <= 3)
                
                if d_idx < len(days_list) - 1:
                    today_last = [idx for idx, t in enumerate(tasks) if t['gun'] == d and t['etiket'] == 'aksam']
                    tomorrow_first = [idx for idx, t in enumerate(tasks) if t['gun'] == days_list[d_idx+1] and t['etiket'] == 'sabah']
                    for tl in today_last:
                        for tf in tomorrow_first:
                            model.Add(x[i, tl] + x[i, tf] <= 1)

        # Muafiyetler
        if unavailable_input:
            for entry in unavailable_input.split(','):
                if ':' in entry:
                    try:
                        s_part, d_part = entry.split(':')
                        s_no = int(s_part.strip().replace("Gözetmen ", ""))
                        if s_no in invs:
                            for idx, t in enumerate(tasks):
                                if t['gun'] == d_part.strip(): model.Add(x[s_no, idx] == 0)
                    except: continue

        # İş Yükü ve Adalet Değişkenleri
        total_mins, big_mins, morn_cnt, eve_cnt, critical_sum = {}, {}, {}, {}, {}
        for i in invs:
            total_mins[i] = model.NewIntVar(0, 10000, f'tm_{i}')
            big_mins[i] = model.NewIntVar(0, 10000, f'bm_{i}')
            morn_cnt[i] = model.NewIntVar(0, 50, f'mc_{i}')
            eve_cnt[i] = model.NewIntVar(0, 50, f'ec_{i}')
            critical_sum[i] = model.NewIntVar(0, 50, f'cs_{i}')

            model.Add(total_mins[i] == sum(x[i, t] * tasks[t]['sure'] for t in range(num_t)))
            model.Add(big_mins[i] == sum(x[i, t] * tasks[t]['sure'] for t in range(num_t) if tasks[t]['sinif'] in big_rooms))
            model.Add(morn_cnt[i] == sum(x[i, t] for t in range(num_t) if tasks[t]['etiket'] == 'sabah'))
            model.Add(eve_cnt[i] == sum(x[i, t] for t in range(num_t) if tasks[t]['etiket'] == 'aksam'))
            model.Add(critical_sum[i] == morn_cnt[i] + eve_cnt[i])

        # S+A Toplam Farkı <= 1 (Arka planda çalışır)
        max_cs, min_cs = model.NewIntVar(0, 50, 'max_cs'), model.NewIntVar(0, 50, 'min_cs')
        model.AddMaxEquality(max_cs, critical_sum.values())
        model.AddMinEquality(min_cs, critical_sum.values())
        model.Add(max_cs - min_cs <= 1)

        # Optimizasyon
        ma_t, mi_t = model.NewIntVar(0, 10000, 'ma_t'), model.NewIntVar(0, 10000, 'mi_t')
        model.AddMaxEquality(ma_t, total_mins.values())
        model.AddMinEquality(mi_t, total_mins.values())
        model.Minimize(ma_t - mi_t)

        solver = cp_model.CpSolver()
        if solver.Solve(model) in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            st.success("✅ Operasyonel planlama başarıyla optimize edildi.")
            
            results = []
            for t_idx, t in enumerate(tasks):
                for i in invs:
                    if solver.Value(x[i, t_idx]):
                        row = t.copy()
                        row['Gözetmen'] = f"Gözetmen {i}"
                        results.append(row)
            
            df_final = pd.DataFrame(results)
            
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_final[['gun', 'sinav', 'saat', 'sinif', 'Gözetmen']].to_excel(writer, index=False)
            
            tab1, tab2, tab3 = st.tabs(["📋 Görev Çizelgesi", "📊 İş Yükü Analizi", "📖 Metodoloji"])
            
            with tab1:
                st.download_button("📥 Excel Olarak İndir", output.getvalue(), "gorev_plani.xlsx")
                st.dataframe(df_final[['gun', 'sinav', 'saat', 'sinif', 'Gözetmen']], use_container_width=True)
            
            with tab2:
                stats = []
                for i in invs:
                    stats.append({
                        "Gözetmen": f"Gözetmen {i}",
                        "Toplam Mesai (dk)": solver.Value(total_mins[i]),
                        "Büyük Sınıf Mesaisi (dk)": solver.Value(big_mins[i]),
                        "Sabah Görevi": solver.Value(morn_cnt[i]),
                        "Akşam Görevi": solver.Value(eve_cnt[i])
                    })
                st.table(pd.DataFrame(stats))

            with tab3:
                st.write("**Planlama İlkeleri:**")
                st.write("1. **Süre Dengesi:** Personel arasındaki toplam mesai süreleri homojenize edilir.")
                st.write("2. **Dinlenme Kuralı:** Akşam görevinden sonraki sabah görevi atanmaz.")
                st.write("3. **Kritik Oturum Dengesi:** Sabah ve akşam sınavları toplamı personel arasında en fazla 1 sınav fark edecek şekilde dağıtılır.")
        else:
            st.error("Uygun bir plan bulunamadı.")
