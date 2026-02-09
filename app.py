import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import io
import re

# Sayfa Yapılandırması
st.set_page_config(page_title="Gözetmen Planlama Sistemi", layout="wide")
st.title("🏛️ Gözetmen Optimizasyon ve Görev Planlama Sistemi")

# --- YARDIMCI FONKSİYONLAR ---
def to_min(time_str):
    if not time_str or pd.isna(time_str): return None
    try:
        clean_time = re.sub(r'[^0-9:]', ':', str(time_str).replace('.', ':')).strip()
        if ':' not in clean_time: return None
        h, m = map(int, clean_time.split(':')[:2])
        return h * 60 + m
    except:
        return None

def parse_excel(file):
    df = pd.read_excel(file)
    df.columns = [c.strip().upper() for c in df.columns]
    
    raw_tasks = []
    days_order = []
    
    for _, row in df.iterrows():
        if pd.isna(row.get('GÜN')) or pd.isna(row.get('SAAT')): continue
            
        gun_adi = str(row['GÜN']).strip()
        if gun_adi not in days_order: days_order.append(gun_adi)
            
        ders_adi = str(row.get('DERSLER', 'Bilinmeyen Ders'))
        saat_araligi = str(row['SAAT'])
        sinav_yerleri = str(row.get('SINAV YERİ', ''))
        
        try:
            bas_str, bit_str = saat_araligi.split('-')
            bas_dakika = to_min(bas_str)
            bit_dakika = to_min(bit_str)
            sure = bit_dakika - bas_dakika
        except: continue

        sinif_listesi = [s.strip() for s in sinav_yerleri.replace(',', '-').split('-') if s.strip()]
        
        for s in sinif_listesi:
            raw_tasks.append({
                'Gün': gun_adi, 'Ders Adı': ders_adi, 'Sınav Saati': saat_araligi,
                'bas_dk': bas_dakika, 'Sınav Salonu': s, 'Süre (Dakika)': sure,
                'bas_str': bas_str.strip()
            })

    # Dinamik Sabah/Akşam Etiketleme
    tasks = []
    all_rooms = set()
    for d in days_order:
        day_tasks = [t for t in raw_tasks if t['Gün'] == d]
        if not day_tasks: continue
        
        min_start = min(t['bas_dk'] for t in day_tasks) # Günün ilk sınav saati
        
        for t in day_tasks:
            # Etiketleme Mantığı
            label = 'Normal'
            if t['bas_dk'] == min_start: label = 'Sabah'
            elif t['bas_dk'] >= 960: label = 'Akşam' # 16:00 kuralı devam ediyor
            
            t['Mesai Türü'] = label
            t['slot_id'] = f"{t['Gün']}_{t['bas_str']}"
            all_rooms.add(t['Sınav Salonu'])
            tasks.append(t)
            
    return tasks, sorted(list(all_rooms)), days_order

# --- YAN MENÜ ---
st.sidebar.header("⚙️ Sistem Parametreleri")
uploaded_file = st.sidebar.file_uploader("Sınav Takvimi (Excel)", type=["xlsx", "xls"])
staff_count = st.sidebar.number_input("Toplam Personel Sayısı", min_value=1, value=6)

st.sidebar.divider()
st.sidebar.subheader("🚫 Görev Muafiyet Tanımları")
unavailable_days_input = st.sidebar.text_area("Günlük Muafiyet (PersonelNo:Gün)", placeholder="Örn: 1:Pazartesi")
unavailable_times_input = st.sidebar.text_area("Saatlik Muafiyet (PersonelNo:Saat)", placeholder="Örn: 1:16:00-21:00")

st.sidebar.divider()
st.sidebar.header("🎯 Dağılım Stratejileri (Toplam: 100)")
w_total = st.sidebar.number_input("Toplam İş Yükü Dengesi", 0, 100, 20)
w_big = st.sidebar.number_input("Büyük Salon Dağılımı", 0, 100, 20)
w_morn = st.sidebar.number_input("Sabah Seansı Dengesi", 0, 100, 20)
w_eve = st.sidebar.number_input("Akşam Seansı Dengesi", 0, 100, 20)
w_sa_total = st.sidebar.number_input("Kritik Seans Toplamı Dengesi", 0, 100, 20)

