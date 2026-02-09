import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
from ortools.sat.python import cp_model
import io

# Sayfa Yapılandırması
st.set_page_config(page_title="Gözetmen Planlama Sistemi", layout="wide")

# Kurumsal Başlık
st.title("🏛️ Gözetmen Optimizasyon ve Görev Planlama Sistemi")

# --- YARDIMCI FONKSİYONLAR ---
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

# --- YAN MENÜ (PARAMETRELER) ---
st.sidebar.header("⚙️ Operasyonel Ayarlar")
uploaded_file = st.sidebar.file_uploader("Sınav Takvimi (XML)", type=["xml"])
staff_count = st.sidebar.number_input("Toplam Personel Sayısı", min_value=1, value=6)

if uploaded_file:
    tasks, rooms, days_list = parse_xml(uploaded_file.read().decode("utf-8"))
    
    # Çoklu Sınıf Seçimi
    big_rooms = st.sidebar.multiselect("Büyük Sınıf Kategorisindeki Odalar", rooms, default=[r for r in rooms if r in ['301', '309']])
    
    # Personel Müsaitlik Girişi
    st.sidebar.subheader("🚫 Görev Muafiyetleri")
    unavailable_input = st.sidebar.text_area("İzinli Personel (Örn: Gözetmen 1:Pazartesi)", help="Gözetmen No:Gün formatında giriniz.")

    if st.sidebar.button("Planlamayı Optimize Et"):
        model = cp_model.CpModel()
        invs = list(range(1, staff_count + 1))
        num_t = len(tasks)

        # Karar Değişkenleri
        x = {(i, t): model.NewBoolVar(f'x_{i}_{t}') for i in invs for t in range(num_t)}

        # 1. OPERASYONEL KISITLAR
        for t in range(num_t):
            model.Add(sum(x[i, t] for i in invs) == 1) # Her sınıfa 1 gözetmen
        
        for i in invs:
            for slot in set(t['slot_id'] for t in tasks):
                overlap = [idx for idx, t in enumerate(tasks) if t['slot_id'] == slot]
                model.Add(sum(x[i, idx] for idx in overlap) <= 1) # Çakışma engelleme

        # 2. İNSANİ ÇALIŞMA KOSULLARI
        for i in invs:
            # Günlük Maksimum Görev (3 Sınav)
            for d in days_list:
                day_tasks = [idx for idx, t in enumerate(tasks) if t['gun'] == d]
                model.Add(sum(x[i, idx] for idx in day_tasks) <= 3)

            # Dinlenme Kuralı (Akşam görevinden sonra ertesi sabah görev alamaz)
            for d_idx in range(len(days_list) - 1):
                today_last = [idx for idx, t in enumerate(tasks) if t['gun'] == days_list[d_idx] and t['etiket'] == 'aksam']
                tomorrow_first = [idx for idx, t in enumerate(tasks) if t['gun'] == days_list[d_idx+1] and t['etiket'] == 'sabah']
                for tl in today_last:
                    for tf in tomorrow_first:
                        model.Add(x[i, tl] + x[i, tf] <= 1)

        # 3. ÖZEL MUAFİYETLERİN İŞLENMESİ
        if unavailable_input:
            for entry in unavailable_input.split(','):
                if ':' in entry:
                    try:
                        staff_part, day_part = entry.split(':')
                        s_no = int(staff_part.strip().replace("Gözetmen ", ""))
                        d_name = day_part.strip()
                        for idx, t in enumerate(tasks):
                            if t['gun'] == d_name and s_no in invs:
                                model.Add(x[s_no, idx] == 0)
                    except: continue

        # 4. İŞ YÜKÜ DENGELEME MANTIĞI
        total_mins, critical_sessions = {}, {}
        for i in invs:
            total_mins[i] = model.NewIntVar(0, 10000, f'tm_{i}')
            model.Add(total_mins[i] == sum(x[i, t] * tasks[t]['sure'] for t in range(num_t)))
            
            # Personelin sabah+akşam toplam görev sayısı
            critical_sessions[i] = model.NewIntVar(0, 50, f'cs_{i}')
            model.Add(critical_sessions[i] == sum(x[i, t] for t in range(num_t) if tasks[t]['etiket'] in ['sabah', 'aksam']))

        # S+A Farkı Maksimum 1 Sınav Olmalı (İstediğin Düzenleme)
        cs_vars = list(critical_sessions.values())
        max_cs = model.NewIntVar(0, 50, 'max_cs')
        min_cs = model.NewIntVar(0, 50, 'min_cs')
        model.AddMaxEquality(max_cs, cs_vars)
        model.AddMinEquality(min_cs, cs_vars)
        model.Add(max_cs - min_cs <= 1)

        # AMAÇ: Toplam Süre Farkını Minimize Et
        ma_t, mi_t = model.NewIntVar(0, 10000, 'ma_t'), model.NewIntVar(0, 10000, 'mi_t')
        model.AddMaxEquality(ma_t, list(total_mins.values()))
        model.AddMinEquality(mi_t, list(total_mins.values()))
        model.Minimize(ma_t - mi_t)

        # ÇÖZÜM
        solver = cp_model.CpSolver()
        status = solver.Solve(model)

        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            st.success("✅ Operasyonel planlama başarıyla optimize edildi.")
            
            results = []
            for t_idx, t in enumerate(tasks):
                for i in invs:
                    if solver.Value(x[i, t_idx]):
                        row = t.copy()
                        row['Gözetmen'] = f"Gözetmen {i}"
                        results.append(row)
            
            df_final = pd.DataFrame(results)

            # Excel Hazırlama
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_final[['gun', 'sinav', 'saat', 'sinif', 'Gözetmen']].to_excel(writer, index=False, sheet_name='Gorev_Listesi')
            excel_data = output.getvalue()

            tab1, tab2, tab3 = st.tabs(["📋 Görev Çizelgesi", "📊 İş Yükü Analizi", "📖 Metodoloji"])
            
            with tab1:
                st.download_button("📥 Çizelgeyi Excel Olarak İndir", excel_data, "gorev_plani.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                st.dataframe(df_final[['gun', 'sinav', 'saat', 'sinif', 'Gözetmen']], use_container_width=True)
            
            with tab2:
                stats = [{"Gözetmen": f"Gözetmen {i}", "Toplam Mesai (dk)": solver.Value(total_mins[i]), "Kritik Oturum (S+A)": solver.Value(critical_sessions[i])} for i in invs]
                st.table(pd.DataFrame(stats))

            with tab3:
                st.info("**Algoritma Bilgisi:** Sistem, Google OR-Tools motorunu kullanarak 'Constraint Programming' prensibiyle çalışır.")
                st.write("**Planlama İlkeleri:**")
                st.write("1. **Adil Dağılım:** Toplam süreler ve kritik saat yoğunlukları personel arasında homojenize edilir.")
                st.write("2. **Dinlenme Süresi:** Gece görevini takiben sabah görevi atanması matematiksel olarak engellenmiştir.")
                st.write("3. **Yığılma Engelleme:** Personelin günlük iş yükü 3 sınav ile sınırlandırılmıştır.")
        else:
            st.error("Mevcut kısıtlar altında uygun bir plan bulunamadı. Lütfen personel sayısını artırın veya muafiyetleri azaltın.")
else:
    st.info("Lütfen sol menüden sınav takvimini (XML) yükleyerek operasyonu başlatın.")
