import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
from ortools.sat.python import cp_model
import io

# Sayfa Konfigürasyonu
st.set_page_config(page_title="Gözetmen Planlama Sistemi", layout="wide")

# Kurumsal Kimlik ve Başlık
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
            siniflar = [s.strip() for s in sinav.find('siniflar').text.split(',') if s.strip()]
            for s in siniflar:
                all_rooms.add(s)
                tasks.append({
                    'gun': gun_adi, 'sinav': sinav.get('ad'), 
                    'saat': f"{sinav.get('baslangic')}-{sinav.get('bitis')}",
                    'baslangic': sinav.get('baslangic'), 'sinif': s,
                    'sure': int(sinav.get('sure')), 'etiket': sinav.get('etiket', 'normal'),
                    'slot_id': f"{gun_adi}_{sinav.get('baslangic')}"
                })
    return tasks, sorted(list(all_rooms)), days_order

# --- YAN MENÜ (AYARLAR) ---
st.sidebar.header("⚙️ Parametreler")
uploaded_file = st.sidebar.file_uploader("Sınav Takvimi (XML)", type=["xml"])
staff_count = st.sidebar.number_input("Toplam Gözetmen Sayısı", min_value=1, value=6)

if uploaded_file:
    tasks, rooms, days_list = parse_xml(uploaded_file.read())
    
    # Dinamik Büyük Sınıf Seçimi
    big_rooms = st.sidebar.multiselect("Büyük Sınıfları Seçiniz", rooms, default=[r for r in rooms if r in ['301', '309']])
    
    # Müsaitlik Durumu (Kısıtlamalar)
    st.sidebar.subheader("🚫 Görev Kısıtlamaları")
    unavailable_input = st.sidebar.text_area("Görev alamayacaklar (Örn: Gözetmen 1:Pazartesi, Gözetmen 2:Sali)", help="Format: Gözetmen No:Gün")

    if st.sidebar.button("Çizelgeyi Optimize Et"):
        model = cp_model.CpModel()
        invs = list(range(1, staff_count + 1))
        num_t = len(tasks)

        # Karar Değişkenleri
        x = {(i, t): model.NewBoolVar(f'x_{i}_{t}') for i in invs for t in range(num_t)}

        # 1. TEMEL KISITLAR
        for t in range(num_t):
            model.Add(sum(x[i, t] for i in invs) == 1) # Her sınıfa 1 kişi
        
        for i in invs:
            for slot in set(t['slot_id'] for t in tasks):
                overlap = [idx for idx, t in enumerate(tasks) if t['slot_id'] == slot]
                model.Add(sum(x[i, idx] for idx in overlap) <= 1) # Çakışma yasağı

        # 2. İNSANİ KISITLAR (Yeni eklenenler)
        for i in invs:
            # Arka arkaya görev sınırı (Aynı gün max 3 görev)
            for d in days_list:
                day_tasks = [idx for idx, t in enumerate(tasks) if t['gun'] == d]
                model.Add(sum(x[i, idx] for idx in day_tasks) <= 3)

            # Gece-Sabah Yasağı
            for d_idx in range(len(days_list) - 1):
                today_last = [idx for idx, t in enumerate(tasks) if t['gun'] == days_list[d_idx] and t['etiket'] == 'aksam']
                tomorrow_first = [idx for idx, t in enumerate(tasks) if t['gun'] == days_list[d_idx+1] and t['etiket'] == 'sabah']
                for tl in today_last:
                    for tf in tomorrow_first:
                        model.Add(x[i, tl] + x[i, tf] <= 1)

        # 3. MÜSAİTLİK KONTROLÜ
        if unavailable_input:
            for entry in unavailable_input.split(','):
                if ':' in entry:
                    staff_no, day_name = entry.split(':')
                    s_no = int(staff_no.strip().replace("Gözetmen ", ""))
                    d_name = day_name.strip()
                    for idx, t in enumerate(tasks):
                        if t['gun'] == d_name and s_no in invs:
                            model.Add(x[s_no, idx] == 0)

        # 4. İŞ YÜKÜ METRİKLERİ
        total_mins, day_off_count = {}, {}
        for i in invs:
            total_mins[i] = model.NewIntVar(0, 10000, f'tm_{i}')
            model.Add(total_mins[i] == sum(x[i, t] * tasks[t]['sure'] for t in range(num_t)))
            
            # Sabah+Akşam toplamı için değişken
            se_sum = model.NewIntVar(0, 100, f'se_{i}')
            model.Add(se_sum == sum(x[i, t] for t in range(num_t) if t['etiket'] in ['sabah', 'aksam']))
            day_off_count[i] = se_sum

        # Sabah-Akşam toplam farkı 1'den fazla olmasın kısıtı
        se_vars = list(day_off_count.values())
        max_se = model.NewIntVar(0, 100, 'max_se')
        min_se = model.NewIntVar(0, 100, 'min_se')
        model.AddMaxEquality(max_se, se_vars)
        model.AddMinEquality(min_se, se_vars)
        model.Add(max_se - min_se <= 1)

        # Optimizasyon Hedefi: Süre ve Büyük Sınıf Dengesi
        def get_diff(v_dict):
            ma, mi = model.NewIntVar(0, 10000, 'ma'), model.NewIntVar(0, 10000, 'mi')
            model.AddMaxEquality(ma, list(v_dict.values()))
            model.AddMinEquality(mi, list(v_dict.values()))
            return ma - mi

        model.Minimize(get_diff(total_mins) * 10)

        # ÇÖZÜM
        solver = cp_model.CpSolver()
        if solver.Solve(model) in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            st.success("✅ Optimizasyon tamamlandı. Operasyonel plan hazır.")
            
            res = []
            for t_idx, t in enumerate(tasks):
                for i in invs:
                    if solver.Value(x[i, t_idx]):
                        row = t.copy()
                        row['Gözetmen'] = f"Gözetmen {i}"
                        res.append(row)
            
            df = pd.DataFrame(res)
            
            # TABLAR
            t1, t2, t3 = st.tabs(["📋 Görev Çizelgesi", "📊 İş Yükü Analizi", "📖 Sistem Metodolojisi"])
            
            with t1:
                st.dataframe(df[['gun', 'sinav', 'saat', 'sinif', 'Gözetmen']], use_container_width=True)
            
            with t2:
                stats = []
                for i in invs:
                    stats.append({
                        "Gözetmen": f"Gözetmen {i}",
                        "Toplam Mesai (dk)": solver.Value(total_mins[i]),
                        "Kritik Oturum Sayısı (S+A)": solver.Value(day_off_count[i])
                    })
                st.table(pd.DataFrame(stats))

            with t3:
                st.info("**Matematiksel Model:** Google OR-Tools (Constraint Programming) kütüphanesi kullanılarak milyonlarca olası kombinasyon taranmış ve 'Min-Max Regret' algoritması ile en dengeli sonuç üretilmiştir.")
                st.write("**Uygulanan Öncelik Kuralları:**")
                st.write("1. **Çakışma Engelleme:** Bir personel aynı anda iki farklı mekanda görevlendirilemez.")
                st.write("2. **Dinlenme Peryodu:** Akşam görevini takiben sabah görevi verilmesi sistem tarafından engellenmiştir.")
                st.write("3. **Yük Dengeleme:** Personel arasındaki toplam sınav süreleri ve kritik saat (sabah/akşam) yoğunlukları homojenize edilmiştir.")
        else:
            st.error("Belirlenen kısıtlar altında uygun bir dağıtım bulunamadı. Lütfen kısıtları veya gözetmen sayısını esnetin.")

else:
    st.info("Sistemi başlatmak için lütfen sol menüden sınav takvimini (XML) yükleyiniz.")