if uploaded_file:
    tasks, rooms, days_list = parse_excel(uploaded_file)
    big_rooms = st.sidebar.multiselect("Büyük Salon Olarak Tanımlananlar", rooms, default=[r for r in rooms if r in ['301', '303', '304']])
    
    if st.sidebar.button("Optimizasyon Sürecini Başlat"):
        total_weight = w_total + w_big + w_morn + w_eve + w_sa_total
        if total_weight != 100:
            st.sidebar.error("⚠️ Strateji ağırlıkları toplamı 100 birim olmalıdır.")
        else:
            with st.spinner('Matematiksel modelleme üzerinden görev dağılımı yapılıyor...'):
                model = cp_model.CpModel()
                invs = list(range(1, staff_count + 1))
                num_t = len(tasks)
                x = {(i, t): model.NewBoolVar(f'x_{i}_{t}') for i in invs for t in range(num_t)}
                
                # Saatlik muafiyeti olan personelleri belirle
                restricted_staff = set()
                if unavailable_times_input:
                    for entry in unavailable_times_input.split(','):
                        if ':' in entry:
                            try: restricted_staff.add(int(entry.split(':')[0].strip()))
                            except: pass

                evening_clusters = []
                for i in invs:
                    for slot in set(t['slot_id'] for t in tasks):
                        overlap = [idx for idx, t in enumerate(tasks) if t['slot_id'] == slot]
                        model.Add(sum(x[i, idx] for idx in overlap) <= 1)
                    
                    for d in days_list:
                        day_tasks_idx = [idx for idx, t in enumerate(tasks) if t['Gün'] == d]
                        model.Add(sum(x[i, idx] for idx in day_tasks_idx) <= 4)
                        
                        eve_tasks_in_day = [idx for idx in day_tasks_idx if tasks[idx]['Mesai Türü'] == 'Akşam']
                        if len(eve_tasks_in_day) > 1:
                            has_multiple_eve = model.NewBoolVar(f'multi_eve_{i}_{d}')
                            model.Add(sum(x[i, idx] for idx in eve_tasks_in_day) >= 2).OnlyEnforceIf(has_multiple_eve)
                            evening_clusters.append(has_multiple_eve)

                for t in range(num_t):
                    model.Add(sum(x[i, t] for i in invs) == 1)

                # Muafiyet Kısıtları (Hard Constraints)
                if unavailable_days_input:
                    for entry in unavailable_days_input.split(','):
                        try:
                            s_no, d_name = entry.split(':')
                            s_no = int(s_no.strip()); d_name = d_name.strip().lower()
                            if s_no in invs:
                                for idx, t in enumerate(tasks):
                                    if t['Gün'].strip().lower() == d_name: model.Add(x[s_no, idx] == 0)
                        except: continue

                if unavailable_times_input:
                    for entry in unavailable_times_input.split(','):
                        try:
                            parts = entry.split(':', 1)
                            s_no, t_range = int(parts[0]), parts[1].strip()
                            st_str, en_str = t_range.split('-')
                            ex_s, ex_e = to_min(st_str), to_min(en_str)
                            if s_no in invs:
                                for idx, t in enumerate(tasks):
                                    ts, te = t['bas_dk'], t['bas_dk'] + t['Süre (Dakika)']
                                    if max(ts, ex_s) < min(te, ex_e): model.Add(x[s_no, idx] == 0)
                        except: continue

                # İstatistik Değişkenleri
                total_mins, big_mins, total_exams, morn_cnt, eve_cnt, critical_sum = {}, {}, {}, {}, {}, {}
                for i in invs:
                    total_mins[i] = model.NewIntVar(0, 10000, f'tm_{i}')
                    big_mins[i] = model.NewIntVar(0, 10000, f'bm_{i}')
                    total_exams[i] = model.NewIntVar(0, 100, f'te_{i}')
                    morn_cnt[i] = model.NewIntVar(0, 100, f'mc_{i}')
                    eve_cnt[i] = model.NewIntVar(0, 100, f'ec_{i}')
                    critical_sum[i] = model.NewIntVar(0, 200, f'cs_{i}')
                    
                    model.Add(total_mins[i] == sum(x[i, t] * tasks[t]['Süre (Dakika)'] for t in range(num_t)))
                    model.Add(big_mins[i] == sum(x[i, t] * tasks[t]['Süre (Dakika)'] for t in range(num_t) if tasks[t]['Sınav Salonu'] in big_rooms))
                    model.Add(total_exams[i] == sum(x[i, t] for t in range(num_t)))
                    model.Add(morn_cnt[i] == sum(x[i, t] for t in range(num_t) if tasks[t]['Mesai Türü'] == 'Sabah'))
                    model.Add(eve_cnt[i] == sum(x[i, t] for t in range(num_t) if tasks[t]['Mesai Türü'] == 'Akşam'))
                    model.Add(critical_sum[i] == morn_cnt[i] + eve_cnt[i])

                # SERT ADALET KISITI: Max Sınav - Min Sınav <= 2
                max_e, min_e = model.NewIntVar(0, 100, 'max_e'), model.NewIntVar(0, 100, 'min_e')
                model.AddMaxEquality(max_e, [total_exams[i] for i in invs])
                model.AddMinEquality(min_e, [total_exams[i] for i in invs])
                model.Add(max_e - min_e <= 2)

                # Dinamik Dengeleme Fonksiyonu
                def get_diff(v_dict, filtered_invs, name):
                    if not filtered_invs: return 0
                    subset = [v_dict[i] for i in filtered_invs]
                    ma, mi = model.NewIntVar(0, 10000, f'ma_{name}'), model.NewIntVar(0, 10000, f'mi_{name}')
                    model.AddMaxEquality(ma, subset); model.AddMinEquality(mi, subset)
                    d = model.NewIntVar(0, 10000, f'd_{name}'); model.Add(d == ma - mi)
                    return d

                # Puanlamaya dahil edilecek personeller (Kısıtlılar hariç)
                scoring_invs = [i for i in invs if i not in restricted_staff]

                model.Minimize(
                    get_diff(total_mins, invs, "t") * w_total * 100 +
                    get_diff(big_mins, invs, "b") * w_big * 100 +
                    get_diff(morn_cnt, scoring_invs, "m") * w_morn * 1000 + 
                    get_diff(eve_cnt, scoring_invs, "e") * w_eve * 1000 +
                    get_diff(critical_sum, scoring_invs, "c") * w_sa_total * 1000 -
                    sum(evening_clusters) * 5000 
                )

                solver = cp_model.CpSolver()
                solver.parameters.max_time_in_seconds = 30.0
                if solver.Solve(model) in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
                    st.success("✅ Kurumsal görev planlaması başarıyla oluşturulmuştur.")
                    res = []
                    for t_idx, t in enumerate(tasks):
                        for i in invs:
                            if solver.Value(x[i, t_idx]):
                                row = t.copy(); row['Görevli Personel'] = i; res.append(row)
                    
                    df_res = pd.DataFrame(res)
                    tab1, tab2, tab3 = st.tabs(["📋 Görev Çizelgesi", "📊 Görev Dağılım İstatistikleri", "📖 Uygulama Metodolojisi"])
                    with tab1:
                        final_df = df_res[['Gün', 'Ders Adı', 'Sınav Saati', 'Sınav Salonu', 'Görevli Personel']]
                        st.dataframe(final_df, use_container_width=True)
                        buffer = io.BytesIO()
                        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                            final_df.to_excel(writer, index=False, sheet_name='Gorev_Plani')
                        st.download_button("📥 Çizelgeyi Excel İndir", buffer.getvalue(), "kurumsal_gozetmen_plani.xlsx")
                    
                    with tab2:
                        stats = []
                        for i in invs:
                            tag = " (Muaf)" if i in restricted_staff else ""
                            stats.append({
                                "Personel": f"{i}{tag}", "Top. Mesai (Dk)": solver.Value(total_mins[i]), 
                                "Büyük Salon (Dk)": solver.Value(big_mins[i]), "Toplam Sınav Sayısı": solver.Value(total_exams[i]),
                                "Sabah Seansı": solver.Value(morn_cnt[i]), "Akşam Seansı": solver.Value(eve_cnt[i]), 
                                "Kritik Seans Toplamı": solver.Value(critical_sum[i])
                            })
                        st.table(pd.DataFrame(stats))
                    
                    with tab3:
                        st.subheader("Sistem Nasıl Çalışır?")
                        st.markdown("""
                        Bu yazılım, personel görevlendirme sürecini bilimsel yöntemlerle çözer. İşte temel çalışma adımları:

                        ### 1. Dinamik Seans Sınıflandırması
                        Sistem, Excel dosyasındaki sınav saatlerini her gün için ayrı ayrı analiz eder. Her takvim gününün **başlangıç saati en erkene denk gelen sınavı** otomatik olarak **"Sabah Seansı"** olarak tanımlanır. Saat 16:00 ve sonrası ise "Akşam Mesaisi" olarak etiketlenir.

                        ### 2. Sert Kurallar (Asla Esnetilmez)
                        * **Çakışma Engeli:** Bir personel aynı saat diliminde iki farklı görev alamaz.
                        * **±2 Sınav Adalet Kuralı:** En çok sınav görevine sahip personel ile en az görev alan personel arasındaki fark **asla 2'yi geçemez.** (Örn: En az alan 8 sınav görevindeyse, en çok alan max 10 görev alabilir).
                        * **Muafiyet Kontrolü:** Günlük veya saatlik girilen tüm personel kısıtları sisteme sert kural olarak işlenir.

                        ### 3. Akıllı Dengeleme ve Muafiyet Yönetimi
                        Sistem tüm personeli eşit iş yüküne ulaştırmaya çalışırken, **saatlik muafiyeti olan (örn: sadece gündüz çalışan) personelleri sabah/akşam dengesi hesaplamasından hariç tutar.** Böylece kısıtlı bir personelin mecburen düşük olan akşam mesai sayısı, genel "Adalet Skoru"nu bozmaz; diğer personel kendi içinde dengelenmeye devam eder.

                        ### 4. Matematiksel Optimizasyon
                        Sistem milyonlarca olası kombinasyonu aşağıdaki amaç fonksiyonu üzerinden değerlendirerek en verimli olanı seçer:
                        """)
                        st.latex(r"Minimize: \sum_{i \in Criteria} (Weight_i \times (Max_i - Min_i)) - Reward_{cluster}")
                else:
                    st.error("❌ Mevcut kısıtlar altında uygun bir senaryo üretilemedi. Personel sayısını artırmayı veya ±2 kuralını karşılamak için muafiyetleri azaltmayı deneyiniz.")
